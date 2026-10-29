"""Bounded aggregate-repair diagnostics; these are not admitted gate results.

Run: python3 tests/verify.py --probe aggregate-repair
Run again with --repair-of <previous project-relative receipt> to measure reuse
from a completed, independently retained compiler receipt. Every selected tuple
executes its complete frozen scenario list on both simulators.

The smaller follow-up is: python3 tests/verify.py --probe simulator-reuse
"""

import copy
import json
import time

from evidence import ROOT, file_hash, require
from reference import Config
from simulation import (
    STREAM_TB, compile_sim, execute_partition, parse_stream_output, scenario_records,
)


def row_method_checks(e):
    c = Config(1, 1, 1, 1)
    records = [{"pattern": "zero", "stall": 0, "seed": 17, "abort": 0}]
    models = [{"id": 0}]
    text = ("ROW_RUN 0 0 1 1\nROW_RUN 0 1 1 2\nROW_RUN 0 2 1 3\n"
            "RESULT 0 3 3 3 3 0 0 0 0 10\nSCENARIO_CLOCKS 0 20\n"
            "COMPLETE 1\nTOTAL_CLOCKS 20\n")
    parsed = parse_stream_output(c, "fixture", records, models, text)
    e.assertion("compact row fixture expands every row",
                len(parsed["cases"][0]["observed_row_runs"]), 3)
    legacy = text.replace("ROW_RUN 0 0 1 1", "ROW 0 0 1").replace(
        "ROW_RUN 0 1 1 2", "ROW 0 1 2").replace("ROW_RUN 0 2 1 3", "ROW 0 2 3")
    e.assertion("historical row parsing preserves observations",
                parse_stream_output(c, "fixture", records, models, legacy), parsed)
    fixtures = {
        "unknown scenario": text + "ROW_RUN 999 0 1 1\n",
        "zero run": text.replace("ROW_RUN 0 0 1 1", "ROW_RUN 0 0 0 1"),
        "overlap": text.replace("ROW_RUN 0 1 1 2", "ROW_RUN 0 0 1 2"),
        "gap": text.replace("ROW_RUN 0 1 1 2", "ROW_RUN 0 4 1 2"),
        "missing": text.replace("ROW_RUN 0 1 1 2\n", ""),
        "mixed duplicate": text + "ROW 0 0 1\n",
        "wrong ownership": text.replace("ROW_RUN 0 1 1 2", "ROW_RUN 0 1 1 1"),
    }
    for name, output in fixtures.items():
        try:
            parse_stream_output(c, "fixture", records, models, output)
        except AssertionError:
            continue
        raise AssertionError(f"Malformed row fixture accepted: {name}")
    e.assertion("malformed compact observations rejected", len(fixtures), 7)


def aggregate_repair(e):
    require(e.record["acceptance_evidence"] is False, "Repair diagnostics require preparation")
    original = "evidence/checks/behavior-04ab3eabc63e4473a5744c878b042d23/receipt.json"
    e.record["repair_request"] = {
        "original_receipt": original, "available_in_project": (ROOT / original).is_file(),
        "scope": "bounded diagnostics only; no aggregate or engineering acceptance",
    }
    e.save()
    row_method_checks(e)
    for name, flags in (
        ("iverilog", ("-V",)), ("vvp", ("-V",)), ("verilator", ("--version",)),
        ("make", ("--version",)), ("g++", ("--version",)),
    ):
        e.tool(name, flags)
    runs = []
    configurations = (
        Config(),
        Config(2, 7, 3, 4096, channels=3, frac=16),
        Config(7, 2, 4096, 3, channels=3, frac=16, mode=1),
    )
    for index, c in enumerate(configurations):
        for simulator in ("icarus", "verilator"):
            started = time.monotonic()
            first = len(e.record["commands"])
            result = execute_partition(e, simulator, c, e.path / f"{simulator}-{index}")
            require(len(result["cases"]) == len(list(scenario_records(c))),
                    "Bounded tuple omitted a frozen scenario")
            commands = e.record["commands"][first:]
            run = {
                "configuration": c.parameters(), "simulator": simulator,
                "wall_s": time.monotonic() - started, "first_command": first,
                "last_command": len(e.record["commands"]) - 1,
                "scenarios": len(result["cases"]),
                "complete_clocks": sum(case["complete_cycles"] for case in result["cases"]),
                "max_scenario_clocks": max(case["complete_cycles"] for case in result["cases"]),
                "subprocess_s": sum(command["duration_s"] for command in commands),
                "raw_stdout_bytes": sum((ROOT / command["stdout"]).stat().st_size
                                        for command in commands),
                "output_rows_checked": sum(3*c.dh for _ in result["cases"]),
                "row_records": sum(len(case["observed_row_runs"]) for case in result["cases"]),
            }
            if simulator == "icarus":
                run["max_scenario_s"] = max(
                    item["duration_s"] for item in result["scenario_observations"])
            runs.append(run)
            (e.path / f"{simulator}-{index}-observed.json").write_text(
                json.dumps(result, indent=2) + "\n")
            e.record["bounded_runs"] = runs
            e.save()

    c = configurations[0]
    directory = e.path / "verilator-reuse"
    before = len(e.record["commands"])
    command = compile_sim(e, "verilator", c, directory)
    e.assertion("validated reuse runs no new compiler", len(e.record["commands"]), before)
    require(len(command) == 1 and command[0].is_file(), "No reused executable")
    e.assertion("reused transactor remains unchanged",
                (directory / "tb.sv").read_text(), STREAM_TB)
    from simulation import write_vectors
    records = list(scenario_records(c))
    path, models = write_vectors(c, directory, records)
    out, _, _ = e.run([*command, f"+vectors={path.name}"], cwd=directory)
    result = parse_stream_output(c, "verilator", records, models, out)
    e.assertion("reused binary executes every fresh comparison",
                len(result["cases"]), len(records))
    (directory / "observed.json").write_text(json.dumps(result, indent=2) + "\n")
    from simulator_builds import validate_build
    reuse = e.record["reused_compilations"][-1]
    key = reuse["origin"]["key"]
    validate_build(reuse["origin"], key, open_evidence=e)
    for name in ("key", "binary", "command", "raw-log"):
        changed = copy.deepcopy(reuse["origin"])
        if name == "key":
            changed["key"]["parameters"]["SRC_WIDTH"] += 1
        elif name == "binary":
            changed["files"][changed["binary"]] = "0"*64
        elif name == "command":
            changed["command_index"] = 0
        else:
            raw = next(item for item in changed["files"] if item.endswith(".stdout"))
            changed["files"][raw] = "0"*64
        try:
            validate_build(changed, key, open_evidence=e)
        except (AssertionError, KeyError):
            continue
        raise AssertionError(f"Invalid simulator cache provenance accepted: {name}")
    e.assertion("altered compiled-build bindings rejected", 4, 4)
    e.record["transient_bytes_removed"] = sum(
        item["bytes"] for item in e.record.get("removed_compilation_intermediates", []))
    e.record["reused_executable_sha256"] = file_hash(command[0])
    e.save()


def simulator_reuse(e):
    """Validate retained-build consumption separately from the cold-build probe."""
    from simulator_builds import validate_reuse

    require(e.record["acceptance_evidence"] is False, "Reuse diagnostic requires preparation")
    for name in ("verilator", "make", "g++"):
        e.tool(name)
    c = Config()
    before = len(e.record["commands"])
    started = time.monotonic()
    result = execute_partition(e, "verilator", c, e.path / "retained-verilator")
    commands = e.record["commands"][before:]
    e.assertion("retained build executes only the new simulator sweep", len(commands), 1)
    reuse = e.record["reused_compilations"][-1]
    require(reuse["origin"]["receipt"] != e.relative(e.path / "receipt.json"),
            "Reuse diagnostic requires a separately completed compiler receipt")
    tb = e.path / "retained-verilator" / "tb.sv"
    validate_reuse(e.path / "receipt.json", e.record, c.parameters(), tb)
    e.assertion("retained build rechecks every frozen scenario",
                len(result["cases"]), len(list(scenario_records(c))))
    e.record["retained_reuse_measurement"] = {
        "wall_s": time.monotonic() - started,
        "simulation_s": commands[0]["duration_s"], "scenarios": len(result["cases"]),
        "origin_receipt": reuse["origin"]["receipt"],
        "origin_receipt_sha256": reuse["origin_receipt_sha256"],
        "new_compiler_commands": 0,
    }
    (e.path / "reuse-observed.json").write_text(json.dumps(result, indent=2) + "\n")
    # Also validate the previously completed same-receipt reuse, not just its build.
    origin = ROOT / reuse["origin"]["receipt"]
    original = json.loads(origin.read_text())
    for item in original.get("reused_compilations", []):
        target = ROOT / item["target"]
        validate_reuse(origin, original, item["origin"]["key"]["parameters"],
                       target.parent / "tb.sv")
    e.save()
