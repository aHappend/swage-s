"""Leaf expectations from mathematical image operators, not DUT internals."""

from oracle import c2f, convolution, fixture, pack, scene


def leaf_check(root, output, run, kind, width, height, seed):
    layers = fixture()
    rgb, masks = scene(width, height, seed)
    image = [[[p[c] - 128 for c in range(3)] for p in row] for row in rgb]
    vectors = [pack(layers[0].dw, 8), pack(layers[0].pw, 8),
               pack(layers[0].bias[:3], 32), pack(layers[0].bias[3:], 32),
               pack(layers[0].shift[:3], 5), pack(layers[0].shift[3:], 5),
               pack(layers[1].dw, 8), pack(layers[1].pw, 8),
               pack(layers[1].bias[:3], 32), pack(layers[1].bias[3:], 32),
               pack(layers[1].shift[:3], 5), pack(layers[1].shift[3:], 5),
               pack(layers[3].pw, 8), pack(layers[3].bias, 32),
               pack(layers[3].shift, 5), 0]
    if kind == "c2f":
        image = [[p + [p[2], p[0], p[1]] for p in row] for row in image]
        masks = [[k | (k << 3) for k in row] for row in masks]
    masked = [[[p[c] if masks[y][x] & (1 << c) else 0 for c in range(len(p))]
               for x, p in enumerate(row)] for y, row in enumerate(image)]
    if kind == "dsconv":
        expected = convolution(masked, layers[0])
    elif kind == "c2f":
        expected = c2f(masked, layers)
    else:
        expected = []
        for y in range(height):
            expected.append([])
            for x in range(width):
                taps = []
                for c in range(3):
                    for i in range(3):
                        for j in range(3):
                            sy, sx = y + i - 2, x + j - 2
                            taps.append(masked[sy][sx][c] if sy >= 0 and sx >= 0 else 0)
                expected[-1].append(taps)
    inputs = [pack(p, 8) | masks[y][x] << 48
              for y, row in enumerate(image) for x, p in enumerate(row)]
    records = []
    for y, row in enumerate(expected):
        for x, p in enumerate(row):
            keep = masks[y][x] if kind == "window" else 63
            records.append(pack(p, 8) | keep << 216 |
                           int(x == 0 and y == 0) << 222 | int(x == width - 1) << 223 |
                           int(x == width - 1 and y == height - 1) << 224 |
                           x << 225 | y << 238)
    for name, values in (("leaf-input", inputs), ("leaf-expected", records), ("leaf-weights", vectors)):
        (output / f"{name}.hex").write_text("".join(f"{v:x}\n" for v in values))
    relative = str(output.relative_to(root))
    rtl = [str(p.relative_to(root)) for p in sorted(root.glob("rtl/*.sv"))]
    selection = ("window", "dsconv", "c2f").index(kind)
    binary = f"{relative}/leaf.vvp"
    run(["iverilog", "-g2012", "-Wall", "-s", "leaf_tb", f"-Pleaf_tb.KIND={selection}",
         f"-Pleaf_tb.W={width}", f"-Pleaf_tb.H={height}", "-o", binary,
         "tests/leaf_tb.sv", *rtl], "leaf-compile")
    for stalled in (0, 1):
        run(["vvp", binary, f"+ROOT={relative}", f"+STALL={stalled}"],
            f"leaf-simulation-{stalled}", timeout=600)
    return {"kind": kind, "width": width, "height": height, "seed": seed,
            "elements_per_replay": width * height}
