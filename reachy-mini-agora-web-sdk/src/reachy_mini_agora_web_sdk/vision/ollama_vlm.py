"""One-shot webcam capture + Ollama OpenAI-compatible vision chat."""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, cast

import requests  # type: ignore[import-untyped]


logger = logging.getLogger(__name__)


def _ollama_v1_base() -> str:
    raw = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").strip().rstrip("/")
    return raw


def _chat_completions_url() -> str:
    return f"{_ollama_v1_base()}/chat/completions"


def capture_webcam_jpeg(device_index: int = 0) -> bytes:
    """Capture a single frame from the default webcam and return JPEG bytes."""
    import cv2

    cap = cv2.VideoCapture(device_index)
    if not cap.isOpened():
        raise RuntimeError(f"camera not available (index={device_index})")

    try:
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError("failed to read frame from camera")
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok or buf is None:
            raise RuntimeError("failed to encode JPEG")
        return cast(bytes, buf.tobytes())
    finally:
        cap.release()


def describe_image_with_ollama(
    jpeg_bytes: bytes,
    *,
    prompt: str,
    model: str,
    timeout_s: float = 120.0,
) -> str:
    """Call Ollama `/v1/chat/completions` with one image (OpenAI multimodal content)."""
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    data_uri = f"data:image/jpeg;base64,{b64}"

    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
        "stream": False,
    }

    url = _chat_completions_url()
    r = requests.post(url, json=body, timeout=timeout_s)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"ollama HTTP {r.status_code}: {r.text[:500]}")

    data = r.json()
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(f"unexpected ollama response: {data!r}")

    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str) and content.strip():
        return content.strip()

    raise RuntimeError(f"empty model content in response: {data!r}")


def run_vision_read_sync(
    *,
    prompt: str,
    model: str | None,
    device_index: int,
) -> str:
    """Capture one webcam frame and return the model description text."""
    m = (model or os.getenv("OLLAMA_VISION_MODEL", "llava").strip()) or "llava"
    jpeg = capture_webcam_jpeg(device_index=device_index)
    return describe_image_with_ollama(jpeg_bytes=jpeg, prompt=prompt, model=m)
