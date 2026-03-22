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
                        "voice_id": "English_Upbeat_Woman",
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

# Default volume boost — Agora TTS output is quiet (~-9 dBFS peak),
# Reachy's speaker needs more headroom to be audible in a room.
DEFAULT_VOLUME = 3.0


def _pcm_frame_to_float32(frame, volume: float = DEFAULT_VOLUME) -> np.ndarray:
    """Convert an Agora PcmAudioFrame (16-bit PCM bytes) to float32 numpy."""
    pcm_bytes = bytes(frame.data)
    num_samples = len(pcm_bytes) // 2
    if num_samples == 0:
        return np.array([], dtype=np.float32)
    int16_samples = struct.unpack(f"<{num_samples}h", pcm_bytes)
    samples = np.array(int16_samples, dtype=np.float32) / 32768.0
    # Boost volume and clip to [-1, 1] to avoid distortion
    samples = np.clip(samples * volume, -1.0, 1.0)
    return samples


# ---------------------------------------------------------------------------
# TTSClient
# ---------------------------------------------------------------------------

class TTSClient:
    """Agora RTC-based TTS that pushes audio directly to Reachy's speaker.

    Runs an asyncio event loop in a background thread. On speak():
    1. Starts an Agora Conversational AI agent via REST API
    2. Waits for the agent to join the RTC channel
    3. Subscribes to the agent's audio stream
    4. Forwards PCM frames to Reachy's speaker
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
        self._speaking = False  # True from speak() until audio playback ends

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
        """Connect to Agora RTC channel. Don't subscribe yet — agent isn't there."""
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
        self._connected = True

    async def _wait_for_agent_and_forward(self):
        """Wait for the TTS agent to join, subscribe to its audio, forward to Reachy."""
        agent_uid_str = str(AGENT_UID)
        agent_uid_int = AGENT_UID

        # Nuke any stale AudioStream objects from previous agents/subscribe cycles.
        # The SDK keeps these around keyed by UID and they contain old audio data.
        for uid_key in [agent_uid_str, agent_uid_int]:
            old_stream = self._channel.channel_event_observer.audio_streams.pop(uid_key, None)
            if old_stream:
                # Drain it so nothing leaks
                while not old_stream.queue.empty():
                    try:
                        old_stream.queue.get_nowait()
                    except Exception:
                        break
                logger.debug(f"TTS: cleared stale audio stream for UID {uid_key}")

        # Wait for agent to appear in the channel (up to 8 seconds)
        for _ in range(80):
            if agent_uid_str in self._channel.remote_users or agent_uid_int in self._channel.remote_users:
                break
            await asyncio.sleep(0.1)
        else:
            logger.warning(f"TTS: agent never joined. remote_users={self._channel.remote_users}")
            return

        logger.info(f"TTS: agent UID {AGENT_UID} joined channel")

        # Subscribe to agent's audio — this creates a fresh AudioStream
        await self._channel.subscribe_audio(agent_uid_str)
        logger.info("TTS: subscribed to agent audio")

        # Wait for the NEW audio stream to appear
        audio_stream = None
        for _ in range(30):
            audio_stream = (
                self._channel.get_audio_frames(agent_uid_int)
                or self._channel.get_audio_frames(agent_uid_str)
            )
            if audio_stream is not None:
                break
            await asyncio.sleep(0.1)

        if audio_stream is None:
            logger.warning(
                f"TTS: no audio stream. available streams="
                f"{list(self._channel.channel_event_observer.audio_streams.keys())}"
            )
            return

        # Forward audio frames to Reachy's speaker.
        # Use wait_for() with a timeout on each frame — the SDK can orphan
        # the queue if it creates a replacement AudioStream during subscribe
        # thrashing, leaving us blocked forever on .get().
        frame_count = 0
        self._speaking = True
        FRAME_TIMEOUT = 3.0  # seconds with no audio → assume agent is done
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(
                        audio_stream.queue.get(), timeout=FRAME_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.debug("TTS: no audio frames for 3s — assuming done")
                    break
                if frame is None:
                    break  # sentinel from SDK when agent leaves cleanly
                samples = _pcm_frame_to_float32(frame)
                if len(samples) > 0:
                    try:
                        self.mini.media.push_audio_sample(samples)
                        frame_count += 1
                    except Exception as e:
                        logger.debug(f"TTS: push_audio_sample failed: {e}")
        except Exception as e:
            logger.debug(f"TTS: audio stream ended: {e}")
        finally:
            self._speaking = False

        logger.info(f"TTS: forwarded {frame_count} audio frames to Reachy")

    def speak(self, text: str) -> None:
        """Start an Agora TTS agent that speaks `text` through Reachy.

        Non-blocking — runs in the background event loop.
        Drops the request if already speaking to avoid overlapping agents.
        """
        if not self.enabled or not self._connected:
            return
        if not text or not text.strip():
            return
        if self._speaking:
            logger.debug(f"TTS: busy, dropping: {text[:50]}")
            return

        def _start_and_forward():
            # Stop any previous agent
            if self._active_agent_id:
                _stop_tts_agent(self._active_agent_id, self.app_id, self._auth_header)
                self._active_agent_id = None

            # Cancel previous audio forwarding task
            if self._audio_task and not self._audio_task.done():
                self._audio_task.cancel()

            # Start new agent via REST API
            agent_id = _start_tts_agent(
                text, self.app_id, self._auth_header, self.channel_token,
            )
            if not agent_id:
                return
            self._active_agent_id = agent_id

            # Schedule audio forwarding on the event loop
            asyncio.run_coroutine_threadsafe(
                self._wait_for_agent_and_forward(), self._loop,
            )

        threading.Thread(target=_start_and_forward, daemon=True).start()

    def stop_speaking(self) -> None:
        """Stop the current TTS agent immediately."""
        if self._active_agent_id:
            agent_id = self._active_agent_id
            self._active_agent_id = None
            if self._audio_task and not self._audio_task.done():
                self._audio_task.cancel()
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
