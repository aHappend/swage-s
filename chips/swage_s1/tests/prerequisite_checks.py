"""Isolated orchestration regressions, never scaler or physical measurements.

Run: python3 tests/verify.py --probe self-contained-checks
Fixture subprocesses use a copied source tree beneath the preparation output.
Only leaf work/result interpretation is replaced; allocation, CLI dispatch,
source/artifact/command validation, dependency traversal and collection are real.
"""

from contextlib import ExitStack
import json
import shutil
import subprocess
import sys
import time
from unittest.mock import patch
import uuid

from evidence import Evidence, ROOT, file_hash, require, sources, stamp


def fixture_result(e, path, record, spec):
    """Validate the actual tiny Python result, not a physical-result declaration."""
    command = record["commands"][-1]
    require(command["requested_argv"] == ["python3", "-c", "print(6 * 7)"]
            and command["exit_status"] == 0,
            "Fixture leaf command differs")
    raw = ROOT / command["stdout"]
    require(raw.read_text().strip() == "42"
            and record["partition_result"] == {"observed": 42},
            "Fixture leaf observation differs")
    return record["partition_result"]


def fixture_partition(e, check, shard, simulator):
    import partitions

    spec = partitions.spec_for(check, shard, simulator)
    e.record.update(partition=spec, scheduling_contract=partitions.contract(),
                    fixture_scope="SYNTHETIC_ONLY_NOT_PHYSICAL_ACCEPTANCE")
    e.save()
    found = partitions.execute_prerequisites(e, spec["id"], partitions.successes(e), workers=1)
    e.record["partition_execution"] = {
        "start": stamp(), "command_offset": len(e.record["commands"]),
    }
    e.record["predecessors"] = partitions.predecessor_links(e, spec, found)
    control = json.loads((ROOT / "fixture-control.json").read_text())
    if control.get("fail") == spec["id"]:
        e.run(["python3", "-c", "raise SystemExit(7)"])
    if control.get("sleep") == spec["id"]:
        e.run(["python3", "-c", "import time; time.sleep(30)"])
    e.run(["python3", "-c", "print(6 * 7)"])
    e.record["partition_result"] = {"observed": 42}
    e.record["artifact_sha256"] = partitions.artifact_hashes(e.path)
    e.save()


def fixture_main():
    """Used only by the generated entrypoint in the isolated preparation tree."""
    import partitions
    import verify_real

    specs = json.loads((ROOT / "fixture-specs.json").read_text())
    original_discovery = partitions.partition_receipts
    with patch.object(partitions, "check_definitions", return_value=specs), \
            patch.object(partitions, "run_partition", side_effect=fixture_partition), \
            patch.object(partitions, "partition_receipts",
                         side_effect=lambda directory: fixture_receipts(
                             ROOT, original_discovery, directory)), \
            patch.object(partitions, "validate_partition_result", side_effect=fixture_result):
        return verify_real.main()


def fixture_receipts(root, discover, directory):
    hidden = json.loads((root / "fixture-control.json").read_text()).get("hidden", [])
    return [path for path in discover(directory) if path.relative_to(root).as_posix() not in hidden]


def method_checks(e):
    import evidence
    import partitions
    import prerequisite_checks

    require(e.record["acceptance_evidence"] is False,
            "Orchestration fixtures require isolated preparation evidence")
    e.record["method_scope"] = "SYNTHETIC_ONLY_NOT_PHYSICAL_ACCEPTANCE"
    e.record["regression"] = "self-contained-prerequisite-execution"
    e.save()
    directory = e.path / f"synthetic-prerequisites-{uuid.uuid4().hex}"
    directory.mkdir()
    definitions = partitions.check_definitions()
    identities = ("behavior-shard-icarus-154", "quartus-sta-shard-178", "quartus-sta-shard-8")
    specs = []
    for index, ident in enumerate(identities):
        spec = dict(next(item for item in definitions if item["id"] == ident))
        spec.update(requires=["python3"], depends_on=[] if index == 0 else [identities[index-1]])
        specs.append(spec)
    original_outputs = {spec["id"]: list(spec["outputs"]) for spec in specs}
    e.assertion("fixture includes an unmapped physical prerequisite", specs[-1]["outputs"], [])
    candidate = {
        "scheduling_contract": partitions.contract(),
        "source_sha256": e.record["source_sha256"],
        "partition": {"id": identities[0]}, "status": "passed",
    }
    found, excluded = partitions.select_successes(
        [(directory / "synthetic-only.json", {**candidate, "acceptance_evidence": True,
                                             "synthetic_only": True}),
         (directory / "preparation-only.json", {**candidate, "acceptance_evidence": False})],
        e.record["source_sha256"], partitions.contract(), set(identities),
    )
    e.assertion("synthetic and preparation receipts never satisfy collection", found, {})
    e.assertion("synthetic exclusions explicitly retained", len(excluded), 2)

    def tree(name):
        root = directory / name
        root.mkdir()
        for source in sources():
            target = root / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        shutil.copyfile(root / "tests/verify.py", root / "tests/verify_real.py")
        # This is a generated disposable fixture, never a delivered entrypoint.
        (root / "tests/verify.py").write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
            "from prerequisite_checks import fixture_main\n"
            "raise SystemExit(fixture_main())\n"
        )
        (root / "fixture-specs.json").write_text(json.dumps(specs))
        (root / "fixture-control.json").write_text("{}")
        return root

    def scope(root):
        stack = ExitStack()
        for module in (evidence, partitions, prerequisite_checks):
            stack.enter_context(patch.object(module, "ROOT", root))
        stack.enter_context(patch.object(partitions, "check_definitions", return_value=specs))
        discovery = partitions.partition_receipts
        stack.enter_context(patch.object(
            partitions, "partition_receipts",
            side_effect=lambda directory: fixture_receipts(root, discovery, directory)))
        stack.enter_context(patch.object(
            partitions, "validate_partition_result", side_effect=fixture_result))
        return stack

    def parent():
        item = Evidence("quartus-sta")
        item.record["fixture_scope"] = "SYNTHETIC_ONLY_NOT_PHYSICAL_ACCEPTANCE"
        item.save()
        item.tool("python3")
        return item

    def reject(item, action, diagnostic):
        try:
            action()
        except (AssertionError, FileExistsError) as error:
            require(diagnostic in str(error), f"Unexpected fixture diagnostic: {error}")
            item.finish(error)
        else:
            raise AssertionError(f"Unsafe prerequisite fixture accepted: {diagnostic}")

    root = tree("automatic-and-reuse")
    with scope(root):
        item = parent()
        found = partitions.execute_prerequisites(item, "quartus-sta", {}, workers=1)
        e.assertion("missing children run automatically in dependency order",
                    item.record["prerequisite_execution"]["completed"], list(identities))
        e.assertion("full closure observed", sorted(found), sorted(identities))
        e.assertion("no external allocation invented",
                    {spec["id"]: spec["outputs"] for spec in partitions.check_definitions()},
                    original_outputs)
        for ident, (path, _) in found.items():
            retained = json.loads(path.read_text())
            e.assertion(f"{ident} parent-owned exclusive destination",
                        str(path.parent.parent), str(item.path / "prerequisites"))
            e.assertion(f"{ident} not a runtime allocation",
                        retained["parent_execution"]["runtime_allocation"], False)
        item.finish()
        subsequent = parent()
        reused = partitions.successes(subsequent)
        partitions.execute_prerequisites(subsequent, "quartus-sta", reused, workers=1)
        e.assertion("source-bound completed work is reused without relaunch",
                    subsequent.record["prerequisite_execution"]["launched"], [])
        subsequent.finish()
        # Actual source change excludes all earlier receipts, never newest-wins.
        (root / "tests/fixture_revision.py").write_text("REVISION = 2\n")
        revised = parent()
        e.assertion("stale source receipts are excluded", partitions.successes(revised), {})
        current = partitions.execute_prerequisites(revised, "quartus-sta", {}, workers=1)
        e.assertion("stale prerequisites automatically recollected", sorted(current), sorted(identities))
        revised.finish()
        # A current envelope cannot promote declarations into measurements.
        path, _ = current[identities[-1]]
        record = json.loads(path.read_text())
        record["partition_result"] = {"configuration": specs[-1]["configuration"]}
        with patch.object(partitions, "validate_receipt"):
            try:
                partitions.validate_partition_result(revised, path, record, specs[-1])
            except AssertionError:
                e.assertion("declaration-only child rejected by observation checker", True, True)
            else:
                raise AssertionError("Fixture observation validation accepted declarations")

    root = tree("failed-child")
    (root / "fixture-control.json").write_text(json.dumps({"fail": identities[1]}))
    with scope(root):
        item = parent()
        reject(item, lambda: partitions.execute_prerequisites(
            item, "quartus-sta", {}, workers=1), "Command exit 1")
        e.assertion("failed child prevents its dependent from launching",
                    item.record["prerequisite_execution"]["launched"], list(identities[:2]))
        repaired = parent()
        e.assertion("completed predecessor retained despite parent failure",
                    list(partitions.successes(repaired)), [identities[0]])
        failed = [json.loads(path.read_text()) for path in
                  (item.path / "prerequisites").glob("*/receipt.json")]
        e.assertion("failed child raw exit retained",
                    any(record["status"] == "failed"
                        and record["commands"][-1]["exit_status"] == 7 for record in failed), True)
        (root / "fixture-control.json").write_text("{}")
        partitions.execute_prerequisites(repaired, "quartus-sta", partitions.successes(repaired),
                                         workers=1)
        e.assertion("retry reuses completed source-bound predecessor",
                    repaired.record["prerequisite_execution"]["launched"], list(identities[1:]))
        repaired.finish()

    root = tree("missing-predecessor")
    with scope(root):
        item = parent()
        found = partitions.execute_prerequisites(item, "quartus-sta", {}, workers=1)
        item.finish()
        originals = {path: file_hash(path) for path, _ in found.values()}
        # Simulate unavailable retained evidence through fixture-only discovery.
        # No completed receipt is removed, edited, or overwritten.
        (root / "fixture-control.json").write_text(json.dumps({
            "hidden": [found[identities[0]][0].relative_to(root).as_posix()],
        }))
        resumed = parent()
        candidates = partitions.successes(resumed)
        e.assertion("missing predecessor invalidates dependent candidates", candidates, {})
        collected = partitions.execute_prerequisites(resumed, "quartus-sta", candidates, workers=1)
        e.assertion("missing transitive predecessor and stale links automatically repaired",
                    sorted(collected), sorted(identities))
        e.assertion("dependency repair preserves all prior receipt bytes",
                    {str(path): file_hash(path) for path in originals},
                    {str(path): digest for path, digest in originals.items()})
        resumed.finish()

    root = tree("collisions-and-contract")
    with scope(root):
        item = parent()
        argv = partitions.internal_command(item, specs[-1])
        output = root / item.record["internal_prerequisites"][0]["output"]
        with patch.object(evidence.sys, "argv", argv[1:]):
            child = Evidence(specs[-1]["check"], parent_receipt=item.relative(item.path / "receipt.json"),
                             child_output=item.relative(output), argv=specs[-1]["argv"])
            child.finish(AssertionError("Intentional fixture, no leaf work"))
            before = file_hash(child.path / "receipt.json")
            try:
                Evidence(specs[-1]["check"], parent_receipt=item.relative(item.path / "receipt.json"),
                         child_output=item.relative(output), argv=specs[-1]["argv"])
            except FileExistsError:
                e.assertion("occupied child output rejected", True, True)
            else:
                raise AssertionError("Occupied child output overwritten")
            e.assertion("collision preserves original receipt", file_hash(child.path / "receipt.json"),
                        before)
        for changed in (
            [*argv[:-1], item.relative(output) + "-escape"],
            [*argv[:3], "board-hil-shard", *argv[4:]],
        ):
            try:
                evidence.child_contract(item.relative(item.path / "receipt.json"),
                                        item.relative(output), changed)
            except AssertionError:
                e.assertion("changed internal invocation rejected", True, True)
            else:
                raise AssertionError("Altered internal invocation accepted")
        with patch.object(partitions.uuid, "uuid4", return_value=uuid.UUID(output.name[-32:])):
            try:
                partitions.internal_command(item, specs[-1])
            except AssertionError as error:
                require("collision" in str(error), "Unexpected reservation diagnostic")
                e.assertion("parent reservation refuses an occupied output", True, True)
            else:
                raise AssertionError("Parent reused an occupied child directory")
        fresh_argv = partitions.internal_command(item, specs[0])
        fresh_output = fresh_argv[-1]
        original_sources = item.record["source_sha256"]
        item.record["source_sha256"] = {"fixture": "stale"}
        item.save()
        with patch.object(evidence.sys, "argv", fresh_argv[1:]):
            try:
                Evidence(specs[0]["check"],
                         parent_receipt=item.relative(item.path / "receipt.json"),
                         child_output=fresh_output, argv=specs[0]["argv"])
            except AssertionError as error:
                require("stale sources" in str(error), "Unexpected source identity diagnostic")
                e.assertion("stale parent rejected before child output creation",
                            (root / fresh_output).exists(), False)
            else:
                raise AssertionError("Stale parent authorized a child")
        item.record["source_sha256"] = original_sources
        item.save()
        item.finish()

    root = tree("budget")
    (root / "fixture-control.json").write_text(json.dumps({"sleep": identities[1]}))
    with scope(root):
        item = parent()
        # Obtain one actual completed predecessor before exhausting a later child.
        partitions.execute_prerequisites(item, identities[1], {}, workers=1)
        item.finish()
        item = parent()
        item.deadline = time.time() + 6
        item.record["execution_budget"]["deadline_epoch"] = item.deadline
        item.save()
        started = time.monotonic()
        try:
            partitions.execute_prerequisites(item, "quartus-sta",
                                             partitions.successes(item), workers=1)
        except (AssertionError, TimeoutError) as error:
            item.finish(error)
        except subprocess.TimeoutExpired as error:
            item.finish(error)
        else:
            raise AssertionError("Partial collection passed at outer budget exhaustion")
        e.assertion("budget stops collection before patrol timeout",
                    time.monotonic() - started < 10, True)
        subsequent = parent()
        e.assertion("budget failure retains completed source-bound work",
                    list(partitions.successes(subsequent)), [identities[0]])
        subsequent.finish()
        e.assertion("partial coverage is not passed", item.record["status"], "failed")
        timed_out = [
            json.loads(path.read_text())
            for path in (item.path / "prerequisites").glob("*/receipt.json")
        ]
        e.assertion("child timeout retained without successful partial coverage",
                    any(record["status"] == "failed"
                        and record["commands"][-1]["timed_out"] for record in timed_out), True)

    e.record["isolated_fixture_directory"] = e.relative(directory)
    e.record["physical_acceptance_claimed"] = False
    e.save()
