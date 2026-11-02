"""Bounded project-local preparation for newly authored L1 components."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracle import selftest
from receipts import Execution, fresh
from s5 import compile_top, compare, directed, matrix, mutations
from pipeline import pipeline_check
from primitives import primitive_check
from control import queue_check, topk_check
from boundaries import boundary_check
from synthesis import synthesize
from parent import compare_parent, compile_parent, parent_matrix
from parent import scenarios as parent_scenarios


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("control", "matrix", "directed", "mutations",
                                            "pipelines", "primitives", "queues", "topk",
                                            "boundaries", "synthesis", "parent-smoke", "parent-matrix",
                                            "parent-compact"))
    parser.add_argument("--repair-of")
    parser.add_argument("--mutation", action="append")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    output = fresh(root / "evidence" / "preparation", args.component)
    execution = Execution(root, output, ["python3", *sys.argv], args.repair_of)
    try:
        selftest()
        execution.versions((("verilator", "--version"),))
        if args.component == "control":
            geometry = (13, 17, 3, 16, 8)
            executable = compile_top(root, output, execution.run, geometry)
            result = compare(root, output, execution.run, executable, geometry, 11,
                             reset_mid=True, controls=True, frames=2)
        elif args.component == "parent-smoke":
            case = {"name": "s5-enhanced", "TOP": 5, "W": 9, "H": 17, "TILE": 8,
                    "flat": True, "frames": 3,
                    "config": {"s4": 7, "patch": 14, "bf16": 1,
                               "means": [32641]*3, "reciprocals": [513]*3}}
            executable = compile_parent(root, output, execution.run, case)
            result = compare_parent(root, output, execution.run, executable, case, 11)
        elif args.component == "parent-compact":
            execution.versions((("iverilog", "-V"), ("vvp", "-V")))
            names = {"s1-scale-8bit-3ch-f16-i0", "s2-demosaic-convolution",
                     "s3-crop-demosaic-conv-hist", "s3-csc1", "s3-csc3",
                     "s5-scale-10bit-1ch-f16-i1"}
            result = []
            for case in parent_scenarios():
                if case["name"] in names:
                    directory = fresh(output, case["name"])
                    executable = compile_parent(root, directory, execution.run, case, simulator="icarus")
                    result.append(compare_parent(root, directory, execution.run, executable,
                                                 case, 11, "icarus"))
        elif args.component == "parent-matrix":
            result = parent_matrix(root, output, execution.run)
        elif args.component == "boundaries":
            execution.versions((("iverilog", "-V"), ("vvp", "-V")))
            result = boundary_check(root, output, execution.run)
        elif args.component == "synthesis":
            execution.versions((("yosys", "-V"),))
            result = synthesize(root, output, execution.run)
        elif args.component in ("queues", "topk"):
            execution.versions((("iverilog", "-V"), ("vvp", "-V")))
            result = []
            if args.component == "queues":
                for inherited in (False, True):
                    for depth in (1, 3, 16):
                        case = fresh(output, f"queue{int(inherited)}-depth{depth}")
                        result.append(queue_check(root, case, execution.run, depth, inherited))
            else:
                for topk in (1, 3, 16):
                    case = fresh(output, f"topk{topk}")
                    result.append(topk_check(root, case, execution.run, topk))
                case = fresh(output, "empty")
                result.append(topk_check(root, case, execution.run, empty=True))
        elif args.component == "primitives":
            execution.versions((("iverilog", "-V"), ("vvp", "-V")))
            result = []
            for kind in ("quantize", "normalizer", "temporal"):
                case = fresh(output, kind)
                result.append(primitive_check(root, case, execution.run, kind))
        elif args.component == "pipelines":
            execution.versions((("iverilog", "-V"), ("vvp", "-V")))
            result = []
            for kind in ("pyramid", "head"):
                case = fresh(output, kind)
                result.append(pipeline_check(root, case, execution.run, kind))
        elif args.component == "mutations":
            result = mutations(root, output, execution.run, args.mutation)
        else:
            result = {"matrix": matrix, "directed": directed, "mutations": mutations}[
                args.component](root, output, execution.run)
        execution.finish(0, result=result)
    except Exception as error:
        execution.finish(1, error=f"{type(error).__name__}: {error}")
    print(f"receipt={output.relative_to(root)}/receipt.json exit={execution.record['exit_code']}")
    return execution.record["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
