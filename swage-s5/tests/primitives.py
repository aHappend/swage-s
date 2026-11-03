"""Analytical quantization, normalizer and temporal arithmetic fixtures."""

from oracle import clamp, normalize, pack, rne
from receipts import prune_compile


def primitive_check(root, output, run, kind, simulator="icarus", rtl_root=None, mutation=False):
    inputs, expected = [], []
    bins = {}
    if kind == "quantize":
        for value in (-2**39, -2**31, -1025, -257, -129, -128, -127, -7, -5, -3, -1,
                      0, 1, 3, 5, 7, 127, 128, 129, 257, 1025, 2**31-1, 2**39-1):
            for shift in (0, 1, 2, 8, 31):
                for relu in (0, 1):
                    rounded = rne(value, shift)
                    quantized = clamp(rounded, 0 if relu else -128, 127)
                    inputs.append((value & ((1 << 40) - 1)) | shift << 40 | relu << 45)
                    expected.append((rounded & ((1 << 40) - 1)) | (quantized & 255) << 40)
                    if rounded > 127:
                        bins["positive_saturation"] = bins.get("positive_saturation", 0) + 1
                    if rounded < -128 and not relu:
                        bins["negative_saturation"] = bins.get("negative_saturation", 0) + 1
                    if shift and abs(value) % (1 << shift) == 1 << (shift - 1):
                        key = "negative_tie" if value < 0 else "positive_tie"
                        bins[key] = bins.get(key, 0) + 1
    elif kind == "normalizer":
        for bf16 in (0, 1):
            for means, reciprocal in (([32640]*3, [512]*3), ([32641]*3, [513]*3),
                                      ([-8388608, 8388607, -1], [16777215, 1, 65536]),
                                      ([0, 65536, -65536], [1, 65535, 16777215])):
                for p in (0, 1, 2, 63, 127, 128, 129, 253, 254, 255):
                    for keep in (0, 1, 5, 7):
                        pixel = [p, 255-p, (p*19) & 255]
                        inputs.append(pack(pixel, 8) | pack(means, 24) << 68 |
                                      pack(reciprocal, 24) << 140 | bf16 << 212 | keep << 213)
                        index = len(inputs) - 1
                        data = normalize(pixel, keep, means, reciprocal, bf16)
                        expected.append(data | keep << 48 | int(index % 64 == 0) << 51 |
                                        int(index % 64 == 63) << 52 | index << 54 |
                                        (index & 8191) << 86 | (index >> 13) << 99 |
                                        (index & 255) << 112)
                        bins[f"{'bf16' if bf16 else 'q8_8'}-mask{keep}"] = bins.get(
                            f"{'bf16' if bf16 else 'q8_8'}-mask{keep}", 0) + 1
        expected[-1] |= 1 << 53
    else:
        for alpha in (0, 128, 256):
            for threshold in (0, 1, 16, 255, 256):
                for difference in sorted({0, max(0, threshold - 1), min(255, threshold),
                                          min(255, threshold + 1)}):
                    for use in (0, 1, 2):
                        history = [0, 30, 70]
                        pixel = [difference, min(255, 30+difference), min(255, 70+difference)]
                        motion = max(abs(a-b) for a, b in zip(history, pixel))
                        history_valid, enabled = use != 0, use != 1
                        blended = enabled and history_valid and motion < threshold
                        result = [(a*(256-alpha)+b*alpha+128)//256 if blended else a
                                  for a, b in zip(pixel, history)]
                        inputs.append(pack(pixel, 8) | pack(history, 8) << 24 |
                                      int(history_valid) << 48 | int(enabled) << 49 |
                                      alpha << 50 | threshold << 59)
                        expected.append(pack(result, 8))
                        relation = "below" if motion < threshold else (
                            "equal" if motion == threshold else "above")
                        key = f"alpha{alpha}-{relation}-use{use}"
                        bins[key] = bins.get(key, 0) + 1
    for name, values in (("input", inputs), ("expected", expected)):
        (output / f"primitive-{name}.hex").write_text("".join(f"{v:x}\n" for v in values))
    selection = ("quantize", "normalizer", "temporal").index(kind)
    rtl_root = root if rtl_root is None else rtl_root
    rtl = [str(p.relative_to(root)) for p in sorted(rtl_root.glob("rtl/*.sv"))]
    relative = str(output.relative_to(root))
    if simulator == "icarus":
        binary = f"{relative}/simulation.vvp"
        run(["iverilog", "-g2012", "-Wall", "-s", "primitive_tb",
             f"-Pprimitive_tb.KIND={selection}", f"-Pprimitive_tb.N={len(inputs)}",
             "-o", binary, "tests/primitive_tb.sv", *rtl], "primitive-compile")
        executable = ["vvp", binary]
    else:
        obj = output / "compile-obj"
        run(["verilator", "--binary", "--timing", "-Wno-fatal", "--top-module", "primitive_tb",
             f"-GKIND={selection}", f"-GN={len(inputs)}", "--Mdir",
             str(obj.relative_to(root)), "-o", "simulation", "-j", "2",
             "tests/primitive_tb.sv", *rtl], "primitive-compile")
        prune_compile(obj)
        executable = [str((obj / "simulation").relative_to(root))]
    for stalled in ((0,) if mutation or kind == "quantize" else (0, 1)):
        argv = [*executable, f"+ROOT={relative}", f"+STALL={stalled}"]
        if mutation:
            text = run(argv, "mutation-simulation", expected_exit=None)
            command = run.__self__.record["commands"][-1]
            assert command["exit_code"] != 0 and not command["timed_out"], "Undetected primitive mutation"
            assert "PRIMITIVE numerical mismatch" in text, f"Unrelated failure: {text[-1000:]}"
        else:
            text = run(argv, f"primitive-simulation-{stalled}",
                       timeout=600 if simulator == "icarus" else 3600)
            assert f"records={len(inputs)}" in text
    assert bins and all(value > 0 for value in bins.values())
    return {"kind": kind, "compared_per_replay": len(inputs), "observed_compared_bins": bins}
