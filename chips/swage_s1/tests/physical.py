"""Real Quartus, physical PCIe validation, and clean-source reproduction.

No simulator or software scaler is accepted as a physical transport. The board
configuration is an authorized project-relative JSON supplied with actual board
integration inputs; an absent board or tool is an explicit failing prerequisite.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil

from evidence import ROOT, file_hash, hashes, require, sources


DEVICE = "10AX115N2F45E1SG"


def relative_input(value):
    path = Path(value)
    require(not path.is_absolute() and ".." not in path.parts,
            f"External input must be project-relative: {value}")
    path = ROOT / path
    require(path.is_file(), f"Unavailable required integration input: {value}")
    require(path.resolve().is_relative_to(ROOT), "Input symlink escapes the project")
    return path


def generate_project(e, c, directory, board=None):
    require(not directory.exists(), "Quartus output must be fresh")
    command = ["quartus_sh", "-t", "fpga/create_project.tcl",
               "--output", e.relative(directory), "--top", "pcie" if board else "scaler"]
    for key, value in c.parameters().items():
        command.extend(["--" + key.lower().replace("_", "-"), str(value)])
    if board:
        for key in ("board-system", "board-settings", "board-sdc"):
            command.extend(["--" + key, board[key]])
    e.run(command)
    return validate_project(e, c, directory, board)


def validate_project(e, c, directory, board=None):
    for name in ("swage_s1.qpf", "swage_s1.qsf", "source_manifest.json"):
        require((directory / name).is_file(), f"Quartus project artifact missing: {name}")
    qsf = (directory / "swage_s1.qsf").read_text()
    def assignments(name):
        prefix = ["set_global_assignment", "-name", name]
        rows = [shlex.split(line) for line in qsf.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
        return [row[3] for row in rows if row[:3] == prefix and len(row) == 4]
    require(assignments("DEVICE") == [DEVICE]
            and assignments("TOP_LEVEL_ENTITY") == [
                "swage_s1_pcie_top" if board else "swage_s1_top"],
            "Quartus project has wrong device or top")
    expected_hdl = [*sorted((ROOT / "rtl").glob("*.sv"))]
    if board:
        expected_hdl += [ROOT / "fpga/swage_s1_host_bridge.sv",
                         ROOT / "fpga/swage_s1_pcie_top.sv"]
    require(sorted(assignments("SYSTEMVERILOG_FILE")) == sorted(map(str, expected_hdl)),
            "Actual compiled source inventory differs from the current product")
    expected_sdc = [str(ROOT / "fpga" / ("pcie.sdc" if board else "scaler.sdc"))]
    if board:
        expected_sdc.append(str(relative_input(board["board-sdc"])))
    require(sorted(assignments("SDC_FILE")) == sorted(expected_sdc),
            "Actual timing constraints differ from bound inputs")
    manifest = json.loads((directory / "source_manifest.json").read_text())
    require(manifest.get("sources") == {
        name: digest for name, digest in hashes().items() if not name.startswith("tests/")
    }, "Quartus source inventory is incomplete or stale")
    payload = {key: manifest[key] for key in ("sources", "external_inputs", "parameters")}
    digest = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("ascii")).hexdigest()
    require(manifest["build_id"] == digest and int(digest, 16) != 0,
            "Project source identity is invalid")
    for path, expected in manifest["sources"].items():
        require(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected,
                f"Project manifest source identity mismatch: {path}")
    for key, value in c.parameters().items():
        require(manifest["parameters"].get(key) == str(value),
                f"Project parameter override not applied: {key}")
        require(re.findall(r"^set_parameter -name " + re.escape(key)
                           + r'\s+"?(\d+)"?\s*$', qsf, re.MULTILINE) == [str(value)],
                f"Actual QSF configuration differs from manifest: {key}")
    require(manifest["parameters"].get("DEVICE") == DEVICE, "Manifest device differs")
    require(manifest["parameters"].get("TOP") == ("pcie" if board else "scaler"),
            "Manifest integration top differs")
    require(len(manifest["external_inputs"]) == (3 if board else 0),
            "Board input identity inventory incomplete")
    if board:
        for index, key in enumerate(("board-system", "board-settings", "board-sdc")):
            path = relative_input(board[key])
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            require(manifest["external_inputs"].get(f"board-input-{index}/{path.name}")
                    == expected, "Board input not bound to build identity")
    return manifest


def values_for_key(data, key):
    values = []
    if isinstance(data, dict):
        for name, value in data.items():
            if name == key:
                values.append(value)
            else:
                values.extend(values_for_key(value, key))
    elif isinstance(data, list):
        for item in data:
            values.extend(values_for_key(item, key))
    return values


def unconstrained_counts(text):
    categories = (
        "Illegal Clocks", "Unconstrained Clocks", "Unconstrained Input Ports",
        "Unconstrained Input Port Paths", "Unconstrained Output Ports",
        "Unconstrained Output Port Paths",
    )
    counts = {}
    for category in categories:
        rows = re.findall(
            r"^\s*;\s*" + re.escape(category)
            + r"\s*;\s*(\d+)\s*;\s*(\d+)\s*;\s*$",
            text, re.MULTILINE,
        )
        require(len(rows) == 1,
                f"TimeQuest UCP category missing, duplicated or malformed: {category}")
        setup, hold = map(int, rows[0])
        require(setup == 0 and hold == 0,
                f"Unconstrained paths in {category}: setup={setup}, hold={hold}")
        counts[category] = {"setup": setup, "hold": hold}
    return counts


POSTFIT_TCL = """package require ::quartus::project
package require ::quartus::sta
project_open swage_s1
create_timing_netlist
read_sdc
update_timing_netlist
puts "NETLIST_MODE:post_fit"
foreach_in_collection clock [get_clocks *] {
    puts "CLOCK:[get_clock_info -name $clock]:[get_clock_info -period $clock]"
}
foreach condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    puts "CORNER:$condition"
    foreach kind {setup hold} {
        set paths [get_timing_paths -$kind -npaths 1]
        if {[get_collection_size $paths] == 0} {
            error "No $kind paths for $condition"
        }
        foreach_in_collection path $paths {
            puts "[string toupper $kind]_PATH|[get_path_info -slack $path]"
        }
    }
}
report_ucp -file unconstrained.rpt
report_clock_fmax_summary -file fmax.rpt
delete_timing_netlist
project_close
"""


def parse_postfit_output(text):
    from physical_evidence import finite_number
    require(re.findall(r"^NETLIST_MODE:(\S+)$", text, re.MULTILINE) == ["post_fit"],
            "Direct STA did not use exactly one post-fit netlist")
    corners = re.findall(r"^CORNER:(.+)$", text, re.MULTILINE)
    require(corners and len(corners) == len(set(corners)),
            "Missing/duplicated operating-condition sweep")
    clocks = [{"name": name, "period_ns": float(period)}
              for name, period in re.findall(
                  r"^CLOCK:([^:]+):([0-9.]+)$", text, re.MULTILINE)]
    require(clocks and any(clock["period_ns"] == 10.0 for clock in clocks),
            "Actual 10 ns scaler clock absent")
    result = {"netlist_mode": "post_fit", "clocks": clocks, "corners": corners}
    for block in re.split(r"^CORNER:.+$", text, flags=re.MULTILINE)[1:]:
        for kind in ("SETUP", "HOLD"):
            require(len(re.findall(rf"^{kind}_PATH\|([-+0-9.eE]+)$",
                                   block, re.MULTILINE)) == 1,
                    f"Missing/duplicate {kind} paths in operating condition")
    for kind in ("setup", "hold"):
        values = [float(value) for value in re.findall(
            rf"^{kind.upper()}_PATH\|([-+0-9.eE]+)$", text, re.MULTILINE)]
        require(len(values) == len(corners) and all(finite_number(v) for v in values),
                f"Missing/invalid {kind} timing for operating conditions")
        result[f"wns_{kind}_ns"] = min(values)
        result[f"corner_{kind}_slacks_ns"] = values
    require("TimeQuest Timing Analyzer was successful" in text
            and not re.search(r"\bError(?:\s*\(|:)", text), "Raw STA execution failed")
    return result


def compile_and_sta(e, c, directory, board=None):
    """Execute only independently admitted variant builds, not native gates."""
    manifest = generate_project(e, c, directory, board)
    e.run(["quartus_sh", "--flow", "compile", directory / "swage_s1"], timeout=21600)
    analysis = directory / "extra_sta.tcl"
    analysis.write_text(POSTFIT_TCL, encoding="ascii")
    out, err, _ = e.run(["quartus_sta", "-t", "extra_sta.tcl"],
                        cwd=directory, timeout=21600)
    observed = parse_postfit_output(out + "\n" + err)
    receipt = directory / "timequest.json"
    receipt.write_text(json.dumps({
        **observed, "device": DEVICE, "configuration": c.parameters(),
        "build_id": manifest["build_id"], "origin": "direct_quartus_sta",
    }, indent=2) + "\n")
    result = validate_sta(e, c, directory, board)
    (directory / "validated.json").write_text(json.dumps(result, indent=2)+"\n")
    return result


def validate_sta(e, c, directory, board=None, *, execution=None):
    """Reparse original fitted reports without rerunning or rewriting evidence."""
    manifest = validate_project(e, c, directory, board)
    receipt = directory / "timequest.json"
    require(receipt.is_file(), "Variant STA did not retain structured evidence")
    timing = json.loads(receipt.read_text())
    require(timing.get("origin") == "direct_quartus_sta"
            and timing.get("build_id") == manifest["build_id"]
            and timing.get("configuration") == c.parameters()
            and timing.get("device") == DEVICE, "STA source/build/configuration binding differs")
    record = e.record if execution is None else execution
    validate_physical_command(e, record,
                              ["quartus_sh", "--flow", "compile", str(directory / "swage_s1")],
                              ROOT)
    command = validate_physical_command(
        e, record, ["quartus_sta", "-t", "extra_sta.tcl"], directory)
    require((directory / "extra_sta.tcl").read_text() == POSTFIT_TCL,
            "Retained post-fit analysis differs from current method")
    raw = "\n".join((ROOT / command[key]).read_text() for key in ("stdout", "stderr"))
    observed = parse_postfit_output(raw)
    scaler_clock = board.get("scaler-clock") if board else "scaler_clk"
    require(isinstance(scaler_clock, str) and scaler_clock
            and [clock["period_ns"] for clock in observed["clocks"]
                 if clock["name"] == scaler_clock] == [10.0],
            "The scaler's actual clock, not just a PCIe reference, must be 10 ns")
    require(timing == {
        **observed, "device": DEVICE, "configuration": c.parameters(),
        "build_id": manifest["build_id"], "origin": "direct_quartus_sta",
    }, "STA summary differs from successful raw execution")
    modes = values_for_key(timing, "netlist_mode")
    require(modes and all(mode == "post_fit" for mode in modes),
            "STA must explicitly identify the post-fit timing netlist")
    setup = values_for_key(timing, "wns_setup_ns")
    hold = values_for_key(timing, "wns_hold_ns")
    require(setup and hold, "Missing actual setup/hold slack in STA evidence")
    require(all(isinstance(v, (float, int)) and v >= 0 for v in setup + hold),
            "Nonnegative setup/hold slack not demonstrated")
    require(not values_for_key(timing, "post_map") or
            all(v is False for v in values_for_key(timing, "post_map")),
            "Post-map timing cannot substitute for post-fit STA")
    reports = sorted([*(directory / "output_files").glob("*.rpt"),
                      *(directory / "output_files").glob("*.summary")])
    require(reports, "Quartus compilation reports missing")
    text = "\n".join(path.read_text(errors="replace") for path in reports)
    require("Error (272006)" not in text and "Warning (10268)" not in text,
            "Quartus rejected required sequential assignment discipline")
    require(DEVICE in text, "Physical reports do not identify the exact required device")
    require("Fitter was successful" in text or re.search(
        r"Fitter Status\s*[;:|]\s*Successful", text
    ) is not None,
            "Physical resource fit not established")
    from physical_evidence import resource_scope
    map_text = (directory / "output_files/swage_s1.map.rpt").read_text()
    fit_text = (directory / "output_files/swage_s1.fit.summary").read_text()
    require(re.findall(r"^Device\s*:\s*(\S+)\s*$", fit_text, re.MULTILINE) == [DEVICE],
            "Fitted report exact device differs")
    measured_resources = resource_scope(map_text, fit_text)
    require(measured_resources["memory_acceptance"],
            "Fitted embedded memory not established; synthesis instances are a different scope: "
            + json.dumps(measured_resources, sort_keys=True))
    # Preserve raw Fitter resource rows, not generic synthesis delay estimates.
    resources = [line for line in text.splitlines()
                 if re.search(r"M20K|logic utilization|ALMs|DSP|block memory", line, re.I)]
    require(resources, "Fitted resource report absent")
    ucp_path, fmax_path = directory / "unconstrained.rpt", directory / "fmax.rpt"
    require(ucp_path.is_file() and fmax_path.is_file(), "Timing scope reports missing")
    ucp = ucp_path.read_text()
    unconstrained = unconstrained_counts(ucp)
    fmax = re.findall(r"(\d+(?:\.\d+)?)\s*MHz", fmax_path.read_text())
    require(fmax and all(float(v) >= 100.0 for v in fmax),
            "Measured fMAX evidence missing or below 100 MHz")
    sdc = (ROOT / "fpga" / ("pcie.sdc" if board else "scaler.sdc")).read_text()
    require("10.000" in sdc or board is not None, "100 MHz clock constraint missing")
    result = {
        "configuration": c.parameters(), "device": DEVICE,
        "project": e.relative(directory), "build_id": manifest["build_id"],
        "wns_setup_ns": setup, "wns_hold_ns": hold, "fmax_mhz": list(map(float, fmax)),
        "unconstrained_counts": unconstrained,
        "resources": resources, "timing_receipt": e.relative(receipt),
        "resource_scope": measured_resources,
        "report_sha256": {e.relative(path): file_hash(path) for path in
                          [*reports, ucp_path, fmax_path, receipt, directory / "extra_sta.tcl"]},
    }
    return result


def validate_physical_command(e, record, argv, cwd):
    matches = [command for command in record.get("commands", [])
               if command.get("requested_argv") == argv]
    require(len(matches) == 1, f"No unique actual physical command: {argv}")
    command = matches[0]
    require(command.get("exit_status") == 0 and command.get("end")
            and not command.get("timed_out") and not command.get("launch_error")
            and command.get("cwd") == e.relative(cwd),
            "Physical execution failed, incomplete, or used a different project")
    for key in ("stdout", "stderr"):
        name = Path(command[key])
        require(not name.is_absolute() and ".." not in name.parts
                and (ROOT / name).is_file()
                and (ROOT / name).resolve().is_relative_to((ROOT / record["commands"][0]["stdout"]).parent.resolve()),
                "Missing or escaping physical raw execution output")
    return command


def quartus_sta(e):
    from physical_evidence import validate_native_gate
    return validate_native_gate(e, "quartus-sta")


def board_hil(e):
    from physical_evidence import validate_native_gate
    return validate_native_gate(e, "board-hil")


def board_inputs(e):
    e.tool("quartus_sh")
    e.tool("quartus_sta")
    e.tool("quartus_pgm")
    e.tool("vulcan")
    config_name = os.environ.get("SWAGE_BOARD_CONFIG", "board/verification.json")
    config_file = relative_input(config_name)
    board = json.loads(config_file.read_text())
    require(isinstance(board.get("scaler-clock"), str) and board["scaler-clock"],
            "Board input must name the actual TimeQuest scaler-clock")
    for key in ("board-system", "board-settings", "board-sdc"):
        relative_input(board[key])
    require(board.get("device") == DEVICE, "Board configuration must target exact required device")
    require(re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]",
                         board["bdf"]) is not None, "Invalid PCIe BDF")
    require(isinstance(board.get("dependencies"), list) and board["dependencies"],
            "Board/IP dependency inventory is required")
    dependencies = {}
    for value in [config_name, *(board[key] for key in (
            "board-system", "board-settings", "board-sdc")), *board["dependencies"]]:
        dependencies[value] = hashlib.sha256(relative_input(value).read_bytes()).hexdigest()
    e.record["board_input_sha256"] = dependencies
    e.save()
    # The shared runtime's remote board helper requires a board-specific remote
    # test command. Local Linux PCIe MMIO is used here, never a token-only remote
    # success response. The actual JTAG programmer and every returned pixel are
    # independently checked below.
    return board, dependencies


def board_configuration(e, c):
    import fcntl
    from board_driver import run_passes, inspect_link

    board, dependencies = board_inputs(e)
    # A nonblocking advisory lock on the unchanged configuration file prevents
    # overlapping programming if the shared runtime launches two shards.
    config_name = os.environ.get("SWAGE_BOARD_CONFIG", "board/verification.json")
    with relative_input(config_name).open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _board_configuration(e, c, board, dependencies, run_passes, inspect_link)


def _board_configuration(e, c, board, dependencies, run_passes, inspect_link):
    inspect_link(e, board, "initial")
    detected, _, _ = e.run(["quartus_pgm", "-c", board["cable"], "--auto"])
    require("10AX115" in detected, "JTAG scan did not detect the Arria 10 115 device")
    directory = e.path / c.label
    timing = compile_and_sta(e, c, directory, board)
    sof = directory / "output_files" / "swage_s1.sof"
    require(sof.is_file(), "Current fitted programming image missing")
    bitstream_hash = hashlib.sha256(sof.read_bytes()).hexdigest()
    e.run(["quartus_pgm", "-c", board["cable"], "-m", "JTAG", "-o",
           f"p;{e.relative(sof)}@{int(board['jtag_index'])}"], timeout=21600)
    require(hashlib.sha256(sof.read_bytes()).hexdigest() == bitstream_hash,
            "Programming image changed")
    manifest = json.loads((directory / "source_manifest.json").read_text())
    passes = run_passes(e, board, c, manifest["build_id"], directory)
    result = {"configuration": c.parameters(), "timing": timing,
              "programmed_sof_sha256": bitstream_hash, "passes": passes}
    (e.path / "hil-ledger.json").write_text(json.dumps([result], indent=2)+"\n")
    for name, digest in dependencies.items():
        require(hashlib.sha256(relative_input(name).read_bytes()).hexdigest() == digest,
                f"Board dependency changed: {name}")
    return result


def portable(e):
    e.tool("iverilog", ("-V",))
    e.tool("vvp", ("-V",))
    e.tool("quartus_sh")
    clean = e.path / "clean"
    clean.mkdir()
    for source in sources():
        destination = clean / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    require(not any((clean / name).exists() for name in (".vulcan", ".git", ".tooling", "VULCAN")),
            "Runtime-owned metadata leaked into reproduction")
    out, _, _ = e.run(["python3", "tests/reproduce.py"], cwd=clean, timeout=14400)
    match = re.search(r"^REPRODUCTION (\S+)$", out, re.MULTILINE)
    require(match is not None, "Reproducer did not return a receipt")
    receipt = clean / match[1]
    require(receipt.resolve().is_relative_to(clean.resolve()), "Reproduction receipt escapes bundle")
    record = json.loads(receipt.read_text())
    require(record["status"] == "passed" and record["source_sha256"] == e.record["source_sha256"],
            "Clean-source comparison/project-generation did not pass on identical delivered inputs")
    require(record.get("compared_pixels", 0) > 0 and record.get("quartus_project_validated") is True,
            "Portable copy or command alone is not real reproduction")
    e.record["clean_reproduction_receipt"] = e.relative(receipt)
    e.save()
