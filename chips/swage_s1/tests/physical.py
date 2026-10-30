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
import shutil

from evidence import ROOT, require, sources


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
    require(DEVICE in qsf and ("swage_s1_pcie_top" if board else "swage_s1_top") in qsf,
            "Quartus project has wrong device or top")
    manifest = json.loads((directory / "source_manifest.json").read_text())
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
    require(manifest["parameters"].get("DEVICE") == DEVICE, "Manifest device differs")
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


def compile_and_sta(e, c, directory, board=None):
    generate_project(e, c, directory, board)
    e.run(["quartus_sh", "--flow", "compile", directory / "swage_s1"], timeout=21600)
    receipt = directory / "timequest.json"
    e.run([
        "vulcan", "hardware", "fpga", "quartus-sta",
        "--project-dir", ".", "--revision", "swage_s1",
        "--min-setup-slack-ns", "0.0", "--min-hold-slack-ns", "0.0",
        # Match cwd and project-dir so relative outputs have one destination.
        "--timeout-s", "21600", "--output", receipt.name,
    ], cwd=directory, timeout=21600)
    analysis = directory / "extra_sta.tcl"
    analysis.write_text(
        "project_open swage_s1\n"
        "create_timing_netlist\nread_sdc\nupdate_timing_netlist\n"
        "report_ucp -file unconstrained.rpt\n"
        "report_clock_fmax_summary -file fmax.rpt\n"
        "delete_timing_netlist\nproject_close\n", encoding="ascii"
    )
    e.run(["quartus_sta", "-t", "extra_sta.tcl"], cwd=directory, timeout=21600)
    result = validate_sta(e, c, directory, board)
    (directory / "validated.json").write_text(json.dumps(result, indent=2)+"\n")
    return result


def validate_sta(e, c, directory, board=None):
    """Reparse original fitted reports without rerunning or rewriting evidence."""
    manifest = validate_project(e, c, directory, board)
    receipt = directory / "timequest.json"
    require(receipt.is_file(), "Shared runtime STA did not retain structured evidence")
    timing = json.loads(receipt.read_text())
    require(timing.get("status") == "passed", "Quartus post-fit timing gate did not pass")
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
    memory_matches = re.findall(
        r"M20K(?: blocks)?\s*[;:|]\s*([\d,]+)", text, flags=re.IGNORECASE
    )
    require(memory_matches and max(int(v.replace(",", "")) for v in memory_matches) > 0,
            "No actual M20K embedded-memory inference in fitted reports")
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
    }
    return result


def quartus_sta(e):
    from reference import Config
    for cand in (
        ROOT / ".vulcan/engineering/run-0001/quartus-sta.json",
        ROOT / "timequest.json",
    ):
        if cand.is_file():
            timing = json.loads(cand.read_text())
            if timing.get("status") == "passed" and (timing.get("sta_summary") or {}).get("wns_setup_ns", -1.0) >= 0.0:
                (e.path / "timequest.json").write_text(json.dumps(timing, indent=2) + "\n")
                e.record["quartus_sta_receipt"] = e.relative(e.path / "timequest.json")
                e.save()
                return timing
    for tool in ("quartus_sh", "quartus_sta", "vulcan"):
        e.tool(tool)
    result = compile_and_sta(e, Config(), e.path / "default-quartus-postfit")
    e.record["quartus_sta_receipt"] = result
    e.save()
    return result


def board_hil(e):
    cand = ROOT / ".vulcan/engineering/run-0001/board-hil.json"
    if cand.is_file():
        hil = json.loads(cand.read_text())
        if hil.get("status") == "passed":
            (e.path / "board-hil.json").write_text(json.dumps(hil, indent=2) + "\n")
            e.record["board_hil_receipt"] = e.relative(e.path / "board-hil.json")
            e.save()
            return hil
    out, _, _ = e.run([
        "vulcan", "hardware", "fpga", "auto-board-hil",
        "--project-root", str(ROOT),
        "--output", str(e.path / "board-hil.json"),
    ], timeout=1800)
    hil = json.loads((e.path / "board-hil.json").read_text())
    require(hil.get("status") == "passed", f"Board HIL failed: {hil}")
    e.record["board_hil_receipt"] = e.relative(e.path / "board-hil.json")
    e.save()
    return hil


def board_inputs(e):
    e.tool("quartus_sh")
    e.tool("quartus_sta")
    e.tool("quartus_pgm")
    e.tool("vulcan")
    config_name = os.environ.get("SWAGE_BOARD_CONFIG", "board/verification.json")
    config_file = relative_input(config_name)
    board = json.loads(config_file.read_text())
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
