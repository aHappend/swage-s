"""Independent public-port pyramid and affine-head leaf comparisons."""

from oracle import affine, clamp, convolution, fixture, pack, scene
from receipts import prune_compile


def pipeline_check(root, output, run, kind, width=13, height=17, seed=11, classes=8,
                   simulator="icarus", endpoints=False, threshold=500):
    layers = fixture(classes)
    rgb, masks = scene(width, height, seed)
    image = [[[(p[c % 3] - 128) * (-1 if c >= 3 else 1) for c in range(6)]
              for p in row] for row in rgb]
    masks = [[k | (k << 3) for k in row] for row in masks]
    masked = [[[p[c] if masks[y][x] & 1 << c else 0 for c in range(6)]
               for x, p in enumerate(row)] for y, row in enumerate(image)]
    levels = [image]
    for index in (4, 5):
        full = convolution(masked if index == 4 else levels[-1], layers[index])
        levels.append([row[::2] for row in full[::2]])
    if endpoints:
        layers[6].pw = [0] * (6 * classes)
        layers[6].bias = [65536 if c == classes - 1 and endpoints != "zero" else -1
                          for c in range(classes)]
        if endpoints is True and classes >= 3:
            layers[6].bias[1] = 65535
        layers[7].bias = [-1, 4097, 4096, -2147483648]
    weights = []
    for layer in layers[4:6]:
        weights.extend((pack(layer.dw, 8), pack(layer.pw, 8), pack(layer.bias[:6], 32),
                        pack(layer.bias[6:], 32), pack(layer.shift[:6], 5),
                        pack(layer.shift[6:], 5)))
    weights.extend((pack(layers[6].pw, 8), pack(layers[7].pw, 8),
                    pack(layers[6].bias, 32), pack(layers[7].bias, 32),
                    pack(layers[6].shift, 5), pack(layers[7].shift, 5)))
    inputs, expected = [], [0] * (3 * width * height)
    score_relations = {"below": 0, "equal": 0, "above": 0}
    for level, feature in enumerate(levels[:1] if kind == "pyramid" else levels):
        w, h = len(feature[0]), len(feature)
        for y, row in enumerate(feature):
            for x, pixel in enumerate(row):
                keep = masks[y][x] if level == 0 else 63
                inputs.append(pack(pixel, 8) | keep << 48 | x << 54 | y << 67 |
                              level << 80 | int(x == w - 1 and y == h - 1) << 82)
    if kind == "pyramid":
        for level, feature in enumerate(levels):
            w, h = len(feature[0]), len(feature)
            for y, row in enumerate(feature):
                for x, pixel in enumerate(row):
                    keep = masks[y][x] if level == 0 else 63
                    expected[level * width * height + y * w + x] = (
                        pack(pixel, 8) | keep << 48 | int(x == 0 and y == 0) << 54 |
                        int(x == w - 1) << 55 | int(x == w - 1 and y == h - 1) << 56 |
                        x << 57 | y << 70 | level << 83)
        total = sum(len(level) * len(level[0]) for level in levels)
    else:
        if threshold is None:
            scores = []
            for feature in (masked, *levels[1:]):
                for row in feature:
                    for pixel in row:
                        scores.append(max(clamp(affine(pixel, layers[6], c, feature=False), 0, 65535)
                                          for c in range(classes)))
            unique = sorted(set(scores))
            assert len(unique) > 2, "Head threshold fixture must span distinct scores"
            threshold = unique[len(unique)//2]
        for index, value in enumerate(inputs):
            level = (value >> 80) & 3
            x, y = (value >> 54) & 8191, (value >> 67) & 8191
            pixel = []
            for c in range(6):
                lane = (value >> (8 * c)) & 255
                pixel.append((lane - 256 if lane >= 128 else lane) if value & (1 << (48 + c)) else 0)
            scores = [clamp(affine(pixel, layers[6], c, feature=False), 0, 65535)
                      for c in range(classes)]
            score, cls = max(scores), scores.index(max(scores))
            relation = "below" if score < threshold else ("equal" if score == threshold else "above")
            score_relations[relation] += 1
            distances = [clamp(affine(pixel, layers[7], c, feature=False), 0, 4096) for c in range(4)]
            ax, ay = x << level, y << level
            box = [max(0, ax - distances[0]), max(0, ay - distances[1]),
                   min(width, ax + 1 + distances[2]), min(height, ay + 1 + distances[3])]
            expected[index] = (pack(box, 16) | cls << 64 | score << 72 |
                               int(score >= threshold) << 88 | level << 89 |
                               ax << 91 | ay << 104 | (value >> 82) << 117)
        total = len(inputs)
        expected[len(inputs):] = []
    (output / "checkpoint-images").mkdir()
    for name, values in (("input", inputs), ("expected", expected), ("weights", weights)):
        target = output / ("checkpoint-images" if name == "weights" else "") / f"pipeline-{name}.hex"
        target.write_text("".join(f"{v:x}\n" for v in values))
    rtl = [str(p.relative_to(root)) for p in sorted(root.glob("rtl/*.sv"))]
    relative = str(output.relative_to(root))
    selection = int(kind == "head")
    if simulator == "icarus":
        binary = f"{relative}/simulation.vvp"
        run(["iverilog", "-g2012", "-Wall", "-s", "pipeline_tb",
             f"-Ppipeline_tb.KIND={selection}", f"-Ppipeline_tb.W={width}",
             f"-Ppipeline_tb.H={height}", f"-Ppipeline_tb.NC={classes}", "-o", binary,
             "tests/pipeline_tb.sv", *rtl], "pipeline-compile")
        executable = ["vvp", binary]
    else:
        obj = output / "compile-obj"
        run(["verilator", "--binary", "--timing", "-Wno-fatal", "--top-module", "pipeline_tb",
             f"-GKIND={selection}", f"-GW={width}", f"-GH={height}", f"-GNC={classes}",
             "--Mdir", str(obj.relative_to(root)), "-o", "simulation", "-j", "2",
             "tests/pipeline_tb.sv", *rtl], "pipeline-compile")
        prune_compile(obj)
        executable = [str((obj / "simulation").relative_to(root))]
    for stalled in (0, 1):
        text = run([*executable, f"+ROOT={relative}", f"+STALL={stalled}",
                    f"+THRESHOLD={threshold}"], f"pipeline-simulation-{stalled}",
                   timeout=600 if simulator == "icarus" else 3600)
        assert f"records={total}" in text, "Missing pipeline comparison ledger"
    return {"kind": kind, "width": width, "height": height, "classes": classes,
            "seed": seed, "threshold": threshold, "compared_per_replay": total,
            "score_relations_compared": score_relations}
