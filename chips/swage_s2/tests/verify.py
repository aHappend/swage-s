#!/usr/bin/env python3
"""Portable SWAGE-S2 L1; failures are retained and never converted to skips.

Requirement map: independent equations in reference.py; public-pin multi-frame
scoreboards in bench.py; fixed leaf stages in leaves.py; synthesis, illegal
parameters, mutations and clean-copy reproduction below. Native timing/physical
gates and the final product report belong to the shared runtime.
"""

import argparse
import collections
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from execution import ROOT, Ledger, dump, hashes, relative
from reference import (MATRIX, PATTERNS, PRESETS, convolve, demosaic, frame,
                       image_for, pack, scale, scenarios)
from reference import taps as reference_taps
from bench import PARAMETERS, architectural


def rtl_files(root=ROOT):
    return sorted(root.glob("rtl/*.sv"))


def parent_sources(ledger):
    directory = ledger.path / "parent-reference"
    directory.mkdir()
    files = []
    for path in sorted(ROOT.glob("swage_s1_reference/rtl/*.sv")):
        destination = directory / path.name
        text = re.sub(r"\bswage_s1_", "parent_swage_s1_", path.read_text())
        destination.write_text(text)
        files.append(destination)
    if len(files) != 5:
        raise AssertionError("Five unchanged parent reference modules are required")
    return files


def build(ledger, geometry, name, parents, simulator="verilator", source_root=ROOT):
    directory = ledger.path / name
    directory.mkdir()
    tb = directory / "tb.sv"
    tb.write_text(architectural(geometry))
    sources = [relative(p) for p in rtl_files(source_root)] + [relative(p) for p in parents]
    if simulator == "verilator":
        obj = directory / "compile-obj"
        binary = directory / "simulator"
        argv = ["verilator", "--binary", "--timing", "-Wno-fatal",
                "--top-module", "tb", "-j", "2", "--Mdir", relative(obj),
                "-o", "../simulator", relative(tb), *sources]
        ledger.run(argv, f"{name}-compile")
        # Executable is retained, settled object files are not needed.
        shutil.rmtree(obj)
    else:
        binary = directory / "simulator.vvp"
        ledger.run(["iverilog", "-g2012", "-Wall", "-s", "tb", "-o",
                    relative(binary), relative(tb), *sources], f"{name}-compile")
    return directory, binary


def stimulus(path, frames, seed):
    with path.open("w") as out:
        out.write(f"{len(frames)} {seed}\n")
        for item in frames:
            coeff = sum((c & 65535) << (16 * i) for i, c in enumerate(item["coeff"]))
            out.write(f"{item['mode']} {item['border']} {item['absolute']} "
                      f"{item['shift']} {coeff:036x}\n")
            for word in item["inputs"] + item["expected"]:
                out.write(f"{word:x}\n")


COUNTERS = ("source_bubbles", "output_stalls", "max_stall", "stalled_sof",
            "stalled_eol", "legal_configs", "busy_config_cycles", "illegal_configs",
            "reset_fill", "reset_output", "reset_stall", "source_transfers", "output_transfers")


def compare_results(path, frames, geometry):
    width = geometry[2]
    completed, outputs, parent = {}, collections.defaultdict(list), collections.defaultdict(list)
    counters = None
    for line in path.read_text().splitlines():
        fields = line.split()
        if not fields:
            raise AssertionError("Empty result record")
        kind = fields[0]
        if kind in ("O", "P") and len(fields) == 4:
            fid, index, value = int(fields[1]), int(fields[2]), int(fields[3], 16)
            if fid >= len(frames) or fid < 0:
                raise AssertionError("Invalid frame identifier")
            target = outputs if kind == "O" else parent
            if index != len(target[fid]):
                raise AssertionError("Duplicated, lost or reordered output")
            expected = frames[fid]["expected"]
            if index >= len(expected):
                raise AssertionError("Extra output")
            wanted = (expected[index] << 2) | ((index == 0) << 1) | (index % width == width - 1)
            if value != wanted:
                raise AssertionError(f"Independent {kind} mismatch frame={fid} pixel={index}: "
                                     f"{value:x} != {wanted:x}")
            target[fid].append(value)
        elif kind == "F" and len(fields) == 6:
            fid, ins, outs, pars, cycles = map(int, fields[1:])
            if fid in completed:
                raise AssertionError("Duplicate completion")
            completed[fid] = dict(inputs=ins, outputs=outs, parent=pars, cycles=cycles)
        elif kind == "C" and len(fields) == len(COUNTERS) + 1 and counters is None:
            counters = dict(zip(COUNTERS, map(int, fields[1:])))
        else:
            raise AssertionError(f"Malformed result: {line[:160]}")
    if counters is None or set(completed) != set(range(len(frames))):
        raise AssertionError("Missing complete frames or measured coverage")
    for fid, item in enumerate(frames):
        result = completed[fid]
        n, m = geometry[0] * geometry[1], geometry[2] * geometry[3]
        if result["inputs"] != n or result["outputs"] != m or len(outputs[fid]) != m:
            raise AssertionError("Incomplete comparison denominator")
        if item["mode"] == 0:
            if len(parent[fid]) != m or outputs[fid] != parent[fid] or result["parent"] != m:
                raise AssertionError("Missing parent differential comparison")
        elif parent[fid] or result["parent"]:
            raise AssertionError("Disabled parent consumed outputs")
    return dict(frames=completed, counters=counters, compared=sum(map(len, outputs.values())),
                parent_compared=sum(map(len, parent.values())))


def execute_frames(ledger, build_result, geometry, frames, seed, name,
                   simulator="verilator", mutation=False):
    directory, binary = build_result
    inp, result = directory / f"{name}.stimulus.txt", directory / f"{name}.results.txt"
    stimulus(inp, frames, seed)
    dump(directory / f"{name}.scenarios.json", [
        {k: v for k, v in item.items() if k not in ("source", "inputs", "expected")}
        for item in frames])
    argv = (["vvp", relative(binary)] if simulator == "icarus" else [relative(binary)])
    record = ledger.run([*argv, f"+stimulus={relative(inp)}", f"+result={relative(result)}"],
                        name, allowed_failure=mutation)
    if mutation:
        text = (ROOT / record["stdout"]).read_text() + (ROOT / record["stderr"]).read_text()
        if record["exit"] == 0:
            compare_results(result, frames, geometry)
            raise AssertionError(f"Mutation survived: {name}")
        discriminators = ("PIXEL_MISMATCH", "STALL_STABILITY", "EARLY_FRAME_RELEASE",
                          "PARENT_MISMATCH", "EXTRA_OUTPUT", "FINAL_DRAIN")
        if not any(x in text for x in discriminators):
            raise AssertionError(f"Mutation did not fail a numerical/protocol comparison: {name}")
        return dict(killed=True, observed_failure=text[-3000:], command=record,
                    compared_before_failure=sum(1 for line in result.read_text().splitlines()
                                               if line.startswith(("O ", "P "))))
    checked = compare_results(result, frames, geometry)
    checked.update(geometry=geometry, seed=seed,
                   stimulus_sha256=hashlib.sha256(inp.read_bytes()).hexdigest())
    dump(directory / f"{name}.comparison.json", checked)
    return checked


def static_checks():
    results = {}
    for path in rtl_files():
        text = re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(), flags=re.S)
        if re.search(r"[/%%]", text):
            raise AssertionError(f"Forbidden division/modulo operator: {path.name}")
        for match in re.finditer(r"\balways\s*@\s*\([^)]*(?:posedge|negedge)[^)]*\)"
                                 r"\s*begin\b", text):
            depth, end = 1, None
            for token in re.finditer(r"\bbegin\b|\bend\b", text[match.end():]):
                depth += 1 if token.group() == "begin" else -1
                if depth == 0:
                    end = match.end() + token.start()
                    break
            if end is None:
                raise AssertionError(f"Unterminated sequential process: {path.name}")
            body = text[match.end():end]
            if re.search(r"(?<![<>=!])=(?!=)", body):
                raise AssertionError(f"Blocking assignment in sequential process: {path.name}")
        results[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(ROOT.glob("swage_s1_reference/rtl/*.sv")):
        if path.read_bytes() != (ROOT / "rtl" / path.name).read_bytes():
            raise AssertionError(f"Parent modified: {path.name}")
    return results


def synthesis(ledger, stem="synthesis", compact=False):
    reports = []
    formats = [(8, 1)] if compact else [(8, 1), (8, 3), (10, 1)]
    for bits, channels in formats:
        output = ledger.path / f"{stem}-{bits}-{channels}.json"
        script = ledger.path / f"{stem}-{bits}-{channels}.ys"
        commands = [
            "read_verilog -sv " + " ".join(relative(p) for p in rtl_files()),
            f"chparam -set PIXEL_BITS {bits} -set CHANNELS {channels} swage_s2_top",
            "hierarchy -check -top swage_s2_top",
            "proc", "opt -fast", "fsm", "opt -fast", "wreduce", "peepopt",
            "opt_clean", "share", "check -noinit -assert",
            "memory -nomap", "opt_clean", "opt -fast",
            "check -noinit -assert",
            "select -assert-none t:$dlatch t:$adlatch t:$dlatchsr t:$_DLATCH_*",
            "select -assert-none t:$div t:$mod t:$divfloor t:$modfloor",
            "stat", f"write_json {relative(output)}",
        ]
        script.write_text("\n".join(commands) + "\n")
        ledger.run(["yosys", "-Q", "-T", "-s", relative(script)], f"{stem}-{bits}-{channels}")
        netlist = json.loads(output.read_text())
        cells = [cell for mod in netlist["modules"].values() for cell in mod.get("cells", {}).values()]
        types = collections.Counter(c["type"] for c in cells)
        flops = sum(n for t, n in types.items() if "dff" in t.lower())
        memories = [c for c in cells if c["type"].startswith("$mem")]
        memory_bits = sum(int(c["parameters"]["WIDTH"], 2) * int(c["parameters"]["SIZE"], 2)
                          for c in memories if "SIZE" in c["parameters"])
        if flops < 20 or memory_bits < 128 or len(cells) < 100:
            raise AssertionError("Synthesis lost meaningful state/memory/datapath structure")
        for t in types:
            if "latch" in t.lower() or re.match(r"\$(div|mod)", t):
                raise AssertionError(f"Forbidden synthesized cell: {t}")
        window_memories = []
        for module in netlist["modules"].values():
            if "swage_s2_window" not in module.get("attributes", {}).get("hdlname", ""):
                continue
            banks = {name: cell for name, cell in module["cells"].items()
                     if cell["type"].startswith("$mem")}
            if set(banks) != {"bank0", "bank1", "bank2"}:
                raise AssertionError("Window must retain exactly three circular row memories")
            for name, bank in banks.items():
                params = bank["parameters"]
                actual = {k: int(params[k], 2) for k in
                          ("WIDTH", "SIZE", "RD_PORTS", "RD_CLK_ENABLE", "WR_PORTS")}
                if actual["SIZE"] != 8 or actual["RD_PORTS"] != 1 or (
                        actual["RD_CLK_ENABLE"] != 1 or actual["WR_PORTS"] != 1):
                    raise AssertionError(f"Window RAM is not one synchronous read per row: {actual}")
                window_memories.append(dict(name=name, **actual))
        if not window_memories:
            raise AssertionError("No synthesized circular window structure")
        top = netlist["modules"]["swage_s2_top"]["cells"]
        for name, module in (("u_scaler", "swage_s1_top"),
                             ("u_demosaic", "swage_s2_demosaic"),
                             ("u_convolution", "swage_s2_convolution")):
            if name not in top:
                # Unreachable spatial paths may be optimized out in mono/ten-bit.
                if name == "u_demosaic" and channels != 3:
                    continue
                if name == "u_convolution" and bits == 10:
                    continue
                raise AssertionError(f"Missing hierarchy: {name}")
            mod = netlist["modules"][top[name]["type"]]
            hdlname = mod.get("attributes", {}).get("hdlname", "").lstrip("\\").split()[0]
            if hdlname != module:
                raise AssertionError(f"Wrong hierarchy type: {name} {hdlname}")
            children = mod.get("cells", {})
            required = ("u_phase", "u_scheduler", "u_line_buffer", "u_interp") if (
                name == "u_scaler") else ("u_window",)
            if not all(child in children for child in required):
                raise AssertionError(f"Missing leaf hierarchy under {name}")
        reports.append(dict(bits=bits, channels=channels, cells=len(cells), flops=flops,
                            memory_bits=memory_bits, types=types, windows=window_memories,
                            json=relative(output)))
    dump(ledger.path / f"{stem}-summary.json", reports)
    return reports


def illegal_parameters(ledger):
    cases = [{name: value} for name in PARAMETERS[:4] for value in (0, 4097)]
    cases += [dict(PIXEL_BITS=12), dict(PIXEL_BITS=10, CHANNELS=3),
              dict(CHANNELS=2), dict(PHASE_FRAC=0), dict(PHASE_FRAC=9),
              dict(INTERPOLATION=-1), dict(INTERPOLATION=2)]
    reports = []
    directory = ledger.path / "illegal-parameters"
    directory.mkdir()
    for i, overrides in enumerate(cases):
        binary = directory / f"case{i}.vvp"
        argv = ["iverilog", "-g2012", "-Wall", "-s", "swage_s2_top", "-o", relative(binary)]
        argv += [f"-Pswage_s2_top.{k}={v}" for k, v in overrides.items()]
        ledger.run([*argv, *[relative(p) for p in rtl_files()]], f"illegal-{i}-compile")
        record = ledger.run(["vvp", relative(binary)], f"illegal-{i}-simulate",
                            allowed_failure=True)
        stdout = (ROOT / record["stdout"]).read_text()
        if record["exit"] == 0 or "SWAGE_S2 PARAMETER ERROR" not in stdout:
            raise AssertionError(f"No explicit illegal-parameter rejection: {overrides}")
        reports.append(dict(parameters=overrides, record=record))
    dump(directory / "results.json", reports)
    return reports


def mutations(ledger, parents, selected=None):
    specifications = [
        ("coefficient-signedness", "swage_s2_convolution.sv",
         "coefficient * sample_value", "$unsigned(coefficient) * $unsigned(sample_value)"),
        ("rounding-bias", "swage_s2_convolution.sv",
         "magnitude_s5 + rounding_bias", "magnitude_s5"),
        ("saturation", "swage_s2_convolution.sv",
         "result_s8 <= 8'd255;", "result_s8 <= shifted_s7[7:0];"),
        ("border-selection", "swage_s2_window.sv",
         "request_horizontal_zero <= border &&", "request_horizontal_zero <= !border &&"),
        ("bayer-channel", "swage_s2_demosaic.sv",
         "{blue_s0, rounded_green[8:1], red_s0}", "{red_s0, rounded_green[8:1], blue_s0}"),
        ("row-bank-rotation", "swage_s2_window.sv",
         "bank2[source_x] <= s_axis_tdata;",
         "bank0[source_x] <= s_axis_tdata;"),
        ("stalled-advance", "swage_s2_convolution.sv",
         "            end\n        end\n    end endgenerate",
         "            end else begin\n"
         "                result_s8 <= result_s8 + 8'd1;\n"
         "            end\n        end\n    end endgenerate"),
    ]
    geometry = (8, 6, 8, 6, 8, 3, 8, 0)
    frames = [frame(geometry, 1, "rgb", 17), *scenarios(geometry, 17, extended=True)]
    reports = []
    for name, filename, old, new in specifications:
        if selected is not None and name not in selected:
            continue
        directory = ledger.path / f"mutation-{name}"
        directory.mkdir()
        source_root = directory / "copy"
        shutil.copytree(ROOT / "rtl", source_root / "rtl")
        target = source_root / "rtl" / filename
        before = target.read_text()
        if before.count(old) != 1:
            raise AssertionError(f"Mutation anchor not unique: {name}")
        after = before.replace(old, new)
        target.write_text(after)
        (directory / "mutation.diff").write_text("".join(difflib.unified_diff(
            before.splitlines(True), after.splitlines(True),
            fromfile=f"rtl/{filename}", tofile=f"mutated/rtl/{filename}")))
        dump(directory / "source-hashes.json", {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in rtl_files(source_root)})
        binary = build(ledger, geometry, f"build-mutation-{name}", parents,
                       source_root=source_root)
        killed = execute_frames(ledger, binary, geometry, frames, 17, f"mutation-{name}",
                                mutation=True)
        killed.update(name=name, diff=relative(directory / "mutation.diff"))
        reports.append(killed)
        dump(directory / "result.json", killed)
    denominator = 7 if selected is None else len(selected)
    if len(reports) != denominator or not all(item["killed"] for item in reports):
        raise AssertionError("Mutation denominator not met")
    return reports


def coverage(results):
    totals = collections.Counter()
    modes, presets, borders, absolutes, phases, patterns, taps = (
        set() for _ in range(7))
    shifts, extrema, ties, boundaries, wraps = set(), set(), set(), set(), set()
    arithmetic = collections.Counter()
    tap_influence = collections.Counter()
    per_cell = []
    frames_completed = pixels = parent_pixels = 0
    for geometry, frames, result in results:
        # Only frames with complete pin-level and independent Python comparisons enter coverage.
        if len(result["frames"]) != len(frames):
            raise AssertionError("Coverage without completed comparisons")
        for key, value in result["counters"].items():
            if key == "max_stall":
                totals[key] = max(totals[key], value)
            else:
                totals[key] += value
        pixels += result["compared"]
        parent_pixels += result["parent_compared"]
        frames_completed += len(frames)
        if len(frames) < 8:
            raise AssertionError("Matrix cell has fewer than eight completed frames")
        allowed = {0} if geometry[4] == 10 else (
            {0, 1, 2, 3} if geometry[5] == 3 else {0, 1})
        if {item["mode"] for item in frames} != allowed:
            raise AssertionError("Not every applicable mode completed in matrix cell/seed")
        per_cell.append(dict(geometry=geometry, seed=result["seed"], completed_frames=len(frames),
                             compared_pixels=result["compared"], applicable_modes=sorted(allowed)))
        if result["counters"]["legal_configs"] < len(frames):
            raise AssertionError("Missing accepted configuration transfers")
        previous_config = None
        for item in frames:
            mode = item["mode"]
            config = (mode, item["border"], item["absolute"], item["shift"], tuple(item["coeff"]))
            if previous_config is not None and config != previous_config:
                totals["config_changes"] += 1
            previous_config = config
            modes.add(mode)
            # Bind pattern bins to the actual transferred image, not its scenario label.
            if item["source"] != image_for(geometry, item["pattern"], item["source_seed"]):
                raise AssertionError("Directed image contents do not match their numerical pattern")
            if geometry[0] > 2 and geometry[1] > 2:
                patterns.add(item["pattern"])
            if mode:
                if geometry[:2] == geometry[2:4]:
                    boundaries.update(("top", "bottom", "left", "right"))
                    if geometry[0] > 3:
                        wraps.add("horizontal")
                    if geometry[1] > 3:
                        wraps.add("vertical")
                    alt = [[(p[0],) for p in row] for row in item["source"]] if (
                        mode & 2) else item["source"]
                    if mode & 2:
                        alt = demosaic(alt, 1 - item["border"])
                    if mode & 1:
                        alt = convolve(alt, item["coeff"], item["shift"], item["absolute"],
                                       1 - item["border"])
                    alternate = [pack(p, geometry[4]) for row in alt for p in row]
                    if alternate != item["expected"]:
                        borders.add(item["border"])
                        arithmetic[f"border_{item['border']}_discriminating_frames"] += 1
            if mode & 2 and geometry[:2] == geometry[2:4]:
                phases.update((x & 1, y & 1) for y in range(geometry[1])
                              for x in range(geometry[0]))
            if mode & 1:
                k, s, a = item["coeff"], item["shift"], item["absolute"]
                shifts.add(s)
                absolutes.add(a)
                extrema.update(set(k) & {-32768, 32767})
                for name, (coeff, shift, absolute) in PRESETS.items():
                    if (k, s, a) == (coeff, shift, absolute):
                        presets.add(name)
                if item["tag"] and item["tag"].startswith("tap"):
                    selected = int(item["tag"][3:])
                    if geometry[:2] != geometry[2:4]:
                        raise AssertionError("Tap sensitivity must be directly observable before scaling")
                    influence = sum(word != 0 for word in item["expected"])
                    if influence == 0:
                        raise AssertionError("Isolated tap did not influence a compared output")
                    tap_influence[selected] += influence
                    taps.add(selected)
                if item["tag"] in ("tie-1", "tie1"):
                    ties.add(item["tag"])
                if geometry[:2] == geometry[2:4]:
                    spatial = demosaic([[(p[0],) for p in row] for row in item["source"]],
                                       item["border"]) if mode & 2 else item["source"]
                    for y in range(geometry[1]):
                        for x in range(geometry[0]):
                            neighbors = reference_taps(spatial, x, y, item["border"])
                            for channel in range(len(spatial[0][0])):
                                products = [coefficient * p[channel]
                                            for coefficient, p in zip(k, neighbors)]
                                accumulator = sum(products)
                                magnitude = abs(accumulator)
                                rounded = (magnitude + (1 << (s - 1))) >> s if s else magnitude
                                if s and magnitude % (1 << s) == 1 << (s - 1):
                                    arithmetic["negative_ties" if accumulator < 0
                                               else "positive_ties"] += 1
                                if accumulator < 0 and not a and rounded:
                                    arithmetic["lower_saturation"] += 1
                                if accumulator < 0 and rounded:
                                    arithmetic[f"absolute_{a}_discriminating_pixels"] += 1
                                if rounded > 255 and (accumulator >= 0 or a):
                                    arithmetic["upper_saturation"] += 1
                                if min(products) < 0 < max(products):
                                    arithmetic["mixed_sign_cancellation"] += 1
                                if item["preset"] in ("sobel_x", "sobel_y") and a:
                                    arithmetic["sobel_absolute"] += 1
    requirements = dict(source_bubbles=32, output_stalls=32, max_stall=16, stalled_sof=1,
                        stalled_eol=1, config_changes=16, busy_config_cycles=1,
                        illegal_configs=1, reset_fill=1, reset_output=1, reset_stall=1)
    for name, threshold in requirements.items():
        if totals[name] < threshold:
            raise AssertionError(f"Missing observed coverage: {name}={totals[name]} < {threshold}")
    required_sets = [(modes, {0, 1, 2, 3}), (presets, set(PRESETS)), (borders, {0, 1}),
                     (absolutes, {0, 1}), (phases, {(0, 0), (0, 1), (1, 0), (1, 1)}),
                     (patterns, set(PATTERNS)), (taps, set(range(9))),
                     (shifts, {0, 1, 4, 16, 31}), (extrema, {-32768, 32767}),
                     (ties, {"tie-1", "tie1"}),
                     (boundaries, {"top", "bottom", "left", "right"}),
                     (wraps, {"horizontal", "vertical"})]
    for actual, required in required_sets:
        if not required <= actual:
            raise AssertionError(f"Missing coverage bins: {required - actual}")
    for name in ("positive_ties", "negative_ties", "lower_saturation", "upper_saturation",
                 "mixed_sign_cancellation", "sobel_absolute",
                 "absolute_0_discriminating_pixels", "absolute_1_discriminating_pixels"):
        if arithmetic[name] == 0:
            raise AssertionError(f"Missing compared arithmetic event: {name}")
    expected_cells = {(tuple(geometry), seed) for geometry in MATRIX for seed in (17, 89, 203)}
    actual_cells = {(tuple(row["geometry"]), row["seed"]) for row in per_cell}
    if actual_cells != expected_cells or len(per_cell) != len(expected_cells):
        raise AssertionError("Missing or repeated frozen matrix cell/seed")
    return dict(counters=dict(totals), thresholds=requirements, modes=sorted(modes),
                presets=sorted(presets), borders=sorted(borders), absolutes=sorted(absolutes),
                phases=sorted(phases), patterns=sorted(patterns), taps=sorted(taps),
                shifts=sorted(shifts), extrema=sorted(extrema), rounding_ties=sorted(ties),
                boundaries=sorted(boundaries), wraps=sorted(wraps),
                arithmetic=dict(arithmetic), tap_influence=dict(tap_influence), cells=per_cell,
                completed_frames=frames_completed, compared_pixels=pixels,
                parent_compared_pixels=parent_pixels)


def clean_reproduction(ledger):
    directory = ledger.path / "clean-reproduction"
    directory.mkdir()
    for name in hashes():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    dump(ledger.path / "clean-input-hashes.json", hashes(directory))
    if hashes(directory) != hashes():
        raise AssertionError("Clean reproduction source mismatch")
    record = ledger.run(["python3", "tests/verify.py", "--compact-reproduce"],
                        "clean-reproduction", cwd=directory, clean_environment=True)
    receipts = list(directory.glob("evidence/l1-*/receipt.json"))
    if len(receipts) != 1:
        raise AssertionError("Clean reproduction must produce one fresh receipt")
    receipt = json.loads(receipts[0].read_text())
    if receipt["exit"] != 0:
        raise AssertionError("Clean reproduction failed")
    summary = json.loads((receipts[0].parent / "summary.json").read_text())
    if summary["compared_pixels"] < 2 * 7 * 9 or not summary["synthesis"]:
        raise AssertionError("Clean reproduction missing full frames/synthesis")
    return dict(command=record, receipt=relative(receipts[0]), summary=summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compact-reproduce", action="store_true")
    parser.add_argument("--probe", choices=("smoke", "leaf", "synthesis", "illegal", "behavior",
                                          "mutations", "reproduction", "geometry"))
    parser.add_argument("--mutation", action="append",
                        help="Bounded diagnostic selection; only with --probe mutations")
    parser.add_argument("--cell", type=int, action="append", choices=range(len(MATRIX)),
                        help="Bounded seed-17 geometry diagnostic; only with --probe geometry")
    parser.add_argument("--repair-of")
    args = parser.parse_args()
    if args.mutation and args.probe != "mutations":
        parser.error("--mutation is only a preparation diagnostic selector")
    if args.cell and args.probe != "geometry":
        parser.error("--cell is only a preparation diagnostic selector")
    ledger = Ledger(preparation=bool(args.probe), repair_of=args.repair_of)
    try:
        ledger.tools()
        dump(ledger.path / "static-checks.json", static_checks())
        for total in range(2296):
            if ((7282 * total + 32768) >> 16) != (total + 4) // 9:
                raise AssertionError(f"Box preset not exact at sum {total}")
        dump(ledger.path / "box-exhaustive.json", dict(compared_sums=2296, mismatches=0))
        if args.probe == "synthesis":
            synthesis(ledger)
        elif args.probe == "illegal":
            illegal_parameters(ledger)
        elif args.probe == "leaf":
            from leaves import verify_leaves
            verify_leaves(ledger)
        elif args.probe == "reproduction":
            clean_reproduction(ledger)
        else:
            parents = parent_sources(ledger)
            if args.probe == "mutations":
                dump(ledger.path / "mutation-results.json", mutations(ledger, parents, args.mutation))
            elif args.probe == "geometry":
                for cell in args.cell or [0]:
                    geometry = MATRIX[cell]
                    frames = scenarios(geometry, 17)
                    binary = build(ledger, geometry, f"geometry-{cell}", parents)
                    execute_frames(ledger, binary, geometry, frames, 17, f"geometry-{cell}")
            elif args.compact_reproduce:
                geometry = (3, 5, 7, 9, 8, 3, 16, 0)
                frames = [frame(geometry, 3, "rggb", 203, "gaussian", 1),
                          frame(geometry, 0, "rgb", 89)]
                binary = build(ledger, geometry, "reproduce", parents)
                result = execute_frames(ledger, binary, geometry, frames, 17, "reproduce")
                synth = synthesis(ledger, compact=True)
                dump(ledger.path / "summary.json",
                     dict(compared_pixels=result["compared"], synthesis=synth))
            else:
                geometry = (3, 5, 7, 9, 8, 3, 16, 0)
                frames = scenarios(geometry, 17)[:4]
                if args.probe == "behavior":
                    geometry = MATRIX[5]
                    frames = scenarios(geometry, 17, extended=True)
                    binary = build(ledger, geometry, "directed-behavior", parents)
                    execute_frames(ledger, binary, geometry, frames, 17, "directed-behavior")
                else:
                    binary = build(ledger, geometry, "icarus-smoke", parents, "icarus")
                    execute_frames(ledger, binary, geometry, frames, 17, "icarus-smoke", "icarus")
                    binary = build(ledger, geometry, "verilator-smoke", parents)
                    execute_frames(ledger, binary, geometry, frames, 17, "verilator-smoke")
                if not args.probe:
                    from leaves import verify_leaves
                    verify_leaves(ledger)
                    illegal_parameters(ledger)
                    results = []
                    for cell, geometry in enumerate(MATRIX):
                        binary = build(ledger, geometry, f"matrix-{cell}", parents)
                        for seed in (17, 89, 203):
                            frames = scenarios(geometry, seed, extended=(cell == 5 and seed == 17))
                            if len(frames) < 8:
                                raise AssertionError("Frozen minimum frames not met")
                            compared = execute_frames(ledger, binary, geometry, frames, seed,
                                                      f"matrix-{cell}-seed-{seed}")
                            results.append((geometry, frames, compared))
                    measured = coverage(results)
                    dump(ledger.path / "coverage.json", measured)
                    killed = mutations(ledger, parents)
                    synth = synthesis(ledger)
                    clean = clean_reproduction(ledger)
                    dump(ledger.path / "summary.json",
                         dict(coverage=measured, mutations=killed, synthesis=synth,
                              clean_reproduction=clean))
        if hashes() != ledger.data["source_sha256"]:
            raise AssertionError("Delivered sources changed during verification")
        ledger.finish(0)
        print(f"L1 results: {relative(ledger.path / 'receipt.json')}", flush=True)
    except Exception as error:
        ledger.finish(1, f"{type(error).__name__}: {error}")
        print(f"L1 failure: {error}\nReceipt: {relative(ledger.path / 'receipt.json')}",
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
