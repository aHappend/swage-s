"""SWAGE-S1 admitted checks. Run from the delivered project root."""

import argparse
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence import Evidence, ROOT, execution_environment, partition_output_prefix, require


CHECKS = (
    "behavior", "defect-sensitivity", "synthesis", "rtl-timing-lint",
    "quartus-sta", "board-hil", "portable-reproduction",
    "behavior-shard", "parameter-shard", "pipeline-shard",
    "quartus-sta-shard", "board-hil-shard",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--check", choices=CHECKS)
    choice.add_argument(
        "--scheduling-plan", action="store_true",
        help="Print advisory command/dependency JSON only; execute no checks or tools",
    )
    choice.add_argument("--probe", choices=(
        "transactors", "prerequisites", "build-interfaces", "quartus-project",
        "board-inputs", "board-discovery", "reset-compute", "quartus-postfit",
        "quartus-interface", "quartus-output",
        "partition-method", "aggregate-repair", "simulator-reuse", "physical-method",
    ))
    parser.add_argument("--repair-of", help="Project-relative original preparation receipt")
    parser.add_argument("--simulator", choices=("icarus", "verilator"))
    parser.add_argument("--shard", type=int)
    args = parser.parse_args()
    if args.scheduling_plan:
        if args.shard is not None or args.simulator is not None or args.repair_of is not None:
            parser.error("--scheduling-plan does not accept execution/probe options")
        from partitions import scheduling_plan
        print(json.dumps(scheduling_plan(), indent=2))
        return 0
    sharded = args.check is not None and args.check.endswith("-shard")
    if sharded:
        if args.shard is None:
            parser.error("--shard is required for partition checks")
        simulated = args.check in ("behavior-shard", "parameter-shard", "pipeline-shard")
        if simulated != (args.simulator is not None):
            parser.error("--simulator is required only for simulation partition checks")
    elif args.shard is not None or args.simulator is not None:
        parser.error("--shard/--simulator are only valid for partition checks")
    output_prefix = partition_output_prefix(args.check, args.shard, args.simulator)
    if sharded and output_prefix is None:
        parser.error(
            "Partition has no declared exclusive output selector; "
            "append-only admission and matching verification wiring are required "
            "before execution"
        )
    require(Path.cwd().resolve() == ROOT, "Run from the delivered project root")
    evidence = Evidence(
        args.check or f"verifier-{args.probe}", preparation=bool(args.probe),
        output_prefix=output_prefix,
    )
    if args.repair_of:
        original = Path(args.repair_of)
        require(not original.is_absolute() and ".." not in original.parts and original.is_file(),
                "--repair-of must identify an existing project-relative receipt")
        evidence.record["repair_of"] = args.repair_of
        evidence.save()
    error = None
    try:
        evidence.tool("python3")
        if sharded:
            from partitions import run_partition
            run_partition(evidence, args.check, args.shard, args.simulator)
        elif args.probe == "partition-method":
            from partitions import method_checks
            method_checks(evidence)
        elif args.probe == "aggregate-repair":
            from recovery_checks import aggregate_repair
            aggregate_repair(evidence)
        elif args.probe == "simulator-reuse":
            from recovery_checks import simulator_reuse
            simulator_reuse(evidence)
        elif args.probe == "physical-method":
            from physical_method import method_checks
            method_checks(evidence)
        elif args.probe == "transactors":
            from simulation import INVALID_TB, compile_sim, execute_config, pipeline_checks
            from reference import Config
            for name, version in (("iverilog", ("-V",)), ("vvp", ("-V",)),
                                  ("verilator", ("--version",)),
                                  ("make", ("--version",)), ("g++", ("--version",))):
                evidence.tool(name, version)
            evidence.record["probe_behavior"] = []
            for index, (simulator, config) in enumerate((
                ("icarus", Config()), ("verilator", Config(1, 1, 1, 1)),
                ("icarus", Config(3, 5, 2, 3, bits=10, frac=16)),
                ("verilator", Config(3, 5, 2, 3, channels=3, frac=16)),
            )):
                result = execute_config(evidence, simulator, config,
                                        evidence.path / f"{simulator}-stream-{index}")
                evidence.record["probe_behavior"].append(result)
                evidence.save()
            for simulator in ("icarus", "verilator"):
                pipeline_checks(evidence, simulator, formats=((8, 1),), fractions=(8,))
                directory = evidence.path / f"{simulator}-invalid"
                command = compile_sim(
                    evidence, simulator, Config(), directory, INVALID_TB,
                    parameters={**Config().parameters(), "SRC_WIDTH": 0},
                )
                out, err, code = evidence.run(
                    command, cwd=directory, expected=(1, -6, 134),
                )
                require(code != 0 and "SWAGE_S1 PARAMETER ERROR" in out + err,
                        "Illegal-parameter diagnostic probe failed")
        elif args.probe == "prerequisites":
            import shutil
            prerequisites = (
                "python3", "iverilog", "vvp", "verilator", "make", "g++",
                "yosys", "vulcan", "quartus_sh", "quartus_sta", "quartus_pgm",
                "qsys-script", "qsys-generate",
            )
            available = {
                name: shutil.which(name, path=execution_environment().get("PATH")) is not None
                for name in prerequisites
            }
            launcher_errors = {}
            for name in ("quartus_sh", "quartus_sta", "quartus_pgm"):
                if available[name]:
                    try:
                        evidence.tool(name)
                    except (AssertionError, OSError) as error:
                        available[name] = False
                        launcher_errors[name] = str(error)
            evidence.record["prerequisites"] = available
            evidence.record["launcher_errors"] = launcher_errors
            evidence.save()
            missing = [name for name, found in available.items() if not found]
            require(not missing, "Unavailable prerequisites: " + ", ".join(missing))
        elif args.probe == "reset-compute":
            from reference import Config
            from simulation import execute_config
            for name, version in (("iverilog", ("-V",)), ("vvp", ("-V",)),
                                  ("verilator", ("--version",)),
                                  ("make", ("--version",)), ("g++", ("--version",))):
                evidence.tool(name, version)
            evidence.record["reset_compute_repair"] = []
            for simulator in ("icarus", "verilator"):
                result = execute_config(
                    evidence, simulator, Config(mode=1),
                    evidence.path / f"{simulator}-nearest-reset",
                )
                evidence.record["reset_compute_repair"].append(result)
                evidence.save()
        elif args.probe == "board-discovery":
            from discovery import discover
            discover(evidence)
        elif args.probe == "board-inputs":
            import os
            from physical import relative_input
            config_name = os.environ.get("SWAGE_BOARD_CONFIG", "board/verification.json")
            evidence.record["board_config"] = config_name
            evidence.save()
            board = json.loads(relative_input(config_name).read_text())
            for key in ("board-system", "board-settings", "board-sdc"):
                relative_input(board[key])
            require(isinstance(board.get("dependencies"), list) and board["dependencies"],
                    "Board/IP dependency inventory is required")
            for dependency in board["dependencies"]:
                relative_input(dependency)
        elif args.probe == "build-interfaces":
            from implementation_checks import synthesize
            from physical import generate_project
            from reference import Config
            evidence.tool("yosys", ("-V",))
            synthesize(evidence, Config(), evidence.path / "default-synthesis")
            evidence.tool("quartus_sh")
            generate_project(evidence, Config(1, 1, 1, 1),
                             evidence.path / "small-quartus-project")
        elif args.probe == "quartus-interface":
            evidence.tool("vulcan")
            evidence.run(["vulcan", "hardware", "fpga", "quartus-sta", "--help"])
        elif args.probe == "quartus-output":
            from physical import compile_and_sta
            from reference import Config
            for tool in ("quartus_sh", "quartus_sta", "vulcan"):
                evidence.tool(tool)
            result = compile_and_sta(
                evidence, Config(bits=10, frac=16, mode=1),
                evidence.path / "gray10-f16-nearest-quartus-output",
            )
            evidence.record["postfit_probe"] = result
            evidence.save()
        elif args.probe == "quartus-postfit":
            from physical import compile_and_sta, unconstrained_counts
            from reference import Config
            categories = (
                "Illegal Clocks", "Unconstrained Clocks", "Unconstrained Input Ports",
                "Unconstrained Input Port Paths", "Unconstrained Output Ports",
                "Unconstrained Output Port Paths",
            )
            rows = [f"; {category} ; 0 ; 0 ;" for category in categories]
            evidence.assertion("all UCP setup/hold categories parsed",
                               len(unconstrained_counts("\n".join(rows))), 6)
            rejected = 0
            fixtures = ["", "\n".join(rows[:-1]), "\n".join(rows + [rows[0]])]
            for index in range(len(rows)):
                for replacement in ("; 1 ; 0 ;", "; 0 ; 1 ;"):
                    changed = list(rows)
                    changed[index] = changed[index].replace("; 0 ; 0 ;", replacement)
                    fixtures.append("\n".join(changed))
            for fixture in fixtures:
                try:
                    unconstrained_counts(fixture)
                except AssertionError:
                    rejected += 1
            evidence.assertion("unsafe UCP fixtures rejected", rejected, len(fixtures))
            for tool in ("quartus_sh", "quartus_sta", "vulcan"):
                evidence.tool(tool)
            result = compile_and_sta(
                evidence, Config(), evidence.path / "default-quartus-postfit",
            )
            evidence.record["postfit_probe"] = result
            evidence.save()
        elif args.probe == "quartus-project":
            from physical import generate_project
            from reference import Config
            evidence.tool("quartus_sh")
            generate_project(evidence, Config(1, 1, 1, 1),
                             evidence.path / "small-quartus-project")
        elif args.check == "behavior":
            from simulation import behavior
            behavior(evidence)
        elif args.check == "defect-sensitivity":
            from implementation_checks import mutations
            mutations(evidence)
        elif args.check == "synthesis":
            from implementation_checks import synthesis
            synthesis(evidence)
        elif args.check == "rtl-timing-lint":
            from implementation_checks import timing_lint
            timing_lint(evidence)
        elif args.check == "quartus-sta":
            from physical import quartus_sta
            quartus_sta(evidence)
        elif args.check == "board-hil":
            from physical import board_hil
            board_hil(evidence)
        else:
            from physical import portable
            portable(evidence)
    except Exception as caught:
        error = caught
        traceback.print_exc()
    finally:
        evidence.finish(error)
    print(json.dumps({"check": args.check or args.probe, "status": "failed" if error else "passed",
                      "receipt": evidence.relative(evidence.path / "receipt.json")}))
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
