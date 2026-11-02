"""Inherited numerical matrix and separate unchanged-source compatibility replay."""

import hashlib
import json
import re

from oracle import pack
from parent_oracle import (clahe, csc, demosaic, resize, resize420, selftest,
                           spatial, temporal, token_records)
from receipts import fresh, prune_compile


def config(case):
    w, h = case.get("W", 5), case.get("H", 3)
    defaults = {"enable": 0, "border": 0, "abs": 0, "shift": 0,
                "coeff": [0, 0, 0, 0, 1, 0, 0, 0, 0], "roi_x": 0, "roi_y": 0,
                "roi_w": w, "roi_h": h, "format": 0, "csc_mode": 4, "out420": 0,
                "csc_coeff": [[8192, 0, 0], [0, 8192, 0], [0, 0, 8192]],
                "in_bias": [0]*3, "out_bias": [0]*3, "hist_channel": 0,
                "s4": 0, "alpha": 128, "motion": 16, "clip": 4, "patch": 8,
                "bf16": 0, "means": [32640]*3, "reciprocals": [512]*3, "mode": 0}
    defaults.update(case.get("config", {}))
    return defaults


def parameters(case):
    defaults = {"TOP": 5, "W": 5, "H": 3, "DW": 7, "DH": 4, "PB": 8,
                "CH": 3, "F": 8, "INTERP": 0, "TILE": 8}
    defaults.update({key: value for key, value in case.items() if key in defaults})
    return defaults


def write_vectors(output, case, seed):
    p, cfg = parameters(case), config(case)
    inputs, outputs, tokens, histograms, sizes = [], [], [], [], []
    history = None
    for frame in range(case.get("frames", 1)):
        image, masks = [], []
        for y in range(p["H"]):
            image.append([])
            masks.append([])
            for x in range(p["W"]):
                if case.get("flat"):
                    pixel = [32+(x & 1)*128, 77, 200]
                else:
                    pixel = [(x*23+y*31+seed+c*43) % 210 for c in range(p["CH"])]
                if p["PB"] == 10:
                    pixel = [(pixel[0]*5) % 1024]
                elif case.get("frames", 1) > 1:
                    pixel = [min(255, v+(0, 16, 31)[frame % 3]) for v in pixel]
                keep = 7 if p["CH"] == 3 else 1
                if p["TOP"] >= 3 and p["CH"] == 3 and not cfg["s4"] and (x+y+seed) % 7 == 0:
                    keep = 5
                image[-1].append(pixel)
                masks[-1].append(keep)
                data = pack(pixel, 8) if p["CH"] == 3 else pixel[0]
                inputs.append(data | keep << 24)
        if p["TOP"] >= 3 and cfg["enable"] & 1:
            x, y = cfg["roi_x"], cfg["roi_y"]
            image = [row[x:x+cfg["roi_w"]] for row in image[y:y+cfg["roi_h"]]]
            masks = [row[x:x+cfg["roi_w"]] for row in masks[y:y+cfg["roi_h"]]]
        origin = (cfg["roi_x"], cfg["roi_y"]) if cfg["enable"] & 1 else (0, 0)
        if (p["TOP"] == 2 and cfg["mode"] & 2) or (p["TOP"] >= 3 and cfg["enable"] & 2):
            image = demosaic(image, cfg["border"], (0, 0) if p["TOP"] == 2 else origin)
            masks = [[7]*len(row) for row in image]
        if (p["TOP"] == 2 and cfg["mode"] & 1) or (p["TOP"] >= 3 and cfg["enable"] & 4):
            image = spatial(image, cfg["coeff"], cfg["shift"], cfg["abs"], cfg["border"])
            masks = [[7 if p["CH"] == 3 else 1]*len(row) for row in image]
        output_format = cfg["format"]
        if p["TOP"] >= 3 and cfg["enable"] & 8:
            image, masks = csc(image, cfg["csc_mode"], cfg["format"], cfg["out420"],
                               cfg["csc_coeff"], cfg["in_bias"], cfg["out_bias"])
            output_format = 0 if cfg["format"] else (2 if cfg["out420"] else 1)
        if p["TOP"] <= 2 or cfg["enable"] & 16:
            if output_format == 2 and p["TOP"] >= 3:
                image, masks = resize420(image, p["DW"], p["DH"], p["F"], p["INTERP"] == 1)
            else:
                image = resize(image, p["DW"], p["DH"], p["F"], p["INTERP"] == 1, p["PB"])
                masks = [[7 if p["CH"] == 3 else 1]*len(row) for row in image]
        if p["TOP"] >= 3 and cfg["enable"] & 32:
            counts = [0]*256
            for y, row in enumerate(image):
                for x, pixel in enumerate(row):
                    c = cfg["hist_channel"]
                    if p["CH"] == 1:
                        value = pixel[0] >> (2 if p["PB"] == 10 else 0)
                    elif c == 3:
                        value = (77*pixel[0]+150*pixel[1]+29*pixel[2]+128)//256
                    elif masks[y][x] & (1 << c):
                        value = pixel[c]
                    else:
                        continue
                    counts[value] += 1
            histograms.extend(pack(counts[address*8:address*8+8], 32) for address in range(32))
        if p["TOP"] >= 4 and cfg["s4"] & 1:
            image = temporal(image, history, cfg["alpha"], cfg["motion"])
            history = image
        if p["TOP"] >= 4 and cfg["s4"] & 2:
            image = clahe(image, p["TILE"], cfg["clip"])
        n_tokens = 0
        if p["TOP"] >= 4 and cfg["s4"] & 4:
            records = token_records(image, cfg["patch"], cfg["means"], cfg["reciprocals"], cfg["bf16"])
            n_tokens = len(records)
            tokens.extend(records)
        w, h = len(image[0]), len(image)
        for y, row in enumerate(image):
            for x, pixel in enumerate(row):
                data = pack(pixel, 8) if p["CH"] == 3 else pixel[0]
                outputs.append(data | masks[y][x] << 24 |
                               int(x == 0 and y == 0) << 27 | int(x == w-1) << 28)
        sizes.append(w*h | n_tokens << 16)
    fields = ((cfg["enable"], 6), (cfg["border"], 1), (cfg["abs"], 1), (cfg["shift"], 5),
              (pack(cfg["coeff"], 16), 144), (cfg["roi_x"], 13), (cfg["roi_y"], 13),
              (cfg["roi_w"], 13), (cfg["roi_h"], 13), (cfg["format"], 2),
              (cfg["csc_mode"], 3), (cfg["out420"], 1),
              (pack([v for row in cfg["csc_coeff"] for v in row], 16), 144),
              (pack(cfg["in_bias"], 16), 48), (pack(cfg["out_bias"], 16), 48),
              (cfg["hist_channel"], 2), (cfg["s4"], 3), (cfg["alpha"], 9), (cfg["motion"], 9),
              (cfg["clip"], 9), (cfg["patch"], 5), (cfg["bf16"], 1),
              (pack(cfg["means"], 24), 72), (pack(cfg["reciprocals"], 24), 72), (cfg["mode"], 2))
    configuration, offset = 0, 0
    for value, bits in fields:
        configuration |= value << offset
        offset += bits
    for name, values in (("input", inputs), ("pixel", outputs), ("token", tokens),
                         ("hist", histograms), ("sizes", sizes), ("config", [configuration])):
        (output / f"parent-{name}.hex").write_text("".join(f"{value:x}\n" for value in values))
    return {"pixels": len(outputs), "tokens": len(tokens), "hist_blocks": len(histograms),
            "frames": case.get("frames", 1),
            "padded_tokens": sum((value >> 117) == 0 for value in tokens),
            "input_sha256": hashlib.sha256((output / "parent-input.hex").read_bytes()).hexdigest()}


def compile_parent(root, output, run, case, reference=False, simulator="verilator"):
    p = parameters(case)
    if reference:
        assert p["TOP"] <= 4
        sources = sorted(root.glob("swage_s4_reference/rtl/*.sv"))
        assert len(sources) == 28, "Incomplete supplied parent reference inventory"
    else:
        sources = sorted(root.glob("rtl/*.sv"))
    rtl = [str(path.relative_to(root)) for path in sources]
    relative = str(output.relative_to(root))
    if simulator == "icarus":
        binary = f"{relative}/simulation.vvp"
        run(["iverilog", "-g2012", "-Wall", "-s", "parent_tb",
             *[f"-Pparent_tb.{key}={value}" for key, value in p.items()], "-o", binary,
             "tests/parent_tb.sv", *rtl], "parent-compile")
        return ["vvp", binary]
    obj = output / "compile-obj"
    run(["verilator", "--binary", "--timing", "-Wno-fatal", "--top-module", "parent_tb",
         *[f"-G{key}={value}" for key, value in p.items()], "--Mdir", str(obj.relative_to(root)),
         "-o", "simulation", "-j", "2", "tests/parent_tb.sv", *rtl], "parent-compile")
    prune_compile(obj)
    return [str((obj / "simulation").relative_to(root))]


def compare_parent(root, output, run, executable, case, seed, simulator="verilator"):
    metadata = write_vectors(output, case, seed)
    ledgers = []
    for stalled in (0, 1):
        text = run([*executable, f"+ROOT={output.relative_to(root)}", f"+FRAMES={metadata['frames']}",
                    f"+NP={metadata['pixels']}", f"+NT={metadata['tokens']}",
                    f"+STALL={stalled}", f"+SEED={seed}"], f"parent-simulation-{stalled}",
                   timeout=600 if simulator == "icarus" else 3600)
        lines = [line for line in text.splitlines() if line.startswith("COMPARED parent=")]
        assert len(lines) == 1
        ledger = {key: int(value) for key, value in re.findall(r"(\w+)=(\d+)", lines[0])}
        assert ledger["pixels"] == metadata["pixels"] and ledger["tokens"] == metadata["tokens"]
        assert ledger["hist"] == metadata["hist_blocks"]
        ledgers.append(ledger)
    result = {"case": case, "seed": seed, "expected": metadata, "observed": ledgers}
    (output / "parent-comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def scenarios():
    cases = []
    for top in (1, 3, 5):
        for pb, ch, fraction, interpolation in ((8, 1, 8, 0), (10, 1, 16, 1),
                                               (8, 3, 16, 0), (8, 3, 8, 1)):
            cases.append({"name": f"s{top}-scale-{pb}bit-{ch}ch-f{fraction}-i{interpolation}",
                          "TOP": top, "PB": pb, "CH": ch, "F": fraction,
                          "INTERP": interpolation, "config": {"enable": 16}})
    for border, absolute, shift in ((0, 0, 0), (1, 1, 2), (0, 1, 31)):
        cases.append({"name": f"s2-convolution-{border}-{absolute}-{shift}", "TOP": 2, "CH": 1,
                      "config": {"mode": 1, "border": border, "abs": absolute, "shift": shift,
                                 "coeff": [-3, 1, 2, 0, -2, 4, -1, 3, -4]}})
    cases.append({"name": "s2-demosaic-convolution", "TOP": 2,
                  "config": {"mode": 3, "border": 1, "shift": 2,
                             "coeff": [-3, 1, 2, 0, 2, 4, -1, 3, -4]}})
    cases.append({"name": "s2-monochrome10", "TOP": 2, "PB": 10, "CH": 1, "F": 16})
    for top in (3, 5):
        cases.append({"name": f"s{top}-crop-demosaic-conv-hist", "TOP": top,
                      "config": {"enable": 1 | 2 | 4 | 32, "roi_x": 2, "roi_y": 1,
                                 "roi_w": 3, "roi_h": 2, "border": 0, "abs": 1,
                                 "shift": 2, "coeff": [-1, 1, -2, 0, 3, 2, -1, 2, 1],
                                 "hist_channel": 3}})
        for mode in range(5):
            fmt = (1 if mode == 2 else 2) if mode in (2, 3) else 0
            cases.append({"name": f"s{top}-csc{mode}", "TOP": top,
                          "config": {"enable": 8 | 16 | 32, "format": fmt,
                                     "csc_mode": mode, "out420": mode == 1, "hist_channel": mode % 3,
                                     "csc_coeff": [[8192, -101, 77], [-4000, 9000, 33], [2, 10, 16000]],
                                     "in_bias": [-123, 0, 42], "out_bias": [-11, 31, 260]}})
        for channel in range(4):
            cases.append({"name": f"s{top}-histogram{channel}", "TOP": top,
                          "config": {"enable": 32, "hist_channel": channel}})
    for top in (4, 5):
        for alpha in (0, 128, 256):
            cases.append({"name": f"s{top}-temporal{alpha}", "TOP": top, "W": 9, "H": 17,
                          "frames": 3, "config": {"s4": 1, "alpha": alpha, "motion": 16}})
        for tile in (8, 16):
            cases.append({"name": f"s{top}-clahe{tile}", "TOP": top, "TILE": tile,
                          "W": tile+1, "H": tile+1, "flat": True,
                          "config": {"s4": 2, "clip": 1}})
        for patch in (8, 14, 16):
            for bf16 in (0, 1):
                cases.append({"name": f"s{top}-patch{patch}-bf16{bf16}", "TOP": top,
                              "W": 9, "H": 17, "config": {"s4": 4, "patch": patch, "bf16": bf16,
                                                         "means": [-8388608, 32641, 8388607],
                                                         "reciprocals": [16777215, 513, 1]}})
        cases.append({"name": f"s{top}-enhanced", "TOP": top, "W": 9, "H": 17,
                      "TILE": 8, "flat": True, "frames": 3,
                      "config": {"s4": 7, "patch": 14, "bf16": 1,
                                 "means": [32641]*3, "reciprocals": [513]*3}})
    return cases


def parent_matrix(root, output, run):
    selftest()
    builds, results = {}, []
    for case in scenarios():
        for reference in (False, True):
            compiled_case = dict(case)
            if reference and parameters(case)["TOP"] == 5:
                compiled_case["TOP"] = 4
            key = (reference, tuple(parameters(compiled_case).items()))
            if key not in builds:
                build = fresh(output, "parent-reference-build" if reference else "parent-build")
                builds[key] = compile_parent(root, build, run, compiled_case, reference)
            for seed in (11, 29, 47):
                directory = fresh(output, f"{case['name']}-{'ref' if reference else 'dut'}-seed{seed}")
                result = compare_parent(root, directory, run, builds[key], case, seed)
                result["supplementary_unchanged_reference"] = reference
                results.append(result)
        print(f"PROGRESS compared inherited scenario={case['name']}", flush=True)
    return results
