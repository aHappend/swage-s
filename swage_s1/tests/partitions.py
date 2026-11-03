"""Admitted scheduling recovery with dependency-aware aggregate execution.

check_definitions() supplies the exact append-only command/dependency inventory
for Coordinator admission. Original gates validate current receipts and
execute missing admitted prerequisites;
they never select the newest receipt or accept partial coverage.
Shard entrypoints collect only their transitive predecessors, serially, before
recording partition_execution.start and its command boundary. Invocation start
is retained unchanged; predecessor completion must precede actual shard work.
First behavior: Icarus 154. First timing: 178, then 166. First hardware: 10.
Call-0128 admits a fresh recovery chain if the shared runtime cannot restore
the original predecessor exactly. It must attempt restoration before scheduling
this chain. Historical receipts are retained, never relinked or selected.
Icarus retains each original scenario in a separate process, including its
original ID, reset prelude and three-frame transaction. Functional watchdogs
and integer/float comparisons are unchanged. Duration/cycle tier violations
fail after retaining the observation, not by shortening a child timeout.
Invalid-parameter cases validate the exact INVALID_TB compiler command or the
source/configuration/tool-bound retained build, then independently validate the
current diagnostic execution and its executable. Build reuse never substitutes
for a fresh diagnostic or a current-source numerical comparison.
Timing shards 0-7 and 15-19 have exclusive allocation wiring matching the
current executable contract. Approved shards 8-14 remain unwired until the
shared runtime restores their missing executable records; approval alone
does not authorize an output allocation or physical collection.
"""

import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import struct
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from functools import lru_cache

from evidence import ROOT, file_hash, hashes, partition_output_prefix, partition_receipts, require, stamp
from reference import Config, FORMATS, GEOMETRIES, HEIGHT_EDGES, configurations
from simulation import parameter_cases, scenario_records


PREVIOUS_IDENTITY = "swage-s1-scheduling-recovery-call-0017-v1"
IDENTITY = "swage-s1-scheduling-recovery-call-0128-v2"
SHARD_CHECKS = (
    "behavior-shard", "parameter-shard", "pipeline-shard",
    "quartus-sta-shard", "board-hil-shard",
)
SIM_TOOLS = {
    "icarus": (("iverilog", ("-V",)), ("vvp", ("-V",))),
    "verilator": (("verilator", ("--version",)), ("make", ("--version",)),
                  ("g++", ("--version",))),
}
PHYSICAL_TOOLS = (("quartus_sh", ("--version",)),
                  ("quartus_sta", ("--version",)), ("vulcan", ("--version",)))
ARTIFACT_SUFFIXES = {
    ".stdout", ".stderr", ".sv", ".vvp", ".txt", ".json", ".jsonl",
    ".rpt", ".summary", ".tcl", ".sdc", ".qsf", ".qpf", ".sof", ".qip", ".ip", ".qsys",
}
HEADER_KEYS = (
    "scheduling_contract", "source_sha256", "status", "partition", "start", "end",
)


def canonical_hash(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("ascii")).hexdigest()


def inventories():
    behavior = list(configurations(GEOMETRIES + HEIGHT_EDGES + ((8, 16, 5, 1),)))
    timing = list(configurations(GEOMETRIES + HEIGHT_EDGES + (
        (4096, 4096, 4096, 4096), (4096, 4096, 1, 1), (1, 1, 4096, 4096),
    )))
    hardware = list(configurations(GEOMETRIES[:4]))
    pipeline = [(bits, channels, frac) for bits, channels in FORMATS for frac in (8, 16)]
    require((len(behavior), len(timing), len(hardware), len(pipeline)) == (168, 192, 48, 6),
            "Frozen partition inventory changed")
    require(tuple(behavior[154].parameters().values()) == (2, 7, 3, 4096, 8, 3, 16, 0),
            "Recovery anchor must be behavior index 154, not index 166")
    return behavior, timing, hardware, pipeline


@lru_cache(maxsize=1)
def contract():
    behavior, timing, hardware, pipeline = inventories()
    payload = {
        "identity": IDENTITY,
        "recovery": {
            "previous_identity": PREVIOUS_IDENTITY,
            "restoration_owner": "shared runtime",
            "activation": "only after exact original-evidence restoration is unavailable",
            "historical_receipts": "retained unchanged; excluded from this chain",
            "fresh_dependency_chain": [
                "behavior-shard-icarus-154", "quartus-sta-shard-178",
                "quartus-sta-shard-166",
            ],
        },
        "behavior": [
            {"configuration": c.parameters(), "scenarios": [
                {"id": index, **record, "frames": 3}
                for index, record in enumerate(scenario_records(c))
            ]} for c in behavior
        ],
        "parameters": parameter_cases(), "parameter_partition_count_per_simulator": 7,
        "pipeline": pipeline, "timing": [c.parameters() for c in timing],
        "hardware": [c.parameters() for c in hardware],
        "first_behavior": ["icarus", 154], "first_timing": [178, 166],
        "first_hardware": 10, "icarus_scenario_processes": True,
        "icarus_max_complete_cycles": 500000, "icarus_duration_exclusive_s": 60,
        "child_timeout_minimum_s": 3600, "check_patrol_s": 2700,
    }
    return {"identity": IDENTITY, "sha256": canonical_hash(payload)}


def check_definitions():
    """Return all 602 exact supplemental checks, ordered before aggregators.

    Requires Coordinator/Reviewer append-only admission; calling this function
    creates no outputs, runs no tool and changes no shared-runtime state.
    """
    behavior, timing, hardware, pipeline = inventories()
    definitions = []

    def add(check, index, simulator=None, configuration=None, predecessor=None, cases=None):
        ident = f"{check}-{simulator + '-' if simulator else ''}{index}"
        argv = ["python3", "tests/verify.py", "--check", check]
        if simulator:
            argv += ["--simulator", simulator]
        argv += ["--shard", str(index)]
        if simulator:
            tools = [name for name, _ in SIM_TOOLS[simulator]]
        else:
            tools = [name for name, _ in PHYSICAL_TOOLS]
            if check == "board-hil-shard":
                tools.append("quartus_pgm")
        prefix = partition_output_prefix(check, index, simulator)
        spec = {
            "id": ident, "check": check, "shard": index, "simulator": simulator,
            "argv": argv, "inputs": ["rtl/*.sv", "design/*.md", "fpga/*", "tests/*.py"],
            "outputs": [] if prefix is None else [f"evidence/checks/{prefix}-*/"],
            "requires": ["python3", *tools],
            "timeout_s": 2700, "max_output_bytes": 1048576,
            "depends_on": [] if predecessor is None else [predecessor],
        }
        if configuration is not None:
            spec["configuration"] = configuration
        if cases is not None:
            spec["case_ids"] = cases
        definitions.append(spec)
        return ident

    anchor = add("behavior-shard", 154, "icarus", behavior[154].parameters())
    for simulator in SIM_TOOLS:
        for index, c in enumerate(behavior):
            if (simulator, index) != ("icarus", 154):
                add("behavior-shard", index, simulator, c.parameters(), anchor)
        for index in range(7):
            add("parameter-shard", index, simulator, predecessor=anchor,
                cases=[case["id"] for case in parameter_cases() if case["id"] % 7 == index])
        for index, (bits, channels, frac) in enumerate(pipeline):
            add("pipeline-shard", index, simulator,
                {"PIXEL_BITS": bits, "CHANNELS": channels, "PHASE_FRAC": frac}, anchor)
    first = add("quartus-sta-shard", 178, configuration=timing[178].parameters(),
                predecessor=anchor)
    second = add("quartus-sta-shard", 166, configuration=timing[166].parameters(),
                 predecessor=first)
    for index, c in enumerate(timing):
        if index not in (178, 166):
            add("quartus-sta-shard", index, configuration=c.parameters(), predecessor=second)
    previous = anchor
    for index in [10, *[index for index in range(48) if index != 10]]:
        previous = add("board-hil-shard", index, configuration=hardware[index].parameters(),
                       predecessor=previous)
    require(len(definitions) == 602 and len({d["id"] for d in definitions}) == 602,
            "Supplemental check inventory is incomplete or duplicated")
    return definitions


def spec_for(check, shard, simulator):
    matches = [spec for spec in check_definitions()
               if (spec["check"], spec["shard"], spec["simulator"]) == (check, shard, simulator)]
    require(len(matches) == 1, "Invalid partition index/simulator selection")
    return matches[0]


def aggregate_dependencies():
    return {
        gate: [spec["id"] for spec in check_definitions() if spec["check"] in checks]
        for gate, checks in (
            ("behavior", ("behavior-shard", "parameter-shard", "pipeline-shard")),
            ("quartus-sta", ("quartus-sta-shard",)),
            ("board-hil", ("board-hil-shard",)),
        )
    }


def execution_order(definitions):
    """Linear advisory order for consumers without dependency-graph support."""
    by_id = {spec["id"]: spec for spec in definitions}
    priority = (
        "behavior-shard-icarus-154", "quartus-sta-shard-178",
        "quartus-sta-shard-166", "board-hil-shard-10",
    )
    require(len(by_id) == len(definitions), "Duplicate partition scheduling identity")
    require(set(priority) <= set(by_id), "Missing recovery priority partition")
    ordered = [by_id[ident] for ident in priority]
    ordered.extend(spec for spec in definitions if spec["id"] not in priority)
    steps, emitted = [], set()
    for spec in ordered:
        require(set(spec["depends_on"]) <= emitted,
                f"Execution order precedes required dependency: {spec['id']}")
        steps.append({"id": spec["id"], "argv": spec["argv"]})
        emitted.add(spec["id"])
    dependencies = aggregate_dependencies()
    for gate in (
        "behavior", "defect-sensitivity", "synthesis", "rtl-timing-lint",
        "quartus-sta", "board-hil", "portable-reproduction",
    ):
        require(set(dependencies.get(gate, [])) <= emitted,
                f"Execution order omits required partitions for {gate}")
        steps.append({"id": gate, "argv": [
            "python3", "tests/verify.py", "--check", gate,
        ]})
    return steps


def scheduling_plan():
    """Export with --scheduling-plan; no evidence or runtime admission is changed.

    Check records and dependency edges are separate so a consumer need not
    reinterpret inventory annotations as executable-contract fields. Only the
    currently admitted partitions declare output selectors; others remain empty.
    The CLI rejects undeclared output contracts before allocating evidence
    or launching tools; this inventory is not execution authorization.
    The shared runtime must independently admit and schedule this advisory DAG.
    """
    definitions = check_definitions()
    fields = ("id", "argv", "inputs", "outputs", "requires",
              "timeout_s", "max_output_bytes")
    return {
        "advisory_only": True,
        "execution_owner": "shared runtime",
        "admission_status": "not inspected; independent admission required",
        "completion_status": "not inspected; no passing results asserted",
        "scheduling_contract": contract(),
        "recovery_activation": (
            "The shared runtime must first attempt exact restoration of the original "
            "behavior predecessor and its artifacts. Only if unavailable, schedule "
            "this call-0128 v2 chain from a fresh anchor. This export neither "
            "attempts restoration nor adjudicates runtime integrity failures."
        ),
        "partition_checks": [
            {key: spec[key] for key in fields} for spec in definitions
        ],
        "partition_dependencies": {
            spec["id"]: spec["depends_on"] for spec in definitions
        },
        "existing_aggregate_dependencies": aggregate_dependencies(),
        "sequential_execution_order": execution_order(definitions),
        "sequential_execution_note": (
            "Advisory order only, not permission to execute or an assertion of admission. "
            "A consumer without DAG support must independently admit missing checks and "
            "resolve existing output-selector conflicts before scheduling this sequence. "
            "Stop on any failed prerequisite; never advance to dependent aggregators."
        ),
        "original_commands_remain_mandatory": [
            ["python3", "tests/verify.py", "--check", name] for name in (
                "behavior", "defect-sensitivity", "synthesis", "rtl-timing-lint",
                "quartus-sta", "board-hil", "portable-reproduction",
            )
        ],
        "first_command": definitions[0]["argv"],
        "output_admission_note": (
            "Anchor 154 and timing 178 retain their admitted broad selectors. "
            "Timing 166 uses evidence/checks/recovery-v2-quartus-sta-shard-166-*/. "
            "Icarus behavior 0..167 except 154 and Verilator behavior 0..167, "
            "plus parameter 0..6 and pipeline 0..5 on both simulators, "
            "use their exact recovery-v2 selectors. "
            "All remaining partition outputs remain empty pending append-only admission; "
            "this export does not authorize their execution or modify runtime records."
        ),
    }


def prerequisite_plan(gate, completed):
    """Describe dependency order, without admitting or executing any command."""
    definitions = check_definitions()
    known = {spec["id"]: spec for spec in definitions}
    require(set(completed) <= set(known), "Unknown completed partition in scheduling plan")
    if gate in known:
        mandatory = known[gate]["depends_on"]
    else:
        require(gate in aggregate_dependencies(), "Unknown prerequisite target")
        mandatory = aggregate_dependencies()[gate]
    required = set()

    def include(ident):
        if ident in required:
            return
        required.add(ident)
        for predecessor in known[ident]["depends_on"]:
            include(predecessor)

    for ident in mandatory:
        include(ident)
    missing = [spec for spec in definitions
               if spec["id"] in required and spec["id"] not in completed]
    candidates = [spec for spec in missing if set(spec["depends_on"]) <= set(completed)]
    return {
        "advisory_only": True,
        "execution_owner": "shared runtime",
        "admission_status": "not inspected; each command requires independent admission",
        "gate": gate,
        "mandatory_partitions": mandatory,
        "required_predecessors": [spec["id"] for spec in definitions
                                  if spec["id"] in required and spec["id"] not in mandatory],
        "missing_partitions": [spec["id"] for spec in missing],
        "candidate_checks": candidates,
        "aggregate_dependencies": aggregate_dependencies(),
    }


def configuration(spec):
    return Config(*(spec["configuration"][key] for key in Config().parameters()))


def tools_for(e, spec):
    tools = SIM_TOOLS[spec["simulator"]] if spec["simulator"] else PHYSICAL_TOOLS
    if spec["check"] == "board-hil-shard":
        tools = (*tools, ("quartus_pgm", ("--version",)))
    for name, version in tools:
        if name not in e.record.get("tools", {}):
            e.tool(name, version)


def safe_artifact(receipt, name):
    path = Path(name)
    require(not path.is_absolute() and ".." not in path.parts,
            f"Artifact path must be project-relative: {name}")
    path = ROOT / path
    require(path.is_file() and path.resolve().is_relative_to(receipt.parent.resolve()),
            f"Missing artifact or artifact outside its exclusive output: {name}")
    return path


def artifact_hashes(directory):
    manifest = {}
    for path in sorted(directory.rglob("*")):
        if (not path.is_file() or path == directory / "receipt.json"
                or "sources" in path.relative_to(directory).parts
                or (path.suffix not in ARTIFACT_SUFFIXES and path.name != "simulation")):
            continue
        require(path.resolve().is_relative_to(directory.resolve()),
                f"Artifact symlink escapes its exclusive output: {path}")
        manifest[path.relative_to(ROOT).as_posix()] = file_hash(path)
    return manifest


def select_successes(records, current_sources, current_contract, known):
    found, excluded = {}, []
    for path, record in records:
        if record.get("scheduling_contract") != current_contract:
            excluded.append({"receipt": path.relative_to(ROOT).as_posix(),
                             "reason": "different scheduling contract; historical evidence retained"})
            continue
        if record.get("source_sha256") != current_sources:
            excluded.append({"receipt": path.relative_to(ROOT).as_posix(),
                             "reason": "stale sources"})
            continue
        if record.get("status") != "passed":
            excluded.append({"receipt": path.relative_to(ROOT).as_posix(),
                             "reason": record.get("status")})
            continue
        ident = record.get("partition", {}).get("id")
        require(ident in known, f"Unknown successful partition: {path}")
        require(ident not in found,
                f"Duplicate successful partition {ident}: {path}; no newest-wins selection")
        found[ident] = (path, record)
    return found, excluded


def successes(e):
    """Only complete, source-current successes are eligible; never newest-wins."""
    known = {spec["id"] for spec in check_definitions()}
    cache = getattr(e, "_partition_headers", {})
    e._partition_headers = cache

    def headers():
        for path in partition_receipts(ROOT / "evidence" / "checks"):
            stat = path.stat()
            identity = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            if path not in cache or cache[path][0] != identity:
                record = json.loads(path.read_text())
                cache[path] = (identity, {key: record.get(key) for key in HEADER_KEYS})
            yield path, cache[path][1]

    records = headers()
    found, excluded = select_successes(records, e.record["source_sha256"], contract(), known)
    e.record["excluded_partition_receipts"] = excluded
    e.save()
    return found


def load_success(found, ident):
    path, header = found[ident]
    record = json.loads(path.read_text())
    require({key: record.get(key) for key in HEADER_KEYS} == header,
            f"Completed partition changed during aggregation: {path}")
    return path, record


def validate_receipt(e, path, record, spec):
    require(record.get("partition") == spec and record.get("check") == spec["check"],
            f"Partition identity/inventory mismatch: {path}")
    prefix = partition_output_prefix(spec["check"], spec["shard"], spec["simulator"])
    require(prefix is not None, f"Partition receipt has no admitted output selector: {path}")
    require(path.parent.parent == ROOT / "evidence" / "checks"
            and re.fullmatch(re.escape(prefix) + r"-[0-9a-f]{32}", path.parent.name),
            f"Partition receipt outside admitted exclusive output: {path}")
    require(record.get("scheduling_contract") == contract()
            and record.get("source_sha256") == hashes()
            and record.get("sources_unchanged") is True
            and record.get("acceptance_evidence") is True
            and record.get("status") == "passed" and record.get("end"),
            f"Receipt is not a completed current admitted partition: {path}")
    require(record.get("argv") == ["python3", *spec["argv"][1:]],
            f"Executed partition command differs from inventory: {path}")
    for name, digest in record["source_sha256"].items():
        snapshot = path.parent / "sources" / name
        require(snapshot.is_file() and file_hash(snapshot) == digest,
                f"Partition source snapshot differs: {snapshot}")
    manifest = record.get("artifact_sha256")
    require(isinstance(manifest, dict) and manifest, f"Artifact binding absent: {path}")
    require(manifest == artifact_hashes(path.parent), f"Partition artifacts changed: {path}")
    commands = record.get("commands", [])
    require(commands, f"No actual commands: {path}")
    for command in commands:
        require(command.get("end") and command.get("duration_s", -1) >= 0
                and command.get("exit_status") in command.get("expected_exit_status", [])
                and not command.get("timed_out") and not command.get("launch_error"),
                f"Unfinished, timed-out or unexpected child exit: {path}")
        for key in ("stdout", "stderr"):
            safe_artifact(path, command[key])
            require(command[key] in manifest, "Raw command output not hash-bound")
    require(set(record.get("tools", {})) == set(spec["requires"]),
            f"Tool inventory differs from required tools: {path}")
    for name, identity in record["tools"].items():
        version_command = commands[record["tool_commands"][name]]
        require(version_command["requested_argv"] == [name, *identity["version_args"]]
                and version_command["exit_status"] == 0,
                f"Tool version command binding differs: {name}")
        for key in ("stdout", "stderr"):
            raw = safe_artifact(path, version_command[key]).read_text(errors="replace")
            require(hashlib.sha256(raw.encode()).hexdigest() == identity[f"{key}_sha256"],
                    f"Tool version raw output differs: {name}")
        if name not in e.record.get("tools", {}):
            e.tool(name, tuple(identity["version_args"]))
        require(e.record["tools"][name] == identity, f"Tool identity changed: {name}")
    duration = (datetime.datetime.fromisoformat(record["end"])
                - datetime.datetime.fromisoformat(record["start"])).total_seconds()
    partition_start(record)
    require(0 <= duration < 2700, f"Completed partition exceeded check tier: {path}")


def partition_start(record):
    """Keep invocation time intact when dependencies execute inside an entrypoint."""
    boundary = record.get("partition_execution")
    if boundary is None:
        return datetime.datetime.fromisoformat(record["start"])
    start = datetime.datetime.fromisoformat(boundary["start"])
    require(datetime.datetime.fromisoformat(record["start"]) <= start,
            "Partition execution precedes invocation")
    if record.get("end"):
        require(start <= datetime.datetime.fromisoformat(record["end"]),
                "Partition execution follows receipt completion")
    offset = boundary["command_offset"]
    commands = record.get("commands", [])
    require(type(offset) is int and 0 <= offset <= len(commands),
            "Invalid partition execution command boundary")
    for index, command in enumerate(commands):
        if index < offset:
            ordered = datetime.datetime.fromisoformat(command["end"]) <= start
        else:
            ordered = datetime.datetime.fromisoformat(command["start"]) >= start
        require(ordered, "Command crosses partition execution boundary")
    return start


def validate_dependencies(e, found, identities, *, validated=None):
    """Validate the entire retained prerequisite chain before consuming it."""
    specs = {spec["id"]: spec for spec in check_definitions()}
    validated = {} if validated is None else validated

    def visit(ident):
        require(ident in found, f"Missing predecessor success {ident}")
        if ident in validated:
            load_success(found, ident)
            return
        path, record = load_success(found, ident)
        spec = specs[ident]
        validate_receipt(e, path, record, spec)
        expected_links = []
        for predecessor in spec["depends_on"]:
            visit(predecessor)
            predecessor_path, predecessor_record = found[predecessor]
            require(datetime.datetime.fromisoformat(predecessor_record["end"])
                    <= partition_start(record),
                    "Dependency order was not honored")
            expected_links.append({
                "id": predecessor, "receipt": e.relative(predecessor_path),
                "sha256": file_hash(predecessor_path),
            })
        require(record.get("predecessors") == expected_links,
                "Explicit predecessor receipt binding differs")
        validated[ident] = validate_partition_result(e, path, record, spec)

    for ident in identities:
        visit(ident)
    return validated


def predecessor_links(e, spec, found):
    validate_dependencies(e, found, spec["depends_on"])
    links = []
    for ident in spec["depends_on"]:
        path, record = load_success(found, ident)
        require(datetime.datetime.fromisoformat(record["end"])
                <= partition_start(e.record),
                "Partition predecessor did not complete before partition execution")
        links.append({"id": ident, "receipt": e.relative(path), "sha256": file_hash(path)})
    return links


def run_partition(e, check, shard, simulator):
    spec = spec_for(check, shard, simulator)
    e.record["partition"] = spec
    e.record["scheduling_contract"] = contract()
    e.save()
    if check == "behavior-shard" and (simulator, shard) == ("icarus", 154):
        e.assertion("first behavior index", spec["shard"], 154)
        e.assertion("first behavior full parameter tuple",
                    list(spec["configuration"].values()), [2, 7, 3, 4096, 8, 3, 16, 0])
        e.assertion("scheduling contract identity", e.record["scheduling_contract"]["identity"],
                    IDENTITY)
    found = successes(e)
    require(spec["id"] not in found, f"Partition already has a current success: {spec['id']}")
    found = execute_prerequisites(e, spec["id"], found, workers=1)
    e.record["partition_execution"] = {
        "start": stamp(), "command_offset": len(e.record["commands"]),
    }
    e.record["predecessors"] = predecessor_links(e, spec, found)
    tools_for(e, spec)
    # Predecessor validation may need other tools. Only this partition's tools
    # form its tool binding; predecessor tool bindings remain in their receipts.
    e.record["tools"] = {name: e.record["tools"][name] for name in spec["requires"]}
    e.record["tool_commands"] = {
        name: e.record["tool_commands"][name] for name in spec["requires"]}
    e.save()
    behavior, timing, hardware, pipeline = inventories()
    if check == "behavior-shard":
        from simulation import execute_partition
        c = behavior[shard]
        result = execute_partition(e, simulator, c, e.path / f"{simulator}-{c.label}")
    elif check == "parameter-shard":
        from simulation import parameter_checks
        result = parameter_checks(e, simulator, shard=shard)
    elif check == "pipeline-shard":
        from simulation import pipeline_checks
        bits, channels, frac = pipeline[shard]
        result = pipeline_checks(e, simulator, formats=((bits, channels),), fractions=(frac,))
    elif check == "quartus-sta-shard":
        from physical import compile_and_sta
        c = timing[shard]
        result = compile_and_sta(e, c, e.path / c.label)
    else:
        from physical import board_configuration
        result = board_configuration(e, hardware[shard])
    e.record["partition_result"] = result
    e.record["artifact_sha256"] = artifact_hashes(e.path)
    e.save()
    require(spec["id"] not in successes(e), "Concurrent duplicate success; diagnosis required")


def read_raw(path, name):
    return safe_artifact(path, name).read_text()


def validate_compile(path, record, simulator, parameters, tb_path, tb_text):
    require(tb_path.read_text() == tb_text, "Retained transactor differs from current checker")
    commands = [command for command in record["commands"]
                if str(tb_path) in command["requested_argv"]]
    if simulator == "verilator" and not commands:
        from simulator_builds import validate_reuse
        validate_reuse(path, record, parameters, tb_path)
        return
    require(len(commands) == 1, "Missing or duplicated simulator compilation")
    command = commands[0]
    argv = command["requested_argv"]
    prefix = "-Ptb." if simulator == "icarus" else "-G"
    require(argv[0] == ("iverilog" if simulator == "icarus" else "verilator")
            and command["exit_status"] == 0
            and all(f"{prefix}{key}={value}" in argv for key, value in parameters.items())
            and all(str(source) in argv for source in sorted((ROOT / "rtl").glob("*.sv"))),
            "Actual compiled source/parameter tuple differs from declared partition")


def validate_behavior(path, record, spec):
    from simulation import STREAM_TB, parse_stream_output
    c = configuration(spec)
    result = record["partition_result"]
    directory = path.parent / f"{spec['simulator']}-{c.label}"
    validate_compile(path, record, spec["simulator"], c.parameters(),
                     directory / "tb.sv", STREAM_TB)
    scenarios = list(scenario_records(c))
    parsed = []
    if spec["simulator"] == "icarus":
        observations = result["scenario_observations"]
        require([obs["id"] for obs in observations] == list(range(len(scenarios))),
                "Icarus scenario process inventory incomplete")
        for ident, (scenario, observation) in enumerate(zip(scenarios, observations)):
            require(observation["duration_s"] < 60
                    and observation["complete_cycles"] <= 500000,
                    "Retained Icarus scenario violates interpreted tier")
            model_path = directory / f"scenario-{ident:02d}" / "model.json"
            models = json.loads(model_path.read_text())
            require(len(models) == 1 and
                    {key: models[0][key] for key in ("id", *scenario, "frames")}
                    == {"id": ident, **scenario, "frames": 3},
                    "Split scenario lost its global identity")
            raw = read_raw(path, observation["raw_output"])
            child = next((command for command in record["commands"]
                          if command["stdout"] == observation["raw_output"]), None)
            require(child is not None and child["exit_status"] == 0
                    and child["duration_s"] == observation["duration_s"],
                    "Scenario duration is not its actual retained process duration")
            run = parse_stream_output(c, "icarus", [scenario], models, raw)
            require(run["complete_cycles"] == observation["complete_cycles"],
                    "Complete scenario clock count differs from raw observation")
            parsed.extend(run["cases"])
    else:
        models = json.loads((directory / "model.json").read_text())
        require([{key: model[key] for key in ("id", "pattern", "stall", "seed", "abort", "frames")}
                 for model in models] == [
                     {"id": ident, **scenario, "frames": 3}
                     for ident, scenario in enumerate(scenarios)],
                "Verilator scenario inventory incomplete")
        parsed = parse_stream_output(c, "verilator", scenarios, models,
                                     read_raw(path, result["raw_output"]))["cases"]
    require(parsed == result["cases"], "Behavior ledger differs from original raw observations")
    require(result["configuration"] == c.parameters() and result["simulator"] == spec["simulator"],
            "Behavior ledger configuration differs")
    return {**result, "cases": [
        {key: value for key, value in case.items() if key != "observed_row_runs"}
        for case in parsed
    ]}


def validate_parameters(path, record, spec):
    result = record["partition_result"]
    expected = [case for case in parameter_cases() if case["id"] in spec["case_ids"]]
    require([{key: case[key] for key in ("id", "legal", "parameters")}
             for case in result["cases"]] == expected, "Parameter partition inventory differs")
    require(result["simulator"] == spec["simulator"]
            and result["legal_elaborations"] == sum(c["legal"] for c in expected)
            and result["invalid_diagnostics"] == sum(not c["legal"] for c in expected),
            "Parameter comparison counts differ")
    for case in result["cases"]:
        indices = case["command_indices"]
        require(indices and len(set(indices)) == len(indices)
                and all(type(index) is int and 0 <= index < len(record["commands"])
                        for index in indices),
                "Invalid parameter command indices")
        commands = [record["commands"][index] for index in case["command_indices"]]
        if not case["legal"]:
            validate_invalid_parameter(path, record, spec["simulator"], case)
            continue
        require(len(commands) == 1, "Missing parameter tool execution")
        compile_command = commands[0]["requested_argv"]
        prefix = "-Pswage_s1_top." if spec["simulator"] == "icarus" else "-G"
        require(all(f"{prefix}{key}={value}" in compile_command
                    for key, value in case["parameters"].items()),
                "Parameter configuration was not supplied to actual compiler")
        require(commands[0]["exit_status"] == 0, "Parameter elaboration failed")
    return result


def validate_invalid_parameter(path, record, simulator, case):
    from simulation import INVALID_TB

    directory = path.parent / f"parameters-{simulator}" / f"invalid-{case['id']}"
    tb = directory / "tb.sv"
    commands = [record["commands"][index] for index in case["command_indices"]]
    scoped = {**record, "commands": commands[:-1]}
    validate_compile(path, scoped, simulator, case["parameters"], tb, INVALID_TB)
    if commands[:-1]:
        require(len(commands) == 2, "Duplicated invalid-parameter compilation")
        binary = directory / ("simulation.vvp" if simulator == "icarus" else "obj/simulation")
        if simulator == "icarus":
            prefix = ["iverilog", "-g2012", "-Wall", "-s", "tb"]
            suffix = ["-o", str(binary)]
            parameter_prefix = "-Ptb."
        else:
            prefix = ["verilator", "--binary", "--timing", "--assert", "-Wno-fatal",
                      "--top-module", "tb", "--Mdir", str(binary.parent), "-o", "simulation"]
            suffix = []
            parameter_prefix = "-G"
        suffix += [str(source) for source in sorted((ROOT / "rtl").glob("*.sv"))] + [str(tb)]
        compiler = commands[0]
        argv = compiler["requested_argv"]
        require(argv[:len(prefix)] == prefix and argv[-len(suffix):] == suffix
                and sorted(argv[len(prefix):-len(suffix)]) == sorted(
                    f"{parameter_prefix}{key}={value}" for key, value in case["parameters"].items())
                and compiler["cwd"] == "."
                and not compiler["timed_out"] and not compiler.get("launch_error")
                and compiler.get("end")
                and compiler.get("duration_s", -1) >= 0,
                "Invalid-parameter compiler command differs")
    else:
        require(simulator == "verilator" and len(commands) == 1,
                "Invalid-parameter build reuse requires exactly one diagnostic execution")
        binary = directory / "simulation"
    require(binary.is_file() and (simulator == "icarus" or binary.stat().st_mode & 0o111)
            and record["artifact_sha256"].get(
        binary.relative_to(ROOT).as_posix()) == file_hash(binary),
        "Invalid-parameter executable artifact changed")
    child = commands[-1]
    expected_run = ["vvp", str(binary)] if simulator == "icarus" else [str(binary)]
    require(child["requested_argv"] == expected_run
            and child["cwd"] == directory.relative_to(ROOT).as_posix()
            and not child["timed_out"] and not child.get("launch_error") and child.get("end")
            and child.get("duration_s", -1) >= 0
            and child["exit_status"] in (1, -6, 134)
            and child["exit_status"] in child["expected_exit_status"],
            "Missing completed invalid-parameter diagnostic execution of the compiled binary")
    text = read_raw(path, child["stdout"]) + read_raw(path, child["stderr"])
    require("SWAGE_S1 PARAMETER ERROR" in text, "Missing explicit invalid diagnostic")


def validate_pipeline(path, record, spec):
    from simulation import PIPELINE_TB
    results = record["partition_result"]
    require(len(results) == 1, "Pipeline partition must contain one format/precision tuple")
    result = results[0]
    params = spec["configuration"]
    require((result["bits"], result["channels"], result["frac"], result["simulator"]) == (
        params["PIXEL_BITS"], params["CHANNELS"], params["PHASE_FRAC"], spec["simulator"]),
        "Pipeline configuration differs")
    directory = path.parent / (
        f"pipeline-{spec['simulator']}-{result['bits']}-{result['channels']}-{result['frac']}")
    validate_compile(path, record, spec["simulator"], params,
                     directory / "tb.sv", PIPELINE_TB)
    matches = re.findall(r"^PIPELINE (\d+) (\d+) (\d+)$",
                         read_raw(path, result["raw_output"]), re.MULTILINE)
    require(len(matches) == 1, "Pipeline observations incomplete")
    values = tuple(map(int, matches[0]))
    require(values == (result["compared"], result["stalled"], result["simultaneous"])
            and values[0] == 256 and values[1] >= 32 and values[2] > 0,
            "Six advancing stages/overlap/stall not established")
    return result


def validate_hardware(e, path, record, spec):
    from physical import relative_input, validate_sta
    from reference import image, integer_reference

    config_name = os.environ.get("SWAGE_BOARD_CONFIG", "board/verification.json")
    board = json.loads(relative_input(config_name).read_text())
    from physical_evidence import DEVICE
    require(board.get("device") == DEVICE, "Hardware board target differs")
    require(isinstance(board.get("dependencies"), list) and board["dependencies"],
            "Physical board/IP dependency inventory is incomplete")
    names = [config_name, *(board[key] for key in (
        "board-system", "board-settings", "board-sdc")), *board["dependencies"]]
    require(record["board_input_sha256"] == {
        name: file_hash(relative_input(name)) for name in names
    }, "Physical integration inputs differ from programmed configuration")
    c = configuration(spec)
    directory = path.parent / c.label
    result = record["partition_result"]
    require(result["configuration"] == c.parameters(), "Hardware configuration differs")
    require(validate_sta(e, c, directory, board, execution=record) == result["timing"],
            "Hardware fitted timing ledger differs from reports")
    sof = directory / "output_files" / "swage_s1.sof"
    require(file_hash(sof) == result["programmed_sof_sha256"],
            "Programmed image binding changed")
    program = [command for command in record["commands"] if command["requested_argv"] == [
        "quartus_pgm", "-c", board["cable"], "-m", "JTAG", "-o",
        f"p;{e.relative(sof)}@{int(board['jtag_index'])}",
    ]]
    require(len(program) == 1 and program[0]["exit_status"] == 0,
            "No unique successful programming of current image")
    require(program[0].get("end") and not program[0].get("timed_out")
            and not program[0].get("launch_error"),
            "Programming execution is incomplete")
    program_text = "\n".join(read_raw(path, program[0][key])
                             for key in ("stdout", "stderr"))
    require("successful" in program_text.lower()
            and re.search(r"\bError(?:\s*\(|:)", program_text) is None,
            "Raw programmer output does not establish successful programming")
    build_id = result["timing"]["build_id"]
    identity = json.loads((directory / "hardware-identity.json").read_text())
    require(identity == {
        "abi": 0x53573101, "build_id": f"{int(build_id, 16):064x}",
        "source_shape": c.sh << 16 | c.sw, "destination_shape": c.dh << 16 | c.dw,
        "format": c.mode << 24 | c.frac << 16 | c.channels << 8 | c.bits,
    }, "Actual hardware registers differ from current source/build/parameter identity")
    links = record["physical_link_observations"]
    require([link["label"] for link in links] == [
        "initial", f"programmed-{c.label}",
        *[f"pass-{index}-{c.label}" for index in range(3)],
    ], "Physical link observation inventory incomplete")
    for link in links:
        require(link["current_link_speed"].split()[0] == "8.0"
                and int(link["current_link_width"]) == 8
                and int(link["vendor"], 0) == int(board["vendor_id"], 0)
                and int(link["device"], 0) == int(board["device_id"], 0),
                "Raw physical identity/link observation invalid")
    require([p["pass"] for p in result["passes"]] == [0, 1, 2],
            "Three consecutive hardware passes incomplete")
    digests = []
    for run in result["passes"]:
        events = [json.loads(line) for line in read_raw(path, run["raw_transport"]).splitlines()]
        cursor = pixels_count = 0
        digest = hashlib.sha256()
        for pattern in ("random", "horizontal", "vertical", "checker", "full", "impulse"):
            inputs, outputs = [], []
            for frame in range(3):
                pixels = image(c, pattern, 2026, frame)
                inputs.extend(p | (int(index == 0) << 24)
                              | (int(index % c.sw == c.sw-1) << 25)
                              for index, p in enumerate(pixels))
                outputs.extend(p | (sof << 24) | (eol << 25)
                               for p, sof, eol in integer_reference(c, pixels))
            reset = events[cursor]
            cursor += 1
            require(0 < reset["reset_aborted_inputs"] <= len(inputs)
                    and reset["held_status"] & 2 != 0
                    and reset["aborted_input_count"] == reset["reset_aborted_inputs"]
                    and reset["aborted_output_count"] == 0
                    and reset["post_reset"] == {"inputs": 0, "outputs": 0, "pending": 0},
                    "Physical partial-frame reset raw observations invalid")
            ni = no = holds = conservation = 0
            while cursor < len(events) and "reset_aborted_inputs" not in events[cursor]:
                event = events[cursor]
                cursor += 1
                require(conservation == 0, "Hardware trace continued after conservation check")
                if "input" in event:
                    require(ni < len(inputs) and event == {"input": ni, "word": inputs[ni]},
                            "Physical input transport is incomplete, duplicated or changed")
                    ni += 1
                elif "output" in event:
                    require(no < len(outputs) and event == {
                        "output": no, "actual": outputs[no], "expected": outputs[no],
                    }, "Actual returned hardware pixel/sideband differs from independent oracle")
                    digest.update(struct.pack("<I", event["actual"]))
                    no += 1
                elif "conservation" in event:
                    require(event == {"conservation": {
                        "inputs": len(inputs), "outputs": len(outputs), "pending_output": 0,
                    }} and (ni, no) == (len(inputs), len(outputs)),
                        "Actual physical conservation counters differ from completed transport")
                    conservation += 1
                else:
                    require(set(event) == {"hold_seconds", "output_count", "after_status",
                                           "after_output_count"}
                            and event["hold_seconds"] >= 32e-8
                            and event["output_count"] == event["after_output_count"] == no
                            and event["after_status"] & 0xa == 0xa,
                            "Host-driven physical output stall not observed")
                    holds += 1
            require((ni, no, holds, conservation) == (len(inputs), len(outputs), 1, 1),
                    "Physical pattern transport/hold coverage incomplete")
            pixels_count += no
        require(cursor == len(events) and run["compared_pixels"] == pixels_count
                and run["frames"] == 18 and run["host_stalls"] == 6
                and run["build_id"] == build_id and run["returned_sha256"] == digest.hexdigest(),
                "Physical pass ledger differs from raw returned observations")
        digests.append(digest.hexdigest())
    require(len(set(digests)) == 1, "Consecutive hardware passes were not deterministic")
    return result


def validate_partition_result(e, path, record, spec):
    """Reparse observed results, including prerequisites outside a gate's inventory."""
    require(spec["check"] in SHARD_CHECKS, "Unknown partition result type")
    result = record.get("partition_result")
    if spec["check"] == "pipeline-shard":
        require(isinstance(result, list) and result,
                "Missing observed pipeline results; a receipt header is not evidence")
    else:
        require(isinstance(result, dict) and result,
                "Missing observed partition result; a receipt header is not evidence")
        if "configuration" in spec:
            require(result.get("configuration") == spec["configuration"],
                    "Observed scaler configuration missing or different")
    if spec["check"] == "behavior-shard":
        return validate_behavior(path, record, spec)
    if spec["check"] == "parameter-shard":
        return validate_parameters(path, record, spec)
    if spec["check"] == "pipeline-shard":
        return validate_pipeline(path, record, spec)
    if spec["check"] == "quartus-sta-shard":
        require(all(isinstance(result.get(key), list) and result[key]
                    for key in ("wns_setup_ns", "wns_hold_ns"))
                and isinstance(result.get("report_sha256"), dict) and result["report_sha256"],
                "Missing measured physical result: STA declarations are not fitted reports")
        from physical import validate_sta
        c = configuration(spec)
        result = validate_sta(e, c, path.parent / c.label, execution=record)
        require(result == record["partition_result"], "STA ledger differs from raw reports")
        return result
    require(spec["check"] == "board-hil-shard", "Unknown partition result type")
    require(isinstance(result.get("passes"), list) and result["passes"]
            and isinstance(result.get("programmed_sof_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", result["programmed_sof_sha256"]) is not None,
            "Missing measured physical result: HIL declarations are not programmed workloads")
    return validate_hardware(e, path, record, spec)


def execute_prerequisites(e, gate, found, *, workers=4):
    """Run only admitted dependency-ready commands, retaining every parent exit."""
    definitions = check_definitions()
    by_id = {spec["id"]: spec for spec in definitions}
    recovery = prerequisite_plan(gate, found)
    recovery_path = e.path / "prerequisite-plan.json"
    recovery_path.write_text(json.dumps(recovery, indent=2) + "\n")
    e.record["prerequisite_plan"] = {
        "path": e.relative(recovery_path), "sha256": file_hash(recovery_path),
    }
    needed = set(recovery["mandatory_partitions"]) | set(recovery["required_predecessors"])
    missing = needed - set(found)
    unadmitted = sorted(ident for ident in missing if not by_id[ident]["outputs"])
    e.record["unadmitted_prerequisites"] = unadmitted
    e.save()
    require(not unadmitted,
            f"{gate}: {len(unadmitted)} missing prerequisites require append-only admission "
            f"and exclusive output selectors before automatic execution: {unadmitted}")
    validated = validate_dependencies(e, found, needed & set(found))
    require(isinstance(workers, int) and 1 <= workers <= 4, "Invalid bounded worker count")
    pending = {spec["id"]: spec for spec in definitions if spec["id"] in missing}
    active = {}
    e.record["prerequisite_execution"] = {
        "workers": workers, "hardware_serialized": True, "launched": [], "completed": [],
    }
    execution = e.record["prerequisite_execution"]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while pending or active:
            for ident, spec in list(pending.items()):
                if len(active) >= workers:
                    break
                if not set(spec["depends_on"]) <= set(validated):
                    continue
                # Vendor compilation and programming never overlap another child.
                physical = spec["simulator"] is None
                if (physical and active) or any(
                        by_id[running]["simulator"] is None for running in active.values()):
                    continue
                execution["launched"].append(ident)
                e.save()
                future = pool.submit(e.run, spec["argv"], name=f"prerequisite-{ident}")
                active[future] = ident
                del pending[ident]
                if physical:
                    break
            require(active, f"Dependency graph made no progress: {sorted(pending)}")
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                ident = active.pop(future)
                # Settle already-running children before propagating a failure.
                future.result()
                found = successes(e)
                require(ident in found, f"Successful command lacks completed receipt: {ident}")
                validate_dependencies(e, found, [ident], validated=validated)
                execution["completed"].append(ident)
                e.save()
    return found


def aggregate(e, gate):
    """All expected partitions are mandatory; failed/interrupted ones never count."""
    wanted = {
        "behavior": ("behavior-shard", "parameter-shard", "pipeline-shard"),
        "quartus-sta": ("quartus-sta-shard",), "board-hil": ("board-hil-shard",),
    }[gate]
    definitions = check_definitions()
    specs = [spec for spec in definitions if spec["check"] in wanted]
    found = successes(e)
    found = execute_prerequisites(e, gate, found)
    missing = [spec["id"] for spec in specs if spec["id"] not in found]
    e.record["missing_partitions"] = missing
    e.record["scheduling_contract"] = contract()
    recovery = prerequisite_plan(gate, found)
    recovery_path = e.path / "prerequisite-plan.json"
    recovery_path.write_text(json.dumps(recovery, indent=2) + "\n")
    e.record["prerequisite_plan"] = {
        "path": e.relative(recovery_path), "sha256": file_hash(recovery_path),
    }
    e.save()
    candidates = recovery["candidate_checks"]
    next_command = " ".join(candidates[0]["argv"]) if candidates else "none"
    require(not missing, f"{gate}: {len(missing)} mandatory partitions missing; "
            f"next dependency command: {next_command}. "
            f"See {e.relative(recovery_path)}. Automatic admitted prerequisite execution "
            "did not produce the complete current receipt inventory.")
    ledger, links, parameters, pipelines = [], [], [], []
    validated = validate_dependencies(e, found, [spec["id"] for spec in specs])

    for spec in specs:
        path, record = load_success(found, spec["id"])
        if spec["check"] == "parameter-shard":
            parameters.append(validated[spec["id"]])
        elif spec["check"] == "pipeline-shard":
            pipelines.append(validated[spec["id"]])
        else:
            ledger.append(validated[spec["id"]])
        links.append({"id": spec["id"], "receipt": e.relative(path), "sha256": file_hash(path)})
    if gate == "behavior":
        from simulation import coverage_totals
        e.assertion("configuration/simulator partitions", len(ledger), 336)
        e.assertion("parameter partitions", len(parameters), 14)
        e.assertion("pipeline partitions", len(pipelines), 12)
        for simulator in SIM_TOOLS:
            cases = [case for run in parameters if run["simulator"] == simulator
                     for case in run["cases"]]
            e.assertion(f"{simulator} exact legal/illegal parameter inventory",
                        sorted(case["id"] for case in cases),
                        list(range(len(parameter_cases()))))
        coverage_totals(e, ledger)
    else:
        e.assertion("full physical configuration inventory", len(ledger),
                    192 if gate == "quartus-sta" else 48)
    e.record["predecessors"] = links
    e.record["parameter_results"] = parameters
    e.record["pipeline_results"] = pipelines
    name = {"behavior": "behavior", "quartus-sta": "sta", "board-hil": "hil"}[gate]
    (e.path / f"{name}-ledger.json").write_text(json.dumps(ledger, indent=2)+"\n")
    e.save()
    return ledger


def method_checks(e):
    """Advisory Python-only method tests, not RTL or acceptance execution."""
    from simulation import parse_stream_output

    require(e.record["acceptance_evidence"] is False,
            "Method fixtures must never be written as acceptance evidence")
    for source in sorted((ROOT / "tests").glob("*.py")):
        compile(source.read_text(), source.relative_to(ROOT).as_posix(), "exec")
    definitions = check_definitions()
    dependency_method_checks(e)
    orchestration_method_checks(e)
    discovery_method_checks(e)
    atomic_receipt_method_checks(e)
    from recovery_checks import parameter_provenance_method_checks
    parameter_provenance_method_checks(e)
    deps = aggregate_dependencies()
    e.assertion("exact supplemental inventory", len(definitions), 602)
    plan = scheduling_plan()
    fields = {"id", "argv", "inputs", "outputs", "requires",
              "timeout_s", "max_output_bytes"}
    e.assertion("export contains only executable check fields",
                all(set(spec) == fields for spec in plan["partition_checks"]), True)
    e.assertion("export preserves every exact executable command",
                [spec["argv"] for spec in plan["partition_checks"]],
                [spec["argv"] for spec in definitions])
    e.assertion("export preserves every dependency edge",
                plan["partition_dependencies"],
                {spec["id"]: spec["depends_on"] for spec in definitions})
    e.assertion("export retains original aggregate prerequisites",
                plan["existing_aggregate_dependencies"], deps)
    declared_outputs = {
        spec["id"]: spec["outputs"] for spec in plan["partition_checks"] if spec["outputs"]
    }
    expected_outputs = {
                    "behavior-shard-icarus-154": ["evidence/checks/behavior-shard-*/"],
                    "quartus-sta-shard-178": ["evidence/checks/quartus-sta-shard-*/"],
                    "quartus-sta-shard-166": [
                        "evidence/checks/recovery-v2-quartus-sta-shard-166-*/",
                    ],
    }
    for check, simulator, count in (
        ("behavior-shard", "icarus", 168), ("parameter-shard", "icarus", 7),
        ("pipeline-shard", "icarus", 6), ("behavior-shard", "verilator", 168),
        ("parameter-shard", "verilator", 7), ("pipeline-shard", "verilator", 6),
    ):
        for index in range(count):
            ident = f"{check}-{simulator}-{index}"
            if ident != "behavior-shard-icarus-154":
                expected_outputs[ident] = [f"evidence/checks/recovery-v2-{ident}-*/"]
    for index in (*range(8), *range(15, 20)):
        ident = f"quartus-sta-shard-{index}"
        expected_outputs[ident] = [f"evidence/checks/recovery-v2-{ident}-*/"]
    e.assertion("export preserves exactly the supplied admitted selectors",
                declared_outputs, expected_outputs)
    e.assertion("admitted selector denominator", len(declared_outputs), 377)
    e.assertion("every behavior prerequisite has an admitted output",
                all(spec["outputs"] for spec in definitions
                    if spec["id"] in deps["behavior"]), True)
    output_method_checks(e)
    steps = plan["sequential_execution_order"]
    e.assertion("linear schedule includes all partitions and seven original gates",
                len(steps), 609)
    e.assertion("linear schedule has no duplicate steps",
                len({step["id"] for step in steps}), len(steps))
    e.assertion("linear schedule prioritizes admitted recovery observations",
                [step["id"] for step in steps[:4]], [
                    "behavior-shard-icarus-154", "quartus-sta-shard-178",
                    "quartus-sta-shard-166", "board-hil-shard-10",
                ])
    e.assertion("linear schedule preserves exact partition commands",
                {step["id"]: step["argv"] for step in steps[:-7]},
                {spec["id"]: spec["argv"] for spec in definitions})
    e.assertion("linear schedule retains every unchanged original command",
                [step["argv"] for step in steps[-7:]],
                plan["original_commands_remain_mandatory"])
    for altered, diagnostic in (
        ([spec for spec in definitions if spec["id"] != "behavior-shard-icarus-154"],
         "Missing recovery priority partition"),
        ([*definitions, definitions[0]], "Duplicate partition scheduling identity"),
        ([{**spec, "depends_on": ["quartus-sta-shard-166"]}
          if spec["id"] == "quartus-sta-shard-178" else spec
          for spec in definitions], "Execution order precedes required dependency"),
    ):
        try:
            execution_order(altered)
        except AssertionError as error:
            require(diagnostic in str(error), f"Unexpected schedule diagnostic: {error}")
        else:
            raise AssertionError(f"Unsafe linear scheduling fixture accepted: {diagnostic}")
    out, _, _ = e.run(
        ["python3", "tests/verify.py", "--scheduling-plan"],
        name="read-only-scheduling-export",
    )
    e.assertion("fresh-process CLI emits exact advisory scheduling JSON",
                json.loads(out), plan)
    for options in (["--shard", "154"], ["--simulator", "icarus"],
                    ["--repair-of", "not-an-evidence-input"]):
        _, err, _ = e.run(
            ["python3", "tests/verify.py", "--scheduling-plan", *options],
            expected=(2,), name="reject-scheduling-execution-options",
        )
        require("--scheduling-plan does not accept execution/probe options" in err,
                "Scheduling export did not reject execution options explicitly")
    e.assertion("behavior aggregate dependencies", len(deps["behavior"]), 362)
    e.assertion("timing aggregate dependencies", len(deps["quartus-sta"]), 192)
    e.assertion("hardware aggregate dependencies", len(deps["board-hil"]), 48)
    emitted = set()
    for spec in definitions:
        require(set(spec["depends_on"]) <= emitted, "Dependency is scheduled after its consumer")
        emitted.add(spec["id"])
        require(spec_for(spec["check"], spec["shard"], spec["simulator"]) == spec,
                "CLI selector does not resolve its exact inventory entry")
    e.assertion("first exact command", definitions[0]["argv"], [
        "python3", "tests/verify.py", "--check", "behavior-shard",
        "--simulator", "icarus", "--shard", "154",
    ])
    e.assertion("first exact parameter tuple",
                list(definitions[0]["configuration"].values()), [2, 7, 3, 4096, 8, 3, 16, 0])
    e.assertion("first timing indices",
                [s["shard"] for s in definitions if s["check"] == "quartus-sta-shard"][:2],
                [178, 166])
    e.assertion("first hardware index",
                next(s["shard"] for s in definitions if s["check"] == "board-hil-shard"), 10)
    for gate in deps:
        plan = prerequisite_plan(gate, set())
        e.assertion(f"{gate} empty inventory exposes only anchor",
                    [spec["id"] for spec in plan["candidate_checks"]],
                    ["behavior-shard-icarus-154"])
        e.assertion(f"{gate} retains exact mandatory inventory",
                    plan["mandatory_partitions"], deps[gate])
        e.assertion(f"{gate} complete dependency closure has no missing entries",
                    prerequisite_plan(gate, set(plan["missing_partitions"]))["missing_partitions"],
                    [])
    anchor_done = {"behavior-shard-icarus-154"}
    for completed, expected in (
        (anchor_done, ["quartus-sta-shard-178"]),
        (anchor_done | {"quartus-sta-shard-178"}, ["quartus-sta-shard-166"]),
    ):
        e.assertion("timing frontier preserves admitted recovery order",
                    [spec["id"] for spec in
                     prerequisite_plan("quartus-sta", completed)["candidate_checks"]],
                    expected)
    e.assertion("hardware frontier starts with serialized partition ten",
                [spec["id"] for spec in
                 prerequisite_plan("board-hil", anchor_done)["candidate_checks"]],
                ["board-hil-shard-10"])
    e.assertion("hardware frontier advances only after predecessor",
                [spec["id"] for spec in prerequisite_plan(
                    "board-hil", anchor_done | {"board-hil-shard-10"})["candidate_checks"]],
                ["board-hil-shard-0"])
    try:
        prerequisite_plan("quartus-sta", {"unknown"})
    except AssertionError:
        unknown_rejected = True
    else:
        unknown_rejected = False
    e.assertion("unknown scheduler completion fixture rejected", unknown_rejected, True)
    for simulator in SIM_TOOLS:
        cases = [ident for spec in definitions if spec["check"] == "parameter-shard"
                 and spec["simulator"] == simulator for ident in spec["case_ids"]]
        e.assertion(f"{simulator} exact parameter inventory", sorted(cases),
                    list(range(len(parameter_cases()))))
    e.record["scheduling_contract"] = contract()
    (e.path / "proposed-checks.json").write_text(json.dumps({
        "advisory_only": True, "scheduling_contract": contract(),
        "append_only_checks": definitions, "existing_aggregate_dependencies": deps,
        "original_commands_remain_mandatory": [
            ["python3", "tests/verify.py", "--check", name] for name in (
                "behavior", "defect-sensitivity", "synthesis", "rtl-timing-lint",
                "quartus-sta", "board-hil", "portable-reproduction",
            )
        ],
    }, indent=2)+"\n")
    ident = definitions[0]["id"]
    fixture_path = e.path / "synthetic-only-receipt.json"
    success = {
        "scheduling_contract": contract(), "source_sha256": {"fixture": "current"},
        "status": "passed", "partition": {"id": ident},
    }
    known = {ident}
    old_contract = {**success, "scheduling_contract": {"identity": PREVIOUS_IDENTITY}}
    stale = {**success, "source_sha256": {"fixture": "stale"}}
    failed = {**success, "status": "failed"}
    interrupted = {**success, "status": "running"}
    records = [(fixture_path, item) for item in (old_contract, stale, failed, interrupted)]
    found, excluded = select_successes(records, {"fixture": "current"}, contract(), known)
    e.assertion("failed/interrupted/stale/legacy fixtures not eligible", len(found), 0)
    e.assertion("explicit exclusions include historical-contract fixtures", len(excluded), 4)
    e.assertion("historical success is explicitly excluded, not relinked",
                excluded[0]["reason"],
                "different scheduling contract; historical evidence retained")
    e.assertion("recovery uses independently admitted new identity", IDENTITY,
                "swage-s1-scheduling-recovery-call-0128-v2")
    rejected = 0
    for records in (
        [(fixture_path, success), (fixture_path, success)],
        [(fixture_path, {**success, "partition": {"id": "unknown"}})],
    ):
        try:
            select_successes(records, {"fixture": "current"}, contract(), known)
        except AssertionError:
            rejected += 1
    e.assertion("duplicate/unknown success fixtures rejected", rejected, 2)
    rejected = 0
    diagnostic_only = {
        "status": "passed", "requested_runs": 3,
        "runs": [{"stdout": "Read register 57 (0x00000039): 178 (0x000000b2)"}] * 3,
    }
    for spec in (definitions[0], next(s for s in definitions
                 if s["check"] == "quartus-sta-shard"), next(s for s in definitions
                 if s["check"] == "board-hil-shard")):
        for result in (None, {}, diagnostic_only):
            try:
                validate_partition_result(e, fixture_path,
                                          {"partition_result": result}, spec)
            except AssertionError:
                rejected += 1
    e.assertion("header-only and register-only scaler prerequisites rejected", rejected, 9)
    # Synthetic text tests parsing/accounting only. No simulator result is
    # created, no fixture is eligible for aggregation, and no RTL claim is made.
    scenario = {"pattern": "zero", "stall": 0, "seed": 17, "abort": 0}
    model = {"id": 9, **scenario, "frames": 3, "model_comparisons": []}
    raw = (
        "ROW 9 0 1\nROW 9 1 2\nROW 9 2 3\n"
        "RESULT 9 3 3 3 3 0 0 0 0 12\n"
        "SCENARIO_CLOCKS 9 32\nCOMPLETE 1\nTOTAL_CLOCKS 32\n"
    )
    run = parse_stream_output(Config(1, 1, 1, 1), "icarus", [scenario], [model], raw)
    e.assertion("synthetic global scenario ID preserved", run["cases"][0]["id"], 9)
    e.assertion("synthetic complete reset/quiet clock accounting", run["complete_cycles"], 32)
    e.assertion("observation ledger survives JSON round-trip", run, json.loads(json.dumps(run)))
    fixtures = [
        raw.replace("SCENARIO_CLOCKS 9 32\n", ""),
        raw.replace("TOTAL_CLOCKS 32", "TOTAL_CLOCKS 31"),
        raw.replace("SCENARIO_CLOCKS 9 32", "SCENARIO_CLOCKS 9 12")
           .replace("TOTAL_CLOCKS 32", "TOTAL_CLOCKS 12"),
        raw.replace("RESULT 9", "RESULT 0"),
        raw.replace("ROW 9 1 2", "ROW 9 1 1"),
        raw.replace("COMPLETE 1", "COMPLETE 2"),
        raw + "COMPLETE 1\n",
        raw.replace("RESULT 9 3 3 3 3", "RESULT 9 3 2 3 3"),
        "Read register 57 (0x00000039): 178 (0x000000b2)\n",
        "COMPLETE 1\nTOTAL_CLOCKS 32\n",
    ]
    rejected = 0
    for fixture in fixtures:
        try:
            parse_stream_output(Config(1, 1, 1, 1), "icarus", [scenario], [model], fixture)
        except AssertionError:
            rejected += 1
    e.assertion("malformed clock/identity/row/count/completion fixtures rejected",
                rejected, len(fixtures))
    candidates = successes(e)
    e.record["observed_partition_inventory"] = {
        "advisory_only": True,
        "header_candidates_not_validated_results": {
            ident: e.relative(path) for ident, (path, _) in candidates.items()
        },
        "missing_header_count": len(definitions) - len(candidates),
        "anchor_header_present": definitions[0]["id"] in candidates,
        "anchor_command": definitions[0]["argv"],
        "execution_owner": "shared runtime",
    }
    e.record["method_validation_only"] = True
    e.save()


def output_method_checks(e):
    """Exercise allocation/discovery and CLI wiring without running a partition."""
    from contextlib import redirect_stderr, redirect_stdout
    import io
    from unittest.mock import patch
    import verify

    directory = e.path / "synthetic-output-contracts"
    directory.mkdir(exist_ok=False)
    choices = (
        ("behavior-shard", 154, "icarus", "behavior-shard"),
        ("behavior-shard", 0, "icarus", "recovery-v2-behavior-shard-icarus-0"),
        ("quartus-sta-shard", 178, None, "quartus-sta-shard"),
        ("quartus-sta-shard", 166, None, "recovery-v2-quartus-sta-shard-166"),
        ("behavior-shard", 167, "icarus", "recovery-v2-behavior-shard-icarus-167"),
        ("parameter-shard", 6, "icarus", "recovery-v2-parameter-shard-icarus-6"),
        ("pipeline-shard", 5, "icarus", "recovery-v2-pipeline-shard-icarus-5"),
        ("behavior-shard", 140, "verilator", "recovery-v2-behavior-shard-verilator-140"),
        *(("behavior-shard", index, "verilator",
           f"recovery-v2-behavior-shard-verilator-{index}") for index in range(141, 168)),
        *((check, index, "verilator", f"recovery-v2-{check}-verilator-{index}")
          for check, count in (("parameter-shard", 7), ("pipeline-shard", 6))
          for index in range(count)),
        *(("quartus-sta-shard", index, None, f"recovery-v2-quartus-sta-shard-{index}")
          for index in (*range(8), *range(15, 20))),
    )
    discovered = []
    for check, shard, simulator, prefix in choices:
        spec = spec_for(check, shard, simulator)
        name = f"{prefix}-{'a' * 32}"
        path = directory / name / "receipt.json"
        path.parent.mkdir(exist_ok=False)
        path.write_text(json.dumps({"synthetic_only": True, "acceptance_evidence": False})+"\n")
        discovered.append(path)
        e.assertion(f"{check}-{shard} directory matches admitted selector",
                    path.parent.match(Path(spec["outputs"][0]).name), True)
        argv = spec["argv"][1:]
        with patch.object(verify.sys, "argv", argv), \
                patch.object(verify, "Evidence") as factory, \
                patch(f"{__name__}.run_partition") as run, \
                redirect_stdout(io.StringIO()):
            factory.return_value.relative.return_value = path.relative_to(ROOT).as_posix()
            e.assertion(f"{check}-{shard} CLI fixture exit", verify.main(), 0)
            factory.assert_called_once_with(check, preparation=False, output_prefix=prefix)
            run.assert_called_once_with(factory.return_value, check, shard, simulator)
    undeclared = [spec for spec in check_definitions() if not spec["outputs"]]
    e.assertion("undeclared partitions retain no allocation fallback",
                all(partition_output_prefix(spec["check"], spec["shard"], spec["simulator"])
                    is None for spec in undeclared), True)
    e.assertion("undeclared partition denominator", len(undeclared), 225)
    e.assertion("only unadmitted physical partitions lack output selectors",
                sorted(spec["id"] for spec in undeclared),
                sorted({f"quartus-sta-shard-{index}" for index in range(192)
                        if index not in (178, 166, *range(8), *range(15, 20))}
                       | {f"board-hil-shard-{index}" for index in range(48)}))
    representatives = {}
    for spec in undeclared:
        representatives.setdefault((spec["check"], spec["simulator"]), spec)
    pending_restoration = [
        spec for spec in undeclared
        if spec["check"] == "quartus-sta-shard" and spec["shard"] in range(8, 15)
    ]
    e.assertion("approved timing records awaiting executable restoration",
                [spec["shard"] for spec in pending_restoration], list(range(8, 15)))
    rejected_specs = {spec["id"]: spec
                      for spec in [*representatives.values(), *pending_restoration]}
    for spec in rejected_specs.values():
        diagnostic = io.StringIO()
        with patch.object(verify.sys, "argv", spec["argv"][1:]), \
                patch.object(verify, "Evidence") as factory, \
                patch(f"{__name__}.run_partition") as run, \
                redirect_stderr(diagnostic), redirect_stdout(io.StringIO()):
            try:
                verify.main()
            except SystemExit as error:
                require(error.code == 2, "Undeclared partition must be a CLI contract error")
            else:
                raise AssertionError("Undeclared partition output fell back to a shared directory")
            factory.assert_not_called()
            run.assert_not_called()
        e.assertion(f"{spec['id']} rejected before evidence allocation or tool execution",
                    "Partition has no declared exclusive output selector" in diagnostic.getvalue(),
                    True)
    e.assertion("receipt discovery includes all admitted outputs exactly once",
                [e.relative(path) for path in partition_receipts(directory)],
                sorted(e.relative(path) for path in discovered))
    historical = directory / f"quartus-sta-shard-{'b' * 32}" / "receipt.json"
    historical.parent.mkdir(exist_ok=False)
    historical.write_text('{"synthetic_only": true, "acceptance_evidence": false}\n')
    e.assertion("receipt discovery retains historical outputs without duplicates",
                [e.relative(path) for path in partition_receipts(directory)],
                sorted(e.relative(path) for path in [*discovered, historical]))
    for check, shard, simulator, prefix in choices:
        if not prefix.startswith("recovery-v2-"):
            continue
        recovery_allocation_checks(e, directory, check, shard, simulator, prefix)


def recovery_allocation_checks(e, directory, check, shard, simulator, prefix):
    """Keep both versioned output contracts exclusive and independently checked."""
    from contextlib import redirect_stdout
    import io
    from unittest.mock import patch
    import evidence

    with patch.object(evidence, "ROOT", directory), \
            patch.object(evidence, "sources", return_value=[]), \
            patch.object(evidence, "hashes", return_value={}), \
            redirect_stdout(io.StringIO()):
        first = evidence.Evidence(
            check, preparation=True, output_prefix=prefix,
        )
        second = evidence.Evidence(
            check, preparation=True, output_prefix=prefix,
        )
        for fixture in (first, second):
            fixture.record["synthetic_only"] = True
            fixture.finish()
            require(fixture.path.name.startswith(f"{prefix}-")
                    and fixture.record["check"] == check
                    and fixture.record["acceptance_evidence"] is False,
                    "Allocation changed receipt check identity or fixture evidence status")
        e.assertion(f"{prefix} allocations use distinct exclusive directories",
                    first.path != second.path, True)
        with patch.object(evidence.uuid, "uuid4",
                          return_value=evidence.uuid.UUID(first.path.name[-32:])):
            try:
                evidence.Evidence(
                    check, preparation=True, output_prefix=prefix,
                )
            except FileExistsError:
                collision_rejected = True
            else:
                collision_rejected = False
        e.assertion(f"{prefix} allocation refuses occupied output directories",
                    collision_rejected, True)
    spec = spec_for(check, shard, simulator)
    historical = directory / f"{check}-{simulator}-{shard}-{'c' * 32}" / "receipt.json"
    historical.parent.mkdir(exist_ok=False)
    historical.write_text('{"synthetic_only": true, "acceptance_evidence": false}\n')
    try:
        validate_receipt(e, historical, {"partition": spec, "check": spec["check"]}, spec)
    except AssertionError as error:
        require("outside admitted exclusive output" in str(error),
                f"Unexpected output contract fixture diagnostic: {error}")
    else:
        raise AssertionError("Recovery receipt outside its admitted selector was accepted")
    e.assertion(f"{prefix} receipt outside admitted output rejected", True, True)


def dependency_method_checks(e):
    """Synthetic dependency graphs test orchestration, not tool or RTL outcomes."""
    from unittest.mock import patch

    require(e.record["acceptance_evidence"] is False,
            "Dependency fixtures must remain preparation-only")
    definitions = check_definitions()
    specs = {spec["id"]: spec for spec in definitions}
    anchor = specs["behavior-shard-icarus-154"]
    first = specs["quartus-sta-shard-178"]
    second = specs["quartus-sta-shard-166"]
    directory = e.path / "synthetic-dependencies"
    directory.mkdir(exist_ok=False)
    paths = {}
    fixture_index = 0

    def fixtures(*, corrupt_link=False, reversed_order=False, invalid=None):
        nonlocal paths, fixture_index
        current = directory / str(fixture_index)
        current.mkdir(exist_ok=False)
        fixture_index += 1
        paths = {spec["id"]: current / f"{spec['id']}.json" for spec in (anchor, first)}
        found = {}
        for index, spec in enumerate((anchor, first)):
            record = {
                "synthetic_only": True, "acceptance_evidence": False,
                "partition": spec, "status": "passed",
                "start": f"2026-01-01T00:00:0{index * 2}+00:00",
                "end": f"2026-01-01T00:00:0{index * 2 + 1}+00:00",
                "predecessors": [
                    {"id": ident, "receipt": e.relative(paths[ident]),
                     "sha256": "incorrect" if corrupt_link else file_hash(paths[ident])}
                    for ident in spec["depends_on"]
                ],
            }
            if reversed_order and spec == first:
                record["start"] = "2026-01-01T00:00:00+00:00"
            if invalid and spec == anchor:
                record[invalid] = True
            paths[spec["id"]].write_text(json.dumps(record, sort_keys=True) + "\n")
            found[spec["id"]] = (
                paths[spec["id"]], {key: record.get(key) for key in HEADER_KEYS},
            )
        return found

    validated = []

    def receipt_validator(evidence, path, record, spec):
        require(not record.get("invalid_receipt"), "synthetic invalid receipt")

    def result_validator(evidence, path, record, spec):
        require(not record.get("invalid_result"), "synthetic invalid observation")
        validated.append(spec["id"])
        return {"synthetic_only": True}

    # Only leaf validators are substituted. The real receipt loader, hashes,
    # dependency traversal and ordering/link checks run on preparation fixtures.
    module = __name__
    with patch(f"{module}.validate_receipt", side_effect=receipt_validator), \
            patch(f"{module}.validate_partition_result", side_effect=result_validator):
        cases = (
            ("missing transitive predecessor", {}, True,
             "Missing predecessor success behavior-shard-icarus-154"),
            ("corrupt transitive receipt link", {"corrupt_link": True}, False,
             "Explicit predecessor receipt binding differs"),
            ("reversed transitive completion order", {"reversed_order": True}, False,
             "Dependency order was not honored"),
            ("invalid transitive receipt", {"invalid": "invalid_receipt"}, False,
             "synthetic invalid receipt"),
            ("invalid transitive observation", {"invalid": "invalid_result"}, False,
             "synthetic invalid observation"),
        )
        for name, options, missing_anchor, diagnostic in cases:
            found = fixtures(**options)
            if missing_anchor:
                del found[anchor["id"]]
            try:
                predecessor_links(e, second, found)
            except AssertionError as error:
                require(diagnostic in str(error),
                        f"Unexpected dependency fixture failure for {name}: {error}")
                rejected = True
            else:
                rejected = False
            e.assertion(f"partition startup rejects {name}", rejected, True)
        found = fixtures()
        validated.clear()
        links = predecessor_links(e, second, found)
        e.assertion("partition startup validates full chain before new execution",
                    validated, [anchor["id"], first["id"]])
        e.assertion("partition startup retains exact immediate predecessor binding",
                    links, [{"id": first["id"], "receipt": e.relative(paths[first["id"]]),
                             "sha256": file_hash(paths[first["id"]])}])


def orchestration_method_checks(e):
    """Run real tiny Python children on synthetic DAGs; never scaler acceptance."""
    import threading
    from unittest.mock import patch

    original_run = e.run
    synthetic = []
    for ident, predecessors, simulator in (
        ("anchor", [], "icarus"), ("parallel-a", ["anchor"], "icarus"),
        ("parallel-b", ["anchor"], "verilator"), ("hardware-10", ["parallel-a", "parallel-b"], None),
        ("hardware-0", ["hardware-10"], None),
    ):
        synthetic.append({
            "id": ident, "depends_on": predecessors, "simulator": simulator,
            "check": "behavior-shard", "outputs": ["synthetic-only"],
            "argv": ["python3", "-c",
                     f"import time; time.sleep(0.05); print('SYNTHETIC_DAG {ident}')"],
        })
    by_id = {spec["id"]: spec for spec in synthetic}
    complete, active, peak = {}, set(), 0
    lock = threading.Lock()

    def run(argv, **kwargs):
        nonlocal peak
        spec = next(spec for spec in synthetic if spec["argv"] == argv)
        ident = spec["id"]
        with lock:
            require(set(spec["depends_on"]) <= set(complete), "Fixture launched before predecessor")
            require(spec["simulator"] is not None or not active, "Hardware fixture overlapped")
            require(not any(by_id[item]["simulator"] is None for item in active),
                    "Child overlapped physical fixture")
            active.add(ident)
            peak = max(peak, len(active))
        try:
            result = original_run(argv, **kwargs)
            with lock:
                complete[ident] = (e.path / "synthetic-only.json", {"synthetic_only": True})
            return result
        finally:
            with lock:
                active.remove(ident)

    def validate(evidence, found, identities, *, validated=None):
        validated = {} if validated is None else validated
        for ident in identities:
            require(ident in complete, "Synthetic completion absent")
            validated[ident] = {"synthetic_only": True}
        return validated

    module = __name__
    with patch(f"{module}.check_definitions", return_value=synthetic), \
            patch(f"{module}.successes", side_effect=lambda evidence: dict(complete)), \
            patch(f"{module}.validate_dependencies", side_effect=validate), \
            patch.object(e, "run", side_effect=run):
        result = execute_prerequisites(e, "behavior", {}, workers=2)
        e.assertion("synthetic automatic prerequisite closure", sorted(result), sorted(by_id))
        e.assertion("synthetic bounded independent children overlap", peak, 2)
        command_count = len(e.record["commands"])
        execute_prerequisites(e, "behavior", result, workers=2)
        e.assertion("current synthetic completions execute no duplicate commands",
                    len(e.record["commands"]), command_count)
        complete.clear()
        unadmitted = [{**spec, "outputs": []} if spec["id"] == "parallel-b" else spec
                      for spec in synthetic]
        with patch(f"{module}.check_definitions", return_value=unadmitted):
            try:
                execute_prerequisites(e, "behavior", {}, workers=2)
            except AssertionError as error:
                require("append-only admission" in str(error), "Unexpected admission diagnostic")
            else:
                raise AssertionError("Synthetic unmapped command executed")
        e.assertion("unadmitted synthetic DAG rejected before subprocess execution",
                    len(e.record["commands"]), command_count)
        failing = [{**spec, "argv": ["python3", "-c",
                    "print('SYNTHETIC_EXPECTED_FAILURE'); raise SystemExit(7)"]}
                   if spec["id"] == "anchor" else spec for spec in synthetic]
        with patch(f"{module}.check_definitions", return_value=failing), \
                patch.object(e, "run", side_effect=original_run):
            try:
                execute_prerequisites(e, "behavior", {}, workers=2)
            except AssertionError as error:
                require("Command exit 7" in str(error), "Failure propagation diagnostic differs")
            else:
                raise AssertionError("Failed prerequisite did not stop dependents")
        e.assertion("failed synthetic predecessor stops every dependent",
                    len(e.record["commands"]), command_count + 1)
        e.assertion("failed synthetic child retains exact unexpected exit",
                    e.record["commands"][-1]["exit_status"], 7)
        # This is an intentionally failing method fixture, not a passed child.
        e.record["commands"][-1]["negative_method_fixture"] = True
    e.record["synthetic_orchestration_only"] = True
    e.save()


def discovery_method_checks(e):
    """Incremental headers still discover changed receipts and reject duplicates."""
    from unittest.mock import patch

    directory = e.path / "synthetic-incremental-discovery"
    directory.mkdir()
    spec = check_definitions()[0]
    record = {
        "synthetic_only": True, "acceptance_evidence": False,
        "scheduling_contract": contract(), "source_sha256": e.record["source_sha256"],
        "status": "running", "partition": spec,
    }
    first = directory / "first.json"
    first.write_text(json.dumps(record))
    paths = [first]
    module = __name__
    with patch(f"{module}.partition_receipts", side_effect=lambda directory: list(paths)):
        e.assertion("running synthetic receipt excluded", len(successes(e)), 0)
        record["status"] = "passed"
        first.write_text(json.dumps(record))
        found = successes(e)
        e.assertion("changed synthetic receipt invalidates header cache", list(found), [spec["id"]])
        with patch(f"{module}.json.loads", side_effect=AssertionError("Unchanged header reparsed")):
            e.assertion("unchanged receipt uses incremental header", list(successes(e)), [spec["id"]])
        second = directory / "second.json"
        second.write_text(json.dumps(record))
        paths.append(second)
        try:
            successes(e)
        except AssertionError as error:
            require("Duplicate successful partition" in str(error), "Duplicate cache diagnostic differs")
        else:
            raise AssertionError("Incremental discovery hid a duplicate")
    e.record.pop("excluded_partition_receipts", None)
    e.save()


def atomic_receipt_method_checks(e):
    """Concurrent discovery must never observe a partially published receipt."""
    def publish():
        for index in range(64):
            e.record["atomic_publication_fixture"] = index
            e.save()

    def discover():
        for _ in range(128):
            record = json.loads((e.path / "receipt.json").read_text())
            require(record["source_sha256"] == e.record["source_sha256"],
                    "Concurrent receipt reader observed an incomplete source inventory")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish), pool.submit(discover)]
        for future in futures:
            future.result()
    e.assertion("concurrent atomic receipt reads remain complete", 128, 128)
