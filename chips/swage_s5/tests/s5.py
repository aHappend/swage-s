"""Compiled S5 matrix, protocol replay, and unchanged-oracle mutation checks."""

import copy
import json
from pathlib import Path
import re
import shutil

from oracle import candidates, fixture, pack, scene
from leaf import leaf_check
from primitives import primitive_check
from receipts import fresh, prune_compile
from run_focused import vectors


MATRIX = ((1, 1, 1, 1, 8), (1, 9, 3, 3, 14), (9, 1, 8, 16, 16),
          (13, 17, 3, 3, 8), (64, 48, 8, 16, 14), (256, 256, 8, 16, 16))


def compile_top(root, output, run, geometry, rtl_root=None, simulator="verilator", depth=3):
    width, height, classes, topk, patch = geometry
    rtl_root = root if rtl_root is None else rtl_root
    rtl = [str(p.relative_to(root)) for p in sorted((rtl_root / "rtl").glob("*.sv"))]
    parameters = {"W": width, "H": height, "NCLASS": classes, "K": topk,
                  "PS": patch, "FD": depth}
    relative = str(output.relative_to(root))
    if simulator == "icarus":
        binary = f"{relative}/simulation.vvp"
        run(["iverilog", "-g2012", "-Wall", "-s", "focused_tb",
             *[f"-Pfocused_tb.{key}={value}" for key, value in parameters.items()],
             "-o", binary, "tests/focused_tb.sv", *rtl], "compile")
        return ["vvp", binary]
    obj = output / "compile-obj"
    run(["verilator", "--binary", "--timing", "-Wno-fatal", "--top-module", "focused_tb",
         *[f"-G{key}={value}" for key, value in parameters.items()], "--Mdir",
         str(obj.relative_to(root)), "-o", "simulation", "-j", "2",
         "tests/focused_tb.sv", *rtl], "compile")
    prune_compile(obj)
    return [str((obj / "simulation").relative_to(root))]


def compare(root, output, run, executable, geometry, seed, threshold=500, bf16=False,
            layers=None, pixels=None, masks=None, frames=1, reset_mid=False, controls=False,
            simulator="verilator", means=None, reciprocals=None, roi_enable=True,
            inherited_tokens=False):
    width, height, classes, topk, patch = geometry
    case = vectors(output, width, height, seed, threshold, topk, patch, classes, bf16,
                   layers, pixels, masks, means, reciprocals, roi_enable, inherited_tokens)
    records = []
    for stalled in (0, 1):
        normalization = ([] if means is None else [f"+MEAN={pack(means, 24):x}"])
        normalization += ([] if reciprocals is None else
                          [f"+RECIPROCAL={pack(reciprocals, 24):x}"])
        text = run([*executable, f"+ROOT={output.relative_to(root)}",
                    f"+WC={case['weight_count']}", f"+DC={case['descriptor_count']}",
                    f"+RC={case['roi_count']}", f"+STALL={stalled}", f"+SEED={seed}",
                    f"+BC={case['base_token_count']}", f"+S4={4 if inherited_tokens else 0}",
                    f"+S5={3 if roi_enable else 1}",
                    f"+THRESHOLD={threshold}", f"+BF16={int(bf16)}", f"+FRAMES={frames}",
                    f"+RESET_MID={int(reset_mid)}", f"+CONTROL={int(controls)}", *normalization],
                   f"simulation-{stalled}", timeout=600 if simulator == "icarus" else 3600)
        lines = [line for line in text.splitlines() if line.startswith("COMPARED ")]
        assert len(lines) == frames, f"Missing complete comparison ledger: {text[-1000:]}"
        for line in lines:
            record = {key: int(value) for key, value in re.findall(r"(\w+)=(\d+)", line)}
            assert record["pixels"] == width * height
            assert record["descriptors"] == case["descriptor_count"]
            assert record["roi"] == case["roi_count"]
            assert record["base_tokens"] == case["base_token_count"]
            assert record["stalled"] == stalled
            if reset_mid:
                assert record["abort_beats"] > 0 and record["blocked_cfg"] > 0
                assert record["blocked_weight"] > 0
            if controls:
                assert record["errors"] >= 14
            if geometry == (256, 256, 8, 16, 16) and not stalled:
                assert record["delivery_cycles"] <= 3200000
            records.append(record)
    result = {"geometry": geometry, "seed": seed, "threshold": threshold, "bf16": bf16,
              "expected": case, "compared_replays": records}
    (output / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def matrix(root, output, run):
    results = []
    for geometry in MATRIX:
        build = fresh(output, f"build-{geometry[0]}x{geometry[1]}")
        executable = compile_top(root, build, run, geometry)
        for seed in (11, 29, 47):
            case = fresh(output, f"case-{geometry[0]}x{geometry[1]}-seed{seed}")
            results.append(compare(root, case, run, executable, geometry, seed,
                                   bf16=seed == 29, frames=2 if geometry[0] < 64 else 1))
            print(f"PROGRESS compared S5 {geometry[0]}x{geometry[1]} seed={seed}", flush=True)
    assert len(results) == 18
    for seed in (11, 29, 47):
        rows = [r for result in results if result["seed"] == seed
                for r in result["compared_replays"] if r["stalled"]]
        for field in ("pixel_stalls", "descriptor_stalls", "roi_stalls", "bubbles"):
            assert sum(row[field] for row in rows) > 0, f"Unobserved {field} seed={seed}"
    return results


def directed(root, output, run):
    geometry = (13, 17, 3, 16, 8)
    build = fresh(output, "directed-build")
    executable = compile_top(root, build, run, geometry, depth=1)
    results = []
    for seed in (11, 29, 47):
        rgb, masks = scene(13, 17, seed)
        layers = fixture(3)
        records = candidates(rgb, masks, layers)
        scores = sorted({-record[0][0] for record in records})
        threshold = scores[len(scores) // 2]
        assert min(scores) < threshold < max(scores)
        case = fresh(output, f"threshold-seed{seed}")
        results.append(compare(root, case, run, executable, geometry, seed, threshold,
                               frames=2, reset_mid=True, controls=True))
        case = fresh(output, f"empty-seed{seed}")
        results.append(compare(root, case, run, executable, geometry, seed, 65535,
                               frames=2))
        ties = copy.deepcopy(layers)
        for c in range(3):
            ties[6].pw[c * 6:(c + 1) * 6] = ties[6].pw[:6]
            ties[6].bias[c] = ties[6].bias[0]
        case = fresh(output, f"class-tie-seed{seed}")
        results.append(compare(root, case, run, executable, geometry, seed, 320, layers=ties))
        objects = copy.deepcopy(layers)
        objects[3].shift = [3] * 6
        case = fresh(output, f"multiple-patches-seed{seed}")
        result = compare(root, case, run, executable, geometry, seed, 350, bf16=True,
                         layers=objects, means=[32641] * 3, reciprocals=[513] * 3)
        patches = {(record["x"] // 8, record["y"] // 8)
                   for record in result["expected"]["retained"]}
        assert (0, 0) in patches and any(x > 0 and y > 0 for x, y in patches)
        assert result["expected"]["eligible_count"] > 16
        results.append(result)
        case = fresh(output, f"detection-only-seed{seed}")
        results.append(compare(root, case, run, executable, geometry, seed, 350,
                               layers=objects, roi_enable=False, frames=2))
    singleton = (1, 1, 8, 16, 16)
    build = fresh(output, "singleton-build")
    executable = compile_top(root, build, run, singleton, depth=16)
    for seed in (11, 29, 47):
        case = fresh(output, f"levels-seed{seed}")
        result = compare(root, case, run, executable, singleton, seed, 0, frames=2)
        assert {r["sort_key"][2] for r in result["expected"]["retained"]} == {0, 1, 2}
        results.append(result)
        case = fresh(output, f"separate-tokens-seed{seed}")
        results.append(compare(root, case, run, executable, singleton, seed, 0,
                               inherited_tokens=True, frames=2, pixels=[[[201, 155, 227]]],
                               masks=[[5]]))
    return results


MUTATIONS = (
    ("spatial-row", "swage_s5_window.sv", "y0[0] ? odd_q : even_q",
     "y0[0] ? even_q : odd_q", "DESCRIPTOR mismatch"),
    ("pointwise-order", "swage_s5_pointwise.sv", "s_data[i*8 +:8]",
     "s_data[(CI-1-i)*8 +:8]", "DESCRIPTOR mismatch"),
    ("c2f-bypass", "swage_s5_c2f.sv", "{b1_data,saved_b1,original_data}",
     "{original_data[47:24],original_data[47:24],original_data}", "DESCRIPTOR mismatch"),
    ("negative-tie", "swage_s5_quantize.sv", "quotient[0]", "!quotient[0]",
     "DESCRIPTOR mismatch"),
    ("topk-reversed", "swage_s5_topk.sv", "a_data[87:72] > b_data[87:72]",
     "a_data[87:72] < b_data[87:72]", "DESCRIPTOR mismatch"),
    ("threshold-exclusive", "swage_s5_head.sv", "confidence6 >= threshold",
     "confidence6 > threshold", "DESCRIPTOR mismatch"),
    ("roi-association", "swage_s5_roi_tokens.sv", "slot <= cmd_slot;",
     "slot <= cmd_slot == K-1 ? 0 : cmd_slot+1'b1;", "ROI mismatch"),
    ("bf16-truncated", "swage_s4_normalizer.sv", "bf_increment4 <= bf_increment;",
     "bf_increment4 <= 0;", "ROI mismatch"),
    ("temporal-equality", "swage_s4_temporal.sv", "motion < threshold1",
     "motion <= threshold1", "PRIMITIVE numerical mismatch"),
)


def mutations(root, output, run, selected=None):
    geometry = (13, 17, 3, 16, 8)
    results = []
    rgb, masks = scene(13, 17, 11)
    layers = fixture(3)
    scores = sorted({-r[0][0] for r in candidates(rgb, masks, layers)})
    threshold = scores[len(scores) // 2]
    for name, filename, before, after, assertion in MUTATIONS:
        if selected is not None and name not in selected:
            continue
        directory = fresh(output, name)
        copied = directory / "mutant-rtl"
        (copied / "rtl").mkdir(parents=True)
        for source in sorted(root.glob("rtl/*.sv")):
            shutil.copyfile(source, copied / "rtl" / source.name)
        source = copied / "rtl" / filename
        original = source.read_text()
        assert original.count(before) == 1, f"Mutation site must be unique: {name}"
        source.write_text(original.replace(before, after))
        if name == "temporal-equality":
            primitive_check(root, directory, run, "temporal", "verilator", copied, True)
        elif name in ("spatial-row", "pointwise-order", "c2f-bypass", "negative-tie"):
            kind = "window" if name == "spatial-row" else (
                "dsconv" if name == "negative-tie" else "c2f")
            leaf_check(root, directory, run, kind, 13, 17, 11, "verilator", copied, True)
            assertion = "LEAF numerical mismatch"
        else:
            actual_threshold = max(scores) if name == "threshold-exclusive" else threshold
            actual_layers = None
            if name == "roi-association":
                actual_layers = copy.deepcopy(layers)
                actual_layers[3].shift = [3] * 6
                actual_threshold = 350
            means, reciprocals = [32641] * 3, [513] * 3
            executable = compile_top(root, directory, run, geometry, rtl_root=copied)
            case = vectors(directory, 13, 17, 11, actual_threshold, 16, 8, 3, True,
                           layers=actual_layers, means=means, reciprocals=reciprocals)
            text = run([*executable, f"+ROOT={directory.relative_to(root)}",
                        f"+WC={case['weight_count']}", f"+DC={case['descriptor_count']}",
                        f"+RC={case['roi_count']}", "+STALL=0", f"+THRESHOLD={actual_threshold}",
                        "+BF16=1", f"+MEAN={pack(means, 24):x}",
                        f"+RECIPROCAL={pack(reciprocals, 24):x}"],
                       "mutation-simulation", expected_exit=None)
            command = run.__self__.record["commands"][-1]
            assert not command["timed_out"] and command["exit_code"] != 0, f"Undetected mutation: {name}"
            assert assertion in text, f"Mutation {name} failed without intended mismatch: {text[-1500:]}"
        command = run.__self__.record["commands"][-1]
        result = {"mutation": name, "source": filename, "before": before, "after": after,
                  "assertion": assertion, "stdout": command["stdout"],
                  "exit_code": command["exit_code"], "expected_vectors_unchanged": True}
        (directory / "mutation.json").write_text(json.dumps(result, indent=2) + "\n")
        results.append(result)
    return results
