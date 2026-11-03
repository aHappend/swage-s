"""Bounded portable entrypoint: python3 tests/reproduce.py, from project root.

Uses only delivered source files and newly generated evidence: seven default
patterns, three consecutive frames each, real Icarus simulation, independent
integer/float comparisons, and real Quartus project-generation validation.
This does not replace the full behavior, post-fit STA or physical HIL checks.
"""

from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence import Evidence, ROOT, require
from physical import generate_project
from reference import Config
from simulation import execute_config


def main():
    require(Path.cwd().resolve() == ROOT, "Run reproducer from delivered project root")
    e = Evidence("bounded-reproduction")
    error = None
    try:
        e.tool("python3")
        e.tool("iverilog", ("-V",))
        e.tool("vvp", ("-V",))
        e.tool("quartus_sh")
        result = execute_config(e, "icarus", Config(), e.path / "simulation", small=True)
        e.record["compared_pixels"] = sum(case["outputs"] for case in result["cases"])
        e.record["behavior"] = result
        generate_project(e, Config(), e.path / "quartus-project")
        e.record["quartus_project_validated"] = True
    except Exception as caught:
        error = caught
        traceback.print_exc()
    finally:
        e.finish(error)
    print("REPRODUCTION " + e.relative(e.path / "receipt.json"))
    return int(error is not None)


if __name__ == "__main__":
    sys.exit(main())
