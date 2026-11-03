"""Independent integer image model from the frozen SWAGE-S2 equations."""

import random

MATRIX = [
    (1, 1, 1, 1, 8, 1, 8, 0), (1, 5, 3, 7, 8, 1, 8, 1),
    (5, 1, 7, 3, 8, 3, 8, 0), (3, 5, 7, 9, 8, 3, 16, 0),
    (7, 9, 3, 4, 8, 1, 16, 1), (8, 6, 8, 6, 8, 3, 8, 0),
    (9, 7, 4, 3, 10, 1, 16, 0), (5, 3, 9, 7, 10, 1, 8, 1),
    (4096, 1, 1, 1, 8, 1, 8, 0), (1, 4096, 1, 3, 8, 1, 16, 0),
    (64, 48, 96, 72, 8, 3, 16, 1),
]
PRESETS = {
    "identity": ([0, 0, 0, 0, 1, 0, 0, 0, 0], 0, 0),
    "gaussian": ([1, 2, 1, 2, 4, 2, 1, 2, 1], 4, 0),
    "sobel_x": ([-1, 0, 1, -2, 0, 2, -1, 0, 1], 0, 1),
    "sobel_y": ([-1, -2, -1, 0, 0, 0, 1, 2, 1], 0, 1),
    "sharpen": ([0, -1, 0, -1, 5, -1, 0, -1, 0], 0, 0),
    "box": ([7282] * 9, 16, 0),
}
PATTERNS = [
    "zero", "maximum", "corner_tl", "corner_tr", "corner_bl", "corner_br",
    "interior", "horizontal", "vertical", "checker", "rgb", "random", "rggb",
]


def unpack(word, channels, bits=8):
    return tuple((word >> (bits * c)) & ((1 << bits) - 1) for c in range(channels))


def pack(pixel, bits=8):
    return sum(v << (bits * c) for c, v in enumerate(pixel))


def sample(image, x, y, border):
    h, w = len(image), len(image[0])
    if border and not (0 <= x < w and 0 <= y < h):
        return (0,) * len(image[0][0])
    return image[min(h - 1, max(0, y))][min(w - 1, max(0, x))]


def taps(image, x, y, border):
    return [sample(image, x + dx, y + dy, border)
            for dy in (-1, 0, 1) for dx in (-1, 0, 1)]


def quantize(accumulator, shift, absolute):
    magnitude = abs(accumulator)
    if shift:
        magnitude = (magnitude + (1 << (shift - 1))) // (1 << shift)
    value = -magnitude if accumulator < 0 and not absolute else magnitude
    return min(255, max(0, value))


def convolve(image, coeff, shift, absolute, border):
    return [[tuple(quantize(sum(k * p[c] for k, p in zip(
        coeff, taps(image, x, y, border))), shift, absolute)
        for c in range(len(image[0][0]))) for x in range(len(image[0]))]
        for y in range(len(image))]


def demosaic(image, border):
    result = []
    for y, row in enumerate(image):
        out = []
        for x in range(len(row)):
            a, b, c, d = (sample(image, x + dx, y + dy, border)[0]
                          for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)))
            if not (x & 1) and not (y & 1):
                pixel = a, (b + c + 1) // 2, d
            elif (x & 1) and not (y & 1):
                pixel = b, (a + d + 1) // 2, c
            elif not (x & 1):
                pixel = c, (a + d + 1) // 2, b
            else:
                pixel = d, (b + c + 1) // 2, a
            out.append(pixel)
        result.append(out)
    return result


def scale(image, width, height, frac, nearest):
    sh, sw, unit = len(image), len(image[0]), 1 << frac
    sx, sy = sw * unit // width, sh * unit // height
    result = []
    for y in range(height):
        py = y * sy
        out = []
        for x in range(width):
            px = x * sx
            if nearest:
                pixel = sample(image, (px + unit // 2) // unit,
                               (py + unit // 2) // unit, 0)
            else:
                ix, iy, fx, fy = px // unit, py // unit, px % unit, py % unit
                weights = ((unit - fx) * (unit - fy), fx * (unit - fy),
                           (unit - fx) * fy, fx * fy)
                neighbors = [sample(image, ix + dx, iy + dy, 0)
                             for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1))]
                pixel = tuple((sum(p[c] * w for p, w in zip(neighbors, weights))
                               + unit * unit // 2) // (unit * unit)
                              for c in range(len(image[0][0])))
            out.append(pixel)
        result.append(out)
    return result


def image_for(geometry, pattern, seed):
    w, h, _, _, bits, channels, _, _ = geometry
    maximum, rng = (1 << bits) - 1, random.Random(seed)
    corners = {"corner_tl": (0, 0), "corner_tr": (w - 1, 0),
               "corner_bl": (0, h - 1), "corner_br": (w - 1, h - 1),
               "interior": (w // 2, h // 2)}
    image = []
    for y in range(h):
        row = []
        for x in range(w):
            if pattern == "zero":
                p = (0,) * channels
            elif pattern == "maximum":
                p = (maximum,) * channels
            elif pattern in corners:
                p = (maximum if (x, y) == corners[pattern] else 0,) * channels
            elif pattern == "horizontal":
                p = (x * maximum // max(1, w - 1),) * channels
            elif pattern == "vertical":
                p = (y * maximum // max(1, h - 1),) * channels
            elif pattern == "checker":
                p = (maximum if (x + y) & 1 else 0,) * channels
            elif pattern == "rgb":
                p = tuple((31 + 43 * x + 17 * y + 71 * c) & maximum
                          for c in range(channels))
            elif pattern == "rggb":
                p = tuple(((23, 89, 143, 211)[(y & 1) * 2 + (x & 1)]
                           + 3 * x + 5 * y + c * 47) & maximum
                          for c in range(channels))
            elif pattern == "one":
                p = (1,) * channels
            else:
                p = tuple(rng.randint(0, maximum) for _ in range(channels))
            row.append(p)
        image.append(row)
    return image


def frame(geometry, mode, pattern, seed, preset="identity", border=0,
          coeff=None, shift=None, absolute=None, tag=None):
    k, s, a = PRESETS[preset]
    k, s, a = list(k if coeff is None else coeff), s if shift is None else shift, (
        a if absolute is None else absolute)
    source = image_for(geometry, pattern, seed)
    work = [[(p[0],) for p in row] for row in source] if mode & 2 else source
    if mode & 2:
        work = demosaic(work, border)
    if mode & 1:
        work = convolve(work, k, s, a, border)
    _, _, dw, dh, bits, _, f, nearest = geometry
    expected = scale(work, dw, dh, f, nearest)
    return dict(mode=mode, border=border, absolute=a, shift=s, coeff=k, source_seed=seed,
                pattern=pattern, preset=preset, tag=tag, source=source,
                inputs=[pack(p, bits) for row in source for p in row],
                expected=[pack(p, bits) for row in expected for p in row])


def scenarios(geometry, seed, extended=False):
    bits, channels = geometry[4:6]
    modes = [0] if bits == 10 else ([0, 1, 2, 3] if channels == 3 else [0, 1])
    frames = [frame(geometry, modes[i % len(modes)], pattern, seed + i,
                    preset=list(PRESETS)[i % len(PRESETS)], border=i % 2)
              for i, pattern in enumerate(PATTERNS)]
    if extended:
        for mode in modes:
            for border in (0, 1):
                for pattern in PATTERNS:
                    frames.append(frame(geometry, mode, pattern, seed, border=border))
        for name in PRESETS:
            for border in (0, 1):
                frames.append(frame(geometry, 3, "rgb", seed, name, border))
        for tap in range(9):
            k = [0] * 9
            k[tap] = 1
            frames.append(frame(geometry, 1, "rgb", seed, coeff=k, tag=f"tap{tap}"))
        for s in (0, 1, 4, 16, 31):
            for a in (0, 1):
                for k in ([32767] * 9, [-32768] * 9,
                          [32767, -32768, 32767, -32768, 1, 32767, -32768, 1, 0]):
                    frames.append(frame(geometry, 1, "random", seed, coeff=k,
                                        shift=s, absolute=a, tag="extrema"))
        for sign in (-1, 1):
            k = [0] * 9
            k[4] = sign
            frames.append(frame(geometry, 1, "one", seed, coeff=k,
                                shift=1, absolute=1, tag=f"tie{sign}"))
    return frames
