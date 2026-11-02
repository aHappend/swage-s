"""Bounded project-local preparation for newly authored L1 components."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracle import selftest
from receipts import Execution, fresh
from s5 import compile_top, compare, directed, matrix, mutations, reconfiguration
from pipeline import pipeline_check
from primitives import primitive_check
from control import queue_check, topk_check
from boundaries import boundary_check
from synthesis import synthesize
from parent import compare_parent, compile_parent, parent_matrix
from parent import scenarios as parent_scenarios
from verify import reproduction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("control", "matrix", "directed", "mutations",
                                            "pipelines", "primitives", "queues", "topk",
                                            "boundaries", "synthesis", "parent-smoke", "parent-matrix",
                                            "parent-compact", "reproduction", "parent-control",
                                            "head-boundaries", "synthesis-leaves",
                                            "reconfiguration", "parent-updates"))
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
        elif args.component == "reconfiguration":
            geometry = (13, 17, 3, 16, 8)
            executable = compile_top(root, output, execution.run, geometry)
            result = []
            for seed in (11, 29, 47):
                case = fresh(output, f"reconfiguration-seed{seed}")
                result.append(reconfiguration(root, case, execution.run, executable, seed))
        elif args.component == "parent-updates":
            names = {"s4-scale-10bit-1ch-f16-i1", "s4-scale-8bit-3ch-f8-i1",
                     "s5-clahe8-unclipped-hist", "s4-temporal128", "s5-enhanced"}
            result = []
            for case in parent_scenarios():
                if case["name"] in names:
                    directory = fresh(output, case["name"])
                    executable = compile_parent(root, directory, execution.run, case)
                    result.append(compare_parent(root, directory, execution.run, executable, case, 11))
            assert len(result) == len(names)
        elif args.component == "reproduction":
            result = reproduction(root, output, execution)
        elif args.component == "parent-control":
            result = []
            for top, alpha in ((4, 128), (5, 256)):
                case = {"name": f"s{top}-temporal{alpha}", "TOP": top, "W": 9, "H": 17,
                        "frames": 3, "config": {"s4": 1, "alpha": alpha, "motion": 16}}
                directory = fresh(output, case["name"])
                executable = compile_parent(root, directory, execution.run, case)
                result.append(compare_parent(root, directory, execution.run, executable, case, 11))
        elif args.component == "head-boundaries":
            execution.versions((("iverilog", "-V"), ("vvp", "-V")))
            result = []
            for seed in (11, 29, 47):
                case = fresh(output, f"head-seed{seed}")
                comparison = pipeline_check(root, case, execution.run, "head", seed=seed,
                                            simulator="verilator", threshold=None)
                assert all(comparison["score_relations_compared"][relation] > 0
                           for relation in ("below", "equal", "above"))
                result.append(comparison)
            for classes in (1, 3, 8):
                for endpoint in (True, "zero"):
                    case = fresh(output, f"head-{classes}-{endpoint}")
                    result.append(pipeline_check(root, case, execution.run, "head", 3, 2,
                                                 classes=classes, endpoints=endpoint,
                                                 threshold=65535 if endpoint is True else 0))
        elif args.component == "synthesis-leaves":
            execution.versions((("yosys", "-V"),))
            result = []
            for top, parameters in (("swage_s5_head", {"NUM_CLASSES": 8}),
                                    ("swage_s1_top", {"SRC_WIDTH": 3, "SRC_HEIGHT": 2,
                                                      "DST_WIDTH": 5, "DST_HEIGHT": 3})):
                case = fresh(output, top)
                result.append(synthesize(root, case, execution.run, parameters, top))
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
