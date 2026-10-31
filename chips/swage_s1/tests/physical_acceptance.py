"""Synthetic dispatcher regression, never STA or physical scaler acceptance.

Admission command: python3 tests/verify.py --check physical-acceptance-path-parity
Bounded preparation: python3 tests/verify.py --probe physical-acceptance-path-parity

Receipt discovery and receipt-envelope validation use isolated synthetic inputs.
Rejection cases keep every production result validator and collector unchanged.
Only the valid control stubs measured leaf results; it proves dispatcher plumbing,
not the validity of synthetic physical measurements. No FPGA tool is executed.
"""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import datetime
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from evidence import file_hash, require
from physical_evidence import BRIDGE_TIER, native_scope


def method_checks(e):
    import partitions
    import physical
    import verify
    from physical_method import write_inventory

    e.record["method_scope"] = "SYNTHETIC_ONLY_NOT_PHYSICAL_ACCEPTANCE"
    e.record["regression"] = "physical-acceptance-path-parity"
    e.record["fixture_isolation"] = (
        "Synthetic discovery and receipt envelopes; external behavior predecessor "
        "removed only in fixture definitions. Physical dependency order and all "
        "192 timing/48 hardware configurations retained. Production result "
        "validators unmodified in every rejection case."
    )
    e.save()
    directory = e.path / f"synthetic-physical-parity-{uuid.uuid4().hex}"
    directory.mkdir()
    definitions = partitions.check_definitions()
    original = {
        "aggregate": partitions.aggregate,
        "result": partitions.validate_partition_result,
        "sta": physical.validate_sta,
        "hardware": partitions.validate_hardware,
        "collector": partitions.execute_prerequisites,
    }
    observations = []
    for gate in ("quartus-sta", "board-hil"):
        specs = [dict(spec) for spec in definitions if spec["check"] == gate + "-shard"]
        identities = {spec["id"] for spec in specs}
        for spec in specs:
            spec["depends_on"] = [ident for ident in spec["depends_on"] if ident in identities]
        for case in ("missing-partition", "declaration-only", "declaration-ledger",
                     "valid-control"):
            pair = []
            for acceptance in (False, True):
                current = directory / f"{gate}-{case}-{int(acceptance)}-{uuid.uuid4().hex}"
                current.mkdir()
                native_directory = current / "synthetic-native"
                native_directory.mkdir()
                native = {
                    "gate": gate, "status": "passed", "source_sha256": "1" * 64,
                    "synthetic_only": True,
                }
                if gate == "quartus-sta":
                    native["sta_summary"] = {
                        "netlist_mode": "post_fit", "wns_setup_ns": 0.495,
                        "wns_hold_ns": 0.059,
                    }
                else:
                    native.update(requested_runs=3, deterministic_across_runs=True,
                                  reg57_probe={"synthetic_only": True})
                (native_directory / f"{gate}.json").write_text(json.dumps(native) + "\n")
                record = {
                    "synthetic_only": True,
                    "fixture_scope": "SYNTHETIC_ONLY_NOT_PHYSICAL_ACCEPTANCE",
                    "acceptance_evidence": acceptance,
                    "source_sha256": e.record["source_sha256"],
                    "start": e.record["start"], "commands": [],
                }
                found = {}
                for index, spec in enumerate(specs):
                    if case == "missing-partition" and spec == specs[-1]:
                        continue
                    path = current / f"{spec['id']}.json"
                    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
                    start += datetime.timedelta(seconds=index * 2)
                    result = {"configuration": spec["configuration"]}
                    if case == "valid-control":
                        result.update(
                            synthetic_measurement=spec["id"],
                            **({"wns_setup_ns": [0.495], "wns_hold_ns": [0.059],
                                "report_sha256": {"synthetic": "1" * 64}}
                               if gate == "quartus-sta" else
                               {"passes": [{"synthetic_only": True}] * 3,
                                "programmed_sof_sha256": "1" * 64}),
                        )
                    retained = {
                        "synthetic_only": True, "acceptance_evidence": acceptance,
                        "partition": spec, "status": "passed",
                        "start": start.isoformat(),
                        "end": (start + datetime.timedelta(seconds=1)).isoformat(),
                        "predecessors": [
                            {"id": ident, "receipt": e.relative(found[ident][0]),
                             "sha256": file_hash(found[ident][0])}
                            for ident in spec["depends_on"]
                        ],
                        "partition_result": result,
                    }
                    path.write_text(json.dumps(retained, indent=2) + "\n")
                    found[spec["id"]] = (
                        path, {key: retained.get(key) for key in partitions.HEADER_KEYS},
                    )
                ledger_name = "sta-ledger.json" if gate == "quartus-sta" else "hil-ledger.json"
                if case == "declaration-ledger":
                    (current / ledger_name).write_text(json.dumps([
                        {"configuration": spec["configuration"]} for spec in specs
                    ]) + "\n")

                def save():
                    (current / "receipt.json").write_text(json.dumps(record, indent=2) + "\n")

                def finish(error):
                    record.update(status="failed" if error else "passed",
                                  error=str(error) if error else None)
                    save()

                def no_execution(*args, **kwargs):
                    raise AssertionError("Synthetic fixture attempted physical execution")

                def envelope(evidence, path, retained, spec):
                    require(path.parent == current and retained["synthetic_only"]
                            and retained["acceptance_evidence"] is acceptance
                            and spec in specs, "Unlabelled synthetic receipt envelope")

                def control_result(evidence, *args, **kwargs):
                    if gate == "quartus-sta":
                        retained = kwargs["execution"]
                    else:
                        retained = args[1]
                    require(retained["synthetic_only"], "Control escaped synthetic inputs")
                    return retained["partition_result"]

                fixture = SimpleNamespace(
                    path=current, record=record, relative=e.relative, save=save, finish=finish,
                    assertion=lambda name, actual, expected: e.assertion(
                        f"synthetic parity {gate} {name}", actual, expected),
                    run=no_execution,
                    tool=lambda name: require(name == "python3", "Physical tool attempted"),
                )
                stdout, stderr = io.StringIO(), io.StringIO()
                with ExitStack() as stack:
                    for target, replacement in (
                        ("verify.Evidence", lambda *args, **kwargs: fixture),
                        ("physical_evidence.ROOT", current),
                        ("physical_evidence.NATIVE_DIRECTORY", Path("synthetic-native")),
                        ("partitions.check_definitions", lambda: specs),
                        ("partitions.successes", lambda evidence: dict(found)),
                        ("partitions.validate_receipt", envelope),
                    ):
                        stack.enter_context(patch(target, replacement))
                    if case == "valid-control":
                        target = ("physical.validate_sta" if gate == "quartus-sta"
                                  else "partitions.validate_hardware")
                        stack.enter_context(patch(target, control_result))
                    else:
                        require(physical.validate_sta is original["sta"]
                                and partitions.validate_hardware is original["hardware"],
                                "Rejection case replaced a real physical result validator")
                    require(partitions.aggregate is original["aggregate"]
                            and partitions.execute_prerequisites is original["collector"]
                            and partitions.validate_partition_result is original["result"],
                            "Fixture replaced the real aggregate/collector/result dispatcher")
                    stack.enter_context(patch.object(
                        sys, "argv", ["tests/verify.py", "--check", gate]))
                    stack.enter_context(redirect_stdout(stdout))
                    stack.enter_context(redirect_stderr(stderr))
                    code = verify.main()
                (current / "stdout.log").write_text(stdout.getvalue())
                (current / "stderr.log").write_text(stderr.getvalue())
                failed = case != "valid-control"
                label = f"{gate} {case} acceptance={acceptance}"
                e.assertion(label + " dispatcher exit", code, 1 if failed else 0)
                e.assertion(label + " retained receipt status",
                            json.loads((current / "receipt.json").read_text())["status"],
                            "failed" if failed else "passed")
                e.assertion(label + " public JSON status",
                            json.loads(stdout.getvalue())["status"],
                            "failed" if failed else "passed")
                e.assertion(label + " real prerequisite collector entered",
                            "prerequisite_plan" in record, True)
                e.assertion(label + " native binding",
                            record["authoritative_physical_evidence"]["status"],
                            "not_validated" if failed else "validated")
                e.assertion(label + " native scope retained",
                            record["authoritative_physical_evidence"]["scope"],
                            native_scope(gate, native))
                if failed:
                    diagnostic = ("1 missing prerequisites require append-only admission"
                                  if case == "missing-partition"
                                  else "Missing measured physical result")
                    require(diagnostic in record["error"] and diagnostic in stderr.getvalue(),
                            "Fixture rejected for an unrelated reason: " + label)
                else:
                    ledger = json.loads((current / ledger_name).read_text())
                    e.assertion(label + " complete measured control ledger",
                                [row["synthetic_measurement"] for row in ledger],
                                [spec["id"] for spec in specs])
                    e.assertion(label + " exact actual predecessor receipt/hash links",
                                record["predecessors"], [
                                    {"id": spec["id"], "receipt": e.relative(found[spec["id"]][0]),
                                     "sha256": file_hash(found[spec["id"]][0])}
                                    for spec in specs
                                ])
                observation = {
                    "gate": gate, "case": case, "acceptance_evidence": acceptance,
                    "dispatcher_exit": code, "status": record["status"],
                    "receipt": e.relative(current / "receipt.json"),
                    "sha256": file_hash(current / "receipt.json"),
                    "real_result_validators": failed,
                    "synthetic_control_leaf_stubs": not failed,
                }
                observations.append(observation)
                pair.append((code, record["status"], record["error"]))
                e.record["parity_fixtures"] = observations
                e.save()
            e.assertion(f"{gate} {case} false/true path parity", pair[0], pair[1])
        if gate == "board-hil":
            require(native_scope(gate, native)["tier"] == BRIDGE_TIER,
                    "Connectivity fixture upgraded to scaler verification")
    e.assertion("physical parity gate/case/acceptance denominator", len(observations), 16)
    write_inventory(e)
