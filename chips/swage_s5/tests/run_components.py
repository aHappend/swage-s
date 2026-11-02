"""Bounded project-local preparation for newly authored L1 components."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracle import selftest
from receipts import Execution, fresh
from s5 import compile_top, compare, directed, matrix, mutations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("control", "matrix", "directed", "mutations"))
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
