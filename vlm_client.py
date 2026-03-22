"""Shared VLM client base: tool definitions, frame encoding, history, action execution.

Subclass BaseVLMClient and implement _call_api() for your backend.
"""

import base64
import json
import logging
from abc import ABC, abstractmethod
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import numpy.typing as npt
from openai.types.chat import ChatCompletionMessageToolCall

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH = Path(__file__).parent / "system_prompt.md"

# Thread pool for async LLM calls — overlap network latency with action execution
_llm_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vlm")

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "nod_yes",
            "description": "Nod head to say yes or confirm something is correct.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shake_no",
            "description": "Shake head to say no or signal something is wrong.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "look_at",
            "description": "Turn to look in a direction to keep the patient centered.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["left", "right", "up", "down", "center"],
                        "description": "The direction to look.",
                    }
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remind_medication",
            "description": "Remind the patient to take a due medication.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Medication name to remind about.",
                    },
                    "message": {
                        "type": "string",
                        "description": "The spoken reminder message for the patient.",
                    },
                },
                "required": ["name", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_medication_taken",
            "description": "Mark a medication as taken after the patient confirms with a thumbs up.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Medication name that was taken.",
                    },
                    "due_time": {
                        "type": "string",
                        "description": "The scheduled time for this dose (e.g. '08:00').",
                    },
                },
                "required": ["name", "due_time"],
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
        self._pending_context: Optional[str] = None

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
            "Do NOT repeat the same action if the scene hasn't changed. Vary your reactions.\n"
            "ALWAYS check: Is the patient showing a THUMBS UP (thumb pointing up, fist closed)? "
            "If yes → call mark_medication_taken({\"name\": \"MED_NAME\", \"due_time\": \"HH:MM\"}) with the ACTUAL medication name and time. NEVER use empty arguments."
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

    def inject_context(self, context: str) -> None:
        """Inject extra context into the next VLM call.

        The context is prepended to the user prompt on the next step() call,
        then cleared. Use for person status, medication reminders, etc.
        """
        self._pending_context = context
        logger.info(f"Context injected: {context[:80]}...")

    def step(
        self, frame: npt.NDArray[np.uint8]
    ) -> tuple[str, list[ChatCompletionMessageToolCall]]:
        """Capture a frame, call the VLM, record history, return (text, tool_calls)."""
        text, tool_calls = self._call_api(frame)
        self._record(text, tool_calls)
        return text, tool_calls

    def step_async(
        self, frame: npt.NDArray[np.uint8]
    ) -> Future:
        """Start a VLM call in a background thread. Returns a Future.

        Use step_collect() to get the result. This lets you overlap the
        LLM network round-trip with the last robot action execution.
        """
        # Snapshot and clear the pending context NOW on the main thread,
        # so inject_context() on the next cycle won't clobber this one.
        ctx = self._pending_context
        self._pending_context = None

        def _call_with_context(f):
            self._pending_context = ctx  # restore for _call_api to consume
            return self._call_api(f)

        return _llm_pool.submit(_call_with_context, frame)

    def step_collect(
        self, future: Future
    ) -> tuple[str, list[ChatCompletionMessageToolCall]]:
        """Collect the result of a step_async() call."""
        text, tool_calls = future.result()
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
    reminder: "MedicationReminder | None" = None,
) -> None:
    """Execute tool calls from the VLM by driving Reachy Mini."""
    from reachy_mini.utils import create_head_pose
    from sounds import get_reminder_sound, celebration as celebration_sound

    REST = create_head_pose()

    def _play_sound(samples):
        """Push audio samples to Reachy's speaker (non-blocking)."""
        try:
            mini.media.push_audio_sample(samples)
        except Exception as e:
            logger.debug(f"Audio playback failed (no media backend?): {e}")

    for call in tool_calls:
        name = call.function.name
        raw_args = call.function.arguments
        args = json.loads(raw_args) if raw_args else {}

        # Require medication name for remind/taken calls — skip if missing
        if name in ("remind_medication", "mark_medication_taken"):
            if not args.get("name"):
                print(f"  ⚠ Skipping {name}() — missing required 'name' argument")
                continue

        print(f"  → {name}({args})")

        if name == "nod_yes":
            for _ in range(2):
                mini.goto_target(
                    head=create_head_pose(pitch=10, degrees=True), duration=0.35
                )
                mini.goto_target(
                    head=create_head_pose(pitch=-6, degrees=True), duration=0.35
                )
            mini.goto_target(head=REST, duration=0.4)

        elif name == "shake_no":
            for _ in range(2):
                mini.goto_target(
                    head=create_head_pose(yaw=14, degrees=True), duration=0.35
                )
                mini.goto_target(
                    head=create_head_pose(yaw=-14, degrees=True), duration=0.35
                )
            mini.goto_target(head=REST, duration=0.4)

        elif name == "look_at":
            direction = args.get("direction", "center")
            poses = {
                "left": create_head_pose(yaw=20, degrees=True),
                "right": create_head_pose(yaw=-20, degrees=True),
                "up": create_head_pose(pitch=15, degrees=True),
                "down": create_head_pose(pitch=-15, degrees=True),
                "center": REST,
            }
            mini.goto_target(head=poses.get(direction, REST), duration=0.6)

        elif name == "remind_medication":
            med_name = args["name"]  # guaranteed by guard above
            message = args.get("message", f"Time to take your {med_name}!")
            # Track nag count per medication for escalating gestures
            if not hasattr(execute_tool_calls, "_nag_counts"):
                execute_tool_calls._nag_counts = {}
            nag = execute_tool_calls._nag_counts.get(med_name, 0) + 1
            execute_tool_calls._nag_counts[med_name] = nag

            intensity = min(nag, 4)  # cap at level 4
            labels = {1: "gentle", 2: "nudge", 3: "insistent", 4: "URGENT"}
            print(f"  ⏰ REMINDER ({labels[intensity]} #{nag}): {message}")

            # Play escalating sound through Reachy's speaker
            sound_samples, _ = get_reminder_sound(intensity)
            _play_sound(sound_samples)

            if intensity == 1:
                # Gentle: soft head tilt + curious antenna perk
                mini.goto_target(
                    head=create_head_pose(roll=10, pitch=7, degrees=True),
                    antennas=[0.2, -0.1],
                    duration=0.5,
                )
                mini.goto_target(
                    head=create_head_pose(roll=-7, pitch=4, degrees=True),
                    antennas=[-0.1, 0.2],
                    duration=0.5,
                )
                mini.goto_target(head=REST, antennas=[0.0, 0.0], duration=0.4)

            elif intensity == 2:
                # Nudge: bouncy wiggle side-to-side
                for _ in range(2):
                    mini.goto_target(
                        head=create_head_pose(roll=12, pitch=4, degrees=True),
                        antennas=[0.3, -0.3],
                        duration=0.3,
                    )
                    mini.goto_target(
                        head=create_head_pose(roll=-12, pitch=4, degrees=True),
                        antennas=[-0.3, 0.3],
                        duration=0.3,
                    )
                mini.goto_target(
                    head=create_head_pose(pitch=10, degrees=True),
                    antennas=[0.25, 0.25],
                    duration=0.35,
                )
                mini.goto_target(head=REST, antennas=[0.0, 0.0], duration=0.4)

            elif intensity == 3:
                # Insistent: wiggles + antenna flapping + look-up plea
                for _ in range(3):
                    mini.goto_target(
                        head=create_head_pose(roll=14, pitch=5, degrees=True),
                        antennas=[0.45, -0.45],
                        duration=0.25,
                    )
                    mini.goto_target(
                        head=create_head_pose(roll=-14, pitch=5, degrees=True),
                        antennas=[-0.45, 0.45],
                        duration=0.25,
                    )
                # Pleading look-up
                mini.goto_target(
                    head=create_head_pose(pitch=15, z=8, degrees=True, mm=True),
                    antennas=[0.5, 0.5],
                    duration=0.4,
                )
                mini.goto_target(head=REST, antennas=[0.0, 0.0], duration=0.4)

            else:
                # URGENT (4+): rapid wiggles + sad droop + hopeful perk up
                for _ in range(3):
                    mini.goto_target(
                        head=create_head_pose(roll=16, yaw=7, pitch=4, degrees=True),
                        antennas=[0.5, -0.5],
                        duration=0.2,
                    )
                    mini.goto_target(
                        head=create_head_pose(roll=-16, yaw=-7, pitch=4, degrees=True),
                        antennas=[-0.5, 0.5],
                        duration=0.2,
                    )
                # Sad droop
                mini.goto_target(
                    head=create_head_pose(pitch=-12, z=-5, degrees=True, mm=True),
                    antennas=[-0.3, -0.3],
                    duration=0.6,
                )
                # Hopeful perk up
                mini.goto_target(
                    head=create_head_pose(pitch=12, z=8, degrees=True, mm=True),
                    antennas=[0.5, 0.5],
                    duration=0.35,
                )
                mini.goto_target(head=REST, antennas=[0.0, 0.0], duration=0.4)

        elif name == "mark_medication_taken":
            med_name = args["name"]  # guaranteed by guard above
            due_time = args.get("due_time", "")

            if reminder:
                if due_time:
                    reminder.mark_taken(med_name, due_time)
                else:
                    # Name given but no time — find the matching due med
                    due = reminder.get_due_medications()
                    matched = [d for d in due if d.get("Medication", "").lower() == med_name.lower()]
                    if matched:
                        for m in matched:
                            reminder.mark_taken(m["Medication"], m["due_time"])
                    else:
                        reminder.mark_taken(med_name, "00:00")
            else:
                print(f"  ⚠ No reminder instance — cannot mark {med_name} as taken")

            # Clear nag counter
            if hasattr(execute_tool_calls, "_nag_counts"):
                execute_tool_calls._nag_counts.pop(med_name, None)

            # 🎉 Celebration!
            print(f"  🎉 {med_name} TAKEN! Celebrating!")

            # Play celebration sound through Reachy's speaker
            celeb_samples, _ = celebration_sound()
            _play_sound(celeb_samples)

            # Phase 1: Gentle lift-up
            mini.goto_target(
                head=create_head_pose(pitch=15, z=10, degrees=True, mm=True),
                antennas=[0.5, 0.5],
                duration=0.3,
            )
            # Phase 2: Happy wiggle dance
            for _ in range(2):
                mini.goto_target(
                    head=create_head_pose(roll=12, pitch=10, z=8, degrees=True, mm=True),
                    antennas=[0.4, -0.2],
                    duration=0.25,
                )
                mini.goto_target(
                    head=create_head_pose(roll=-12, pitch=10, z=8, degrees=True, mm=True),
                    antennas=[-0.2, 0.4],
                    duration=0.25,
                )
            # Phase 3: Proud nod
            mini.goto_target(
                head=create_head_pose(pitch=12, degrees=True),
                antennas=[0.35, 0.35],
                duration=0.3,
            )
            mini.goto_target(
                head=create_head_pose(pitch=-3, degrees=True),
                antennas=[0.2, 0.2],
                duration=0.3,
            )
            # Phase 4: Settle with a warm look
            mini.goto_target(
                head=create_head_pose(pitch=5, degrees=True),
                antennas=[0.15, 0.15],
                duration=0.4,
            )
            mini.goto_target(head=REST, antennas=[0.0, 0.0], duration=0.5)

        else:
            print(f"  ⚠ Unknown tool: {name}")
