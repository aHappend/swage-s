"""Synthetic physical-evidence method fixtures; no FPGA/tool acceptance claims.

Run only the bounded Python diagnostic:
    python3 tests/verify.py --probe physical-method
"""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evidence import ROOT, file_hash, hashes, require
from physical_evidence import (
    BRIDGE_TIER, DEVICE, NATIVE_DIRECTORY, SCALER_TIER, configuration_inventory,
    native_requirements, native_scope, product_links, resource_scope, timing_summary,
    validate_linked_products,
)
from reference import Config


def method_checks(e):
    require(e.record["acceptance_evidence"] is False, "Synthetic fixtures require preparation")
    for source in sorted((ROOT / "tests").glob("*.py")):
        compile(source.read_text(), source.relative_to(ROOT).as_posix(), "exec")
    e.record["method_scope"] = "SYNTHETIC_ONLY_NOT_PHYSICAL_ACCEPTANCE"
    e.save()
    summary = {
        "exit_code": 0, "netlist_mode": "post_fit", "wns_setup_ns": 0.495,
        "wns_hold_ns": 0.059, "failing_setup_count": 0, "failing_hold_count": 0,
        "unconstrained_setup_paths": 0, "unconstrained_functional_endpoints": 0,
        "unapproved_timing_exceptions": 0, "clocks": [{"name": "clk", "period_ns": 10.0}],
    }

    def rejected(name, callback, diagnostic):
        try:
            callback()
        except AssertionError as error:
            require(diagnostic in str(error),
                    f"{name}: unexpected rejection {error!s}; expected {diagnostic!r}")
            e.assertion("synthetic negative: " + name, True, True)
        else:
            raise AssertionError("Unsafe synthetic fixture accepted: " + name)

    e.assertion("synthetic positive post-fit summary", timing_summary(summary), summary)
    for field, value, diagnostic in (
        ("wns_hold_ns", -0.187, "hold slack"),
        ("wns_setup_ns", -0.001, "setup slack"),
        ("wns_hold_ns", float("nan"), "hold slack"),
        ("wns_setup_ns", float("inf"), "setup slack"),
        ("wns_hold_ns", True, "hold slack"),
        ("netlist_mode", "post_map", "Post-map"),
        ("exit_code", 1, "execution"),
        ("failing_hold_count", 20, "Failing hold"),
        ("unconstrained_setup_paths", 1, "constraint accounting"),
        ("unconstrained_functional_endpoints", None, "constraint accounting"),
        ("unapproved_timing_exceptions", 1, "constraint accounting"),
        ("clocks", [], "clock inventory"),
        ("clocks", [{"name": "clk", "period_ns": 11.0}], "10 ns"),
    ):
        rejected(field + "=" + repr(value), lambda field=field, value=value:
                 timing_summary({**summary, field: value}), diagnostic)
    native_timing = {
        "gate": "quartus-sta", "applicable": True, "status": "passed",
        "device": DEVICE, "top_module": "swage_s1_top", "clock_period_ns": 10.0,
        "sta_summary": summary,
    }
    native_requirements("quartus-sta", native_timing)
    e.assertion("synthetic positive native timing requirements", True, True)
    for field, value in (("device", "other"), ("clock_period_ns", 11.0),
                         ("top_module", "other"), ("applicable", False)):
        rejected("native " + field, lambda field=field, value=value:
                 native_requirements("quartus-sta", {**native_timing, field: value}),
                 "Native")
    native_hil = {
        "gate": "board-hil", "applicable": True, "status": "passed",
        "device": DEVICE, "chip_id": "SYNTHETIC-NEVER-A-BOARD",
        "verification_tier": SCALER_TIER, "requested_runs": 3,
        "deterministic_across_runs": True,
    }
    native_requirements("board-hil", native_hil)
    e.assertion("synthetic positive native scaler tier requirements", True, True)
    for update in ({"reg57_probe": {"stdout": "Read register 57"}},
                   {"verification_tier": BRIDGE_TIER}, {"verification_tier": None}):
        rejected("register-only/unspecified HIL tier", lambda update=update:
                 native_requirements("board-hil", {**native_hil, **update}), BRIDGE_TIER)
    for update in ({"requested_runs": 2}, {"deterministic_across_runs": False}):
        rejected("incomplete native workload runs", lambda update=update:
                 native_requirements("board-hil", {**native_hil, **update}), "deterministic")
    # A legacy root-level success token must not become a fallback authority.
    fallback = e.path / "synthetic-fallback-root"
    fallback.mkdir()
    (fallback / "timequest.json").write_text(json.dumps(native_timing))
    with patch("physical_evidence.ROOT", fallback):
        from physical_evidence import validate_native_gate
        rejected("root-level timequest fallback",
                 lambda: validate_native_gate(e, "quartus-sta"),
                 "Authoritative runtime receipt unavailable")
    sta_fixture_checks(e, rejected)
    hardware_fixture_checks(e, rejected)
    link_fixture_checks(e, native_timing, rejected)
    observe_retained_native(e, rejected)
    write_inventory(e)


def sta_fixture_checks(e, rejected):
    from physical import POSTFIT_TCL, validate_sta

    c = Config()
    counter = 0

    def fixture(defect=None):
        nonlocal counter
        directory = e.path / "synthetic-sta" / str(counter)
        counter += 1
        output = directory / "output_files"
        output.mkdir(parents=True)
        payload = {
            "sources": {name: digest for name, digest in hashes().items()
                        if not name.startswith("tests/")},
            "external_inputs": {},
            "parameters": {**{k: str(v) for k, v in c.parameters().items()},
                           "DEVICE": DEVICE, "TOP": "scaler"},
        }
        manifest = {**payload, "build_id": hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()}
        (directory / "source_manifest.json").write_text(json.dumps(manifest))
        qsf = (
            f"set_global_assignment -name DEVICE {DEVICE}\n"
            "set_global_assignment -name TOP_LEVEL_ENTITY swage_s1_top\n"
            + "".join(f'set_global_assignment -name SYSTEMVERILOG_FILE "{path}"\n'
                      for path in sorted((ROOT / "rtl").glob("*.sv")))
            + f'set_global_assignment -name SDC_FILE "{ROOT / "fpga/scaler.sdc"}"\n'
            + "".join(f"set_parameter -name {key} {value}\n"
                      for key, value in c.parameters().items())
        )
        (directory / "swage_s1.qsf").write_text(qsf)
        (directory / "swage_s1.qpf").write_text("SYNTHETIC ONLY\n")
        (directory / "extra_sta.tcl").write_text(POSTFIT_TCL)
        map_text = "".join(f"; bank{i} ; M20K block ; Simple Dual Port ; 8 ;\n"
                           for i in range(4))
        (output / "swage_s1.map.rpt").write_text(
            f"SYNTHETIC ONLY\nDevice : {DEVICE}\n" + map_text)
        fit_text = (
            f"SYNTHETIC ONLY\nDevice : {DEVICE}\nFitter was successful\n"
            "Logic utilization (in ALMs) : 219 / 427200\n"
            "Total registers : 160\nTotal block memory bits : 256 / 55562240\n"
            "Total RAM Blocks : 4 / 2713\nTotal DSP Blocks : 6 / 1518\n"
        )
        (output / "swage_s1.fit.summary").write_text(fit_text)
        categories = (
            "Illegal Clocks", "Unconstrained Clocks", "Unconstrained Input Ports",
            "Unconstrained Input Port Paths", "Unconstrained Output Ports",
            "Unconstrained Output Port Paths",
        )
        (directory / "unconstrained.rpt").write_text(
            "\n".join(f"; {name} ; 0 ; 0 ;" for name in categories))
        (directory / "fmax.rpt").write_text("SYNTHETIC ONLY ; 105.0 MHz ;\n")
        raw = (
            "SYNTHETIC ONLY\nNETLIST_MODE:post_fit\nCLOCK:scaler_clk:10.000\n"
            "CORNER:slow\nSETUP_PATH|0.495\nHOLD_PATH|0.059\n"
            "CORNER:fast\nSETUP_PATH|0.600\nHOLD_PATH|0.100\n"
            "TimeQuest Timing Analyzer was successful\n"
        )
        from physical import parse_postfit_output
        timing = {**parse_postfit_output(raw), "device": DEVICE,
                  "configuration": c.parameters(), "build_id": manifest["build_id"],
                  "origin": "direct_quartus_sta"}
        commands = []
        for index, (argv, cwd, text) in enumerate((
            (["quartus_sh", "--flow", "compile", str(directory / "swage_s1")], ROOT,
             "SYNTHETIC ONLY: compile successful"),
            (["quartus_sta", "-t", "extra_sta.tcl"], directory, raw),
        )):
            stdout, stderr = directory / f"{index}.stdout", directory / f"{index}.stderr"
            stdout.write_text(text)
            stderr.write_text("")
            commands.append({
                "requested_argv": argv, "cwd": e.relative(cwd), "exit_status": 0,
                "end": "synthetic-only", "timed_out": False,
                "stdout": e.relative(stdout), "stderr": e.relative(stderr),
            })
        if defect == "negative-hold":
            timing["wns_hold_ns"] = -0.187
        elif defect == "post-map":
            timing["netlist_mode"] = "post_map"
        elif defect == "stale-source":
            manifest["sources"]["rtl/swage_s1_top.sv"] = "0" * 64
            (directory / "source_manifest.json").write_text(json.dumps(manifest))
        elif defect == "build-id":
            timing["build_id"] = "0" * 64
        elif defect == "configuration":
            timing["configuration"] = Config(mode=1).parameters()
        elif defect == "qsf-configuration":
            (directory / "swage_s1.qsf").write_text(
                qsf.replace("set_parameter -name PHASE_FRAC 8", "set_parameter -name PHASE_FRAC 16"))
        elif defect == "raw-slack":
            (directory / "1.stdout").write_text(raw.replace("0.059", "-0.187"))
        elif defect == "raw-post-map":
            (directory / "1.stdout").write_text(raw.replace("post_fit", "post_map"))
        elif defect == "failed-compile":
            commands[0]["exit_status"] = 1
        elif defect == "timeout":
            commands[1]["timed_out"] = True
        elif defect == "missing-compile":
            commands = commands[1:]
        elif defect == "memory-scope":
            (output / "swage_s1.fit.summary").write_text(
                fit_text.replace("Total RAM Blocks : 4", "Total RAM Blocks : 0"))
        elif defect == "ucp":
            (directory / "unconstrained.rpt").write_text(
                "\n".join(f"; {name} ; 0 ; 1 ;" for name in categories))
        elif defect == "fmax":
            (directory / "fmax.rpt").write_text("SYNTHETIC ONLY ; 99.99 MHz ;\n")
        elif defect == "wrong-fitted-device":
            (output / "swage_s1.fit.summary").write_text(
                fit_text.replace(DEVICE, "OTHER-DEVICE"))
        elif defect == "reference-clock-only":
            changed = raw.replace("CLOCK:scaler_clk:10.000", "CLOCK:pcie_reference:10.000")
            (directory / "1.stdout").write_text(changed)
            timing = {**parse_postfit_output(changed), "device": DEVICE,
                      "configuration": c.parameters(), "build_id": manifest["build_id"],
                      "origin": "direct_quartus_sta"}
        (directory / "timequest.json").write_text(json.dumps(timing))
        record = {"synthetic_only": True, "commands": commands}
        (directory / "synthetic-record.json").write_text(json.dumps(record, indent=2))
        fake = SimpleNamespace(record=record, relative=e.relative)
        return lambda: validate_sta(fake, c, directory)

    result = fixture()()
    e.assertion("synthetic full raw/source-bound STA positive",
                (result["wns_setup_ns"], result["wns_hold_ns"]), ([0.495], [0.059]))
    for defect, diagnostic in (
        ("negative-hold", "differs from successful raw"),
        ("post-map", "differs from successful raw"),
        ("stale-source", "source inventory"),
        ("build-id", "binding differs"),
        ("configuration", "binding differs"),
        ("qsf-configuration", "QSF configuration"),
        ("raw-slack", "differs from successful raw"),
        ("raw-post-map", "post-fit netlist"),
        ("failed-compile", "execution failed"),
        ("timeout", "execution failed"),
        ("missing-compile", "unique actual physical command"),
        ("memory-scope", "Fitted embedded memory"),
        ("ucp", "Unconstrained paths"),
        ("fmax", "fMAX"),
        ("wrong-fitted-device", "Fitted report exact device"),
        ("reference-clock-only", "scaler's actual clock"),
    ):
        rejected(defect, fixture(defect), diagnostic)


def hardware_fixture_checks(e, rejected):
    """Reparse synthetic raw transport with the real independent HIL validator."""
    from partitions import validate_hardware
    from reference import image, integer_reference
    import os
    import struct

    c = Config(2, 2, 3, 3)
    counter = 0

    def fixture(defect=None):
        nonlocal counter
        root = e.path / "synthetic-hardware" / str(counter)
        counter += 1
        directory = root / c.label
        output = directory / "output_files"
        output.mkdir(parents=True)
        board_path = root / "synthetic-board.json"
        board = {"device": DEVICE, "vendor_id": "0x1172", "device_id": "0xe003",
                 "cable": "SYNTHETIC NEVER PROGRAM", "jtag_index": 1,
                 "board-system": e.relative(root / "system"),
                 "board-settings": e.relative(root / "settings"),
                 "board-sdc": e.relative(root / "constraints"),
                 "dependencies": [e.relative(root / "synthetic-ip")]}
        for key in ("board-system", "board-settings", "board-sdc"):
            (ROOT / board[key]).write_text("SYNTHETIC ONLY\n")
        for name in board["dependencies"]:
            (ROOT / name).write_text("SYNTHETIC ONLY\n")
        board_path.write_text(json.dumps(board))
        sof = output / "swage_s1.sof"
        sof.write_bytes(b"SYNTHETIC NOT A BITSTREAM")
        build_id = "1" * 64
        timing = {"configuration": c.parameters(), "build_id": build_id}
        identity = {
            "abi": 0x53573101, "build_id": build_id,
            "source_shape": c.sh << 16 | c.sw, "destination_shape": c.dh << 16 | c.dw,
            "format": c.mode << 24 | c.frac << 16 | c.channels << 8 | c.bits,
        }
        if defect == "image-id":
            identity["build_id"] = "0" * 64
        (directory / "hardware-identity.json").write_text(json.dumps(identity))
        result = {"configuration": c.parameters(), "timing": timing,
                  "programmed_sof_sha256": file_hash(sof), "passes": []}
        link = {"current_link_speed": "8.0 GT/s PCIe", "current_link_width": "8",
                "vendor": board["vendor_id"], "device": board["device_id"]}
        labels = ["initial", f"programmed-{c.label}",
                  *[f"pass-{index}-{c.label}" for index in range(3)]]
        observations = [{"label": label, **link} for label in labels]
        if defect == "gen2":
            observations[1]["current_link_speed"] = "5.0 GT/s PCIe"
        elif defect == "x4":
            observations[2]["current_link_width"] = "4"
        for pass_index in range(3):
            events, digest = [], hashlib.sha256()
            for pattern in ("random", "horizontal", "vertical", "checker", "full", "impulse"):
                inputs, outputs = [], []
                for frame in range(3):
                    pixels = image(c, pattern, 2026, frame)
                    inputs.extend(p | (int(index == 0) << 24)
                                  | (int(index % c.sw == c.sw-1) << 25)
                                  for index, p in enumerate(pixels))
                    outputs.extend(p | (sof << 24) | (eol << 25)
                                   for p, sof, eol in integer_reference(c, pixels))
                events.append({
                    "reset_aborted_inputs": 1, "held_status": 2, "aborted_input_count": 1,
                    "aborted_output_count": 0,
                    "post_reset": {"inputs": 0, "outputs": 0, "pending": 0},
                })
                events.extend({"input": index, "word": word}
                              for index, word in enumerate(inputs))
                events.append({"hold_seconds": 0.001, "output_count": 0,
                               "after_status": 0xa, "after_output_count": 0})
                events.extend({"output": index, "actual": word, "expected": word}
                              for index, word in enumerate(outputs))
                events.append({"conservation": {
                    "inputs": len(inputs), "outputs": len(outputs), "pending_output": 0,
                }})
                for word in outputs:
                    digest.update(struct.pack("<I", word))
            if defect == "pixel" and pass_index == 1:
                next(event for event in events if "output" in event)["actual"] ^= 1
            if defect == "sideband" and pass_index == 0:
                next(event for event in events if "output" in event)["actual"] ^= 1 << 24
            if defect == "stall" and pass_index == 0:
                next(event for event in events if "hold_seconds" in event)["hold_seconds"] = 0
            if defect == "register-only":
                events = [{"register": 57, "value": 178}]
            trace = directory / f"hardware-pass-{pass_index}.jsonl"
            trace.write_text("".join(json.dumps(event) + "\n" for event in events))
            result["passes"].append({
                "pass": pass_index, "compared_pixels": 18 * c.dw * c.dh, "frames": 18,
                "host_stalls": 6, "build_id": build_id, "link": link,
                "returned_sha256": digest.hexdigest(), "raw_transport": e.relative(trace),
            })
        if defect == "two-passes":
            result["passes"].pop()
        programmer_out, programmer_err = root / "program.stdout", root / "program.stderr"
        programmer_out.write_text("SYNTHETIC ONLY: programming was successful\n")
        programmer_err.write_text("")
        program = {
            "requested_argv": ["quartus_pgm", "-c", board["cable"], "-m", "JTAG", "-o",
                               f"p;{e.relative(sof)}@1"],
            "exit_status": 0, "end": "synthetic-only", "timed_out": False,
            "stdout": e.relative(programmer_out), "stderr": e.relative(programmer_err),
        }
        if defect == "programming":
            program["exit_status"] = 1
        names = [e.relative(board_path), *(board[key] for key in (
            "board-system", "board-settings", "board-sdc")), *board["dependencies"]]
        record = {
            "synthetic_only": True, "partition_result": result, "commands": [program],
            "physical_link_observations": observations,
            "board_input_sha256": {name: file_hash(ROOT / name) for name in names},
        }
        (root / "synthetic-record.json").write_text(json.dumps(record, indent=2))

        def run():
            with patch.dict(os.environ, {"SWAGE_BOARD_CONFIG": e.relative(board_path)}):
                # Only fitted STA is synthetic here; raw pixel/link/image/transport
                # checking below is the unchanged production HIL validator.
                with patch("physical.validate_sta", return_value=timing):
                    return validate_hardware(
                        e, root / "synthetic-record.json", record,
                        {"configuration": c.parameters()})
        return run

    fixture()()
    e.assertion("synthetic positive three-pass HIL trace reparse", True, True)
    for defect, diagnostic in (
        ("image-id", "hardware registers"), ("gen2", "identity/link"),
        ("x4", "identity/link"), ("pixel", "pixel/sideband"),
        ("sideband", "pixel/sideband"), ("stall", "output stall"),
        ("two-passes", "Three consecutive"), ("programming", "programming"),
    ):
        rejected(defect, fixture(defect), diagnostic)
    # Register-only traces are rejected explicitly before attempting a workload
    # parser; no KeyError or missing-file accident is counted as a method pass.
    rejected("register-only native HIL",
             lambda: native_requirements("board-hil", {
                 "gate": "board-hil", "applicable": True, "status": "passed",
                 "reg57_probe": {"exit_code": 0}, "requested_runs": 3,
             }), BRIDGE_TIER)


def link_fixture_checks(e, native_timing, rejected):
    from partitions import check_definitions

    directory = e.path / "synthetic-bindings"
    directory.mkdir()
    specs = [spec for spec in check_definitions() if spec["check"] == "quartus-sta-shard"]
    results, found, links = {}, {}, []
    for spec in specs:
        path = directory / f"{spec['id']}.json"
        result = {
            "configuration": spec["configuration"], "build_id": "1" * 64,
            "wns_setup_ns": [0.495], "wns_hold_ns": [0.059],
        }
        path.write_text(json.dumps({"synthetic_only": True, "result": result}))
        found[spec["id"]] = (path, {})
        results[spec["id"]] = result
        links.append({"id": spec["id"], "receipt": e.relative(path), "sha256": file_hash(path)})
    native = {
        **native_timing, "product_evidence": links,
        "execution_receipt": links[0], "build_id": "1" * 64,
        "parameters": specs[0]["configuration"],
        "source_sha256": {name: value for name, value in e.record["source_sha256"].items()
                          if not name.startswith("tests/")},
    }

    def run(record):
        return validate_linked_products(
            e, "quartus-sta", record, found, specs,
            lambda evidence, available, ids: {ident: results[ident] for ident in ids},
            lambda available, ident: available[ident],
        )

    e.assertion("synthetic exact 192-configuration binding coverage", len(run(native)), 192)
    for gate, check in (("timing", "quartus-sta-shard"), ("hardware", "board-hil-shard")):
        expected = [spec["configuration"] for spec in check_definitions()
                    if spec["check"] == check]
        configuration_inventory(expected, expected)
        rejected("incomplete " + gate, lambda: configuration_inventory(expected[:-1], expected),
                 "Incomplete")
        rejected("duplicate " + gate, lambda: configuration_inventory(
            [*expected[:-1], expected[0]], expected), "Duplicate")
    for defect, diagnostic in (
        ("hash", "Stale/mismatched"), ("path", "Stale/mismatched"),
        ("coverage", "Incomplete"), ("duplicate", "Duplicate"),
        ("source", "source identity"), ("build", "build/configuration"),
        ("primary", "raw execution"), ("slack", "slack differs"),
    ):
        altered = copy.deepcopy(native)
        if defect == "hash":
            altered["product_evidence"][0]["sha256"] = "0" * 64
        elif defect == "path":
            altered["product_evidence"][0]["receipt"] = "evidence/not-the-bound-receipt.json"
        elif defect == "coverage":
            altered["product_evidence"].pop()
        elif defect == "duplicate":
            altered["product_evidence"][-1] = altered["product_evidence"][0]
        elif defect == "source":
            altered["source_sha256"] = "opaque-unverifiable-digest"
        elif defect == "build":
            altered["build_id"] = "0" * 64
        elif defect == "primary":
            altered["execution_receipt"] = None
        else:
            altered["sta_summary"]["wns_hold_ns"] = 0.123
        rejected("native binding " + defect, lambda altered=altered: run(altered), diagnostic)
    rejected("missing product links", lambda: product_links(native_timing, specs),
             "lacks source/build")


def observe_retained_native(e, rejected):
    from physical import board_hil, quartus_sta

    observations = []
    for gate in ("quartus-sta", "board-hil"):
        path = ROOT / NATIVE_DIRECTORY / f"{gate}.json"
        require(path.is_file(), f"Required retained diagnostic input unavailable: {e.relative(path)}")
        before = file_hash(path)
        record = json.loads(path.read_text())
        try:
            native_requirements(gate, record)
        except AssertionError as error:
            failure = str(error)
        else:
            failure = None
        observation = {
            "original_receipt": e.relative(path), "sha256": before,
            "scope": native_scope(gate, record), "product_validation_error": failure,
            "historical_observation_only": True,
        }
        if gate == "quartus-sta":
            directory = Path(record["project_dir"])
            require(directory.resolve().is_relative_to((ROOT / NATIVE_DIRECTORY).resolve()),
                    "Native report directory escapes its runtime run")
            inputs = [directory / "top.map.rpt", directory / "top.fit.summary"]
            require(all(path.is_file() for path in inputs), "Native resource reports missing")
            observation["resource_reports"] = {
                e.relative(path): file_hash(path) for path in inputs}
            observation["resource_scope"] = resource_scope(
                inputs[0].read_text(), inputs[1].read_text())
            reports = [directory / name for name in (
                "top.qsf", "timing.sdc", "top.map.rpt", "top.fit.rpt",
                "top.fit.summary", "top.sta.rpt", "top.sta.ucp.rpt",
            )]
            require(all(report.is_file() for report in reports),
                    "Retained raw Quartus scope reports missing")
            observation["raw_report_sha256"] = {
                e.relative(report): file_hash(report) for report in reports}
            observation["staged_rtl_sha256"] = {
                e.relative(source): file_hash(source)
                for source in sorted((directory / "rtl_staged").glob("*.sv"))}
            observation["staging_source_pairs"] = [
                {"source": e.relative(ROOT / "rtl" / source.name),
                 "source_sha256": file_hash(ROOT / "rtl" / source.name),
                 "staged": e.relative(source), "staged_sha256": file_hash(source),
                 "byte_identical": file_hash(source) == file_hash(ROOT / "rtl" / source.name)}
                for source in sorted((directory / "rtl_staged").glob("*.sv"))]
        if failure is not None:
            count = len(e.record["commands"])
            rejected("retained actual " + gate + " cannot certify product",
                     lambda: (quartus_sta if gate == "quartus-sta" else board_hil)(e),
                     failure)
            e.assertion("no duplicate native execution for " + gate,
                        len(e.record["commands"]), count)
        require(file_hash(path) == before, "Runtime receipt changed during bounded read")
        observations.append(observation)
    (e.path / "retained-native-observations.json").write_text(json.dumps(observations, indent=2)+"\n")
    e.record["retained_native_observations"] = observations
    e.save()


def write_inventory(e):
    from evidence import partition_output_prefix
    from partitions import check_definitions, execution_order

    definitions = check_definitions()
    physical = [spec for spec in definitions
                if spec["check"] in ("quartus-sta-shard", "board-hil-shard")]
    unbound = [spec for spec in physical if partition_output_prefix(
        spec["check"], spec["shard"], spec["simulator"]) is None]
    inventory = {
        "advisory_only": True, "executed": False, "selectors_invented": False,
        "implemented_physical_definitions": physical,
        "remaining_append_only_admission": unbound,
        "counts": {"timing": sum(spec["check"] == "quartus-sta-shard" for spec in unbound),
                   "hardware": sum(spec["check"] == "board-hil-shard" for spec in unbound)},
        "execution_order": [step for step in execution_order(definitions)
                            if step["id"] in {spec["id"] for spec in physical}
                            or step["id"] == "behavior-shard-icarus-154"],
        "constraints": [
            "Shared runtime determines exact predecessor restoration availability first.",
            "If unavailable: fresh behavior 154 -> timing 178 -> timing 166; stop on failure.",
            "Hardware is serialized: 10 -> 0 -> 1 -> ... (excluding repeated 10).",
            "No unadmitted command may execute; no new output selectors are assigned here.",
            "Variant builds use Quartus tools directly, not duplicate vulcan native gates.",
            "All seven original gates remain mandatory.",
        ],
        "native_evidence_contract": {
            "authoritative_receipts": [str(NATIVE_DIRECTORY / f"{gate}.json")
                                       for gate in ("quartus-sta", "board-hil")],
            "required_bindings": [
                "device: exact target; parameters: explicit complete configuration",
                "source_sha256: current design source hash mapping, not opaque digest",
                "product_evidence: every existing physical partition id/receipt/sha256",
                "execution_receipt: exact member of product_evidence for native build",
                "build_id: current source manifest identity of execution_receipt",
                "board verification_tier: L3_PROGRAMMED_SCALER_VERIFICATION",
            ],
            "missing_binding_action": "Coordinator diagnosis and independent admission; "
                                      "Verifier never edits or upgrades native receipts",
        },
        "designer_documentation_handoff": "SPEC.md: distinguish bridge connectivity from "
            "programmed-scaler verification; report measured 219 ALMs, 160 registers, "
            "256 memory bits, six DSPs, four synthesis M20K instances versus zero "
            "fitted RAM blocks in current reports. Memory acceptance remains unestablished.",
    }
    path = e.path / "remaining-physical-admission-inventory.json"
    path.write_text(json.dumps(inventory, indent=2) + "\n")
    e.assertion("remaining physical admission counts", inventory["counts"],
                {"timing": 190, "hardware": 48})
    e.record["remaining_physical_inventory"] = {"path": e.relative(path), "sha256": file_hash(path)}
    e.save()
