"""Standard OpenAI-compatible VLM client.

Uses the proper `tools=` parameter in the API call. Works with any server
that correctly implements OpenAI tool calling (e.g. vLLM, Ollama, OpenAI).

Usage:
    from vlm_client_openai import OpenAIVLMClient
    client = OpenAIVLMClient(base_url="http://localhost:8000/v1", model="my-model")
"""

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import numpy.typing as npt
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageToolCall

from vlm_client import BaseVLMClient, TOOLS

logger = logging.getLogger(__name__)


class OpenAIVLMClient(BaseVLMClient):
    """VLM client using standard OpenAI tool calling."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.client = OpenAI(base_url=self.base_url, api_key=kwargs.get("api_key", "not-needed"))

    def _call_api(
        self, frame: npt.NDArray[np.uint8]
    ) -> tuple[str, list[ChatCompletionMessageToolCall]]:
        image_url = self.encode_frame(frame)
        history_block = self._build_history_block()
        user_text = f"What do you see? Describe any people and their gestures (especially thumbs up). React if appropriate.{history_block}"

        # Inject extra context if pending (person status, reminders, etc.)
        if self._pending_context:
            user_text = f"{self._pending_context}\n\n{user_text}"
            self._pending_context = None

        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": user_text},
                ],
            },
        ]

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOLS,
                max_tokens=self.max_tokens,
            )
        except Exception as e:
            logger.error(f"VLM request failed: {e}")
            return f"[VLM error: {e}]", []

        choice = response.choices[0]
        text = choice.message.content or ""
        tool_calls = list(choice.message.tool_calls or [])

        return text, tool_calls
