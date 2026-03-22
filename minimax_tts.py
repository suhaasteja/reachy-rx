"""Direct Minimax TTS client — no Agora, no RTC.

Calls the Minimax T2A HTTP API, decodes the audio, resamples to 16kHz,
and pushes PCM frames directly to Reachy's speaker.

Usage:
    tts = MinimaxTTSClient(mini=mini)
    tts.speak("Hello!")      # blocking — returns when audio finishes playing
    tts.speaking             # True while audio is being pushed

Requires env vars (from .env):
    MINIMAX_TTS_KEY
    MINIMAX_TTS_GROUP_ID
"""

import io
import json
import logging
import os
import struct
import threading
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

import numpy as np

logger = logging.getLogger(__name__)

# Reachy Mini speaker
REACHY_SAMPLE_RATE = 16000
VOLUME_BOOST = 1.5  # multiply PCM samples before pushing to speaker

# Minimax API
MINIMAX_API_URL = "https://api.minimax.io/v1/t2a_v2"
DEFAULT_MODEL = "speech-2.6-turbo"
DEFAULT_VOICE = "English_Upbeat_Woman"


def _load_env():
    """Load .env from project root if keys aren't already set."""
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_env()


def _decode_wav_to_float32(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    """Decode WAV bytes to float32 numpy array. Returns (samples, sample_rate)."""
    buf = io.BytesIO(wav_bytes)

    # Read RIFF header
    riff = buf.read(4)
    if riff != b"RIFF":
        raise ValueError(f"Not a WAV file (got {riff!r})")
    buf.read(4)  # file size
    wave = buf.read(4)
    if wave != b"WAVE":
        raise ValueError(f"Not a WAVE file (got {wave!r})")

    fmt_parsed = False
    sample_rate = 0
    num_channels = 0
    bits_per_sample = 0
    data_bytes = b""

    while True:
        chunk_id = buf.read(4)
        if len(chunk_id) < 4:
            break
        chunk_size = struct.unpack("<I", buf.read(4))[0]

        if chunk_id == b"fmt ":
            fmt_data = buf.read(chunk_size)
            audio_format = struct.unpack("<H", fmt_data[0:2])[0]
            num_channels = struct.unpack("<H", fmt_data[2:4])[0]
            sample_rate = struct.unpack("<I", fmt_data[4:8])[0]
            bits_per_sample = struct.unpack("<H", fmt_data[14:16])[0]
            fmt_parsed = True
        elif chunk_id == b"data":
            data_bytes = buf.read(chunk_size)
        else:
            buf.read(chunk_size)

    if not fmt_parsed:
        raise ValueError("WAV: no fmt chunk found")
    if not data_bytes:
        raise ValueError("WAV: no data chunk found")

    # Convert to float32
    if bits_per_sample == 16:
        n_samples = len(data_bytes) // 2
        int16 = struct.unpack(f"<{n_samples}h", data_bytes)
        samples = np.array(int16, dtype=np.float32) / 32768.0
    elif bits_per_sample == 32:
        n_samples = len(data_bytes) // 4
        samples = np.frombuffer(data_bytes, dtype=np.float32).copy()
    else:
        raise ValueError(f"Unsupported bits_per_sample: {bits_per_sample}")

    # Mix to mono if stereo
    if num_channels > 1:
        samples = samples.reshape(-1, num_channels).mean(axis=1)

    return samples, sample_rate


def _resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Simple linear resample. Good enough for speech."""
    if src_rate == dst_rate:
        return samples
    ratio = dst_rate / src_rate
    n_out = int(len(samples) * ratio)
    indices = np.arange(n_out) / ratio
    indices = np.clip(indices, 0, len(samples) - 1)
    # Linear interpolation
    idx_floor = indices.astype(np.int64)
    idx_ceil = np.minimum(idx_floor + 1, len(samples) - 1)
    frac = (indices - idx_floor).astype(np.float32)
    return samples[idx_floor] * (1 - frac) + samples[idx_ceil] * frac


class MinimaxTTSClient:
    """Direct Minimax HTTP TTS → Reachy speaker. No Agora."""

    def __init__(self, mini: "ReachyMini"):
        self.mini = mini
        self.api_key = os.environ.get("MINIMAX_TTS_KEY", "")
        self.group_id = os.environ.get("MINIMAX_TTS_GROUP_ID", "")
        self.speaking = False
        self._thread: Optional[threading.Thread] = None

        if not self.api_key:
            logger.warning("MINIMAX_TTS_KEY not set — TTS disabled")
        if not self.group_id:
            logger.warning("MINIMAX_TTS_GROUP_ID not set — TTS disabled")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.group_id)

    def start(self) -> None:
        """No-op — kept for API compat with the Agora TTSClient."""
        if self.enabled:
            logger.info("MinimaxTTS: ready")
        else:
            logger.warning("MinimaxTTS: disabled (missing credentials)")

    def _synthesize(self, text: str) -> Optional[bytes]:
        """Call Minimax T2A API, return WAV bytes or None on failure."""
        payload = json.dumps({
            "model": DEFAULT_MODEL,
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": DEFAULT_VOICE,
                "speed": 1.0,
                "vol": 2.0,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "format": "wav",
                "channel": 1,
            },
            "output_format": "hex",
            "language_boost": "English",
        }).encode()

        req = Request(
            MINIMAX_API_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
        except URLError as e:
            logger.error(f"MinimaxTTS: API request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"MinimaxTTS: unexpected error: {e}")
            return None

        status = body.get("base_resp", {}).get("status_code", -1)
        if status != 0:
            msg = body.get("base_resp", {}).get("status_msg", "unknown")
            logger.error(f"MinimaxTTS: API error {status}: {msg}")
            return None

        hex_audio = body.get("data", {}).get("audio", "")
        if not hex_audio:
            logger.error("MinimaxTTS: no audio in response")
            return None

        return bytes.fromhex(hex_audio)

    def _play(self, text: str) -> None:
        """Synthesize and push audio to speaker. Runs on background thread."""
        self.speaking = True
        try:
            wav_bytes = self._synthesize(text)
            if wav_bytes is None:
                return

            samples, src_rate = _decode_wav_to_float32(wav_bytes)
            samples = _resample(samples, src_rate, REACHY_SAMPLE_RATE)

            # Boost volume and clip to [-1, 1]
            samples = np.clip(samples * VOLUME_BOOST, -1.0, 1.0).astype(np.float32)

            # Push in chunks (~100ms each) for smooth playback
            chunk_size = REACHY_SAMPLE_RATE // 10  # 1600 samples = 100ms
            for i in range(0, len(samples), chunk_size):
                chunk = samples[i : i + chunk_size]
                try:
                    self.mini.media.push_audio_sample(chunk.astype(np.float32))
                except Exception as e:
                    logger.debug(f"MinimaxTTS: push failed: {e}")
                    break

            logger.info(f"MinimaxTTS: played {len(samples)/REACHY_SAMPLE_RATE:.1f}s audio")
        except Exception as e:
            logger.error(f"MinimaxTTS: playback error: {e}")
        finally:
            self.speaking = False

    def speak(self, text: str) -> None:
        """Speak text through Reachy's speaker. Non-blocking.

        Drops the request if already speaking.
        """
        if not self.enabled:
            return
        if not text or not text.strip():
            return
        if self.speaking:
            logger.debug(f"MinimaxTTS: busy, dropping: {text[:50]}")
            return

        self._thread = threading.Thread(
            target=self._play, args=(text.strip(),), daemon=True, name="minimax-tts",
        )
        self._thread.start()

    def stop_speaking(self) -> None:
        """No-op — can't interrupt mid-push. Kept for API compat."""
        pass

    def shutdown(self) -> None:
        """No-op — no persistent connections to clean up."""
        logger.info("MinimaxTTS: shutdown")
