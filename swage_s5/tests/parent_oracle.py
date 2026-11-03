"""Independent S1--S4 image mathematics and inherited token packing."""

from itertools import accumulate
from oracle import clamp, normalize, pack


CSC = (((2104, 4130, 802), (-1214, -2384, 3598), (3598, -3013, -585)),
       ((1496, 5032, 508), (-824, -2774, 3598), (3598, -3268, -330)),
       ((9539, 0, 13075), (9539, -3209, -6660), (9539, 16525, 0)),
       ((9539, 0, 14686), (9539, -1747, -4366), (9539, 17305, 0)))


def resize(image, width, height, fraction=8, nearest=False, pixel_bits=8):
    sw, sh, scale = len(image[0]), len(image), 1 << fraction
    xstep, ystep = sw * scale // width, sh * scale // height
    result = []
    for y in range(height):
        result.append([])
        for x in range(width):
            px, py = x * xstep, y * ystep
            if nearest:
                ix, iy = min(sw-1, (px+scale//2)//scale), min(sh-1, (py+scale//2)//scale)
                result[-1].append(list(image[iy][ix]))
                continue
            ix, iy = min(sw-1, px//scale), min(sh-1, py//scale)
            fx, fy = px % scale, py % scale
            a, b = image[iy][ix], image[iy][min(ix+1, sw-1)]
            c, d = image[min(iy+1, sh-1)][ix], image[min(iy+1, sh-1)][min(ix+1, sw-1)]
            result[-1].append([(u*(scale-fx)*(scale-fy)+v*fx*(scale-fy)+
                               w*(scale-fx)*fy+z*fx*fy+scale*scale//2)//(scale*scale)
                              for u, v, w, z in zip(a, b, c, d)])
    return result


def sample(image, x, y, border):
    w, h = len(image[0]), len(image)
    if not (0 <= x < w and 0 <= y < h):
        if border:
            return [0] * len(image[0][0])
        x, y = clamp(x, 0, w-1), clamp(y, 0, h-1)
    return image[y][x]


def demosaic(image, border=0, origin=(0, 0)):
    result = []
    for y, row in enumerate(image):
        result.append([])
        for x, _ in enumerate(row):
            a, b, c, d = [sample(image, sx, sy, border)[0]
                          for sx, sy in ((x, y), (x+1, y), (x, y+1), (x+1, y+1))]
            px, py = (x+origin[0]) & 1, (y+origin[1]) & 1
            if px == py:
                pixel = [d if px else a, (b+c+1)//2, a if px else d]
            else:
                pixel = [b if px else c, (a+d+1)//2, c if px else b]
            result[-1].append(pixel)
    return result


def spatial(image, coefficients, shift=0, absolute=False, border=0):
    result = []
    for y, row in enumerate(image):
        result.append([])
        for x, pixel in enumerate(row):
            lanes = []
            for channel in range(len(pixel)):
                value = sum(sample(image, x+j-1, y+i-1, border)[channel] * coefficients[3*i+j]
                            for i in range(3) for j in range(3))
                magnitude = (abs(value)+(1 << (shift-1) if shift else 0)) >> shift
                lanes.append(clamp(magnitude, 0, 255) if absolute or value >= 0 else 0)
            result[-1].append(lanes)
    return result


def csc(image, mode, input_format, output_420, matrix, in_bias, out_bias):
    if mode < 4:
        matrix = CSC[mode]
        in_bias = [-16, -128, -128] if mode >= 2 else [0]*3
        out_bias = [0]*3 if mode >= 2 else [16, 128, 128]
    result, masks = [], []
    for y, row in enumerate(image):
        result.append([])
        masks.append([])
        for x, pixel in enumerate(row):
            if input_format == 2:
                chroma = image[y & ~1][x & ~1]
                pixel = [pixel[0], *chroma[1:]]
            lanes = []
            for c in range(3):
                value = sum((p+b)*w for p, b, w in zip(pixel, in_bias, matrix[c]))
                rounded = (abs(value)+4096)//8192
                lanes.append(clamp((-rounded if value < 0 else rounded)+out_bias[c], 0, 255))
            keep = 1 if output_420 and input_format == 0 and (x & 1 or y & 1) else 7
            if keep == 1:
                lanes[1:] = [0, 0]
            result[-1].append(lanes)
            masks[-1].append(keep)
    return result, masks


def resize420(image, width, height, fraction, nearest):
    yplane = [[[p[0]] for p in row] for row in image]
    chroma = [[p[1:] for p in row[::2]] for row in image[::2]]
    yplane = resize(yplane, width, height, fraction, nearest)
    chroma = resize(chroma, (width+1)//2, (height+1)//2, fraction, nearest)
    return ([[[p[0], *(chroma[y//2][x//2] if not (x & 1 or y & 1) else [0, 0])]
              for x, p in enumerate(row)] for y, row in enumerate(yplane)],
            [[7 if not (x & 1 or y & 1) else 1 for x in range(width)] for y in range(height)])


def temporal(image, history, alpha, threshold):
    if history is None:
        return [[list(p) for p in row] for row in image]
    return [[[(p*(256-alpha)+q*alpha+128)//256 if
              max(abs(a-b) for a, b in zip(pixel, previous)) < threshold else p
              for p, q in zip(pixel, previous)]
             for pixel, previous in zip(row, hrow)] for row, hrow in zip(image, history)]


def clahe(image, tile=16, limit=4):
    width, height = len(image[0]), len(image)
    result = [[list(p) for p in row] for row in image]
    for ty in range(0, height, tile):
        for tx in range(0, width, tile):
            coordinates = [(x, y) for y in range(ty, min(ty+tile, height))
                           for x in range(tx, min(tx+tile, width))]
            for c in range(3):
                histogram = [0]*256
                for x, y in coordinates:
                    histogram[image[y][x][c]] += 1
                excess = sum(max(0, n-limit) for n in histogram)
                counts = [min(n, limit)+excess//256+int(b < excess % 256)
                          for b, n in enumerate(histogram)]
                lut = [255*n//len(coordinates) for n in accumulate(counts)]
                for x, y in coordinates:
                    result[y][x][c] = lut[image[y][x][c]]
    return result


def token_records(image, patch, means, reciprocals, bf16):
    w, h = len(image[0]), len(image)
    gw, gh = (w+patch-1)//patch, (h+patch-1)//patch
    records = []
    for py in range(gh):
        for px in range(gw):
            for element in range(patch*patch):
                x, y = px*patch+element % patch, py*patch+element//patch
                inside = x < w and y < h
                keep = 7 if inside else 0
                data = normalize(image[y][x] if inside else [0, 0, 0], keep, means, reciprocals, bf16)
                records.append(data | int(not records) << 48 |
                               int(element == patch*patch-1) << 49 |
                               int(px == gw-1 and py == gh-1 and element == patch*patch-1) << 50 |
                               (py*gw+px) << 51 | px << 83 | py << 96 | element << 109 | keep << 117)
    return records


def selftest():
    assert resize([[[0], [100]]], 3, 1) == [[[0], [66], [100]]]
    assert resize([[[0], [100]]], 3, 1, nearest=True) == [[[0], [100], [100]]]
    assert spatial([[[10]]], [0, 0, 0, 0, -1, 0, 0, 0, 0], absolute=True) == [[[10]]]
    assert demosaic([[[10]]], 1) == [[[10, 0, 0]]]
    assert temporal([[[20, 20, 20]]], [[[4, 4, 4]]], 128, 16) == [[[20, 20, 20]]]
    assert temporal([[[19, 19, 19]]], [[[4, 4, 4]]], 128, 16) == [[[12, 12, 12]]]
    assert clahe([[[100, 100, 100]]], 8, 1) == [[[255, 255, 255]]]
