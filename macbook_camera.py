"""Temporary MacBook camera backend.

Drop-in camera source using the MacBook's FaceTime camera via OpenCV.
Use this while the Reachy Mini hardware is being assembled.

Usage:
    from macbook_camera import MacBookCamera

    with MacBookCamera() as cam:
        frame = cam.get_frame()  # numpy array (H, W, 3) uint8 BGR, or None
"""

from typing import Optional

import cv2
import numpy as np
import numpy.typing as npt


class MacBookCamera:
    """OpenCV wrapper around the MacBook's built-in FaceTime camera.

    Mirrors the reachy_mini MediaManager camera interface so swapping
    back to `mini.media.get_frame()` later is trivial.
    """

    def __init__(self, device_index: int = 0, width: int = 1280, height: int = 720):
        self.device_index = device_index
        self.width = width
        self.height = height
        self._cap: Optional[cv2.VideoCapture] = None

    # -- context manager --------------------------------------------------

    def __enter__(self) -> "MacBookCamera":
        self.open()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- lifecycle --------------------------------------------------------

    def open(self) -> None:
        """Open the MacBook camera."""
        if self._cap is not None and self._cap.isOpened():
            return
        # Force AVFoundation on macOS — CAP_ANY may pick FFmpeg which lacks camera permissions
        self._cap = cv2.VideoCapture(self.device_index, cv2.CAP_AVFOUNDATION)
        if not self._cap.isOpened():
            raise RuntimeError(
                f"Could not open camera at index {self.device_index}. "
                "Check System Settings > Privacy & Security > Camera permissions."
            )
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

    def close(self) -> None:
        """Release the camera."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    # -- frame capture (same signature as mini.media.get_frame) -----------

    def get_frame(self) -> Optional[npt.NDArray[np.uint8]]:
        """Return a BGR frame as uint8 numpy array, or None on failure."""
        if self._cap is None or not self._cap.isOpened():
            return None
        ret, frame = self._cap.read()
        if not ret:
            return None
        return frame

    @property
    def resolution(self) -> tuple[int, int]:
        """Return (width, height) of the camera."""
        return (self.width, self.height)
