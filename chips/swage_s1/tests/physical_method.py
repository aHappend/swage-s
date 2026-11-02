"""Synthetic physical-evidence method fixtures; no FPGA/tool acceptance claims.

Run only the bounded Python diagnostic:
    python3 tests/verify.py --probe physical-method

Product validation always requires current-source-bound partition reports and
programmed-scaler workloads. Native baseline observations use the supported
runtime schema; connectivity cannot substitute for the product collector.
"""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evidence import ROOT, file_hash, hashes, require
from physical_evidence import (
    BRIDGE_TIER, DEVICE, NATIVE_DIRECTORY, SCALER_TIER, configuration_inventory,
    native_requirements, native_scope, resource_scope, timing_summary,
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
        "sta_summary": summary, "source_sha256": "1" * 64,
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
        "deterministic_across_runs": True, "source_sha256": "1" * 64,
    }
    native_requirements("board-hil", native_hil)
    e.assertion("synthetic positive native scaler tier requirements", True, True)
    for update in ({"reg57_probe": {"stdout": "Read register 57"}},
                   {"verification_tier": BRIDGE_TIER}, {"verification_tier": None}):
        observation = {**native_hil, **update}
        native_requirements("board-hil", observation)
        e.assertion("register-only/unspecified native HIL remains connectivity",
                    native_scope("board-hil", observation)["tier"], BRIDGE_TIER)
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
    coverage_fixture_checks(e, rejected)
    native_gate_fixture_checks(e, native_timing, native_hil, rejected)
    product_entrypoint_fixture_checks(e, native_timing, native_hil)
    shard_entrypoint_fixture_checks(e)
    observe_retained_native(e)
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
    from partitions import check_definitions, validate_partition_result
    spec = next(spec for spec in check_definitions() if spec["check"] == "board-hil-shard")
    rejected("register-only product HIL",
             lambda: validate_partition_result(e, e.path / "register-only.json", {
                 "partition_result": {"status": "passed", "requested_runs": 3,
                                      "reg57_probe": {"exit_code": 0}},
             }, spec), "configuration")


def coverage_fixture_checks(e, rejected):
    from partitions import check_definitions

    for gate, check in (("timing", "quartus-sta-shard"), ("hardware", "board-hil-shard")):
        expected = [spec["configuration"] for spec in check_definitions()
                    if spec["check"] == check]
        configuration_inventory(expected, expected)
        e.assertion("synthetic exact " + gate + " coverage", len(expected),
                    192 if gate == "timing" else 48)
        rejected("incomplete " + gate, lambda: configuration_inventory(expected[:-1], expected),
                 "Incomplete")
        rejected("duplicate " + gate, lambda: configuration_inventory(
            [*expected[:-1], expected[0]], expected), "Duplicate")
        rejected("empty observed " + gate,
                 lambda: configuration_inventory([], expected), "Empty physical configuration")
    rejected("empty expected and observed configurations",
             lambda: configuration_inventory([], []), "Empty physical configuration")


def native_gate_fixture_checks(e, native_timing, native_hil, rejected):
    """Exercise the public validator using only labelled project-local fixtures."""
    from partitions import check_definitions
    from physical_evidence import validate_native_gate

    directory = e.path / "synthetic-native-gates"
    native_directory = directory / "native"
    native_directory.mkdir(parents=True)
    definitions = check_definitions()
    for gate, baseline in (("quartus-sta", native_timing), ("board-hil", native_hil)):
        specs = [spec for spec in definitions if spec["check"] == gate + "-shard"]
        results = [{"configuration": spec["configuration"],
                    "synthetic_measurement": index}
                   for index, spec in enumerate(specs)]
        native = {key: baseline[key] for key in (
            "gate", "status", "source_sha256",
            *(("sta_summary",) if gate == "quartus-sta"
              else ("requested_runs", "deterministic_across_runs")),
        )}
        if gate == "quartus-sta":
            native["sta_summary"] = {key: baseline["sta_summary"][key] for key in (
                "netlist_mode", "wns_setup_ns", "wns_hold_ns")}

        def run(record, *, definitions_override=None, returned=None, failure=None):
            path = native_directory / f"{gate}.json"
            path.write_text(json.dumps(record))
            fixture = SimpleNamespace(
                record={"source_sha256": e.record["source_sha256"]},
                relative=e.relative, save=lambda: None,
            )
            try:
                with (
                    patch("physical_evidence.ROOT", directory),
                    patch("physical_evidence.NATIVE_DIRECTORY", Path("native")),
                    patch("partitions.check_definitions", return_value=(
                        definitions if definitions_override is None else definitions_override)),
                    patch("partitions.aggregate", return_value=(
                        results if returned is None else returned),
                        side_effect=AssertionError(failure) if failure else None) as collector,
                ):
                    result = validate_native_gate(fixture, gate)
                    collector.assert_called_once_with(fixture, gate)
                    return result
            except AssertionError:
                e.assertion("synthetic rejected native " + gate + " remains not validated",
                            fixture.record["authoritative_physical_evidence"]["status"],
                            "not_validated")
                raise
            finally:
                observation = fixture.record.get("authoritative_physical_evidence", {})
                e.assertion("synthetic native " + gate + " preserved scope",
                            observation.get("scope"), native_scope(gate, record))
                if observation.get("status") == "validated":
                    require(definitions_override != [],
                            "Empty fixture incorrectly marked validated")

        result = run(native)
        e.assertion("synthetic end-to-end " + gate + " standard schema positive",
                    len(result["configurations"]), len(specs))
        e.assertion("synthetic end-to-end " + gate + " returns measured results",
                    result["configurations"], results)
        e.assertion("synthetic end-to-end " + gate + " validated positive",
                    result["native"]["status"], "validated")
        rejected("end-to-end " + gate + " collector failure propagation",
                 lambda: run(native, failure="synthetic collector rejection"),
                 "synthetic collector rejection")
        rejected("end-to-end " + gate + " incomplete coverage",
                 lambda: run(native, returned=results[:-1]), "Incomplete")
        rejected("end-to-end " + gate + " duplicate coverage",
                 lambda: run(native, returned=[*results[:-1], results[0]]), "Duplicate")
        rejected("end-to-end " + gate + " missing source digest",
                 lambda: run({**native, "source_sha256": None}), "source identity")
        for identity in ("opaque-invalid-digest", {}, {"rtl/test.sv": "invalid"}):
            rejected("end-to-end " + gate + " malformed source digest",
                     lambda identity=identity: run({**native, "source_sha256": identity}),
                     "source identity")
        result = run({**native, "source_sha256": {"rtl/test.sv": "2" * 64}})
        e.assertion("standard native source map supported", result["native"]["source_sha256"],
                    {"rtl/test.sv": "2" * 64})
        rejected("end-to-end " + gate + " failed native baseline",
                 lambda: run({**native, "status": "failed"}), "Native gate failed")
        if gate == "quartus-sta":
            for key, value, diagnostic in (
                ("netlist_mode", "post_map", "Post-map"),
                ("wns_setup_ns", -0.001, "setup slack"),
                ("wns_hold_ns", -0.187, "hold slack"),
            ):
                rejected("minimal native summary rejects " + key,
                         lambda key=key, value=value: run({
                             **native, "sta_summary": {**native["sta_summary"], key: value},
                         }), diagnostic)
        rejected("end-to-end " + gate + " empty configuration coverage",
                 lambda: run(native, definitions_override=[]), "Empty physical configuration")
        if gate == "board-hil":
            for flag in ("absent", False, True):
                altered = {**native, "reg57_probe": {"stdout": "Read register 57"}}
                if flag != "absent":
                    altered["require_scaler_workload"] = flag
                result = run(altered)
                e.assertion("native register probe never upgraded to scaler tier",
                            result["native"]["scope"]["tier"], BRIDGE_TIER)


def product_entrypoint_fixture_checks(e, native_timing, native_hil):
    """Keep the dispatcher, aggregate, dependency runner and coverage checks real."""
    from contextlib import redirect_stderr, redirect_stdout
    import datetime
    import io
    import sys

    import partitions
    import verify

    definitions = partitions.check_definitions()
    by_id = {spec["id"]: spec for spec in definitions}
    original_result_validator = partitions.validate_partition_result
    directory = e.path / "synthetic-product-entrypoints"
    native_directory = directory / "native"
    native_directory.mkdir(parents=True)
    for gate, baseline in (("quartus-sta", native_timing), ("board-hil", native_hil)):
        specs = [spec for spec in definitions if spec["check"] == gate + "-shard"]
        cases = ["complete", "missing-prerequisites", "missing-partition", "incomplete-configurations",
                 "different-configuration"]
        if gate == "board-hil":
            cases.append("register-only")
        for case in cases:
            record = dict(baseline)
            if gate == "board-hil":
                record["verification_tier"] = BRIDGE_TIER
                record["reg57_probe"] = {"stdout": "SYNTHETIC register-57 reads only"}
            (native_directory / f"{gate}.json").write_text(json.dumps(record))
            label = f"{gate}-{case}"
            fixture_directory = directory / label
            fixture_directory.mkdir()
            fixture_record = {
                "fixture_scope": "SYNTHETIC_ONLY_NOT_PHYSICAL_ACCEPTANCE",
                "acceptance_evidence": False,
                "source_sha256": e.record["source_sha256"],
                "start": e.record["start"], "commands": [],
            }
            found = {}
            required = partitions.prerequisite_plan(gate, {})
            needed = set(required["mandatory_partitions"] + required["required_predecessors"])
            for index, spec in enumerate(definitions):
                if (spec["id"] not in needed or case == "missing-prerequisites"
                        or case == "missing-partition" and spec == specs[-1]):
                    continue
                path = fixture_directory / f"{spec['id']}.json"
                start = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
                start += datetime.timedelta(seconds=index * 2)
                result = {"configuration": spec.get("configuration"),
                          "synthetic_measurement": spec["id"]}
                if spec == specs[-1]:
                    if case == "incomplete-configurations":
                        result["configuration"] = specs[0]["configuration"]
                    elif case == "different-configuration":
                        result["configuration"] = {
                            **spec["configuration"], "SRC_WIDTH": 4095,
                        }
                if case == "register-only" and spec["check"] == "board-hil-shard":
                    result = {"status": "passed", "requested_runs": 3,
                              "reg57_probe": {"exit_code": 0}}
                retained = {
                    "synthetic_only": True, "acceptance_evidence": False,
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

            def save():
                (fixture_directory / "synthetic-record.json").write_text(
                    json.dumps(fixture_record, indent=2) + "\n")

            def finish(error):
                fixture_record["status"] = "failed" if error else "passed"
                fixture_record["error"] = str(error) if error else None
                save()

            def no_physical_execution(*args, **kwargs):
                raise AssertionError("Synthetic entrypoint attempted physical execution")

            def validate_fixture(evidence, path, retained, spec):
                require(path.parent == fixture_directory and retained["synthetic_only"]
                        and retained["acceptance_evidence"] is False
                        and spec == by_id[spec["id"]],
                        "Unlabelled synthetic receipt")

            def observed_result(evidence, path, retained, spec):
                if case == "register-only" and spec["check"] == "board-hil-shard":
                    return original_result_validator(evidence, path, retained, spec)
                return retained["partition_result"]

            fixture = SimpleNamespace(
                path=fixture_directory, record=fixture_record,
                relative=e.relative, save=save, finish=finish,
                assertion=lambda name, actual, expected: e.assertion(
                    "synthetic aggregate " + name, actual, expected),
                run=no_physical_execution,
                tool=lambda tool: require(tool == "python3",
                                          "Synthetic entrypoint attempted a physical tool"),
            )
            aggregate = partitions.aggregate
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                patch.object(sys, "argv", ["tests/verify.py", "--check", gate]),
                patch("verify.Evidence", return_value=fixture),
                patch("physical_evidence.ROOT", directory),
                patch("physical_evidence.NATIVE_DIRECTORY", Path("native")),
                patch("partitions.successes", side_effect=lambda evidence: dict(found)),
                patch("partitions.validate_receipt", side_effect=validate_fixture),
                patch("partitions.validate_partition_result", side_effect=observed_result),
                redirect_stdout(stdout), redirect_stderr(stderr),
            ):
                code = verify.main()
                require(partitions.aggregate is aggregate
                        and not hasattr(partitions.aggregate, "_mock_name"),
                        "Entrypoint fixture replaced the production aggregate")
            (fixture_directory / "stdout.log").write_text(stdout.getvalue())
            (fixture_directory / "stderr.log").write_text(stderr.getvalue())
            # The dispatcher must finish its receipt and return failure, not
            # merely emit a diagnostic while reporting product success.
            failed = case != "complete"
            e.assertion("synthetic product entrypoint " + label + " exit",
                        code, 1 if failed else 0)
            e.assertion("synthetic product entrypoint " + label + " receipt status",
                        fixture_record["status"], "failed" if failed else "passed")
            e.assertion("synthetic product entrypoint " + label + " real collector plan",
                        "prerequisite_plan" in fixture_record, True)
            e.assertion("synthetic product entrypoint " + label + " binding status",
                        fixture_record["authoritative_physical_evidence"]["status"],
                        "not_validated" if failed else "validated")
            if failed:
                diagnostic = {
                    "missing-prerequisites": "append-only admission",
                    "missing-partition": "1 missing prerequisites require append-only admission",
                    "incomplete-configurations": "Duplicate physical configuration",
                    "different-configuration":
                        "Incomplete or different frozen physical configuration",
                    "register-only": "Observed scaler configuration missing or different",
                }[case]
                require(diagnostic in fixture_record["error"]
                        and diagnostic in stderr.getvalue(),
                        "Synthetic entrypoint failed for an unrelated reason: " + label)
                require(json.loads(stdout.getvalue())["status"] == "failed",
                        "Synthetic entrypoint printed product success: " + label)
            else:
                ledger = "sta-ledger.json" if gate == "quartus-sta" else "hil-ledger.json"
                returned = json.loads((fixture_directory / ledger).read_text())
                e.assertion("synthetic product entrypoint " + label + " measured results",
                            [result["synthetic_measurement"] for result in returned],
                            [spec["id"] for spec in specs])
            if gate == "board-hil":
                e.assertion("synthetic register-only baseline " + case + " retains tier",
                            fixture_record["authoritative_physical_evidence"]["scope"]["tier"],
                            BRIDGE_TIER)


def shard_entrypoint_fixture_checks(e):
    """Synthetic leaf tools, real entrypoint/dependency execution and receipt links."""
    from contextlib import redirect_stderr, redirect_stdout
    import copy
    import datetime
    import io
    import sys

    import partitions
    import verify
    from evidence import stamp

    definitions = partitions.check_definitions()
    by_id = {spec["id"]: spec for spec in definitions}
    target = by_id["quartus-sta-shard-0"]
    chain = ["behavior-shard-icarus-154", "quartus-sta-shard-178", "quartus-sta-shard-166"]
    directory = e.path / "synthetic-shard-entrypoints"
    directory.mkdir()
    for case in ("missing", "current", "failed-anchor", "failed-predecessor", "failed-last",
                 "unadmitted", "late-predecessor"):
        current = directory / case
        current.mkdir()
        found, launched = {}, []
        fixture_record = {
            "synthetic_only": True, "acceptance_evidence": False,
            "source_sha256": e.record["source_sha256"], "start": stamp(), "commands": [],
            "tools": {name: {"synthetic_only": True} for name in target["requires"]},
            "tool_commands": {name: 0 for name in target["requires"]},
        }
        invocation_start = fixture_record["start"]

        def retain(ident, *, late=False):
            spec = by_id[ident]
            path = current / f"{ident}.json"
            retained = {
                "synthetic_only": True, "acceptance_evidence": False,
                "partition": spec, "status": "passed", "start": stamp(),
                "predecessors": [
                    {"id": dep, "receipt": e.relative(found[dep][0]),
                     "sha256": file_hash(found[dep][0])}
                    for dep in spec["depends_on"]
                ],
                "partition_result": {"synthetic_measurement": ident},
                "end": "2099-01-01T00:00:00+00:00" if late else stamp(),
            }
            path.write_text(json.dumps(retained, indent=2) + "\n")
            found[ident] = (path, {key: retained.get(key) for key in partitions.HEADER_KEYS})

        if case in ("current", "late-predecessor"):
            for ident in chain:
                retain(ident, late=case == "late-predecessor" and ident == chain[-1])

        def save():
            (current / "synthetic-record.json").write_text(
                json.dumps(fixture_record, indent=2) + "\n")

        def finish(error):
            fixture_record["status"] = "failed" if error else "passed"
            fixture_record["error"] = str(error) if error else None
            save()

        def run(argv, **kwargs):
            spec = next(spec for spec in definitions if spec["argv"] == argv)
            ident = spec["id"]
            require(ident in chain and spec["outputs"],
                    "Synthetic shard launched an unadmitted/unrelated command")
            require(all(dep in found for dep in spec["depends_on"]),
                    "Synthetic shard launched out of dependency order")
            launched.append(ident)
            start = stamp()
            fail = ident == {
                "failed-anchor": chain[0], "failed-predecessor": chain[1],
                "failed-last": chain[2],
            }.get(case)
            index = len(e.record["commands"])
            try:
                result = e.run(
                    ["python3", "-c", "print('SYNTHETIC_DEPENDENCY " + ident + "'); "
                     + ("raise SystemExit(7)" if fail else "raise SystemExit(0)")],
                    name=f"synthetic-{case}-{ident}",
                )
            finally:
                if fail:
                    e.record["commands"][index]["negative_method_fixture"] = True
                    e.save()
                fixture_record["commands"].append({
                    "requested_argv": argv, "start": start, "end": stamp(),
                    "exit_status": 7 if fail else 0,
                })
                save()
            retain(ident)
            return result

        def validate_fixture(evidence, path, retained, spec):
            require(path.parent == current and retained["synthetic_only"]
                    and retained["partition"] == spec,
                    "Unlabelled dependency fixture")

        def target_work(evidence, c, output):
            e.assertion("synthetic target starts after complete dependency chain",
                        sorted(found), sorted(chain))
            e.assertion("synthetic target retains exact dependency order",
                        evidence.record["predecessors"], [{
                            "id": chain[-1], "receipt": e.relative(found[chain[-1]][0]),
                            "sha256": file_hash(found[chain[-1]][0]),
                        }])
            partitions.partition_start(evidence.record)
            require(evidence.record["start"] == invocation_start,
                    "Invocation start was overwritten")
            return {"configuration": c.parameters(), "synthetic_measurement": "target"}

        fixture = SimpleNamespace(
            path=current, record=fixture_record, relative=e.relative, save=save,
            finish=finish, run=run, tool=lambda name: None,
        )
        altered = [{**spec, "outputs": []} if case == "unadmitted"
                   and spec["id"] == chain[1] else spec for spec in definitions]
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "argv", target["argv"][1:]),
            patch("verify.Evidence", return_value=fixture),
            patch("partitions.check_definitions", return_value=altered),
            patch("partitions.successes", side_effect=lambda evidence: dict(found)),
            patch("partitions.validate_receipt", side_effect=validate_fixture),
            patch("partitions.validate_partition_result",
                  side_effect=lambda evidence, path, retained, spec: retained["partition_result"]),
            patch("partitions.tools_for"),
            patch("partitions.artifact_hashes", return_value={"synthetic_only": True}),
            patch("physical.compile_and_sta", side_effect=target_work) as work,
            redirect_stdout(stdout), redirect_stderr(stderr),
        ):
            code = verify.main()
        (current / "stdout.log").write_text(stdout.getvalue())
        (current / "stderr.log").write_text(stderr.getvalue())
        failed = case not in ("missing", "current")
        e.assertion("synthetic shard " + case + " exit", code, int(failed))
        e.assertion("synthetic shard " + case + " receipt",
                    fixture_record["status"], "failed" if failed else "passed")
        e.assertion("synthetic shard " + case + " target execution",
                    work.call_count, 0 if failed else 1)
        expected = {
            "missing": chain, "failed-anchor": chain[:1],
            "failed-predecessor": chain[:2], "failed-last": chain,
        }.get(case, [])
        e.assertion("synthetic shard " + case + " exact admitted dependency launches",
                    launched, expected)
        if failed:
            diagnostic = {
                "failed-predecessor": "Command exit 7",
                "failed-anchor": "Command exit 7",
                "failed-last": "Command exit 7",
                "unadmitted": "append-only admission",
                "late-predecessor": "Partition predecessor did not complete",
            }[case]
            require(diagnostic in fixture_record["error"] and diagnostic in stderr.getvalue(),
                    "Synthetic shard failed for an unrelated reason")
        if case == "missing":
            boundary = partitions.partition_start(fixture_record)
            require(boundary > datetime.datetime.fromisoformat(fixture_record["start"]),
                    "Synthetic collection did not exercise post-invocation prerequisites")
            for defect, diagnostic in (
                ("before-invocation", "precedes invocation"),
                ("after-completion", "follows receipt completion"),
                ("invalid-offset", "command boundary"),
                ("unfinished-collection", "crosses partition execution boundary"),
                ("early-work", "crosses partition execution boundary"),
            ):
                altered = copy.deepcopy(fixture_record)
                if defect == "before-invocation":
                    altered["partition_execution"]["start"] = "2026-01-01T00:00:00+00:00"
                elif defect == "after-completion":
                    altered["end"] = (boundary - datetime.timedelta(seconds=1)).isoformat()
                elif defect == "invalid-offset":
                    altered["partition_execution"]["command_offset"] = -1
                elif defect == "unfinished-collection":
                    altered["commands"][-1]["end"] = (
                        boundary + datetime.timedelta(seconds=1)).isoformat()
                else:
                    altered["commands"].append({
                        "start": (boundary - datetime.timedelta(seconds=1)).isoformat(),
                    })
                try:
                    partitions.partition_start(altered)
                except AssertionError as error:
                    require(diagnostic in str(error), "Unexpected chronology rejection")
                else:
                    raise AssertionError("Synthetic invalid execution boundary accepted: " + defect)
                e.assertion("synthetic execution boundary rejects " + defect, True, True)


def observe_retained_native(e):
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
            "scope": native_scope(gate, record), "baseline_validation_error": failure,
            "product_validation": "not executed; separate admitted partitions mandatory",
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
    restoration = [spec for spec in unbound
                   if spec["check"] == "quartus-sta-shard" and spec["shard"] in range(8, 15)]
    remaining = [spec for spec in unbound if spec not in restoration]
    inventory = {
        "advisory_only": True, "executed": False, "selectors_invented": False,
        "implemented_physical_definitions": physical,
        "unwired_physical_definitions": unbound,
        "approved_pending_executable_restoration": restoration,
        "remaining_append_only_admission": remaining,
        "counts": {"timing": sum(spec["check"] == "quartus-sta-shard" for spec in unbound),
                   "hardware": sum(spec["check"] == "board-hil-shard" for spec in unbound)},
        "remaining_admission_counts": {
            "timing": sum(spec["check"] == "quartus-sta-shard" for spec in remaining),
            "hardware": sum(spec["check"] == "board-hil-shard" for spec in remaining),
        },
        "admission_evidence_scope": (
            "Source-defined commands and output wiring only, not a reconstruction "
            "of shared-runtime executable approvals. Coordinator must independently "
            "resolve supported restoration/admission before physical collection."
        ),
        "board_prerequisites": {
            "required_project_inputs": {
                name: {"present": (ROOT / name).is_file(),
                       "sha256": file_hash(ROOT / name) if (ROOT / name).is_file() else None}
                for name in ("board/system.tcl", "board/pins.tcl",
                             "board/timing.sdc", "board/verification.json")
            },
            "configuration_override": "SWAGE_BOARD_CONFIG may name an authorized "
                                      "project-relative configuration; no default identity.",
            "configuration_keys": [
                "device", "bdf", "vendor_id", "device_id", "cable", "jtag_index",
                "board-system", "board-settings", "board-sdc", "scaler-clock",
                "dependencies",
            ],
            "provisioning": [
                "Authorized board/revision-specific Gen3 x8 Qsys generator with "
                "the exact fpga/README.md exported interface.",
                "Matching approved pins, lane order, transceivers, PCIe reference "
                "clock and PERST; actual generated 100 MHz scaler clock and CDC constraints.",
                "Complete transitive generator/IP/constraint inventory with readable "
                "origin, installed versions, authorized license use and file hashes.",
                "Authorized connected host BDF/vendor/device, writable BAR0 access "
                "and programmer cable/JTAG chain mapping; observe actual Gen3 x8.",
            ],
            "resolution_owner": "Coordinator/shared runtime supported board-support export "
                                "to Designer, then independent Verifier execution.",
            "limitation": "Missing local files do not establish unavailable hardware. "
                          "No board probing or programming was executed by this inventory.",
        },
        "execution_order": [step for step in execution_order(definitions)
                            if step["id"] in {spec["id"] for spec in physical}
                            or step["id"] == "behavior-shard-icarus-154"],
        "constraints": [
            "Shared runtime determines exact predecessor restoration availability first.",
            "If unavailable: fresh behavior 154 -> timing 178 -> timing 166; stop on failure.",
            "Hardware is serialized: 10 -> 0 -> 1 -> ... (excluding repeated 10).",
            "Approved timing shards 8-14 require supported executable restoration, "
            "not duplicate admission; allocate only after restoration is established.",
            "No unadmitted command may execute; no new output selectors are assigned here.",
            "Variant builds use Quartus tools directly, not duplicate vulcan native gates.",
            "All seven original gates remain mandatory.",
        ],
        "native_evidence_contract": {
            "authoritative_receipts": [str(NATIVE_DIRECTORY / f"{gate}.json")
                                       for gate in ("quartus-sta", "board-hil")],
            "standard_runtime_fields": [
                "gate, status, source_sha256",
                "sta_summary: post-fit mode and nonnegative setup/hold slack",
                "requested_runs=3 and deterministic_across_runs=true for HIL",
            ],
            "scope": "Native baseline only; an opaque native digest does not establish "
                     "product-source freshness or variant/workload coverage.",
            "product_bindings": "Existing admitted partition receipts independently bind "
                                "current source snapshots, configurations, build identity, "
                                "raw execution, fitted reports and programmed-scaler workloads. "
                                "Their links live in the verifier-owned aggregate receipt, "
                                "never as required custom fields on native runtime receipts.",
            "missing_product_action": "Automatically execute admitted missing prerequisites "
                                      "in dependency order; unadmitted variants require "
                                      "append-only admission and output allocation first.",
        },
        "designer_documentation_handoff": "SPEC.md: distinguish bridge connectivity from "
            "programmed-scaler verification. Use this preparation receipt's retained native "
            "observations and raw-report hashes for measured resources; do not reuse older "
            "post-map resource numbers as current fitted results. Product source/build "
            "associations and full variant coverage remain unestablished.",
    }
    path = e.path / "remaining-physical-admission-inventory.json"
    path.write_text(json.dumps(inventory, indent=2) + "\n")
    e.assertion("unwired physical counts", inventory["counts"],
                {"timing": 177, "hardware": 48})
    e.assertion("approved timing restoration inventory",
                [spec["shard"] for spec in restoration], list(range(8, 15)))
    e.assertion("remaining physical admission counts", inventory["remaining_admission_counts"],
                {"timing": 170, "hardware": 48})
    e.record["remaining_physical_inventory"] = {"path": e.relative(path), "sha256": file_hash(path)}
    e.save()
