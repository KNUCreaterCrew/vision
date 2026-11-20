"""Compare fixed seat polygons with an operator-confirmed empty view, in RAM only."""

import ctypes
import mmap
import sys

from .camera import require_opencv
from .vlm import visibility_stats


def _similar_pixels(pixels, expected, mask):
    """True for a match, None for marginal changes, False for a clear obstruction."""
    import numpy as np

    cv2 = require_opencv()
    # Auto-exposure and nearby shadows can brighten even just one desk. Allow
    # one bounded, neutral offset only when most corresponding samples support it.
    samples = pixels[::8].astype(np.int16) - expected[::8]
    offset = int(np.rint(np.median(samples)))
    if 0 < abs(offset) <= 32 and (np.abs(samples - offset).max(axis=1) <= 12).mean() >= .65:
        expected = np.clip(expected.astype(np.int16) + offset, 0, 255).astype(np.uint8)
    delta = pixels.astype(np.int16) - expected
    difference = np.abs(delta).max(axis=1)
    changed_fraction = np.count_nonzero(difference > 28) / difference.size
    if changed_fraction > .40:
        return False
    # Scale the veto with the actual polygon area, including on small seats.
    object_area = max(12, min(160, int(np.ceil(0.002 * difference.size))))
    color_change = np.ptp(delta, axis=1)
    strong = (difference > 72) | (color_change > 48)
    marginal_patch = False
    if np.count_nonzero(strong) >= object_area:
        patch = np.zeros(mask.shape, dtype=np.uint8)
        patch[mask] = strong
        _, _, stats, _ = cv2.connectedComponentsWithStats(patch, connectivity=8)
        largest = stats[1:, cv2.CC_STAT_AREA].max()
        if largest >= 4 * object_area:
            return False
        if largest >= object_area:
            # A small neutral highlight can come from a neighboring bottle.
            # A compact, distinctly colored or very high-contrast change is stronger evidence.
            patch[mask] = (difference > 104) | (color_change > 64)
            _, _, stats, _ = cv2.connectedComponentsWithStats(patch, connectivity=8)
            if np.any(stats[1:, cv2.CC_STAT_AREA] >= object_area):
                return False
            marginal_patch = True
    return True if changed_fraction <= .25 and not marginal_patch else None


def _matches_after_small_shift(crop, mask, pixels, expected, original_match):
    """Try a single translation for the whole polygon, bounded to two pixels."""
    import numpy as np

    cv2 = require_opencv()
    ref = np.zeros_like(crop)
    ref[mask] = expected
    gray = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
    # Fit using points whose entire search window is inside the seat.
    interior = cv2.erode(mask.astype(np.uint8), np.ones((5, 5), np.uint8),
                         borderType=cv2.BORDER_CONSTANT, borderValue=0)[2:-2, 2:-2]
    if np.count_nonzero(interior) < 256:
        return original_match
    cost = cv2.matchTemplate(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), gray[2:-2, 2:-2],
                             cv2.TM_SQDIFF, mask=interior)
    _, _, (x, y), _ = cv2.minMaxLoc(cost)
    if cost[y, x] >= cost[2, 2]:
        return original_match
    transform = np.float32([[1, 0, x - 2], [0, 1, y - 2]])
    size = (mask.shape[1], mask.shape[0])
    aligned = cv2.warpAffine(ref, transform, size, flags=cv2.INTER_NEAREST)
    valid = cv2.warpAffine(mask.astype(np.uint8), transform, size, flags=cv2.INTER_NEAREST)
    # Keep testing even the border for which there is no shifted reference.
    aligned[valid == 0] = ref[valid == 0]
    return _similar_pixels(pixels, aligned[mask], mask)


class _LockedPixels:
    """Lock anonymous reference storage before copying any pixels into it."""

    def __init__(self, pixels):
        import numpy as np

        self.shape = pixels.shape
        self.memory = mmap.mmap(-1, pixels.nbytes)
        address = ctypes.c_void_p(ctypes.addressof(ctypes.c_char.from_buffer(self.memory)))
        size = ctypes.c_size_t(pixels.nbytes)
        try:
            if sys.platform.startswith("linux"):
                self.memory.madvise(mmap.MADV_DONTDUMP)
                locked = ctypes.CDLL(None).mlock(address, size) == 0
            elif sys.platform == "win32":
                locked = bool(ctypes.windll.kernel32.VirtualLock(address, size))
            else:
                locked = False
            if not locked:
                raise RuntimeError("Cannot lock empty reference in RAM.")
            np.ndarray(self.shape, dtype=np.uint8, buffer=self.memory)[:] = pixels
        except BaseException:
            self.memory.close()
            raise

    def pixels(self):
        import numpy as np
        return np.ndarray(self.shape, dtype=np.uint8, buffer=self.memory)

    def close(self):
        self.memory.close()


class EmptyReference:
    def __init__(self, config, frame, observations):
        import numpy as np

        cv2 = require_opencv()
        self.seats = {}
        background = np.ones(frame.shape[:2], dtype=bool)
        try:
            for seat in config.seats:
                obs = observations[seat.seat_id]
                if obs.person or obs.belongings or not obs.view_clear:
                    raise ValueError(f"{seat.seat_id}: person, belongings or ambiguous detection")
                points = np.array(seat.polygon, dtype=np.int32)
                x, y, width, height = cv2.boundingRect(points)
                mask = np.zeros((height, width), dtype=np.uint8)
                cv2.fillPoly(mask, [points - (x, y)], 1)
                mask = mask.astype(bool)
                region = (slice(y, y + height), slice(x, x + width))
                background[region][mask] = False
                pixels = frame[region][mask]
                luma = cv2.cvtColor(pixels[:, None, :], cv2.COLOR_BGR2GRAY)
                visibility = visibility_stats(luma, width=width, height=height)
                if not visibility["usable"]:
                    raise ValueError(f"{seat.seat_id}: {visibility['reason']}")
                self.seats[seat.seat_id] = (region, mask, _LockedPixels(pixels))
            # Keep one median color per background tile, never the background image.
            self.background_tiles = []
            height, width = background.shape
            tile_h, tile_w = (height + 3) // 4, (width + 3) // 4
            for y in range(0, height, tile_h):
                for x in range(0, width, tile_w):
                    tile = (slice(y, y + tile_h, 4), slice(x, x + tile_w, 4))
                    valid = background[tile].copy()
                    if valid.sum() >= max(12, valid.size // 4):
                        median = np.median(frame[tile][valid], axis=0)
                        self.background_tiles.append((tile, valid, median))
        except BaseException:
            self.close()
            raise

    def _background_shift(self, frame):
        import numpy as np

        if len(self.background_tiles) < 3:
            return np.zeros(3, dtype=np.int16)
        deltas = np.array([np.median(frame[tile][valid], axis=0) - median
                           for tile, valid, median in self.background_tiles])
        shift = np.median(deltas, axis=0)
        agreeing = (np.abs(deltas - shift) <= 8).all(axis=1)
        if agreeing.mean() < 2 / 3 or np.abs(shift).max() > 32:
            return np.zeros(3, dtype=np.int16)
        return np.rint(shift).astype(np.int16)

    def compare(self, frame):
        """Reject local changes (e.g. a phone) even within a much larger polygon."""
        import numpy as np

        cv2 = require_opencv()
        shift = self._background_shift(frame)
        matches = {}
        for seat_id, (region, mask, reference) in self.seats.items():
            pixels = frame[region][mask]
            luma = cv2.cvtColor(pixels[:, None, :], cv2.COLOR_BGR2GRAY)
            height, width = mask.shape
            matches[seat_id] = False
            if not visibility_stats(luma, width=width, height=height)["usable"]:
                continue
            expected = np.clip(reference.pixels() + shift, 0, 255).astype(np.uint8)
            match = _similar_pixels(pixels, expected, mask)
            if match is not True:
                match = _matches_after_small_shift(frame[region], mask, pixels, expected, match)
            matches[seat_id] = match
        return matches

    def close(self):
        for _, _, reference in self.seats.values():
            reference.close()
        self.seats.clear()
