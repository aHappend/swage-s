"""Independent integer/rational image model of the admitted SWAGE-S3 rules.

Pixels are low-component-first tuples. Sparse chroma is represented as zero
bytes plus an explicit mask, never as interpolatable missing samples.
This module models values and raster framing, not RTL scheduling.
"""

from dataclasses import dataclass
from fractions import Fraction


Pixel = tuple[int, ...]
Image = list[list[Pixel]]
IDENTITY = (0, 0, 0, 0, 1, 0, 0, 0, 0)


def round_away(value: Fraction) -> int:
    magnitude = abs(value)
    rounded = (2 * magnitude.numerator + magnitude.denominator) // (
        2 * magnitude.denominator
    )
    return -rounded if value < 0 else rounded


def clip(value: int) -> int:
    return min(255, max(0, value))


def preset(mode: int) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    if mode not in range(4):
        raise ValueError("preset mode must be 0..3")
    kr, kb = ((Fraction(299, 1000), Fraction(114, 1000))
              if mode % 2 == 0 else
              (Fraction(2126, 10000), Fraction(722, 10000)))
    kg = 1 - kr - kb
    if mode < 2:
        y, c = Fraction(219, 255), Fraction(224, 255)
        matrix = (y * kr, y * kg, y * kb,
                  -c * kr / (2 * (1 - kb)), -c * kg / (2 * (1 - kb)), c / 2,
                  c / 2, -c * kg / (2 * (1 - kr)), -c * kb / (2 * (1 - kr)))
        ib, ob = (0, 0, 0), (16, 128, 128)
    else:
        y, c = Fraction(255, 219), Fraction(255, 224)
        matrix = (y, Fraction(0), c * 2 * (1 - kr),
                  y, -c * 2 * kb * (1 - kb) / kg,
                  -c * 2 * kr * (1 - kr) / kg,
                  y, c * 2 * (1 - kb), Fraction(0))
        ib, ob = (-16, -128, -128), (0, 0, 0)
    return tuple(round_away(v * 8192) for v in matrix), ib, ob


def shape(image: Image) -> tuple[int, int]:
    if not image or not image[0]:
        raise ValueError("empty image")
    width = len(image[0])
    channels = len(image[0][0])
    if any(len(row) != width or any(len(p) != channels for p in row)
           for row in image):
        raise ValueError("nonrectangular image")
    return width, len(image)


def sample(image: Image, x: int, y: int, zero: bool) -> Pixel:
    width, height = len(image[0]), len(image)
    if zero and not (0 <= x < width and 0 <= y < height):
        return (0,) * len(image[0][0])
    return image[min(height - 1, max(0, y))][min(width - 1, max(0, x))]


def demosaic(image: Image, zero: bool, origin: tuple[int, int]) -> Image:
    width, height = shape(image)
    result = []
    for y in range(height):
        row = []
        for x in range(width):
            xp, yp = (x + origin[0]) & 1, (y + origin[1]) & 1
            red = sample(image, x + xp, y + yp, zero)[0]
            blue = sample(image, x + 1 - xp, y + 1 - yp, zero)[0]
            ga = sample(image, x, y, zero)[0] if xp != yp else (
                sample(image, x + 1, y, zero)[0])
            gb = sample(image, x + 1, y + 1, zero)[0] if xp != yp else (
                sample(image, x, y + 1, zero)[0])
            row.append((red, (ga + gb + 1) // 2, blue))
        result.append(row)
    return result


def convolution(image: Image, coeff: tuple[int, ...], shift: int,
                absolute: bool, zero: bool) -> Image:
    width, height = shape(image)
    if len(coeff) != 9 or not 0 <= shift <= 31:
        raise ValueError("invalid filter controls")
    result = []
    for y in range(height):
        row = []
        for x in range(width):
            values = []
            for channel in range(len(image[0][0])):
                total = sum(coeff[3 * dy + dx] *
                            sample(image, x + dx - 1, y + dy - 1, zero)[channel]
                            for dy in range(3) for dx in range(3))
                magnitude = (abs(total) + (1 << (shift - 1) if shift else 0)) >> shift
                values.append(0 if total < 0 and not absolute else min(255, magnitude))
            row.append(tuple(values))
        result.append(row)
    return result


def matrix_pixel(pixel: Pixel, coeff: tuple[int, ...], ib: tuple[int, ...],
                 ob: tuple[int, ...]) -> Pixel:
    return tuple(clip(round_away(Fraction(sum(
        coeff[3 * i + j] * (pixel[j] + ib[j]) for j in range(3)), 8192)) + ob[i])
                 for i in range(3))


def scale_plane(plane: list[list[int]], dw: int, dh: int, frac: int,
                nearest: bool) -> list[list[int]]:
    sw, sh = len(plane[0]), len(plane)
    unit = 1 << frac
    sx, sy = (sw * unit) // dw, (sh * unit) // dh
    result = []
    for y in range(dh):
        row = []
        for x in range(dw):
            px, py = x * sx, y * sy
            if nearest:
                row.append(plane[min(sh - 1, (py + unit // 2) // unit)]
                           [min(sw - 1, (px + unit // 2) // unit)])
            else:
                ix, fx = divmod(px, unit)
                iy, fy = divmod(py, unit)
                total = sum(
                    plane[min(sh - 1, iy + dy)][min(sw - 1, ix + dx)] *
                    (fx if dx else unit - fx) * (fy if dy else unit - fy)
                    for dy in range(2) for dx in range(2))
                row.append((total + unit * unit // 2) // (unit * unit))
        result.append(row)
    return result


def scale(image: Image, dw: int, dh: int, frac: int, nearest: bool,
          sparse: bool) -> Image:
    planes = []
    for channel in range(len(image[0][0])):
        stride = 2 if sparse and channel else 1
        plane = [[p[channel] for p in row[::stride]] for row in image[::stride]]
        planes.append(scale_plane(plane, (dw + stride - 1) // stride,
                                  (dh + stride - 1) // stride, frac, nearest))
    return [[tuple(
        (0 if x & 1 or y & 1 else planes[c][y // 2][x // 2])
        if sparse and c else planes[c][y][x]
        for c in range(len(planes))) for x in range(dw)] for y in range(dh)]


@dataclass(frozen=True)
class Config:
    enable: int = 16
    border: bool = False
    absolute: bool = False
    shift: int = 0
    coeff: tuple[int, ...] = IDENTITY
    roi: tuple[int, int, int, int] = (0, 0, 1, 1)
    input_format: int = 0
    csc_mode: int = 0
    output_420: bool = False
    csc_coeff: tuple[int, ...] = (8192, 0, 0, 0, 8192, 0, 0, 0, 8192)
    in_bias: tuple[int, ...] = (0, 0, 0)
    out_bias: tuple[int, ...] = (0, 0, 0)
    hist_channel: int = 0


def legal(config: Config, sw: int, sh: int, bits: int, channels: int) -> bool:
    c = config
    crop, demo, filt, csc, _, hist = [bool(c.enable & (1 << i)) for i in range(6)]
    color = (bits, channels) == (8, 3)
    if (bits, channels) not in ((8, 1), (10, 1), (8, 3)):
        return False
    if c.input_format not in (0, 1, 2):
        return False
    if c.input_format and (not color or demo or filt):
        return False
    if demo and (not color or c.input_format != 0):
        return False
    if filt and (bits != 8 or c.input_format != 0):
        return False
    if crop:
        x, y, w, h = c.roi
        if x < 0 or y < 0 or w < 1 or h < 1 or x + w > sw or y + h > sh:
            return False
        if c.input_format == 2 and (x & 1 or y & 1):
            return False
    if csc:
        if not color or c.csc_mode not in range(5):
            return False
        if c.csc_mode < 2 and c.input_format != 0:
            return False
        if c.csc_mode in (2, 3) and c.input_format == 0:
            return False
    rgb_output = color and (c.input_format == 0 if not csc else c.input_format != 0)
    if hist and (c.hist_channel not in range(4) or
                 (not color and c.hist_channel != 0) or
                 (c.hist_channel == 3 and not rgb_output)):
        return False
    return True


@dataclass(frozen=True)
class Beat:
    data: int
    mask: int
    sof: bool
    eol: bool


def pipeline(image: Image, config: Config, dw: int, dh: int, *,
             bits: int = 8, channels: int = 3, frac: int = 8,
             nearest: bool = False) -> tuple[list[Beat], list[int]]:
    sw, sh = shape(image)
    if not all(1 <= d <= 4096 for d in (sw, sh, dw, dh)) or frac not in (8, 16):
        raise ValueError("illegal parameters")
    if not legal(config, sw, sh, bits, channels):
        raise ValueError("illegal configuration")
    c = config
    image = [list(row) for row in image]
    origin = (0, 0)
    sparse = c.input_format == 2
    raw = channels == 1 or bool(c.enable & 2)
    if c.enable & 1:
        x, y, w, h = c.roi
        origin = x, y
        image = [row[x:x + w] for row in image[y:y + h]]
    if c.enable & 2:
        image = demosaic(image, c.border, origin)
        raw = False
    if c.enable & 4:
        image = convolution(image, c.coeff, c.shift, c.absolute, c.border)
    if c.enable & 8:
        coeff, ib, ob = ((c.csc_coeff, c.in_bias, c.out_bias)
                         if c.csc_mode == 4 else preset(c.csc_mode))
        expanded = [[(p[0], image[y & ~1][x & ~1][1],
                      image[y & ~1][x & ~1][2]) if sparse else p
                     for x, p in enumerate(row)] for y, row in enumerate(image)]
        image = [[matrix_pixel(p, coeff, ib, ob) for p in row] for row in expanded]
        sparse = c.input_format == 0 and c.output_420
        raw = False
        if sparse:
            image = [[(p[0], 0, 0) if x & 1 or y & 1 else p
                      for x, p in enumerate(row)] for y, row in enumerate(image)]
    if c.enable & 16:
        image = scale(image, dw, dh, frac, nearest, sparse)
    width, height = shape(image)
    beats, bins = [], [0] * 256
    for y in range(height):
        for x in range(width):
            p = image[y][x]
            mask = 1 if raw or (sparse and (x & 1 or y & 1)) else 7
            word = p[0] if channels == 1 else sum(v << (8 * i) for i, v in enumerate(p))
            beats.append(Beat(word, mask, x == 0 and y == 0, x == width - 1))
            if c.enable & 32:
                ch = c.hist_channel
                if ch == 3:
                    value = (77 * p[0] + 150 * p[1] + 29 * p[2] + 128) >> 8
                elif mask & (1 << ch):
                    value = p[ch] >> 2 if bits == 10 else p[ch]
                else:
                    continue
                bins[value] += 1
    return beats, bins


def histogram_words(bins: list[int]) -> list[int]:
    if len(bins) != 256 or any(not 0 <= v < (1 << 32) for v in bins):
        raise ValueError("histogram must contain 256 unsigned32 counters")
    return [sum(bins[8 * a + j] << (32 * j) for j in range(8)) for a in range(32)]
