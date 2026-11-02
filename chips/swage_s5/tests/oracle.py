"""Independent integer SWAGE-S5 graph and token mathematics."""

from dataclasses import dataclass
from fractions import Fraction


def rne(value, shift):
    return round(Fraction(value, 1 << shift))


def clamp(value, lo, hi):
    return min(hi, max(lo, value))


def pack(values, bits):
    return sum((v & ((1 << bits) - 1)) << (bits * i) for i, v in enumerate(values))


def bf16(product):
    if not product:
        return 0
    sign = int(product < 0)
    magnitude = abs(product)
    leading = magnitude.bit_length() - 1
    significand = rne(magnitude, leading - 7) if leading >= 7 else magnitude << (7 - leading)
    exponent = leading - 24
    if significand == 256:
        significand = 128
        exponent += 1
    return (sign << 15) | ((exponent + 127) << 7) | (significand - 128)


def normalize(rgb, keep, means, reciprocals, use_bf16=False):
    if not keep:
        return 0
    products = [(p * 256 - m) * r for p, m, r in zip(rgb, means, reciprocals)]
    return pack([bf16(p) if use_bf16 else clamp(rne(p, 16), -32768, 32767)
                 for p in products], 16)


@dataclass
class Layer:
    inputs: int
    outputs: int
    spatial: bool
    dw: list
    pw: list
    bias: list
    shift: list

    @classmethod
    def zero(cls, inputs, outputs, spatial=True):
        size = inputs + outputs if spatial else outputs
        return cls(inputs, outputs, spatial, [0] * (9 * inputs) if spatial else [],
                   [0] * (inputs * outputs), [0] * size, [0] * size)

    def writes(self, index):
        for kind, values in enumerate((self.dw, self.pw, self.bias, self.shift)):
            for offset, value in enumerate(values):
                yield ((index << 12) | (kind << 10) | offset, value & 0xFFFFFFFF)


def fixture(classes=3):
    layers = [Layer.zero(3, 6), Layer.zero(3, 3), Layer.zero(3, 3),
              Layer.zero(12, 6, False), Layer.zero(6, 6), Layer.zero(6, 6),
              Layer.zero(6, classes, False), Layer.zero(6, 4, False)]
    for li in (0, 1, 2, 4, 5):
        layer = layers[li]
        for c in range(layer.inputs):
            layer.dw[9 * c + 8] = 2
            layer.dw[9 * c + 7] = -1
            layer.dw[9 * c + 5] = 1
            layer.shift[c] = 1
        for o in range(layer.outputs):
            layer.pw[o * layer.inputs + o % layer.inputs] = 1
    for o in range(6):
        layers[3].pw[o * 12 + o] = 1
        layers[3].pw[o * 12 + 6 + o % 3] = 1
        layers[3].pw[o * 12 + 9 + o % 3] = 1
        layers[3].shift[o] = 1
    for c in range(classes):
        layers[6].pw[c * 6 + c % 6] = 7 + c
        layers[6].bias[c] = 320 + c * 3
    for o in range(4):
        layers[7].pw[o * 6 + o] = o + 1
        layers[7].shift[o] = 4
        layers[7].bias[o] = 17 + 3 * o
    return layers


def affine(pixel, layer, output, offset=0, feature=True):
    value = sum(a * b for a, b in zip(pixel,
                layer.pw[output * layer.inputs:(output + 1) * layer.inputs]))
    value = rne(value + layer.bias[offset + output], layer.shift[offset + output])
    return clamp(value, 0, 127) if feature else value


def convolution(image, layer):
    height, width = len(image), len(image[0])
    result = []
    for y in range(height):
        row = []
        for x in range(width):
            depth = []
            for c in range(layer.inputs):
                value = layer.bias[c]
                for i in range(3):
                    for j in range(3):
                        sy, sx = y + i - 2, x + j - 2
                        if sy >= 0 and sx >= 0:
                            value += image[sy][sx][c] * layer.dw[c * 9 + i * 3 + j]
                depth.append(clamp(rne(value, layer.shift[c]), -128, 127))
            row.append([affine(depth, layer, o, layer.inputs) for o in range(layer.outputs)])
        result.append(row)
    return result


def c2f(stem, layers):
    b = [[p[3:] for p in row] for row in stem]
    branches = [b]
    for layer in layers[1:3]:
        transformed = convolution(b, layer)
        b = [[[clamp(u + v, 0, 127) for u, v in zip(p, q)]
              for p, q in zip(row, trow)] for row, trow in zip(b, transformed)]
        branches.append(b)
    f0 = []
    for y, row in enumerate(stem):
        f0.append([])
        for x, p in enumerate(row):
            concatenated = p + branches[1][y][x] + branches[2][y][x]
            f0[-1].append([affine(concatenated, layers[3], o) for o in range(6)])
    return f0


def graph(rgb, masks, layers):
    centered = [[[p[c] - 128 if masks[y][x] & (1 << c) else 0 for c in range(3)]
                 for x, p in enumerate(row)] for y, row in enumerate(rgb)]
    stem = convolution(centered, layers[0])
    levels = [c2f(stem, layers)]
    for layer in layers[4:6]:
        full = convolution(levels[-1], layer)
        levels.append([[p for p in row[::2]] for row in full[::2]])
    return levels


def detections(rgb, masks, layers, threshold, topk):
    width, height = len(rgb[0]), len(rgb)
    candidates = []
    for level, image in enumerate(graph(rgb, masks, layers)):
        for iy, row in enumerate(image):
            for ix, pixel in enumerate(row):
                scores = [clamp(affine(pixel, layers[6], c, feature=False), 0, 65535)
                          for c in range(layers[6].outputs)]
                confidence = max(scores)
                cls = scores.index(confidence)
                if confidence < threshold:
                    continue
                left, top, right, bottom = [
                    clamp(affine(pixel, layers[7], o, feature=False), 0, 4096) for o in range(4)]
                x, y = ix * (1 << level), iy * (1 << level)
                box = [max(0, x - left), max(0, y - top),
                       min(width, x + 1 + right), min(height, y + 1 + bottom)]
                data = pack(box, 16) | cls << 64 | confidence << 72
                candidates.append(((-confidence, cls, level, y, x), data, x, y))
    return sorted(candidates)[:topk], len(candidates)


def scene(width, height, seed):
    # Two distinct bright rectangles on a signed-input background.
    rgb, masks = [], []
    for y in range(height):
        rgb.append([])
        masks.append([])
        for x in range(width):
            bright = (x < max(1, width // 3) and y < max(1, height // 3)) or (
                x >= width * 2 // 3 and y >= height * 2 // 3)
            rgb[-1].append([(211 if bright else 75) + ((x * 19 + y * 31 + seed + c * 7) % 39)
                            for c in range(3)])
            masks[-1].append(7 if (x + 3 * y + seed) % 23 else 5)
    return rgb, masks


def selftest():
    assert [rne(v, 1) for v in (-7, -5, -3, -1, 1, 3, 5, 7)] == [-4, -2, -2, 0, 0, 2, 2, 4]
    assert bf16(1 << 24) == 0x3F80
    assert bf16(-(1 << 24)) == 0xBF80
    assert bf16((1 << 24) + (1 << 16)) == 0x3F80
    assert bf16((1 << 24) + 3 * (1 << 16)) == 0x3F82
    layer = Layer.zero(1, 1)
    layer.dw[:] = [1] * 9
    layer.pw[:] = [1]
    assert convolution([[[1], [2]], [[3], [4]]], layer) == [[[1], [3]], [[4], [10]]]
    layer.dw[:] = [0] * 8 + [127]
    assert convolution([[[127]]], layer) == [[[127]]]
    layer.pw[:] = [-1]
    assert convolution([[[-128]]], layer) == [[[127]]]
    layer.dw[-1] = 1
    layer.shift[0] = 1
    assert convolution([[[-3]]], layer) == [[[2]]]
    endpoint_layers = [Layer.zero(3, 6), Layer.zero(3, 3), Layer.zero(3, 3),
                       Layer.zero(12, 6, False), Layer.zero(6, 6), Layer.zero(6, 6),
                       Layer.zero(6, 3, False), Layer.zero(6, 4, False)]
    endpoint_layers[6].bias[:] = [-1, 65535, 65536]
    endpoint_layers[7].bias[:] = [-1, 4097, 3, 2]
    winners, count = detections([[[128, 128, 128]]], [[7]], endpoint_layers, 65535, 3)
    assert count == 3
    assert [candidate[0] for candidate in winners] == [
        (-65535, 1, level, 0, 0) for level in range(3)]
    assert [candidate[1] for candidate in winners] == [
        pack([0, 0, 1, 1], 16) | 1 << 64 | 65535 << 72] * 3
