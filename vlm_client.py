"""Shared VLM client base: tool definitions, frame encoding, history, action execution.

Subclass BaseVLMClient and implement _call_api() for your backend.
"""

import base64
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import numpy.typing as npt
from openai.types.chat import ChatCompletionMessageToolCall

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH = Path(__file__).parent / "system_prompt.md"
MEDICATION_LOG_PATH = Path(__file__).parent / "medication_log.json"

# ---------------------------------------------------------------------------
# Tool definitions
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
    {
        "type": "function",
        "function": {
            "name": "log_medication",
            "description": "Log an identified medication with its details for patient safety tracking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Medication name (brand and/or generic).",
                    },
                    "dosage": {
                        "type": "string",
                        "description": "Dosage strength (e.g. '500mg', '10mg/5mL').",
                    },
                    "form": {
                        "type": "string",
                        "description": "Dosage form (e.g. 'tablet', 'capsule', 'liquid', 'cream').",
                    },
                    "count": {
                        "type": "string",
                        "description": "Quantity / count on the label (e.g. '90 tablets', '120 mL').",
                    },
                    "description": {
                        "type": "string",
                        "description": "Any other details: manufacturer, warnings, active/inactive ingredients, expiration, NDC, etc.",
                    },
                },
                "required": ["name"],
            },
        },
    },
]

TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


def _load_system_prompt(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"System prompt not found: {path}")
    return path.read_text().strip()


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class BaseVLMClient(ABC):
    """Abstract base for VLM clients with shared frame encoding and history."""

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        model: str = "nvidia-nemotron-nano-12b-v2-vl",
        max_tokens: int = 200,
        system_prompt_path: Optional[Path] = None,
        history_max: int = 10,
    ):
        self.base_url = base_url
        self.model = model
        self.max_tokens = max_tokens
        self.history_max = history_max
        self._history: list[str] = []

        prompt_path = system_prompt_path or DEFAULT_PROMPT_PATH
        self.system_prompt = _load_system_prompt(prompt_path)
        logger.info(f"Loaded system prompt from {prompt_path}")

    # -- frame encoding ---------------------------------------------------

    @staticmethod
    def encode_frame(frame: npt.NDArray[np.uint8]) -> str:
        """Encode a BGR numpy frame to a base64 JPEG data URI."""
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        b64 = base64.b64encode(buf.tobytes()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"

    # -- history ----------------------------------------------------------

    def _build_history_block(self) -> str:
        if not self._history:
            return ""
        lines = "\n".join(self._history)
        return (
            f"\n\nYour recent actions (most recent last):\n{lines}\n\n"
            "Do NOT repeat the same action if the scene hasn't changed. Vary your reactions."
        )

    def _record(self, text: str, tool_calls: list) -> None:
        parts = []
        for tc in tool_calls:
            parts.append(f"[action] {tc.function.name}({tc.function.arguments})")
        if text:
            parts.append(f"[observation] {text[:120]}")
        if parts:
            self._history.append(" | ".join(parts))
        if len(self._history) > self.history_max:
            self._history = self._history[-self.history_max:]

    # -- public API -------------------------------------------------------

    def step(
        self, frame: npt.NDArray[np.uint8]
    ) -> tuple[str, list[ChatCompletionMessageToolCall]]:
        """Capture a frame, call the VLM, record history, return (text, tool_calls)."""
        text, tool_calls = self._call_api(frame)
        self._record(text, tool_calls)
        return text, tool_calls

    @abstractmethod
    def _call_api(
        self, frame: npt.NDArray[np.uint8]
    ) -> tuple[str, list[ChatCompletionMessageToolCall]]:
        """Subclasses implement the actual API call here."""
        ...


# ---------------------------------------------------------------------------
# Action execution (shared across all clients)
# ---------------------------------------------------------------------------


def execute_tool_calls(
    tool_calls: list[ChatCompletionMessageToolCall],
    mini: "ReachyMini",
) -> None:
    """Execute tool calls from the VLM by driving Reachy Mini."""
    from reachy_mini.utils import create_head_pose

    REST = create_head_pose()

    for call in tool_calls:
        name = call.function.name
        raw_args = call.function.arguments
        args = json.loads(raw_args) if raw_args else {}
        print(f"  → {name}({args})")

        if name == "nod_yes":
            for _ in range(2):
                mini.goto_target(
                    head=create_head_pose(pitch=15, degrees=True), duration=0.25
                )
                mini.goto_target(
                    head=create_head_pose(pitch=-10, degrees=True), duration=0.25
                )
            mini.goto_target(head=REST, duration=0.3)

        elif name == "shake_no":
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
                mini.goto_target(
                    head=create_head_pose(pitch=-20, z=-5, degrees=True, mm=True),
                    antennas=[-0.3, -0.3],
                    duration=0.8,
                )
                mini.goto_target(head=REST, antennas=[0.0, 0.0], duration=0.6)

            elif emotion == "surprised":
                mini.goto_target(
                    head=create_head_pose(pitch=20, z=15, degrees=True, mm=True),
                    antennas=[0.7, 0.7],
                    duration=0.2,
                )
                mini.goto_target(head=REST, antennas=[0.0, 0.0], duration=0.5)

            elif emotion == "curious":
                mini.goto_target(
                    head=create_head_pose(roll=20, pitch=10, degrees=True),
                    duration=0.4,
                )
                mini.goto_target(head=REST, duration=0.4)

        elif name == "log_medication":
            entry = {
                "timestamp": datetime.now().isoformat(),
                "name": args.get("name", "unknown"),
                "dosage": args.get("dosage"),
                "form": args.get("form"),
                "count": args.get("count"),
                "description": args.get("description"),
            }
            # Strip None values for cleaner output
            entry = {k: v for k, v in entry.items() if v is not None}

            # Append to JSON log file
            if MEDICATION_LOG_PATH.exists():
                log_data = json.loads(MEDICATION_LOG_PATH.read_text())
            else:
                log_data = []
            log_data.append(entry)
            MEDICATION_LOG_PATH.write_text(json.dumps(log_data, indent=2) + "\n")
            print(f"  💊 Logged: {entry['name']} — {entry.get('dosage', '?')} {entry.get('form', '')} (count: {entry.get('count', '?')})")

            # Reachy nods to confirm the log
            mini.goto_target(
                head=create_head_pose(pitch=15, degrees=True), duration=0.2
            )
            mini.goto_target(
                head=create_head_pose(pitch=-10, degrees=True), duration=0.2
            )
            mini.goto_target(head=REST, duration=0.2)

        else:
            print(f"  ⚠ Unknown tool: {name}")
