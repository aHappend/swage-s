"""Portable independent L1 verification: python3 tests/verify.py.

The default invocation executes fresh behavioral matrices, public-pin leaf
comparisons, numerical oracles, disposable mutations, generic synthesis and
recipient reproduction. No prior component receipts or generated DUT traces
are used to manufacture expected outputs. --compact-reproduce is the explicit
recipient check required by the clean-directory experiment, not the main matrix.
Physical timing and board gates are separate shared-runtime executions.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from boundaries import boundary_check
from control import queue_check, topk_check
from leaf import leaf_check
from oracle import selftest as s5_selftest
from parent import parent_matrix
from parent_oracle import selftest as parent_selftest
from pipeline import pipeline_check
from primitives import primitive_check
from receipts import Execution, fresh, now
from s5 import MATRIX, MUTATIONS, compare, compile_top, directed, matrix, mutations
from synthesis import synthesize


REQUIREMENTS = {
    "s5_graph": "causal windows, exact signed40 reductions/RNE, C2f branch order, pyramid raster/strides",
    "detection": "class selection, threshold, clipped regression, global tuple ranking, empty completion",
    "roi": "retained source patch/mask, rank ownership, repeated patches, Q8.8/BF16, edge padding",
    "protocol": "pre-edge transfers, reset cancellation, configuration/weight priority and errors, stalls",
    "matrix": "six geometries times three seeds; ready reference and independently stalled replay",
    "parent": "independent image mathematics for S1-S4 plus unchanged-source supplementary replay",
    "sensitivity": "nine fresh disposable RTL corruptions; only intended numerical mismatches count",
    "synthesis": "real memory-preserving Yosys netlists, zero latches/div/mod/unresolved cells, connected hierarchy",
    "reproduction": "fresh recipient source-only directory executes compact numerical simulation and synthesis",
}


def distributable(root):
    paths = []
    for pattern in ("rtl/*.sv", "design/SPEC.md", "design/ARCHITECTURE.md", "tests/*"):
        paths.extend(path for path in sorted(root.glob(pattern)) if path.is_file())
    assert paths and all(not any(part in (".vulcan", ".git", ".tooling", "VULCAN")
                                 for part in path.relative_to(root).parts) for path in paths)
    for source in paths:
        text = source.read_text()
        assert not re.search(r"/(?:data|home)/", text), (
            f"Private host path in distributable source: {source.relative_to(root)}")
    return paths


def compact(root, output, run):
    result = {"leaves": []}
    for kind in ("window", "dsconv", "c2f"):
        case = fresh(output, kind)
        result["leaves"].append(leaf_check(root, case, run, kind, 3, 2, 11))
    geometry = (3, 2, 3, 3, 8)
    case = fresh(output, "compact-top")
    executable = compile_top(root, case, run, geometry, simulator="icarus")
    result["integration"] = compare(root, case, run, executable, geometry, 11,
                                    bf16=True, simulator="icarus",
                                    means=[32641]*3, reciprocals=[513]*3)
    case = fresh(output, "compact-synthesis")
    result["synthesis"] = synthesize(root, case, run)
    return result


def reproduction(root, output, execution):
    directory = fresh(output, "clean-reproduction")
    recipient = directory / "recipient"
    recipient.mkdir()
    inventory = {}
    for source in distributable(root):
        relative = source.relative_to(root)
        target = recipient / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
        inventory[str(relative)] = digest
    assert not (recipient / "swage_s4_reference").exists()
    execution.run(["python3", "tests/verify.py", "--compact-reproduce"], "recipient-execution", cwd=recipient)
    summaries = list(recipient.glob("evidence/l1-*/summary.json"))
    assert len(summaries) == 1, "Recipient output namespace was not fresh"
    summary = json.loads(summaries[0].read_text())
    assert summary["exit_code"] == 0 and summary["mode"] == "compact-reproduce"
    receipt_path = summaries[0].parent / "receipt.json"
    record = json.loads(receipt_path.read_text())
    for path, digest in inventory.items():
        assert record["source_sha256"][path] == digest
    result = {"source_sha256": inventory, "recipient_cwd": execution.relative(recipient),
              "receipt": execution.relative(receipt_path), "summary": execution.relative(summaries[0]),
              "scope": "source-only compact numerical Icarus behavior and complete-top generic synthesis"}
    (directory / "reproduction.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def complete(root, output, execution):
    run, result = execution.run, {}
    execution.record["components"] = result
    execution.save()
    for kind in ("window", "dsconv", "c2f"):
        case = fresh(output, f"compact-{kind}")
        result.setdefault("compact_leaves", []).append(leaf_check(root, case, run, kind, 3, 2, 11))
        for seed in (11, 29, 47):
            case = fresh(output, f"{kind}-seed{seed}")
            result.setdefault("compiled_leaves", []).append(
                leaf_check(root, case, run, kind, 13, 17, seed, "verilator"))
    for width, height in ((1, 9), (9, 1), (4096, 3)):
        case = fresh(output, f"window-boundary-{width}x{height}")
        result.setdefault("window_boundaries", []).append(
            leaf_check(root, case, run, "window", width, height, 47, "verilator"))
    for seed in (11, 29, 47):
        for kind in ("pyramid", "head"):
            case = fresh(output, f"{kind}-seed{seed}")
            comparison = pipeline_check(root, case, run, kind, seed=seed, simulator="verilator",
                                        threshold=None if kind == "head" else 500)
            if kind == "head":
                assert all(comparison["score_relations_compared"][relation] > 0
                           for relation in ("below", "equal", "above"))
            result.setdefault("pyramid_head", []).append(comparison)
    for classes in (1, 3, 8):
        for endpoint in (True, "zero"):
            case = fresh(output, f"head-{classes}-endpoint-{endpoint}")
            result.setdefault("head_endpoints", []).append(
                pipeline_check(root, case, run, "head", width=3, height=2, classes=classes,
                               simulator="icarus", endpoints=endpoint,
                               threshold=65535 if endpoint is True else 0))
    for kind in ("quantize", "normalizer", "temporal"):
        case = fresh(output, kind)
        result.setdefault("primitives", []).append(primitive_check(root, case, run, kind))
    case = fresh(output, "compiled-temporal")
    result["compiled_temporal"] = primitive_check(root, case, run, "temporal", "verilator")
    for inherited in (False, True):
        for depth in (1, 3, 16):
            case = fresh(output, f"fifo-{int(inherited)}-depth{depth}")
            result.setdefault("fifos", []).append(queue_check(root, case, run, depth, inherited))
    for topk in (1, 3, 16):
        case = fresh(output, f"topk-{topk}")
        result.setdefault("topk", []).append(topk_check(root, case, run, topk, "verilator"))
    case = fresh(output, "topk-empty")
    result["topk_empty"] = topk_check(root, case, run, empty=True)
    case = fresh(output, "matrix")
    result["matrix"] = matrix(root, case, run)
    assert len(result["matrix"]) == len(MATRIX)*3 == 18
    case = fresh(output, "directed")
    result["directed"] = directed(root, case, run)
    case = fresh(output, "inherited")
    result["inherited"] = parent_matrix(root, case, run)
    case = fresh(output, "static-boundaries")
    result["static_boundaries"] = boundary_check(root, case, run)
    case = fresh(output, "mutations")
    result["mutations"] = mutations(root, case, run)
    assert {r["mutation"] for r in result["mutations"]} == {m[0] for m in MUTATIONS}
    for width, height, classes, topk, depth in ((1, 1, 1, 1, 1), (3, 2, 3, 3, 3),
                                              (13, 17, 8, 16, 16)):
        case = fresh(output, f"synthesis-{width}x{height}")
        parameters = {"SRC_WIDTH": width, "SRC_HEIGHT": height, "DST_WIDTH": width,
                      "DST_HEIGHT": height, "MAX_FRAME_PIXELS": 16,
                      "S5_MAX_WIDTH": width, "S5_NUM_CLASSES": classes,
                      "S5_MAX_TOPK": topk, "S5_FIFO_DEPTH": depth}
        result.setdefault("synthesis", []).append(synthesize(root, case, run, parameters))
    result["reproduction"] = reproduction(root, output, execution)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compact-reproduce", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    output = fresh(root / "evidence", "l1")
    execution = Execution(root, output, ["python3", *sys.argv])
    summary = {"mode": "compact-reproduce" if args.compact_reproduce else "complete",
               "requirements": REQUIREMENTS, "started_at": now(), "exit_code": None}
    try:
        distributable(root)
        s5_selftest()
        parent_selftest()
        execution.versions((("python3", "--version"), ("iverilog", "-V"), ("vvp", "-V"),
                            ("verilator", "--version"), ("yosys", "-V"),
                            ("g++", "--version"), ("make", "--version")))
        summary["components"] = (compact(root, output, execution.run) if args.compact_reproduce else
                                 complete(root, output, execution))
        for path, digest in execution.record["source_sha256"].items():
            assert hashlib.sha256((root / path).read_bytes()).hexdigest() == digest, (
                f"Verification source changed during execution: {path}")
        summary["exit_code"] = 0
    except Exception as error:
        summary["exit_code"] = 1
        summary["error"] = f"{type(error).__name__}: {error}"
    finally:
        summary["ended_at"] = now()
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        execution.finish(summary["exit_code"], summary="summary.json", error=summary.get("error"))
        print(json.dumps({"receipt": str((output / "receipt.json").relative_to(root)),
                          "exit_code": summary["exit_code"], "error": summary.get("error")}), flush=True)
    return summary["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
