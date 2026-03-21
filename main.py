from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

# Temporary: MacBook camera while Reachy hardware is being assembled.
# Once hardware is ready, switch to: ReachyMini(media_backend="sounddevice_opencv")
# and use mini.media.get_frame() instead of cam.get_frame().
from macbook_camera import MacBookCamera
from vlm_client import VLMClient, execute_tool_calls

with ReachyMini(media_backend="no_media") as mini, MacBookCamera() as cam:
    print("✓ Connected to Reachy Mini!")
    frame = cam.get_frame()
    if frame is not None:
        print(f"✓ MacBook camera active — frame shape: {frame.shape}")
    else:
        print("✗ Camera failed — check macOS privacy permissions")

    vlm = VLMClient()
    print(f"✓ VLM client ready — model: {vlm.model}")

    while True:
        frame = cam.get_frame()
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
