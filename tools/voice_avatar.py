"""Animate the 2D sprite atlas on the Tk thread."""

from __future__ import annotations

import time
import tkinter as tk
from itertools import combinations

SIZE = 418
COLUMNS = 8
STANDING = 88
EXPRESSION_PAIRS = tuple(combinations(range(4), 2))
FRAME_COUNT = STANDING + 4 + 3 * len(EXPRESSION_PAIRS)
RISE_SECONDS = 1.92
SIT_SECONDS = 1.84


def expression_index(previous, current, progress):
    quarter = round(min(1, progress) * 4)
    if quarter == 0:
        return STANDING + previous
    if previous == current or quarter == 4:
        return STANDING + current
    first, second = sorted((previous, current))
    if previous == second:
        quarter = 4 - quarter
    pair = EXPRESSION_PAIRS.index((first, second))
    return STANDING + 4 + pair * 3 + quarter - 1


class VoiceAvatar(tk.Label):
    def __init__(self, parent, atlas_path):
        super().__init__(parent, bg="#e8d4ad", borderwidth=0)
        atlas = tk.PhotoImage(master=self, file=str(atlas_path))
        rows = (FRAME_COUNT + COLUMNS - 1) // COLUMNS
        if atlas.width() != COLUMNS * SIZE or atlas.height() != rows * SIZE:
            raise ValueError("Invalid dimensions for the 2D avatar atlas")
        self.frames = []
        for index in range(FRAME_COUNT):
            x, y = (index % COLUMNS) * SIZE, (index // COLUMNS) * SIZE
            frame = tk.PhotoImage(master=self, width=SIZE, height=SIZE)
            self.tk.call(frame, "copy", atlas, "-from", x, y, x + SIZE, y + SIZE)
            self.frames.append(frame)
        self.position = 0.0
        self.motion = None
        self.track = None
        self.playback = None
        self.job = None
        self.shown = None
        self._show(0)

    def _show(self, index):
        if index != self.shown:
            self.config(image=self.frames[index])
            self.shown = index

    def _position(self, now):
        if self.motion is None:
            return self.position
        origin, target, start, duration, _ = self.motion
        fraction = min(1, (now - start) / duration)
        # Ease in and out while remaining reversible at the current pose.
        smooth = fraction * fraction * (3 - 2 * fraction)
        return origin + (target - origin) * smooth

    def move(self, standing, on_complete):
        now = time.monotonic()
        self.position = self._position(now)
        self.stop_speech()
        target = STANDING if standing else 0
        if self.position == target:
            self.motion = None
            on_complete()
            return
        duration = (RISE_SECONDS if standing else SIT_SECONDS) * abs(target - self.position) / STANDING
        self.motion = (self.position, target, now, duration, on_complete)
        self._schedule()

    def prepare_speech(self, turn_id, shapes, step):
        self.track = (turn_id, shapes, step)
        self.playback = None

    def start_speech(self, turn_id, starts_at):
        if self.track is not None and self.track[0] == turn_id:
            self.playback = starts_at
            self._schedule()

    def stop_speech(self):
        self.track = None
        self.playback = None
        if self.motion is None:
            self._show(round(self.position))

    def reset(self):
        self.motion = None
        self.position = 0.0
        self.stop_speech()
        if self.job is not None:
            self.after_cancel(self.job)
            self.job = None

    def _schedule(self):
        if self.job is None:
            self.job = self.after(12, self._tick)

    def _tick(self):
        self.job = None
        now = time.monotonic()
        self.position = self._position(now)
        index = round(self.position)
        if self.motion is not None:
            _, target, start, duration, completed = self.motion
            if now >= start + duration:
                self.motion = None
                self._show(target)
                completed()
        elif self.playback is not None and self.track is not None and index == STANDING:
            _, shapes, step = self.track
            elapsed = now - self.playback
            sample = int(elapsed / step)
            if elapsed >= 0 and sample < len(shapes):
                previous = shapes[sample - 1] if sample else 0
                progress = (elapsed / step - sample) / .72
                index = expression_index(previous, shapes[sample], progress)
            elif sample >= len(shapes):
                self.stop_speech()
        self._show(index)
        if self.motion is not None or self.playback is not None:
            self._schedule()
