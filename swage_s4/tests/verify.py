#!/usr/bin/env python3
"""Portable source-bound L1 entrypoint. Native timing/HIL remain runtime gates."""

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cases import compact_cases, matrix
from harness import generate, reference_sources


PROJECT = Path(__file__).resolve().parent.parent
SOURCE_PATTERNS = ("rtl/*.sv", "tests/*.py", "design/SPEC.md",
                   "design/ARCHITECTURE.md", "swage_s3_reference/rtl/*.sv")
TOOLS = {"python3": ["python3", "--version"], "iverilog": ["iverilog", "-V"],
         "vvp": ["vvp", "-V"], "verilator": ["verilator", "--version"],
         "yosys": ["yosys", "-V"]}


def now():
    return datetime.now(timezone.utc).isoformat()


def unique(stem):
    return f"{stem}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"


def relative(path, base=PROJECT):
    return Path(path).relative_to(base).as_posix()


class Evidence:
    def __init__(self, folder, argv, repair_of=None):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=False)
        self.lock = threading.Lock()
        self.counter = 0
        self.receipt = dict(argv=argv, cwd=".", start=now(), end=None, exit_code=None,
                            source_sha256={}, tool_versions={}, commands=[], repair_of=repair_of)
        for pattern in SOURCE_PATTERNS:
            paths = sorted(PROJECT.glob(pattern))
            if not paths:
                raise AssertionError(f"declared source selector has no matches: {pattern}")
            for path in paths:
                name = relative(path)
                self.receipt["source_sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
                target = folder / "source" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        self.save()

    def save(self):
        (self.folder / "receipt.json").write_text(json.dumps(self.receipt, indent=2) + "\n")

    def command(self, argv, cwd, label, timeout=3600, expect_exit=0):
        with self.lock:
            index = self.counter
            self.counter += 1
        stem = f"{index:04d}-{label}"
        out, err = self.folder / f"{stem}.stdout.txt", self.folder / f"{stem}.stderr.txt"
        record = dict(argv=list(map(str, argv)), cwd=relative(cwd), start=now(), end=None,
                      timeout_s=timeout, timed_out=False, exit_code=None,
                      stdout=relative(out), stderr=relative(err))
        with out.open("wb") as stdout, err.open("wb") as stderr:
            process = subprocess.Popen(record["argv"], cwd=cwd, stdout=stdout, stderr=stderr)
            started, heartbeat = time.monotonic(), time.monotonic()
            while process.poll() is None:
                if time.monotonic() - heartbeat >= 30:
                    print(f"PROGRESS {label} elapsed={int(time.monotonic()-started)}s", flush=True)
                    heartbeat = time.monotonic()
                if time.monotonic() - started >= timeout:
                    record["timed_out"] = True
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    break
                time.sleep(0.1)
            record["exit_code"] = process.wait()
        record["end"] = now()
        with self.lock:
            self.receipt["commands"].append(record)
            self.save()
        if label.endswith(("-compile", "-simulate")):
            build = cwd / "compile-obj"
            if build.exists():
                for pattern in ("*.o", "*.a", "*.gch"):
                    for path in build.glob(pattern):
                        path.unlink()
        if record["timed_out"] or (expect_exit is not None and record["exit_code"] != expect_exit):
            detail = (out.read_text(errors="replace") + err.read_text(errors="replace"))[-6000:]
            raise AssertionError(f"TOOL_FAILURE {label}: exit={record['exit_code']} "
                                 f"timeout={record['timed_out']} receipt={relative(self.folder/'receipt.json')}\n{detail}")
        return record, out.read_text(errors="replace"), err.read_text(errors="replace")

    def tools(self):
        for name, argv in TOOLS.items():
            _, stdout, stderr = self.command(argv, PROJECT, "version-"+name)
            self.receipt["tool_versions"][name] = (stdout + stderr).strip()
        self.save()

    def finish(self, exit_code, reason=None):
        self.receipt.update(end=now(), exit_code=exit_code, failure=reason)
        self.save()


def simulation(evidence, cases, simulator="verilator", source=None, label="behavior"):
    folder = evidence.folder / unique(label)
    folder.mkdir()
    ledger = generate(folder, cases)
    rtl = sorted((source or PROJECT / "rtl").glob("*.sv"))
    refs = reference_sources(PROJECT, folder)
    sources = [str(path.relative_to(folder)) if path.is_relative_to(folder)
               else os.path.relpath(path, folder) for path in [*rtl, *refs, folder/"tb.sv"]]
    if simulator == "iverilog":
        evidence.command(["iverilog", "-g2012", "-Wall", "-s", "tb", "-o", "simulation.vvp", *sources],
                         folder, label+"-compile")
        record, stdout, _ = evidence.command(["vvp", "simulation.vvp"], folder, label+"-simulate")
    else:
        evidence.command(["verilator", "--binary", "--timing", "-Wno-fatal", "--top-module", "tb",
                          "--Mdir", "compile-obj", "-j", "2", "-o", "simulation", *sources],
                         folder, label+"-compile")
        record, stdout, _ = evidence.command(["./compile-obj/simulation"], folder, label+"-simulate")
    rows = []
    for line in stdout.splitlines():
        if line.startswith("FRAME_RESULT "):
            fields = list(map(int, line.split()[1:]))
            assert len(fields) == 9, "malformed frame record"
            index, np, nt, cycles, ps, ts, gaps, fps, fts = fields
            assert index == len(rows) and index < len(ledger), "unexpected frame index"
            expected = ledger[index]
            assert (np, nt) == (expected["expected_pixels"], expected["expected_tokens"])
            assert cycles <= expected["cycle_bound"], "finite-cycle completion bound violated"
            rows.append(dict(expected, compared_pixels=np, compared_tokens=nt, observed_cycles=cycles,
                             pixel_stall_cycles=ps, token_stall_cycles=ts, input_gap_cycles=gaps,
                             final_pixel_stall=fps, final_token_stall=fts,
                             simulation_stdout=record["stdout"]))
    assert len(rows) == len(ledger), f"missing comparisons {len(rows)}/{len(ledger)}"
    controls = [list(map(int, line.split()[1:])) for line in stdout.splitlines()
                if line.startswith("CONTROL_RESULT ")]
    assert len(controls) == 1 and len(controls[0]) == 9
    assert controls[0][3] == sum(row["histogram_bins"] for row in ledger)
    result = dict(frames=rows, control=dict(zip(("accepted_configs", "rejected_configs", "busy_samples",
                                              "compared_histogram_bins", "hist_priority_samples",
                                              "isolated_input_gaps", "sustained_input_gaps",
                                              "isolated_output_stalls", "sustained_output_stalls"), controls[0])),
                  simulator=simulator, source_sha256=evidence.receipt["source_sha256"])
    (folder / "comparison.json").write_text(json.dumps(result, indent=2)+"\n")
    print(f"COMPARED {label}: frames={len(rows)} pixels={sum(r['compared_pixels'] for r in rows)} "
          f"tokens={sum(r['compared_tokens'] for r in rows)}", flush=True)
    return result


def functional_matrix(evidence):
    groups = {}
    for case in matrix():
        groups.setdefault(case.params, []).append(case)
    results = []
    def execute(item):
        index, (params, cases) = item
        return simulation(evidence, cases, label=f"matrix-{index:04d}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(execute, item) for item in enumerate(groups.items())]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    rows = [row for result in results for row in result["frames"]]
    coverage = {}
    for row in rows:
        assert row["compared_pixels"] > 0
        for label in row["labels"]:
            coverage[label] = coverage.get(label, 0) + 1
    targets = {label for case in matrix() for label in case.labels}
    assert all(coverage.get(label, 0) for label in targets), "required coverage comparison missing"
    for mask in range(1, 8):
        for seed in (17, 89, 203):
            assert coverage[f"s4_mask_{mask}_seed_{seed}"] >= 3
    for event in ("isolated_input_gaps", "sustained_input_gaps",
                  "isolated_output_stalls", "sustained_output_stalls"):
        assert sum(result["control"][event] for result in results) > 0, f"missing observed {event}"
    (evidence.folder / "functional-coverage.json").write_text(
        json.dumps(dict(coverage=coverage, frames=rows,
                        controls=[r["control"] for r in results]), indent=2)+"\n")


def oracle_sanity(evidence):
    from fractions import Fraction
    from reference import bf16, round_even, clahe
    fixtures = ((Fraction(0), 0), (Fraction(1), 0x3f80), (Fraction(-1), 0xbf80),
                (Fraction(257, 256), 0x3f80), (Fraction(259, 256), 0x3f82),
                (Fraction(511, 256), 0x4000))
    for rational, expected in fixtures:
        assert bf16(rational) == expected, "independent BF16 oracle analytical fixture"
    for rational, expected in ((Fraction(1, 2), 0), (Fraction(3, 2), 2),
                               (Fraction(-1, 2), 0), (Fraction(-3, 2), -2)):
        assert round_even(rational) == expected
    assert clahe([[(37, 81, 129)]], 8, 1) == [[(255, 255, 255)]]
    copied = []
    for path in sorted((PROJECT/"swage_s3_reference/rtl").glob("*.sv")):
        delivered = PROJECT/"rtl"/path.name
        assert delivered.read_bytes() == path.read_bytes(), f"parent provenance changed: {path.name}"
        copied.append(relative(delivered))
    (evidence.folder/"oracle-fixtures.json").write_text(json.dumps(
        dict(bf16_analytical_fixtures=len(fixtures), q_rounding_fixtures=4,
             clahe_singleton_fixtures=1, byte_identical_parent_sources=copied), indent=2)+"\n")


def synthesis(evidence, selected=None):
    required = {"u_legacy": "swage_s3_top", "u_temporal": "swage_s4_temporal",
                "u_clahe": "swage_s4_clahe", "u_patch_tokenizer": "swage_s4_patch_tokenizer",
                "u_normalizer": "swage_s4_normalizer", "u_frame_store": "swage_s4_frame_store"}
    results = []
    for label, params in (("default-rgb", {}), ("mono8", {"CHANNELS": 1}),
                          ("mono10", {"CHANNELS": 1, "PIXEL_BITS": 10})):
        if selected is not None and label != selected:
            continue
        folder = evidence.folder / unique("synthesis-"+label)
        folder.mkdir()
        paths = " ".join(os.path.relpath(p, folder) for p in sorted((PROJECT/"rtl").glob("*.sv")))
        chparam = "".join(f"chparam -set {name} {value} swage_s4_top;\n" for name, value in params.items())
        script = (f"read_verilog -sv {paths};\n{chparam}"
                  "hierarchy -check -top swage_s4_top;\n"
                  "proc; opt_expr; opt_clean; check -assert -noinit;\n"
                  "memory -nomap; opt_clean; opt; check -assert -noinit;\n"
                  "stat -json;\nwrite_json netlist.json;\n")
        (folder/"synthesis.ys").write_text(script)
        evidence.command(["yosys", "-s", "synthesis.ys"], folder, "yosys-"+label)
        netlist = json.loads((folder/"netlist.json").read_text())
        modules = netlist["modules"]
        sequential, memories = 0, 0
        counts = {}
        for name, module in modules.items():
            assert not int(module.get("attributes", {}).get("blackbox", "0"), 2), f"black box {name}"
            for cell in module.get("cells", {}).values():
                kind = cell["type"]
                assert "latch" not in kind.lower(), f"latch {name}/{kind}"
                assert kind not in ("$div", "$mod", "$divfloor", "$modfloor"), f"combinational quotient {name}"
                assert kind.startswith("$") or kind in modules, f"unresolved module {kind}"
                counts[kind] = counts.get(kind, 0)+1
                sequential += int("dff" in kind.lower())
                memories += int(kind.startswith("$mem"))
        assert sequential > 0 and memories > 0, "empty sequential implementation or lost memory inference"
        top = modules["swage_s4_top"]
        for instance, kind in (required.items() if label == "default-rgb" else ()):
            assert instance in top["cells"], f"missing substantive hierarchy {instance}"
            actual = top["cells"][instance]["type"]
            assert kind in actual, f"wrong functional module {instance}/{actual}"
            assert modules[actual].get("cells"), f"empty enhancement {instance}"
        results.append(dict(configuration=label, sequential_cells=sequential, memory_cells=memories,
                            cell_counts=counts, netlist=relative(folder/"netlist.json")))
    (evidence.folder/"synthesis-results.json").write_text(json.dumps(results, indent=2)+"\n")


def parameters(evidence):
    folder = evidence.folder / unique("parameters")
    folder.mkdir()
    sources = [os.path.relpath(p, folder) for p in sorted((PROJECT/"rtl").glob("*.sv"))]
    legal = [
        {"SRC_WIDTH": 4096, "SRC_HEIGHT": 4096, "DST_WIDTH": 4096, "DST_HEIGHT": 4096,
         "MAX_FRAME_PIXELS": 65536},
        {"SRC_WIDTH": 1, "SRC_HEIGHT": 1, "DST_WIDTH": 1, "DST_HEIGHT": 1,
         "MAX_FRAME_PIXELS": 1, "CLAHE_TILE": 8},
    ]
    illegal = [
        {"SRC_WIDTH": 0}, {"SRC_WIDTH": 4097}, {"SRC_HEIGHT": 0}, {"SRC_HEIGHT": 4097},
        {"DST_WIDTH": 0}, {"DST_WIDTH": 4097}, {"DST_HEIGHT": 0}, {"DST_HEIGHT": 4097},
        {"PIXEL_BITS": 10, "CHANNELS": 3}, {"PIXEL_BITS": 0}, {"CHANNELS": 0},
        {"CHANNELS": 2}, {"PHASE_FRAC": 0}, {"PHASE_FRAC": 9},
        {"INTERPOLATION": -1}, {"INTERPOLATION": 2}, {"MAX_FRAME_PIXELS": 0},
        {"MAX_FRAME_PIXELS": 65537}, {"CLAHE_TILE": 0}, {"CLAHE_TILE": 9},
    ]
    rows = []
    for index, params in enumerate(legal + illegal):
        args = [f"-Pswage_s4_top.{k}={v}" for k, v in params.items()]
        evidence.command(["iverilog", "-g2012", "-Wall", "-s", "swage_s4_top", "-o",
                          f"parameters-{index}.vvp", *args, *sources], folder, f"parameter-{index}-compile")
        if index >= len(legal):
            record, out, err = evidence.command(["vvp", f"parameters-{index}.vvp"], folder,
                                                f"parameter-{index}-reject", expect_exit=None)
            assert record["exit_code"] != 0 and "PARAMETER ERROR" in out+err, f"invalid parameter not diagnosed {params}"
        rows.append(dict(parameters=params, outcome="elaborated" if index < len(legal) else "named rejection"))
    (folder/"results.json").write_text(json.dumps(rows, indent=2)+"\n")


def reproduction(evidence):
    folder = evidence.folder / unique("clean-reproduction")
    folder.mkdir()
    for pattern in SOURCE_PATTERNS:
        for source in PROJECT.glob(pattern):
            target = folder / relative(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    evidence.command(["python3", "tests/verify.py", "--portable-smoke"], folder, "clean-portable-smoke")
    child_receipts = list((folder/"evidence").glob("l1-*/receipt.json"))
    assert len(child_receipts) == 1
    child = json.loads(child_receipts[0].read_text())
    assert child["exit_code"] == 0
    assert child["source_sha256"] == evidence.receipt["source_sha256"]
    (folder/"packaging-result.json").write_text(json.dumps(
        dict(outcome="independently compared legacy/enhanced packaging smoke",
             child_receipt=relative(child_receipts[0])), indent=2)+"\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable-smoke", action="store_true")
    parser.add_argument("--prepare-case", help="bounded advisory public-pin diagnostic by exact case name")
    parser.add_argument("--prepare-leaf", choices=("temporal", "normalizer", "storage1", "storage3"),
                        help="bounded advisory leaf diagnostic")
    parser.add_argument("--simulator", choices=("iverilog", "verilator"), default="iverilog",
                        help="simulator for a selected preparation diagnostic only")
    parser.add_argument("--prepare-synthesis", choices=("default-rgb", "mono8", "mono10"))
    parser.add_argument("--prepare-mutation", help="one exact disposable defect-sensitivity diagnostic")
    parser.add_argument("--prepare-packaging", action="store_true",
                        help="bounded advisory clean-tree packaging diagnostic")
    parser.add_argument("--repair-of")
    args = parser.parse_args()
    if sum(bool(v) for v in (args.prepare_case, args.prepare_leaf,
                             args.prepare_synthesis, args.prepare_mutation, args.prepare_packaging)) > 1:
        parser.error("choose one preparation diagnostic")
    if args.prepare_case:
        selected = [case for case in matrix() + compact_cases() if case.name == args.prepare_case]
        if len(selected) != 1:
            raise AssertionError("preparation case must select exactly one known scenario")
        folder = PROJECT / "evidence/preparation" / unique(args.prepare_case)
    elif args.prepare_leaf:
        folder = PROJECT / "evidence/preparation" / unique(args.prepare_leaf)
    elif args.prepare_synthesis:
        folder = PROJECT / "evidence/preparation" / unique("yosys-"+args.prepare_synthesis)
    elif args.prepare_mutation:
        from mutations import MUTATIONS
        if args.prepare_mutation not in {row[0] for row in MUTATIONS}:
            parser.error("unknown mutation diagnostic")
        folder = PROJECT / "evidence/preparation" / unique(args.prepare_mutation)
    elif args.prepare_packaging:
        folder = PROJECT / "evidence/preparation" / unique("clean-packaging")
    else:
        folder = PROJECT / "evidence" / unique("l1")
    evidence = Evidence(folder, ["python3", "tests/verify.py", *sys.argv[1:]], args.repair_of)
    try:
        evidence.tools()
        if args.prepare_case:
            simulation(evidence, selected, simulator=args.simulator, label="bounded-diagnostic")
        elif args.prepare_leaf:
            from leaves import arithmetic, storage
            if args.prepare_leaf.startswith("storage"):
                storage(evidence, int(args.prepare_leaf[-1]))
            else:
                arithmetic(evidence, args.prepare_leaf, simulator=args.simulator)
        elif args.prepare_synthesis:
            synthesis(evidence, args.prepare_synthesis)
        elif args.prepare_mutation:
            from mutations import run_mutations
            oracle_sanity(evidence)
            run_mutations(evidence, args.prepare_mutation)
        elif args.prepare_packaging:
            reproduction(evidence)
        elif args.portable_smoke:
            for case in compact_cases():
                simulation(evidence, [case], simulator="iverilog", label=case.name)
        else:
            # Imports are local and each missing generator/subcheck executes in this invocation.
            from leaves import run_leaves
            from mutations import run_mutations
            run_leaves(evidence)
            for case in compact_cases():
                simulation(evidence, [case], simulator="iverilog", label=case.name)
            parameters(evidence)
            functional_matrix(evidence)
            run_mutations(evidence)
            synthesis(evidence)
            reproduction(evidence)
        evidence.finish(0)
        print(f"VERIFICATION_COMPLETE receipt={relative(folder/'receipt.json')}", flush=True)
        return 0
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as exc:
        evidence.finish(1, str(exc))
        print(f"VERIFICATION_FAILED {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
