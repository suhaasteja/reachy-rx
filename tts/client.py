"""Agora TTS client — pure Python, no Node server needed.

Starts an Agora Conversational AI agent that speaks text via minimax TTS,
joins the same RTC channel to receive the audio stream, and pushes PCM
frames to Reachy's speaker.

Usage:
    tts = TTSClient(mini=mini)
    tts.start()                  # connect to Agora channel
    tts.speak("Hello!")          # non-blocking
    tts.shutdown()               # cleanup on exit

Requires env vars (from .env):
    AGORA_APP_ID, AGORA_RESTFUL_KEY, AGORA_RESTFUL_SECRET
Optional:
    AGORA_CHANNEL_TOKEN
"""

import asyncio
import base64
import json
import logging
import os
import struct
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

import numpy as np

logger = logging.getLogger(__name__)

# Reachy Mini speaker config
REACHY_SAMPLE_RATE = 16000

# Agora channel config (must match what the agent joins)
AGENT_UID = 1000
CLIENT_UID = 12345
CHANNEL_NAME = "tts_channel"

# Agora Conversational AI REST API
AGORA_API_BASE = "https://api.agora.io/api/conversational-ai-agent/v2/projects"


def _load_env():
    """Load .env from project root if keys aren't already set."""
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


# ---------------------------------------------------------------------------
# Agora Conversational AI REST API helpers
# ---------------------------------------------------------------------------

def _agora_auth_header(rest_key: str, rest_secret: str) -> str:
    """Build the Basic auth header for the Agora REST API."""
    creds = f"{rest_key}:{rest_secret}"
    return "Basic " + base64.b64encode(creds.encode()).decode()


def _start_tts_agent(text: str, app_id: str, auth_header: str,
                     channel_token: str = "") -> Optional[str]:
    """Start an Agora Conversational AI agent that speaks `text`.

    Returns the agent_id on success, or None on failure.
    """
    url = f"{AGORA_API_BASE}/{app_id}/join"
    payload = json.dumps({
        "name": f"tts_{int(time.time() * 1000)}",
        "preset": "openai_gpt_4_1_mini,minimax_speech_2_6_turbo",
        "properties": {
            "channel": CHANNEL_NAME,
            "token": channel_token,
            "agent_rtc_uid": str(AGENT_UID),
            "remote_rtc_uids": [str(CLIENT_UID)],
            "enable_string_uid": False,
            "idle_timeout": 30,
            "asr": {
                "language": "en-US",
            },
            "llm": {
                "system_messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a text-to-speech service. When the user "
                            "sends you text, repeat it back EXACTLY word for "
                            "word. Do not add anything. Do not explain. Just "
                            "say exactly what was sent."
                        ),
                    },
                ],
                "greeting_message": text,
                "max_history": 1,
            },
            "tts": {
                "vendor": "minimax",
                "params": {
                    "voice_setting": {
                        "voice_id": "English_Strong-WilledBoy",
                    },
                    "audio_setting": {
                        "sample_rate": 44100,
                    },
                },
            },
        },
    }).encode()

    req = Request(
        url,
        data=payload,
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            agent_id = data.get("agent_id")
            logger.info(f"TTS agent started: {agent_id}")
            return agent_id
    except URLError as e:
        logger.error(f"Failed to start TTS agent: {e}")
        return None
    except Exception as e:
        logger.error(f"TTS agent start error: {e}")
        return None


def _stop_tts_agent(agent_id: str, app_id: str, auth_header: str) -> None:
    """Stop an Agora Conversational AI agent."""
    url = f"{AGORA_API_BASE}/{app_id}/agents/{agent_id}/leave"
    req = Request(
        url,
        data=b"",
        headers={"Authorization": auth_header},
        method="POST",
    )
    try:
        with urlopen(req, timeout=5) as resp:
            resp.read()
        logger.debug(f"TTS agent stopped: {agent_id}")
    except Exception as e:
        logger.debug(f"TTS agent stop failed (may already be gone): {e}")


# ---------------------------------------------------------------------------
# PCM conversion
# ---------------------------------------------------------------------------

def _pcm_frame_to_float32(frame) -> np.ndarray:
    """Convert an Agora PcmAudioFrame (16-bit PCM bytes) to float32 numpy."""
    pcm_bytes = bytes(frame.data)
    num_samples = len(pcm_bytes) // 2
    if num_samples == 0:
        return np.array([], dtype=np.float32)
    int16_samples = struct.unpack(f"<{num_samples}h", pcm_bytes)
    return np.array(int16_samples, dtype=np.float32) / 32768.0


# ---------------------------------------------------------------------------
# TTSClient
# ---------------------------------------------------------------------------

class TTSClient:
    """Agora RTC-based TTS that pushes audio directly to Reachy's speaker.

    Runs an asyncio event loop in a background thread. On speak(), starts
    an Agora Conversational AI agent via REST API, receives its audio
    stream over RTC, and forwards PCM frames to Reachy.
    """

    def __init__(self, mini: "ReachyMini"):
        self.mini = mini

        # Agora credentials
        self.app_id = os.environ.get("AGORA_APP_ID", "")
        self.rest_key = os.environ.get("AGORA_RESTFUL_KEY", "")
        self.rest_secret = os.environ.get("AGORA_RESTFUL_SECRET", "")
        self.channel_token = os.environ.get("AGORA_CHANNEL_TOKEN", "")

        self._auth_header = ""
        if self.rest_key and self.rest_secret:
            self._auth_header = _agora_auth_header(self.rest_key, self.rest_secret)

        # State
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._channel = None
        self._rtc = None
        self._connected = False
        self._audio_task: Optional[asyncio.Task] = None
        self._active_agent_id: Optional[str] = None

        if not self.app_id:
            logger.warning("AGORA_APP_ID not set — TTS disabled")
        if not self._auth_header:
            logger.warning("AGORA_RESTFUL_KEY/SECRET not set — TTS disabled")

    @property
    def enabled(self) -> bool:
        return bool(self.app_id and self._auth_header)

    def start(self) -> None:
        """Start background event loop and connect to Agora RTC channel."""
        if not self.enabled:
            logger.warning("TTS disabled — missing Agora credentials")
            return

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="tts-agora",
        )
        self._thread.start()

        # Block until connected
        event = threading.Event()

        async def _connect_and_signal():
            await self._connect()
            event.set()

        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(_connect_and_signal(), loop=self._loop)
        )
        if not event.wait(timeout=10):
            logger.error("TTS: timed out connecting to Agora channel")
        else:
            logger.info("TTS: ready")

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _connect(self):
        """Connect to Agora RTC channel and subscribe to agent audio."""
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
        logger.info(f"TTS: joined Agora channel '{CHANNEL_NAME}' as UID {CLIENT_UID}")

        # Subscribe to agent's audio
        await self._channel.subscribe_audio(AGENT_UID)
        logger.info(f"TTS: subscribed to agent UID {AGENT_UID}")
        self._connected = True

        # Start forwarding audio frames to Reachy
        self._audio_task = asyncio.ensure_future(self._forward_audio())

    async def _forward_audio(self):
        """Read PCM frames from the Agora agent and push to Reachy's speaker."""
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
                logger.debug(f"TTS: audio stream interrupted: {e}")
                await asyncio.sleep(0.5)

    def speak(self, text: str) -> None:
        """Start an Agora TTS agent that speaks `text` through Reachy.

        Non-blocking — the REST API call runs in a background thread.
        Audio arrives via the already-connected RTC channel.
        """
        if not self.enabled or not self._connected:
            return
        if not text or not text.strip():
            return

        def _start():
            # Stop any previous agent first
            if self._active_agent_id:
                _stop_tts_agent(self._active_agent_id, self.app_id, self._auth_header)
                self._active_agent_id = None

            agent_id = _start_tts_agent(
                text, self.app_id, self._auth_header, self.channel_token,
            )
            if agent_id:
                self._active_agent_id = agent_id

        threading.Thread(target=_start, daemon=True).start()

    def stop_speaking(self) -> None:
        """Stop the current TTS agent immediately."""
        if self._active_agent_id:
            agent_id = self._active_agent_id
            self._active_agent_id = None
            threading.Thread(
                target=_stop_tts_agent,
                args=(agent_id, self.app_id, self._auth_header),
                daemon=True,
            ).start()

    def shutdown(self) -> None:
        """Clean up: stop agent, disconnect from Agora, stop event loop."""
        self._connected = False

        # Stop active TTS agent
        if self._active_agent_id:
            _stop_tts_agent(self._active_agent_id, self.app_id, self._auth_header)
            self._active_agent_id = None

        # Cancel audio forwarding
        if self._audio_task and not self._audio_task.done():
            self._audio_task.cancel()

        # Disconnect from Agora channel
        if self._channel and self._loop:
            future = asyncio.run_coroutine_threadsafe(
                self._channel.disconnect(), self._loop,
            )
            try:
                future.result(timeout=5)
            except Exception:
                pass

        if self._rtc:
            self._rtc.destroy()

        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

        logger.info("TTS: shutdown complete")
