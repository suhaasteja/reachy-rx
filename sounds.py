"""Cute synthesized sounds for Reachy Mini's speaker.

Generates short audio clips as numpy float32 arrays at 16kHz,
ready to push directly to mini.media.push_audio_sample().

All functions return (samples, duration_seconds) tuples.
"""

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 16000  # Reachy Mini default


def _fade(samples: npt.NDArray[np.float32], fade_ms: int = 10) -> npt.NDArray[np.float32]:
    """Apply a short fade-in/fade-out to avoid clicks."""
    fade_len = int(SAMPLE_RATE * fade_ms / 1000)
    if fade_len > len(samples) // 2:
        fade_len = len(samples) // 2
    ramp = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
    samples[:fade_len] *= ramp
    samples[-fade_len:] *= ramp[::-1]
    return samples


def chirp_up(duration: float = 0.15, freq_start: float = 400, freq_end: float = 900, volume: float = 0.4) -> tuple[npt.NDArray[np.float32], float]:
    """Rising chirp — cute 'boop!' for gentle reminder."""
    n = int(SAMPLE_RATE * duration)
    freqs = np.linspace(freq_start, freq_end, n, dtype=np.float32)
    phase = np.cumsum(2.0 * np.pi * freqs / SAMPLE_RATE).astype(np.float32)
    samples = (volume * np.sin(phase)).astype(np.float32)
    return _fade(samples), duration


def double_chirp(volume: float = 0.45) -> tuple[npt.NDArray[np.float32], float]:
    """Two rising chirps — 'boop boop!' for nudge reminder."""
    c1, d1 = chirp_up(0.12, 450, 850, volume)
    gap = np.zeros(int(SAMPLE_RATE * 0.06), dtype=np.float32)
    c2, d2 = chirp_up(0.12, 500, 950, volume)
    samples = np.concatenate([c1, gap, c2])
    return samples, d1 + 0.06 + d2


def triple_chirp(volume: float = 0.5) -> tuple[npt.NDArray[np.float32], float]:
    """Three ascending chirps — 'boop boop BOOP!' for insistent reminder."""
    c1, _ = chirp_up(0.1, 400, 700, volume * 0.7)
    gap = np.zeros(int(SAMPLE_RATE * 0.05), dtype=np.float32)
    c2, _ = chirp_up(0.1, 500, 850, volume * 0.85)
    c3, _ = chirp_up(0.15, 600, 1100, volume)
    samples = np.concatenate([c1, gap, c2, gap, c3])
    duration = (len(samples) / SAMPLE_RATE)
    return samples, duration


def alarm_beeps(volume: float = 0.55) -> tuple[npt.NDArray[np.float32], float]:
    """Rapid alternating beeps — urgent 'bee-boo-bee-boo!' for level 4."""
    parts = []
    for i in range(4):
        freq = 800 if i % 2 == 0 else 600
        n = int(SAMPLE_RATE * 0.08)
        t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
        tone = (volume * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)
        parts.append(_fade(tone, fade_ms=5))
        parts.append(np.zeros(int(SAMPLE_RATE * 0.03), dtype=np.float32))
    samples = np.concatenate(parts)
    return samples, len(samples) / SAMPLE_RATE


def celebration(volume: float = 0.45) -> tuple[npt.NDArray[np.float32], float]:
    """Happy ascending arpeggio — 'ta-da-da-DAAAH!' for medication taken."""
    # C5 → E5 → G5 → C6 (major chord arpeggio)
    notes = [523.25, 659.25, 783.99, 1046.50]
    durations = [0.08, 0.08, 0.08, 0.2]
    parts = []
    for freq, dur in zip(notes, durations):
        n = int(SAMPLE_RATE * dur)
        t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
        # Add a touch of harmonics for richness
        tone = volume * (
            0.7 * np.sin(2.0 * np.pi * freq * t) +
            0.2 * np.sin(2.0 * np.pi * freq * 2 * t) +
            0.1 * np.sin(2.0 * np.pi * freq * 3 * t)
        )
        parts.append(_fade(tone.astype(np.float32), fade_ms=5))
    samples = np.concatenate(parts)
    return samples, len(samples) / SAMPLE_RATE


# Map nag intensity level → sound generator
REMINDER_SOUNDS = {
    1: chirp_up,        # gentle boop
    2: double_chirp,    # boop boop
    3: triple_chirp,    # boop boop BOOP
    4: alarm_beeps,     # bee-boo-bee-boo
}


def get_reminder_sound(intensity: int) -> tuple[npt.NDArray[np.float32], float]:
    """Get the appropriate reminder sound for the given nag intensity (1-4)."""
    intensity = max(1, min(intensity, 4))
    return REMINDER_SOUNDS[intensity]()
