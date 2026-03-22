import argparse
import time
from datetime import datetime
from pathlib import Path

import cv2
from reachy_mini import ReachyMini
from reachy_mini.media.camera_utils import find_camera

from medication_reminder import MedicationReminder
from minimax_tts import MinimaxTTSClient as TTSClient
from vlm_client import execute_tool_calls

FRAME_DEBUG_DIR = Path("debug_frames")

parser = argparse.ArgumentParser(description="Reachy Mini VLM vision loop")
parser.add_argument(
    "--debug", action="store_true", help="Enable debug logging and frame capture"
)
parser.add_argument(
    "--model", type=str, default="nemotron-nano-12b-vl", help="VLM model name"
)
parser.add_argument(
    "--server",
    type=str,
    default="https://miss-constraint-rna-artwork.trycloudflare.com/v1",
    help="VLM server base URL",
)
parser.add_argument(
    "--lmstudio",
    action="store_true",
    default=True,
    help="Use LM Studio client (text-parsed tool calls) [default: enabled]",
)
parser.add_argument(
    "--no-lmstudio",
    action="store_false",
    dest="lmstudio",
    help="Use standard OpenAI client (structured tool calls only)",
)
parser.add_argument(
    "--sheet-url",
    type=str,
    default=None,
    help="Google Sheets URL or share link for medicine schedule",
)
args = parser.parse_args()
DEBUG = args.debug

if args.lmstudio:
    from vlm_client_lmstudio import LMStudioVLMClient as VLMClient
else:
    from vlm_client_openai import OpenAIVLMClient as VLMClient


def open_camera():
    """Open Reachy USB camera if connected, otherwise fall back to MacBook camera."""
    cap, specs = find_camera()
    if cap is not None:
        print(f"✓ Camera: {specs.name} ({int(cap.get(3))}x{int(cap.get(4))})")
        return cap

    # Fallback: MacBook camera (for dev without Reachy hardware)
    from macbook_camera import MacBookCamera

    cam = MacBookCamera()
    cam.open()
    print(f"✓ Camera: MacBook fallback ({cam.width}x{cam.height})")
    return cam


def save_debug_frame(frame, step: int) -> Path:
    """Save a captured frame to the debug directory."""
    FRAME_DEBUG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = FRAME_DEBUG_DIR / f"frame_{step:05d}_{timestamp}.jpg"
    cv2.imwrite(str(filename), frame)
    return filename


def grab_frame(cam):
    """Grab a frame from whichever camera backend we have."""
    if hasattr(cam, "get_frame"):
        return cam.get_frame()
    ret, frame = cam.read()
    return frame if ret else None


# Use sounddevice_no_video: audio through Reachy's speaker, camera handled separately
with ReachyMini(media_backend="sounddevice_no_video") as mini:
    print("✓ Connected to Reachy Mini!")

    # Start audio output so push_audio_sample() works
    try:
        mini.media.start_playing()
        # Flush any stale audio left in the buffer from a previous run
        try:
            mini.media.audio.clear_output_buffer()
        except Exception:
            pass
        print("✓ Speaker ready")
    except Exception as e:
        print(f"⚠ Speaker unavailable ({e}) — running without audio")

    cam = open_camera()

    vlm = VLMClient(model=args.model, base_url=args.server)
    print(f"✓ VLM client ready — model: {vlm.model}")

    # Medication reminder — reads schedule from Google Sheet
    reminder_kwargs = {}
    if args.sheet_url:
        reminder_kwargs["sheet_url"] = args.sheet_url
    reminder = MedicationReminder(**reminder_kwargs)
    schedule = reminder.get_schedule_with_status()
    print(f"✓ Medication reminder loaded — {len(schedule)} doses/day from Google Sheet")

    # Text-to-speech via Agora RTC (pure Python, no Node server needed)
    tts = TTSClient(mini=mini)
    tts.start()
    print("✓ TTS client ready")

    if DEBUG:
        FRAME_DEBUG_DIR.mkdir(exist_ok=True)
        print(f"✓ Debug frames → {FRAME_DEBUG_DIR.resolve()}")

    # -----------------------------------------------------------------------
    # Vision loop — sequential: capture → think → act → repeat
    #
    # Actions (including speak) run to completion before the next frame.
    # This avoids overlapping TTS agents and garbled audio.
    # -----------------------------------------------------------------------

    step = 0
    person_greeted = False  # has the current person been greeted?
    person_was_present = False  # was a person present last frame?

    try:
        while True:
            frame = grab_frame(cam)
            if frame is None:
                continue

            if DEBUG:
                saved = save_debug_frame(frame, step)
                print(f"[debug] saved {saved}")

            step += 1

            # Build context to inject into the VLM prompt
            context_parts = []

            # Person presence status (VLM-driven state machine)
            if not person_was_present:
                context_parts.append(
                    "🆕 If you see a person, this is a NEW PERSON — greet them ONCE. "
                    'Use nod_yes() and speak({"message": "Hello! ..."}).'
                )
            else:
                if person_greeted:
                    context_parts.append(
                        "👤 PATIENT PRESENT — already greeted. Do NOT say hello again. "
                        "Only speak if you have something new to say (medication reminder, verification, etc.)."
                    )
                else:
                    context_parts.append(
                        "🆕 NEW PERSON DETECTED — greet them ONCE. "
                        'Use nod_yes() and speak({"message": "Hello! ..."}).'
                    )

            # Medication reminders
            due_meds = reminder.check_and_remind()
            if due_meds:
                reminder_lines = [
                    "⏰ MEDICATION REMINDER — The following medications are due NOW:"
                ]
                for med in due_meds:
                    nag = med.get("nag_count", 1)
                    name = med.get("Medication", "unknown")
                    due_time = med.get("due_time", "00:00")
                    dosage = med.get("Dosage", "")
                    form = med.get("Form", "")
                    instructions = med.get("Instructions", "")
                    line = f"{name} {dosage} {form} (scheduled {due_time})"
                    if instructions:
                        line += f" — {instructions}"
                    if nag > 1:
                        line += f" [reminder #{nag}]"
                    reminder_lines.append(f"  • {line}")
                    reminder_lines.append(
                        f'    → Call: remind_medication({{"name": "{name}"}})'
                    )
                    reminder_lines.append(
                        f'    → Call: speak({{"message": "Time to take your {name} {dosage}!"}})'
                    )

                reminder_lines.append(
                    "\n👍 THUMBS UP CHECK: Look at the patient's hands RIGHT NOW. "
                    "If they are showing a THUMBS UP (thumb pointing up, fist closed), "
                    "call mark_medication_taken() for each due medication. Example calls:"
                )
                for med in due_meds:
                    name = med.get("Medication", "unknown")
                    due_time = med.get("due_time", "00:00")
                    reminder_lines.append(
                        f'    → mark_medication_taken({{"name": "{name}", "due_time": "{due_time}"}})'
                    )

                context_parts.append("\n".join(reminder_lines))
                if DEBUG:
                    print(
                        f"[debug] injected {len(due_meds)} reminder(s), nag counts: {[m.get('nag_count') for m in due_meds]}"
                    )
            else:
                # No meds due right now — if some were taken today, tell the model
                taken_today = reminder.get_taken_today()
                if taken_today:
                    taken_names = list(set(k.split("@")[0] for k in taken_today))
                    context_parts.append(
                        f"✅ No medications due right now. "
                        f"Already taken today: {', '.join(taken_names)}. "
                        f"Do NOT call remind_medication() or mark_medication_taken()."
                    )

            # Inject combined context
            if context_parts:
                vlm.inject_context("\n\n".join(context_parts))

            # --- Think ---
            text, tool_calls = vlm.step(frame)

            if text:
                if DEBUG:
                    print(f"VLM: {text}")

            # --- Act (blocking — runs all actions including TTS to completion) ---
            if tool_calls:
                execute_tool_calls(tool_calls, mini, reminder=reminder, tts=tts)

                # Wait for TTS to finish before next frame (timeout 20s)
                if tts.speaking:
                    if DEBUG:
                        print("[debug] waiting for TTS to finish...")
                    deadline = time.monotonic() + 20.0
                    while tts.speaking and time.monotonic() < deadline:
                        time.sleep(0.1)
                    if tts.speaking:
                        print("⚠ TTS wait timed out — continuing")
                        tts.speaking = False

            # Track person presence from VLM output
            text_lower = (text or "").lower()
            person_now_present = any(
                w in text_lower
                for w in [
                    "person",
                    "someone",
                    "patient",
                    "human",
                    "face",
                    "man",
                    "woman",
                    "hello",
                    "hi ",
                    "welcome",
                    "greet",
                    "see you",
                    "looking at me",
                    "thumbs",
                    "holding",
                    "showing",
                ]
            )
            no_one_keywords = any(
                w in text_lower
                for w in [
                    "no one",
                    "nobody",
                    "empty",
                    "no person",
                    "alone",
                    "waiting",
                ]
            )

            if no_one_keywords:
                person_now_present = False

            # State transitions
            if person_now_present and not person_was_present:
                # Person just appeared
                person_greeted = True  # VLM was told to greet, assume it did
                if DEBUG:
                    print("[debug] person appeared → greeted")
            elif not person_now_present and person_was_present:
                # Person left
                person_greeted = False
                if DEBUG:
                    print("[debug] person left")

            person_was_present = person_now_present

            if DEBUG:
                print(
                    f"[debug] tool_calls={len(tool_calls)}: {[(tc.function.name, tc.function.arguments) for tc in tool_calls]}"
                )
                print(f"[debug] history ({len(vlm._history)}/{vlm.history_max}):")
                for i, h in enumerate(vlm._history):
                    print(f"  {i}: {h}")

            if not tool_calls and DEBUG:
                print("[debug] no tool calls returned")

    finally:
        tts.shutdown()

        try:
            mini.media.stop_playing()
        except Exception:
            pass

        if hasattr(cam, "close"):
            cam.close()
        elif hasattr(cam, "release"):
            cam.release()
        if DEBUG:
            print(f"✓ {step} debug frames saved to {FRAME_DEBUG_DIR.resolve()}")
