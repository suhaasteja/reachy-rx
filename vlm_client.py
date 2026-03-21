"""Local VLM client via OpenAI-compatible API.

Sends camera frames + tool definitions to a local vision-language model
and returns tool calls for Reachy to execute.

Usage:
    from vlm_client import VLMClient

    client = VLMClient()
    tool_calls = client.step(frame)
    for call in tool_calls:
        print(call.function.name, call.function.arguments)
"""

import base64
import json
import logging
import re
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import numpy.typing as npt
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageToolCall
from openai.types.chat.chat_completion_message_tool_call import Function

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH = Path(__file__).parent / "system_prompt.md"

# ---------------------------------------------------------------------------
# Tool definitions — empty scaffolds for now, fill in real actions later
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "nod_yes",
            "description": "Make Reachy nod its head up and down to signal agreement.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shake_no",
            "description": "Make Reachy shake its head side to side to signal disagreement.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "look_at",
            "description": "Make Reachy look at a specific direction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["left", "right", "up", "down", "center"],
                        "description": "The direction for Reachy to look.",
                    }
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "express_emotion",
            "description": "Make Reachy express an emotion through body language.",
            "parameters": {
                "type": "object",
                "properties": {
                    "emotion": {
                        "type": "string",
                        "enum": ["happy", "sad", "surprised", "curious"],
                        "description": "The emotion to express.",
                    }
                },
                "required": ["emotion"],
            },
        },
    },
]


def _load_system_prompt(path: Path) -> str:
    """Read the system prompt from a markdown file."""
    if not path.exists():
        raise FileNotFoundError(f"System prompt not found: {path}")
    return path.read_text().strip()


# Valid tool names for fallback parsing
_TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


def _parse_tool_calls_from_text(
    text: str,
) -> tuple[str, list[ChatCompletionMessageToolCall]]:
    """Fallback: extract tool calls that the model wrote as text.

    LM Studio + Nemotron VL often fails to structure tool calls properly,
    so the model outputs them in content as e.g.:
      nod_yes()
      look_at({"direction": "left"})
      express_emotion({"emotion":"happy"})
      <tool_call>{"name":"nod_yes","arguments":{}}</tool_call>

    Returns (cleaned_text, parsed_tool_calls).
    """
    parsed: list[ChatCompletionMessageToolCall] = []
    cleaned_lines: list[str] = []
    counter = 0

    # Pattern 1: function_name({...}) or function_name()
    func_pattern = re.compile(
        r"\b(" + "|".join(re.escape(n) for n in _TOOL_NAMES) + r")\s*\(([^)]*)\)"
    )
    # Pattern 2: <tool_call>...</tool_call> JSON blocks
    xml_pattern = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)

    for line in text.splitlines():
        matched = False

        # Check function-call style
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

        # Check XML style
        for m in xml_pattern.finditer(line):
            try:
                data = json.loads(m.group(1))
                name = data.get("name", "")
                if name in _TOOL_NAMES:
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


class VLMClient:
    """Client for a local OpenAI-compatible VLM with vision + tool calling."""

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        model: str = "nvidia-nemotron-nano-12b-v2-vl",
        max_tokens: int = 200,
        system_prompt_path: Optional[Path] = None,
        history_max: int = 10,
    ):
        self.client = OpenAI(base_url=base_url, api_key="not-needed")
        self.model = model
        self.max_tokens = max_tokens
        self.history_max = history_max
        self._history: list[str] = []  # text-only rolling history

        prompt_path = system_prompt_path or DEFAULT_PROMPT_PATH
        self.system_prompt = _load_system_prompt(prompt_path)
        logger.info(f"Loaded system prompt from {prompt_path}")

    def _encode_frame(self, frame: npt.NDArray[np.uint8]) -> str:
        """Encode a BGR numpy frame to a base64 JPEG data URI."""
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        b64 = base64.b64encode(buf.tobytes()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"

    def _build_history_block(self) -> str:
        """Format recent history as text for the user message."""
        if not self._history:
            return ""
        lines = "\n".join(self._history)
        return (
            f"\n\nYour recent actions (most recent last):\n{lines}\n\n"
            "Do NOT repeat the same action if the scene hasn't changed. Vary your reactions."
        )

    def _record(self, text: str, tool_calls: list) -> None:
        """Append a summary of this turn to rolling history."""
        parts = []
        for tc in tool_calls:
            parts.append(f"[action] {tc.function.name}({tc.function.arguments})")
        if text:
            parts.append(f"[observation] {text[:120]}")
        if parts:
            self._history.append(" | ".join(parts))
        if len(self._history) > self.history_max:
            self._history = self._history[-self.history_max :]

    def step(
        self, frame: npt.NDArray[np.uint8]
    ) -> tuple[str, list[ChatCompletionMessageToolCall]]:
        """Send a frame to the VLM and return (text_response, tool_calls).

        Args:
            frame: BGR uint8 numpy array from the camera.

        Returns:
            Tuple of (assistant text, list of tool calls).
            Either may be empty depending on the model's decision.
        """
        image_url = self._encode_frame(frame)
        history_block = self._build_history_block()
        user_text = f"What do you see? React if appropriate.{history_block}"

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
                # NOTE: tools are described in the system prompt instead of here.
                # LM Studio's tool call parser is broken for Nemotron VL — it
                # intercepts and silently drops tool calls. By omitting `tools`,
                # the model writes tool calls as text and our fallback parser
                # extracts them reliably.
                max_tokens=self.max_tokens,
            )
        except Exception as e:
            logger.error(f"VLM request failed: {e}")
            return f"[VLM error: {e}]", []

        choice = response.choices[0]
        text = choice.message.content or ""
        tool_calls = list(choice.message.tool_calls or [])

        # Parse tool calls from text content — LM Studio's tool parser is broken
        # for Nemotron VL, so tools are described in the system prompt and the
        # model writes them as text (e.g. "nod_yes()" or 'look_at({"direction":"left"})').
        if text:
            text, parsed_calls = _parse_tool_calls_from_text(text)
            if parsed_calls:
                tool_calls.extend(parsed_calls)

        self._record(text, tool_calls)

        return text, tool_calls


def execute_tool_calls(
    tool_calls: list[ChatCompletionMessageToolCall],
    mini: "ReachyMini",
) -> None:
    """Execute tool calls from the VLM by driving Reachy Mini.

    Args:
        tool_calls: Tool calls returned by the VLM.
        mini: An active ReachyMini instance.
    """
    from reachy_mini.utils import create_head_pose

    REST = create_head_pose()  # neutral position

    for call in tool_calls:
        name = call.function.name
        raw_args = call.function.arguments
        args = json.loads(raw_args) if raw_args else {}
        print(f"  → {name}({args})")

        if name == "nod_yes":
            # Two quick pitch nods then return to neutral
            for _ in range(2):
                mini.goto_target(
                    head=create_head_pose(pitch=15, degrees=True), duration=0.25
                )
                mini.goto_target(
                    head=create_head_pose(pitch=-10, degrees=True), duration=0.25
                )
            mini.goto_target(head=REST, duration=0.3)

        elif name == "shake_no":
            # Two quick yaw shakes then return to neutral
            for _ in range(2):
                mini.goto_target(
                    head=create_head_pose(yaw=20, degrees=True), duration=0.25
                )
                mini.goto_target(
                    head=create_head_pose(yaw=-20, degrees=True), duration=0.25
                )
            mini.goto_target(head=REST, duration=0.3)

        elif name == "look_at":
            direction = args.get("direction", "center")
            poses = {
                "left": create_head_pose(yaw=30, degrees=True),
                "right": create_head_pose(yaw=-30, degrees=True),
                "up": create_head_pose(pitch=25, degrees=True),
                "down": create_head_pose(pitch=-25, degrees=True),
                "center": REST,
            }
            mini.goto_target(head=poses.get(direction, REST), duration=0.5)

        elif name == "express_emotion":
            emotion = args.get("emotion", "curious")

            if emotion == "happy":
                # Quick upward bounce + antenna wiggle
                mini.goto_target(
                    head=create_head_pose(pitch=10, z=10, degrees=True, mm=True),
                    antennas=[0.5, -0.5],
                    duration=0.3,
                )
                mini.goto_target(
                    head=create_head_pose(pitch=10, z=10, degrees=True, mm=True),
                    antennas=[-0.5, 0.5],
                    duration=0.3,
                )
                mini.goto_target(head=REST, antennas=[0.0, 0.0], duration=0.3)

            elif emotion == "sad":
                # Slow droop down
                mini.goto_target(
                    head=create_head_pose(pitch=-20, z=-5, degrees=True, mm=True),
                    antennas=[-0.3, -0.3],
                    duration=0.8,
                )
                mini.goto_target(head=REST, antennas=[0.0, 0.0], duration=0.6)

            elif emotion == "surprised":
                # Quick jolt back then forward
                mini.goto_target(
                    head=create_head_pose(pitch=20, z=15, degrees=True, mm=True),
                    antennas=[0.7, 0.7],
                    duration=0.2,
                )
                mini.goto_target(head=REST, antennas=[0.0, 0.0], duration=0.5)

            elif emotion == "curious":
                # Head tilt + slight forward lean
                mini.goto_target(
                    head=create_head_pose(roll=20, pitch=10, degrees=True),
                    duration=0.4,
                )
                mini.goto_target(head=REST, duration=0.4)

        else:
            print(f"  ⚠ Unknown tool: {name}")
