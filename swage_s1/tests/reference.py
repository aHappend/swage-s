"""Independent index-based integer and floating SWAGE-S1 reference models.

The ideal-coordinate bilinear bound permits 1e-9 channel LSB of floating-point
numerical tolerance, not extra fixed-point error. Quantized-coordinate rounded
results must agree exactly. Nearest ideal selection differences are reported
separately from differences in selected pixel values.
"""

from dataclasses import dataclass
import math
import random


GEOMETRIES = (
    (8, 8, 16, 16), (16, 16, 8, 8), (12, 10, 7, 13),
    (8, 8, 8, 8), (1, 1, 1, 1), (1, 3, 5, 1), (5, 1, 1, 7),
    (3, 5, 2, 3), (2, 2, 7, 9), (4096, 2, 7, 3), (7, 2, 4096, 3),
)
HEIGHT_EDGES = ((1, 4096, 2, 7), (2, 7, 3, 4096))
PATTERNS = ("zero", "full", "impulse", "horizontal", "vertical", "checker", "random")
STALLS = ((0, 17), (1, 17), (1, 89), (1, 2026), (2, 17))
FORMATS = ((8, 1), (10, 1), (8, 3))


@dataclass(frozen=True)
class Config:
    sw: int = 8
    sh: int = 8
    dw: int = 16
    dh: int = 16
    bits: int = 8
    channels: int = 1
    frac: int = 8
    mode: int = 0

    def parameters(self):
        return dict(zip(
            ("SRC_WIDTH", "SRC_HEIGHT", "DST_WIDTH", "DST_HEIGHT",
             "PIXEL_BITS", "CHANNELS", "PHASE_FRAC", "INTERPOLATION"),
            (self.sw, self.sh, self.dw, self.dh, self.bits,
             self.channels, self.frac, self.mode),
        ))

    @property
    def label(self):
        return "-".join(str(v) for v in self.parameters().values())

    @property
    def data_width(self):
        return 16 if self.bits == 10 else self.bits * self.channels


def configurations(geometries=GEOMETRIES):
    for geometry in geometries:
        for bits, channels in FORMATS:
            for frac in (8, 16):
                for mode in (0, 1):
                    yield Config(*geometry, bits, channels, frac, mode)


def image(c, pattern, seed, frame=0):
    rng = random.Random((seed << 8) + frame)
    maximum = (1 << c.bits) - 1
    pixels = []
    for y in range(c.sh):
        for x in range(c.sw):
            values = []
            for channel in range(c.channels):
                if pattern == "zero":
                    value = 0
                elif pattern == "full":
                    value = maximum
                elif pattern == "impulse":
                    value = maximum if (x, y) == (
                        (c.sw // 2 + channel + frame) % c.sw,
                        (c.sh // 2 + channel + frame) % c.sh,
                    ) else 0
                elif pattern == "horizontal":
                    value = x * maximum // max(1, c.sw - 1)
                elif pattern == "vertical":
                    value = y * maximum // max(1, c.sh - 1)
                elif pattern == "checker":
                    value = maximum if (x + y + channel + frame) % 2 else 0
                elif pattern == "random":
                    value = rng.randrange(maximum + 1)
                elif pattern == "ties":
                    value = (x + y + channel + frame) % 2
                else:
                    raise ValueError(pattern)
                if c.channels == 3 and pattern in ("horizontal", "vertical"):
                    value = (value + 37 * channel + 19 * frame) % (maximum + 1)
                values.append(value)
            pixels.append(sum(v << (i * c.bits) for i, v in enumerate(values)))
    return pixels


def integer_reference(c, pixels):
    """Coordinates derive directly from indices, never a mirrored accumulator."""
    s = 1 << c.frac
    result = []
    for y in range(c.dh):
        py = y * ((c.sh * s) // c.dh)
        for x in range(c.dw):
            px = x * ((c.sw * s) // c.dw)
            if c.mode:
                sx = min(c.sw - 1, (px + s // 2) // s)
                sy = min(c.sh - 1, (py + s // 2) // s)
                value = pixels[sy * c.sw + sx]
            else:
                ix, fx = divmod(px, s)
                iy, fy = divmod(py, s)
                coordinates = (
                    (min(ix, c.sw - 1), min(iy, c.sh - 1)),
                    (min(ix + 1, c.sw - 1), min(iy, c.sh - 1)),
                    (min(ix, c.sw - 1), min(iy + 1, c.sh - 1)),
                    (min(ix + 1, c.sw - 1), min(iy + 1, c.sh - 1)),
                )
                weights = ((s - fx) * (s - fy), fx * (s - fy),
                           (s - fx) * fy, fx * fy)
                value = 0
                for channel in range(c.channels):
                    mask = (1 << c.bits) - 1
                    total = sum(
                        ((pixels[yy * c.sw + xx] >> (channel * c.bits)) & mask) * w
                        for (xx, yy), w in zip(coordinates, weights)
                    )
                    rounded = (total + s * s // 2) // (s * s)
                    assert 0 <= rounded <= mask
                    value |= rounded << (channel * c.bits)
            result.append((value, int(x == 0 and y == 0), int(x == c.dw - 1)))
    return result


def floating_reference(c, pixels, x, y, quantized):
    """Separately evaluated float spatial interpolation; no integer weights."""
    if quantized:
        scale = float(2 ** c.frac)
        u = x * math.floor(c.sw * scale / c.dw) / scale
        v = y * math.floor(c.sh * scale / c.dh) / scale
    else:
        u = x * c.sw / c.dw
        v = y * c.sh / c.dh
    if c.mode:
        xx = min(c.sw - 1, math.floor(u + 0.5))
        yy = min(c.sh - 1, math.floor(v + 0.5))
        return [
            (pixels[yy * c.sw + xx] >> (c.bits * channel)) & ((1 << c.bits) - 1)
            for channel in range(c.channels)
        ]
    xbase, ybase = math.floor(u), math.floor(v)
    a, b = u - xbase, v - ybase

    def sample(xx, yy, channel):
        return ((pixels[min(c.sh - 1, yy) * c.sw + min(c.sw - 1, xx)]
                 >> (c.bits * channel)) & ((1 << c.bits) - 1))

    return [
        (1.0 - b) * ((1.0 - a) * sample(xbase, ybase, ch)
                     + a * sample(xbase + 1, ybase, ch))
        + b * ((1.0 - a) * sample(xbase, ybase + 1, ch)
               + a * sample(xbase + 1, ybase + 1, ch))
        for ch in range(c.channels)
    ]


def compare_models(c, pixels, expected):
    maximum_error = maximum_bound = 0.0
    nearest_differences = nearest_selection_differences = ties = carry = right = bottom = 0
    scale, maximum = 1 << c.frac, (1 << c.bits) - 1
    for index, (packed, _, _) in enumerate(expected):
        y, x = divmod(index, c.dw)
        quantized = floating_reference(c, pixels, x, y, True)
        ideal = floating_reference(c, pixels, x, y, False)
        uq = x * ((c.sw * scale) // c.dw) / scale
        vq = y * ((c.sh * scale) // c.dh) / scale
        bound = 0.5 + maximum * (abs(uq - x * c.sw / c.dw)
                                 + abs(vq - y * c.sh / c.dh))
        carry += int(uq >= 256 or vq >= 256)
        right += int(math.floor(uq) + 1 >= c.sw)
        bottom += int(math.floor(vq) + 1 >= c.sh)
        if c.mode:
            selected_quantized = (
                min(c.sw-1, math.floor(uq+0.5)), min(c.sh-1, math.floor(vq+0.5)),
            )
            selected_ideal = (
                min(c.sw-1, math.floor(x*c.sw/c.dw+0.5)),
                min(c.sh-1, math.floor(y*c.sh/c.dh+0.5)),
            )
            nearest_selection_differences += int(selected_quantized != selected_ideal)
        for channel in range(c.channels):
            actual = (packed >> (channel * c.bits)) & maximum
            rounded = math.floor(quantized[channel] + 0.5)
            assert actual == rounded, ("integer/float disagreement", c, x, y, channel)
            error = abs(actual - ideal[channel])
            maximum_error = max(maximum_error, error)
            maximum_bound = max(maximum_bound, bound)
            if c.mode:
                nearest_differences += int(actual != ideal[channel])
                ties += int(uq % 1 == 0.5 or vq % 1 == 0.5)
            else:
                assert error <= bound + 1e-9, ("ideal-coordinate bound", c, x, y, error, bound)
                ties += int(quantized[channel] % 1 == 0.5)
    return dict(maximum_error=maximum_error, maximum_bound=maximum_bound,
                nearest_ideal_differences=nearest_differences, ties=ties,
                nearest_ideal_selection_differences=nearest_selection_differences,
                carry=carry, right_clamp=right, bottom_clamp=bottom,
                compared_channels=len(expected) * c.channels)
