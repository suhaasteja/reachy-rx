from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

# Temporary: MacBook camera while Reachy hardware is being assembled.
# Once hardware is ready, switch to: ReachyMini(media_backend="sounddevice_opencv")
# and use mini.media.get_frame() instead of cam.get_frame().
from macbook_camera import MacBookCamera

with ReachyMini(media_backend="no_media") as mini, MacBookCamera() as cam:
    print("✓ Connected to Reachy Mini!")
    frame = cam.get_frame()
    if frame is not None:
        print(f"✓ MacBook camera active — frame shape: {frame.shape}")
    else:
        print("✗ Camera failed — check macOS privacy permissions")

    while True:
        frame = cam.get_frame()
        # TODO: process frame here (e.g. face detection, LLM vision, etc.)

        mini.goto_target(
            head=create_head_pose(roll=20, degrees=True, mm=True), duration=0.8
        )
        mini.goto_target(
            head=create_head_pose(roll=-20, degrees=True, mm=True), duration=0.8
        )
