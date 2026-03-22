"""Agora-based TTS client — joins an RTC channel, triggers speech via the
Node server, receives PCM audio frames, and pushes them to Reachy's speaker.

Usage:
    tts = TTSClient(app_id="...", mini=mini)
    await tts.connect()
    tts.speak("Hello!")          # non-blocking
    tts.shutdown()               # cleanup on exit

Requires:
    - tts/server.js running on localhost:3456
    - AGORA_APP_ID in .env
"""

import asyncio
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

REACHY_SAMPLE_RATE = 16000
TTS_SERVER_URL = "http://localhost:3456"
AGENT_UID = 1000
CLIENT_UID = 12345
CHANNEL_NAME = "tts_channel"


def _load_env():
    """Load .env from project root if keys aren't already in environ."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
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


def _post_speak(text: str, server_url: str = TTS_SERVER_URL) -> bool:
    """Tell the Node TTS server to start an Agora agent that speaks `text`."""
    try:
        data = json.dumps({"text": text}).encode()
        req = Request(
            f"{server_url}/speak",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            logger.info(f"TTS agent started: {result}")
            return True
    except URLError as e:
        logger.error(f"TTS server unreachable ({server_url}): {e}")
        return False
    except Exception as e:
        logger.error(f"TTS /speak failed: {e}")
        return False


def _post_stop(server_url: str = TTS_SERVER_URL) -> None:
    """Stop any active TTS agent."""
    try:
        req = Request(
            f"{server_url}/stop",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception:
        pass


def _pcm_frame_to_float32(frame) -> np.ndarray:
    """Convert an Agora PcmAudioFrame (16-bit PCM bytes) to float32 numpy array."""
    pcm_bytes = bytes(frame.data)
    num_samples = len(pcm_bytes) // 2
    if num_samples == 0:
        return np.array([], dtype=np.float32)
    int16_samples = struct.unpack(f"<{num_samples}h", pcm_bytes)
    return np.array(int16_samples, dtype=np.float32) / 32768.0


class TTSClient:
    """Agora RTC-based TTS client that pushes audio to Reachy's speaker.

    Runs its own asyncio event loop in a background thread so the main
    synchronous vision loop isn't blocked.
    """

    def __init__(self, mini: "ReachyMini", app_id: Optional[str] = None,
                 server_url: str = TTS_SERVER_URL):
        self.mini = mini
        self.app_id = app_id or os.environ.get("AGORA_APP_ID", "")
        self.server_url = server_url

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._channel = None
        self._rtc = None
        self._connected = False
        self._audio_task: Optional[asyncio.Task] = None

        if not self.app_id:
            logger.warning("AGORA_APP_ID not set — TTS will be disabled")

    @property
    def enabled(self) -> bool:
        return bool(self.app_id)

    def start(self) -> None:
        """Start the background event loop and connect to the Agora channel."""
        if not self.enabled:
            logger.warning("TTS disabled (no AGORA_APP_ID)")
            return

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="tts-agora"
        )
        self._thread.start()

        # Block until connected (with timeout)
        event = threading.Event()

        async def _connect_and_signal():
            await self._connect()
            event.set()

        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(_connect_and_signal(), loop=self._loop)
        )
        if not event.wait(timeout=10):
            logger.error("TTS: timed out connecting to Agora channel")

    def _run_loop(self):
        """Run the asyncio event loop in the background thread."""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _connect(self):
        """Connect to the Agora RTC channel and subscribe to agent audio."""
        from agora_realtime_ai_api.rtc import RtcEngine, RtcOptions

        self._rtc = RtcEngine(appid=self.app_id, appcert="")
        options = RtcOptions(
            channel_name=CHANNEL_NAME,
            uid=CLIENT_UID,
            sample_rate=REACHY_SAMPLE_RATE,
            channels=1,
        )
        self._channel = self._rtc.create_channel(options)
        await self._channel.connect()
        logger.info(f"TTS: connected to Agora channel '{CHANNEL_NAME}' as UID {CLIENT_UID}")

        # Subscribe to agent audio
        await self._channel.subscribe_audio(AGENT_UID)
        logger.info(f"TTS: subscribed to agent UID {AGENT_UID}")
        self._connected = True

        # Start the audio forwarding loop
        self._audio_task = asyncio.ensure_future(self._forward_audio())

    async def _forward_audio(self):
        """Continuously read PCM frames from the agent and push to Reachy."""
        while self._connected:
            audio_stream = self._channel.get_audio_frames(AGENT_UID)
            if audio_stream is None:
                await asyncio.sleep(0.1)
                continue

            try:
                async for frame in audio_stream:
                    if not self._connected:
                        break
                    samples = _pcm_frame_to_float32(frame)
                    if len(samples) > 0:
                        try:
                            self.mini.media.push_audio_sample(samples)
                        except Exception as e:
                            logger.debug(f"TTS: push_audio_sample failed: {e}")
            except Exception as e:
                logger.debug(f"TTS: audio stream error: {e}")
                await asyncio.sleep(0.5)

    def speak(self, text: str) -> None:
        """Trigger TTS — tells the Node server to start the agent speaking.

        Non-blocking: the HTTP call runs in a thread, audio arrives via
        the already-connected Agora channel.
        """
        if not self.enabled or not self._connected:
            return
        if not text or not text.strip():
            return

        # Fire the /speak request in a thread so it doesn't block the vision loop
        threading.Thread(
            target=_post_speak,
            args=(text, self.server_url),
            daemon=True,
        ).start()

    def stop_speaking(self) -> None:
        """Stop the current TTS agent."""
        threading.Thread(
            target=_post_stop,
            args=(self.server_url,),
            daemon=True,
        ).start()

    def shutdown(self) -> None:
        """Clean up Agora resources."""
        self._connected = False
        _post_stop(self.server_url)

        if self._audio_task and not self._audio_task.done():
            self._audio_task.cancel()

        if self._channel and self._loop:
            future = asyncio.run_coroutine_threadsafe(
                self._channel.disconnect(), self._loop
            )
            try:
                future.result(timeout=5)
            except Exception:
                pass

        if self._rtc:
            self._rtc.destroy()

        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
