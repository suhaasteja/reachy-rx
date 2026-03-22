"""LM Studio VLM client with text-based tool call workaround.

LM Studio's tool call parser is broken for certain models (e.g. Nemotron VL).
The model generates tool calls but LM Studio fails to parse them into the
structured `tool_calls` response field, silently dropping them.

This client works around the issue by:
1. Describing tools in the system prompt instead of the `tools=` API param
2. Parsing tool calls from the text content (e.g. "nod_yes()" or
   '<tool_call>{"name":"nod_yes"}</tool_call>')

Usage:
    from vlm_client_lmstudio import LMStudioVLMClient
    client = LMStudioVLMClient()
"""

import json
import logging
import re

import numpy as np
import numpy.typing as npt
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageToolCall
from openai.types.chat.chat_completion_message_tool_call import Function

from vlm_client import BaseVLMClient, TOOL_NAMES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Text-based tool call parser
# ---------------------------------------------------------------------------


def parse_tool_calls_from_text(
    text: str,
) -> tuple[str, list[ChatCompletionMessageToolCall]]:
    """Extract tool calls that the model wrote as plain text.

    Handles patterns like:
      nod_yes()
      look_at({"direction": "left"})
      <tool_call>{"name":"nod_yes","arguments":{}}</tool_call>

    Returns (cleaned_text, parsed_tool_calls).
    """
    parsed: list[ChatCompletionMessageToolCall] = []
    cleaned_lines: list[str] = []
    counter = 0

    # Build a pattern that matches tool_name( ... ) where the args may contain
    # nested parens / closing-parens inside JSON strings (e.g. "20mg)").
    # Strategy: match the tool name, then grab everything up to the LAST ')' on
    # the line that still yields valid JSON (greedy match + right-strip).
    func_pattern = re.compile(
        r"\b(" + "|".join(re.escape(n) for n in TOOL_NAMES) + r")\s*\((.+)\)\s*$",
        re.MULTILINE,
    )
    # Also keep a simple pattern for no-arg calls like  nod_yes()
    func_pattern_noargs = re.compile(
        r"\b(" + "|".join(re.escape(n) for n in TOOL_NAMES) + r")\s*\(\s*\)"
    )
    xml_pattern = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)

    for line in text.splitlines():
        matched = False

        # Try no-arg calls first:  nod_yes()  shake_no()
        for m in func_pattern_noargs.finditer(line):
            name = m.group(1)
            parsed.append(
                ChatCompletionMessageToolCall(
                    id=f"fallback_{counter}",
                    type="function",
                    function=Function(name=name, arguments="{}"),
                )
            )
            counter += 1
            matched = True

        # Try calls with arguments (greedy — captures up to last ')' on line)
        if not matched:
            for m in func_pattern.finditer(line):
                name = m.group(1)
                raw_args = m.group(2).strip()
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    args = {}
                parsed.append(
                    ChatCompletionMessageToolCall(
                        id=f"fallback_{counter}",
                        type="function",
                        function=Function(name=name, arguments=json.dumps(args)),
                    )
                )
                counter += 1
                matched = True

        for m in xml_pattern.finditer(line):
            try:
                data = json.loads(m.group(1))
                name = data.get("name", "")
                if name in TOOL_NAMES:
                    args = data.get("arguments", {})
                    if isinstance(args, str):
                        args = json.loads(args)
                    parsed.append(
                        ChatCompletionMessageToolCall(
                            id=f"fallback_{counter}",
                            type="function",
                            function=Function(name=name, arguments=json.dumps(args)),
                        )
                    )
                    counter += 1
                    matched = True
            except (json.JSONDecodeError, AttributeError):
                pass

        if not matched:
            cleaned_lines.append(line)

    cleaned_text = "\n".join(cleaned_lines).strip()
    return cleaned_text, parsed


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class LMStudioVLMClient(BaseVLMClient):
    """VLM client with LM Studio broken-tool-calling workaround.

    Tools are described in the system prompt. The model writes tool calls as
    text, and we parse them out with regex.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.client = OpenAI(base_url=self.base_url, api_key="not-needed")

    def _call_api(
        self, frame: npt.NDArray[np.uint8]
    ) -> tuple[str, list[ChatCompletionMessageToolCall]]:
        image_url = self.encode_frame(frame)
        history_block = self._build_history_block()
        user_text = f"Look at the image. Check for: 1) people and hand gestures (especially thumbs up), 2) medication bottles or labels being held up. Act according to your instructions.{history_block}"

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
            # Do NOT pass tools= here — LM Studio will intercept and silently
            # drop them. Tools are described in the system prompt instead.
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
            )
        except Exception as e:
            logger.error(f"VLM request failed: {e}")
            return f"[VLM error: {e}]", []

        choice = response.choices[0]
        text = choice.message.content or ""
        tool_calls = list(choice.message.tool_calls or [])

        # Parse tool calls from text content since LM Studio drops them
        if text:
            text, parsed_calls = parse_tool_calls_from_text(text)
            if parsed_calls:
                tool_calls.extend(parsed_calls)

        return text, tool_calls
