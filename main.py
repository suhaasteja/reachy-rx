from reachy_mini import ReachyMini
from reachy_mini.media.camera_utils import find_camera

from vlm_client import execute_tool_calls
from vlm_client_lmstudio import LMStudioVLMClient as VLMClient

# from vlm_client_openai import OpenAIVLMClient as VLMClient


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


with ReachyMini(media_backend="no_media") as mini:
    print("✓ Connected to Reachy Mini!")
    cam = open_camera()

    vlm = VLMClient(model="nvidia-nemotron-nano-12b-v2-vl")
    print(f"✓ VLM client ready — model: {vlm.model}")

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

            text, tool_calls = vlm.step(frame)
            if text:
                print(f"VLM: {text}")
            print(
                f"[debug] tool_calls={len(tool_calls)}: {[(tc.function.name, tc.function.arguments) for tc in tool_calls]}"
            )
            print(f"[debug] history ({len(vlm._history)}/{vlm.history_max}):")
            for i, h in enumerate(vlm._history):
                print(f"  {i}: {h}")
            if tool_calls:
                execute_tool_calls(tool_calls, mini)
            else:
                print("[debug] no tool calls returned")
    finally:
        if hasattr(cam, "close"):
            cam.close()
        elif hasattr(cam, "release"):
            cam.release()
