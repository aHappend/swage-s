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


def parameter_provenance_method_checks(e):
    """Synthetic provenance fixtures: no fixture is an executed RTL result."""
    import shutil
    from types import SimpleNamespace
    from partitions import artifact_hashes, validate_parameters
    from simulation import INVALID_TB, parameter_cases
    from simulator_builds import build_key

    require(e.record["acceptance_evidence"] is False, "Parameter fixtures require preparation")
    case = next(case for case in parameter_cases() if not case["legal"])
    counter = 0

    def fixture(simulator, reused, defect=None):
        nonlocal counter
        root = e.path / "synthetic-parameter-provenance" / str(counter)
        counter += 1
        directory = root / f"parameters-{simulator}" / f"invalid-{case['id']}"
        directory.mkdir(parents=True)
        tb = directory / "tb.sv"
        tb.write_text(INVALID_TB)
        rtl = sorted((ROOT / "rtl").glob("*.sv"))
        parameters = case["parameters"]
        tools = {name: {"synthetic_only": name} for name in ("verilator", "make", "g++")}
        record = {"synthetic_only": True, "status": "passed", "sources_unchanged": True,
                  "source_sha256": {p.relative_to(ROOT).as_posix(): file_hash(p) for p in rtl},
                  "tools": tools, "commands": []}

        def command(argv, cwd, stem, output="", status=0):
            stdout, stderr = root / f"{stem}.stdout", root / f"{stem}.stderr"
            stdout.write_text(output)
            stderr.write_text("")
            return {"requested_argv": argv, "cwd": cwd.relative_to(ROOT).as_posix(),
                    "exit_status": status, "expected_exit_status": [1, -6, 134] if status else [0],
                    "timed_out": False, "end": "synthetic-only", "duration_s": 0,
                    "stdout": e.relative(stdout), "stderr": e.relative(stderr)}

        origin_dir = root / "origin" if reused else root
        origin_tb = origin_dir / "tb.sv" if reused else tb
        origin_tb.parent.mkdir(parents=True, exist_ok=True)
        if reused:
            origin_tb.write_text(INVALID_TB)
        binary = (origin_dir / "obj/simulation" if simulator == "verilator"
                  else directory / "simulation.vvp")
        if not reused and simulator == "verilator":
            binary = directory / "obj/simulation"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"synthetic executable; never run\n")
        binary.chmod(0o700)
        if simulator == "icarus":
            argv = ["iverilog", "-g2012", "-Wall", "-s", "tb",
                    *[f"-Ptb.{k}={v}" for k, v in parameters.items()], "-o", str(binary)]
        else:
            argv = ["verilator", "--binary", "--timing", "--assert", "-Wno-fatal",
                    "--top-module", "tb", "--Mdir", str(binary.parent), "-o", "simulation",
                    *[f"-G{k}={v}" for k, v in parameters.items()]]
        argv += [str(p) for p in rtl] + [str(origin_tb)]
        compiler = command(argv, ROOT, "compiler")
        if reused:
            origin = {**record, "commands": [compiler]}
            if defect == "origin-command":
                origin["commands"].append({**compiler, "exit_status": 1})
            for source in rtl:
                target = origin_dir / "sources" / source.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            origin_path = origin_dir / "receipt.json"
            key = build_key(SimpleNamespace(record=record), rtl, origin_tb, parameters)
            build = {
                "key": key, "receipt": e.relative(origin_path), "command_index": 0,
                "testbench": e.relative(origin_tb), "binary": e.relative(binary),
                "files": {e.relative(p): file_hash(p) for p in (
                    origin_tb, binary, ROOT / compiler["stdout"], ROOT / compiler["stderr"])},
            }
            # Compiler logs must be inside the origin's exclusive output.
            for stream in ("stdout", "stderr"):
                old = ROOT / compiler[stream]
                target = origin_dir / old.name
                shutil.copyfile(old, target)
                del build["files"][compiler[stream]]
                compiler[stream] = e.relative(target)
                build["files"][compiler[stream]] = file_hash(target)
            origin_path.write_text(json.dumps(origin))
            target = directory / "simulation"
            shutil.copy2(binary, target)
            record["reused_compilations"] = [{
                "target": e.relative(target), "sha256": file_hash(target), "origin": build,
                "origin_receipt_sha256": file_hash(origin_path),
            }]
            binary = target
        else:
            record["commands"].append(compiler)
        run = command(["vvp", str(binary)] if simulator == "icarus" else [str(binary)],
                      directory, "diagnostic", "SWAGE_S1 PARAMETER ERROR\n", 1)
        record["commands"].append(run)
        result_case = {**case, "command_indices": list(range(len(record["commands"])))}
        record["partition_result"] = {
            "simulator": simulator, "cases": [result_case],
            "legal_elaborations": 0, "invalid_diagnostics": 1,
        }
        record["artifact_sha256"] = artifact_hashes(root)
        if defect == "parameters":
            if reused:
                build["key"]["parameters"] = {**parameters, "SRC_WIDTH": 4097}
            else:
                compiler["requested_argv"][compiler["requested_argv"].index(
                    ("-Ptb." if simulator == "icarus" else "-G") + "SRC_WIDTH=0")] += "1"
        elif defect == "transactor":
            tb.write_text(INVALID_TB + "\n// changed synthetic transactor\n")
        elif defect == "origin":
            record["reused_compilations"][0]["origin_receipt_sha256"] = "0"*64
        elif defect == "origin-binary":
            build["files"][build["binary"]] = "0"*64
        elif defect == "origin-command":
            build["command_index"] = 1
        elif defect == "rtl":
            record["source_sha256"][next(iter(record["source_sha256"]))] = "0"*64
        elif defect == "tools":
            record["tools"] = {**tools, "verilator": {"synthetic_only": "changed"}}
        elif defect == "binary":
            binary.write_bytes(b"changed synthetic binary\n")
        elif defect == "diagnostic":
            (ROOT / run["stdout"]).write_text("Illegal configuration was accepted\n")
        elif defect == "execution":
            run["requested_argv"][-1] += "-wrong"
        elif defect == "success-exit":
            run["exit_status"] = 0
        elif defect == "timeout":
            run["timed_out"] = True
        elif defect == "missing-run":
            result_case["command_indices"] = result_case["command_indices"][:-1]
        elif defect == "compiler-failure":
            compiler["exit_status"] = 1
        spec = {"simulator": simulator, "case_ids": [case["id"]]}
        return root / "receipt.json", record, spec

    for simulator, reused in (("icarus", False), ("verilator", False), ("verilator", True)):
        path, record, spec = fixture(simulator, reused)
        e.assertion(f"{simulator} reused={reused} parameter provenance accepted",
                    validate_parameters(path, record, spec), record["partition_result"])
        defects = ["parameters", "transactor", "binary", "diagnostic", "execution",
                   "success-exit", "timeout", "missing-run"]
        defects += (["origin", "origin-binary", "origin-command", "rtl", "tools"]
                    if reused else ["compiler-failure"])
        for defect in defects:
            path, record, spec = fixture(simulator, reused, defect)
            try:
                validate_parameters(path, record, spec)
            except AssertionError:
                e.assertion(f"{simulator} reused={reused} rejects {defect}", True, True)
            else:
                raise AssertionError(f"Unsafe parameter provenance accepted: {simulator} {defect}")


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
