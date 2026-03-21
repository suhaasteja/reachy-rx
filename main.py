import argparse
from datetime import datetime
from pathlib import Path

import cv2
from reachy_mini import ReachyMini
from reachy_mini.media.camera_utils import find_camera

from vlm_client import execute_tool_calls
from vlm_client_lmstudio import LMStudioVLMClient as VLMClient

# from vlm_client_openai import OpenAIVLMClient as VLMClient

FRAME_DEBUG_DIR = Path("debug_frames")

parser = argparse.ArgumentParser(description="Reachy Mini VLM vision loop")
parser.add_argument("--debug", action="store_true", help="Enable debug logging and frame capture")
args = parser.parse_args()
DEBUG = args.debug


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


with ReachyMini(media_backend="no_media") as mini:
    print("✓ Connected to Reachy Mini!")
    cam = open_camera()

    vlm = VLMClient(model="nvidia-nemotron-nano-12b-v2-vl")
    print(f"✓ VLM client ready — model: {vlm.model}")

    if DEBUG:
        FRAME_DEBUG_DIR.mkdir(exist_ok=True)
        print(f"✓ Debug frames → {FRAME_DEBUG_DIR.resolve()}")

    step = 0
    try:
        while True:
            # Works with both cv2.VideoCapture (Reachy) and MacBookCamera
            if hasattr(cam, "get_frame"):
                frame = cam.get_frame()
            else:
                ret, frame = cam.read()
                frame = frame if ret else None

            if frame is None:
                continue

            if DEBUG:
                saved = save_debug_frame(frame, step)
                print(f"[debug] saved {saved}")

            step += 1

            text, tool_calls = vlm.step(frame)
            if text:
                print(f"VLM: {text}")
            if DEBUG:
                print(
                    f"[debug] tool_calls={len(tool_calls)}: {[(tc.function.name, tc.function.arguments) for tc in tool_calls]}"
                )
                print(f"[debug] history ({len(vlm._history)}/{vlm.history_max}):")
                for i, h in enumerate(vlm._history):
                    print(f"  {i}: {h}")
            if tool_calls:
                execute_tool_calls(tool_calls, mini)
            elif DEBUG:
                print("[debug] no tool calls returned")
    finally:
        if hasattr(cam, "close"):
            cam.close()
        elif hasattr(cam, "release"):
            cam.release()
        if DEBUG:
            print(f"✓ {step} debug frames saved to {FRAME_DEBUG_DIR.resolve()}")
