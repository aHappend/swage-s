"""Independent integer/rational SWAGE reference; no RTL evaluation or imports."""

from fractions import Fraction


def pack(values, bits):
    return sum((v & ((1 << bits) - 1)) << (i * bits) for i, v in enumerate(values))


def round_even(value):
    """Round an exact rational, symmetrically, with even ties."""
    sign = -1 if value < 0 else 1
    n, d = abs(value.numerator), value.denominator
    q, r = divmod(n, d)
    return sign * (q + (2 * r > d or (2 * r == d and q & 1)))


def bf16(value):
    if not value:
        return 0
    sign = int(value < 0) << 15
    value = abs(value)
    exponent = value.numerator.bit_length() - value.denominator.bit_length()
    power = Fraction(2**exponent) if exponent >= 0 else Fraction(1, 2**-exponent)
    if value < power:
        exponent -= 1
        power /= 2
    assert -126 <= exponent <= 127, "admitted coefficients must produce normal finite BF16"
    mantissa = round_even(value / power * 128)
    if mantissa == 256:
        mantissa = 128
        exponent += 1
    assert exponent <= 127
    return sign | ((exponent + 127) << 7) | (mantissa - 128)


def normalize(pixel, means, reciprocals, use_bf16):
    lanes = []
    for x, mean, reciprocal in zip(pixel, means, reciprocals):
        value = Fraction((256 * x - mean) * reciprocal, 1 << 24)
        lane = bf16(value) if use_bf16 else max(-32768, min(32767, round_even(value * 256)))
        lanes.append(lane & 65535)
    return pack(lanes, 16)


CSC = (
    ((2104, 4130, 802), (-1214, -2384, 3598), (3598, -3013, -585)),
    ((1496, 5032, 508), (-824, -2774, 3598), (3598, -3268, -330)),
    ((9539, 0, 13075), (9539, -3209, -6660), (9539, 16525, 0)),
    ((9539, 0, 14686), (9539, -1747, -4366), (9539, 17305, 0)),
)


def clamp_byte(value):
    return max(0, min(255, value))


def away(value, shift):
    magnitude = (abs(value) + ((1 << (shift - 1)) if shift else 0)) >> shift
    return -magnitude if value < 0 else magnitude


def expand420(image):
    h, w = len(image), len(image[0])
    return [[(image[y][x][0], image[y & ~1][x & ~1][1],
              image[y & ~1][x & ~1][2]) for x in range(w)] for y in range(h)]


def scale_plane(image, dw, dh, frac, nearest):
    h, w, channels = len(image), len(image[0]), len(image[0][0])
    unit = 1 << frac
    ix, iy = (w * unit) // dw, (h * unit) // dh
    output = []
    for y in range(dh):
        row = []
        py = y * iy
        for x in range(dw):
            px = x * ix
            if nearest:
                row.append(image[min(h - 1, (py + unit // 2) // unit)]
                           [min(w - 1, (px + unit // 2) // unit)])
                continue
            x0, y0 = min(w - 1, px // unit), min(h - 1, py // unit)
            x1, y1 = min(w - 1, x0 + 1), min(h - 1, y0 + 1)
            fx, fy = px % unit, py % unit
            row.append(tuple(
                (image[y0][x0][c] * (unit - fx) * (unit - fy)
                 + image[y0][x1][c] * fx * (unit - fy)
                 + image[y1][x0][c] * (unit - fx) * fy
                 + image[y1][x1][c] * fx * fy + unit * unit // 2) // (unit * unit)
                for c in range(channels)))
        output.append(row)
    return output


def legacy(image, masks, cfg, params):
    """Return literal contract pixels/masks, independent of implementation timing."""
    sw, sh, dw, dh, bits, channels, frac, nearest, capacity, tile = params
    image = [list(row) for row in image]
    masks = [list(row) for row in masks]
    enable = cfg["enable"]
    ox, oy = (cfg["rx"], cfg["ry"]) if enable & 1 else (0, 0)
    if enable & 1:
        image = [row[ox:ox + cfg["rw"]] for row in image[oy:oy + cfg["rh"]]]
        masks = [row[ox:ox + cfg["rw"]] for row in masks[oy:oy + cfg["rh"]]]
    h, w = len(image), len(image[0])

    def at(source, x, y):
        if cfg["border"] and not (0 <= x < w and 0 <= y < h):
            return (0,) * channels
        return source[min(h - 1, max(0, y))][min(w - 1, max(0, x))]

    if enable & 2:
        result = []
        for y in range(h):
            row = []
            for x in range(w):
                q = [at(image, x + dx, y + dy)[0] for dy in (0, 1) for dx in (0, 1)]
                px, py = (x + ox) & 1, (y + oy) & 1
                red = q[py * 2 + px]
                blue = q[(1 - py) * 2 + 1 - px]
                ga, gb = (q[0], q[3]) if px ^ py else (q[1], q[2])
                row.append((red, (ga + gb + 1) // 2, blue))
            result.append(row)
        image = result
        masks = [[7] * w for _ in range(h)]
    if enable & 4:
        result = []
        for y in range(h):
            row = []
            for x in range(w):
                samples = [at(image, x + dx, y + dy) for dy in (-1, 0, 1)
                           for dx in (-1, 0, 1)]
                values = [sum(k * sample[c] for k, sample in zip(cfg["coeff"], samples))
                          for c in range(channels)]
                row.append(tuple(clamp_byte(away(abs(v) if cfg["abs"] else v, cfg["shift"]))
                                 for v in values))
            result.append(row)
        image = result
        masks = [[7 if channels == 3 else 1] * w for _ in range(h)]
    output_format = cfg["format"]
    if enable & 8:
        if cfg["format"] == 2:
            image = expand420(image)
        mode = cfg["mode"]
        matrix = CSC[mode] if mode < 4 else tuple(tuple(cfg["csc"][3*c:3*c+3]) for c in range(3))
        ib = (0, 0, 0) if mode < 2 else ((-16, -128, -128) if mode < 4 else cfg["ib"])
        ob = (16, 128, 128) if mode < 2 else ((0, 0, 0) if mode < 4 else cfg["ob"])
        output_format = 0 if cfg["format"] else (2 if cfg["out420"] else 1)
        result, masks = [], []
        for y, row in enumerate(image):
            rr, mm = [], []
            for x, pixel in enumerate(row):
                p = tuple(clamp_byte(away(sum(matrix[c][j] * (pixel[j] + ib[j])
                                             for j in range(3)), 13) + ob[c]) for c in range(3))
                mask = 1 if output_format == 2 and (x & 1 or y & 1) else 7
                rr.append((p[0], 0, 0) if mask == 1 else p)
                mm.append(mask)
            result.append(rr)
            masks.append(mm)
        image = result
    if enable & 16:
        if output_format == 2:
            yplane = [[(p[0],) for p in row] for row in image]
            chroma = [[image[y][x][1:] for x in range(0, w, 2)] for y in range(0, h, 2)]
            yplane = scale_plane(yplane, dw, dh, frac, nearest)
            chroma = scale_plane(chroma, (dw + 1)//2, (dh + 1)//2, frac, nearest)
            image = [[(yplane[y][x][0],) + (chroma[y//2][x//2] if not (x & 1 or y & 1)
                                            else (0, 0)) for x in range(dw)] for y in range(dh)]
        else:
            image = scale_plane(image, dw, dh, frac, nearest)
        h, w = dh, dw
        masks = [[1 if channels == 1 or (output_format == 2 and (x & 1 or y & 1)) else 7
                  for x in range(w)] for y in range(h)]
    bins = [0] * 256
    if enable & 32:
        channel = cfg["hist"]
        for row, mask_row in zip(image, masks):
            for p, mask in zip(row, mask_row):
                if channels == 1:
                    bins[p[0] >> (2 if bits == 10 else 0)] += 1
                elif channel == 3:
                    bins[(77*p[0] + 150*p[1] + 29*p[2] + 128) // 256] += 1
                elif mask & (1 << channel):
                    bins[p[channel]] += 1
    return image, masks, bins


def temporal(image, history, alpha, threshold):
    if history is None:
        return [list(row) for row in image]
    return [[p if max(abs(a-b) for a, b in zip(p, old)) >= threshold else
             tuple(((256-alpha)*a + alpha*b + 128)//256 for a, b in zip(p, old))
             for p, old in zip(row, oldrow)] for row, oldrow in zip(image, history)]


def clahe(image, tile, limit):
    h, w = len(image), len(image[0])
    result = [[list(p) for p in row] for row in image]
    for ty in range(0, h, tile):
        for tx in range(0, w, tile):
            sites = [(y, x) for y in range(ty, min(h, ty + tile))
                     for x in range(tx, min(w, tx + tile))]
            for c in range(3):
                hist = [0] * 256
                for y, x in sites:
                    hist[image[y][x][c]] += 1
                clipped = [min(n, limit) for n in hist]
                excess = sum(hist) - sum(clipped)
                q, r = divmod(excess, 256)
                redistributed = [n + q + (i < r) for i, n in enumerate(clipped)]
                assert sum(redistributed) == len(sites)
                cdf, lut = 0, []
                for n in redistributed:
                    cdf += n
                    lut.append(255 * cdf // len(sites))
                for y, x in sites:
                    result[y][x][c] = lut[image[y][x][c]]
    return [[tuple(p) for p in row] for row in result]


def pixels(image, masks, bits):
    h, w = len(image), len(image[0])
    return [pack(p, bits if len(p) == 1 else 8) | (mask << 24) |
            (int(x == 0 and y == 0) << 27) | (int(x == w-1) << 28)
            for y, (row, mm) in enumerate(zip(image, masks))
            for x, (p, mask) in enumerate(zip(row, mm))]


def tokens(image, size, means, reciprocals, use_bf16):
    h, w = len(image), len(image[0])
    cols, rows = (w + size - 1)//size, (h + size - 1)//size
    result = []
    for py in range(rows):
        for px in range(cols):
            pid = py * cols + px
            for ly in range(size):
                for lx in range(size):
                    element = ly * size + lx
                    x, y = px * size + lx, py * size + ly
                    keep = 7 if x < w and y < h else 0
                    data = normalize(image[y][x], means, reciprocals, use_bf16) if keep else 0
                    last = element == size*size-1
                    first = pid == 0 and element == 0
                    final = last and pid == cols*rows-1
                    result.append(data | pid << 48 | px << 80 | py << 93 | element << 106 |
                                  keep << 114 | first << 117 | last << 118 | final << 119)
    return result
