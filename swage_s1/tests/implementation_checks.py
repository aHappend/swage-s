"""Mutation, real synthesis and installed structural timing checks."""

import hashlib
import json
import re
import shutil

from evidence import ROOT, require
from reference import Config
from simulation import execute_config


def mutations(e):
    e.tool("iverilog", ("-V",))
    e.tool("vvp", ("-V",))
    modifications = (
        ("horizontal-phase", "swage_s1_phase.sv",
         "phase_x + STEP_X;", "phase_x + STEP_X + 1'b1;"),
        ("vertical-weight", "swage_s1_interp.sv",
         "h0_s3 * wy0_s3;", "h0_s3 * wy1_s3;"),
        ("row-bank-association", "swage_s1_line_buffer.sv",
         "p10 = selected_bank1 ? bank1_a : bank0_a;",
         "p10 = selected_bank1 ? bank0_a : bank1_a;"),
    )
    results = []
    for name, filename, original, replacement in modifications:
        directory = e.path / name
        rtl = directory / "rtl"
        shutil.copytree(ROOT / "rtl", rtl)
        target = rtl / filename
        before = target.read_text()
        require(before.count(original) == 1, f"Mutation site changed: {filename}")
        target.write_text(before.replace(original, replacement), encoding="ascii")
        manifest = {
            "defect": name, "file": filename, "original": original,
            "replacement": replacement, "pristine_sha256": e.record["source_sha256"],
            "mutated_sha256": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(rtl.glob("*.sv"))
            },
        }
        (directory / "mutation.json").write_text(json.dumps(manifest, indent=2)+"\n")
        result = execute_config(
            e, "icarus", Config(12, 10, 7, 13),
            directory / "simulation", mutation=True, rtl=rtl,
        )
        require(re.search(r"MISMATCH case=\d+ pixel=\d+ expected=", result["mismatch"])
                is not None, "Mutation did not produce an actual pixel/sideband mismatch")
        results.append({"defect": name, **result})
        (e.path / "mutation-ledger.json").write_text(json.dumps(results, indent=2)+"\n")
    e.assertion("separately built and numerically killed defects", len(results), 3)


def number(value):
    return int(value, 2) if isinstance(value, str) else int(value)


def validate_netlist(path, *, memories=False, c=None):
    netlist = json.loads(path.read_text())
    modules = netlist["modules"]
    require("swage_s1_top" in modules, "Synthesized scaler top missing")
    memory_cells = []
    forbidden = []
    for module_name, module in modules.items():
        require(not number(module.get("attributes", {}).get("blackbox", 0)),
                f"Black-box module: {module_name}")
        for name, cell in module.get("cells", {}).items():
            kind = cell["type"]
            if re.search(r"latch|dlatch|^\$(?:div|mod)", kind, re.IGNORECASE):
                forbidden.append((module_name, name, kind))
            if kind.startswith("$mem"):
                memory_cells.append(cell)
            require(kind.startswith("$") or kind in modules,
                    f"Unresolved design cell {module_name}/{name}: {kind}")
    require(not forbidden, f"Forbidden synthesis cells: {forbidden}")
    if memories:
        require(len(memory_cells) == 2, f"Expected exactly two row SRAMs, got {len(memory_cells)}")
        for cell in memory_cells:
            p = cell["parameters"]
            require(number(p["SIZE"]) == c.sw and number(p["WIDTH"]) == c.data_width,
                    "Row bank depth/packed width differs from frozen architecture")
            require(number(p["RD_PORTS"]) == 2 and number(p["WR_PORTS"]) == 1,
                    "Each row bank must have two read ports and one write port")
            require(number(p["RD_CLK_ENABLE"]) == 3, "SRAM reads must be synchronous")
        top_cells = modules["swage_s1_top"]["cells"]
        for instance, suffix in (
            ("u_phase", "swage_s1_phase"), ("u_scheduler", "swage_s1_scheduler"),
            ("u_line_buffer", "swage_s1_line_buffer"), ("u_interp", "swage_s1_interp"),
        ):
            require(instance in top_cells, f"Required direct instance missing: {instance}")
            child = modules[top_cells[instance]["type"]]
            hdl_name = child.get("attributes", {}).get("hdlname", "")
            require(suffix in str(hdl_name) or suffix in top_cells[instance]["type"],
                    f"Required module identity missing: {instance}")
    return {"modules": len(modules), "memory_cells": len(memory_cells), "forbidden": forbidden}


def synthesize(e, c, directory):
    directory.mkdir()
    pre = directory / "memory.json"
    post = directory / "synthesis.json"
    script = directory / "synthesis.ys"
    paths = " ".join(e.relative(p) for p in sorted((ROOT / "rtl").glob("*.sv")))
    params = " ".join(f"-chparam {key} {value}" for key, value in c.parameters().items())
    script.write_text(
        f"read_verilog -sv {paths}\n"
        f"hierarchy -check -top swage_s1_top {params}\n"
        "proc\nopt\nmemory_dff\nmemory_collect\nopt_clean\ncheck -assert\n"
        "scc -expect 0\n"
        "select -assert-none t:$dlatch t:$adlatch t:$div t:$mod t:$divfloor t:$modfloor\n"
        f"write_json {e.relative(pre)}\n"
        "synth -top swage_s1_top\n"
        "hierarchy -check -top swage_s1_top\n"
        "check -assert\nscc -expect 0\n"
        "select -assert-none t:$_DLATCH_* t:$_DLATCHSR_* t:$dlatch "
        "t:$adlatch t:$div t:$mod t:$divfloor t:$modfloor\n"
        f"write_json {e.relative(post)}\nstat\n", encoding="ascii"
    )
    e.run(["yosys", "-Q", "-T", "-s", script], timeout=14400)
    before = validate_netlist(pre, memories=True, c=c)
    after = validate_netlist(post)
    return {"configuration": c.parameters(), "before_mapping": before,
            "after_synthesis": after, "raw_output": e.record["commands"][-1]["stdout"]}


def synthesis(e):
    e.tool("yosys", ("-V",))
    results = []
    for c in (Config(), Config(4096, 2, 7, 3)):
        results.append(synthesize(e, c, e.path / c.label))
        (e.path / "synthesis-ledger.json").write_text(json.dumps(results, indent=2)+"\n")
    e.assertion("synthesized configurations", len(results), 2)


def timing_lint(e):
    e.tool("vulcan")
    output = e.path / "timing-lint.json"
    e.run([
        "vulcan", "hardware", "fpga", "rtl-timing-lint",
        "--project", ".", "--rtl-glob", "rtl/*.sv",
        "--max-comb-div-width", "0", "--output", e.relative(output),
    ])
    require(output.is_file(), "Timing lint did not retain its result")
    data = json.loads(output.read_text())
    e.record["timing_lint"] = data
    e.save()
    require(data.get("status") == "passed" and data.get("gate") == "rtl-timing-lint",
            "Timing lint result is not an explicit pass in the installed schema")
    require(data.get("rtl_files_checked") == len(list((ROOT / "rtl").glob("*.sv"))),
            "Timing lint omitted delivered RTL")
    for key in ("rtl_div_mod_hazards", "netlist_div_mod_hazards",
                "mixed_assign_hazards", "lut_clock_gate_hazards"):
        require(data.get(key) == [], f"Timing hazard or missing result category: {key}")
    require(data.get("source_sha256") == {
        key: value for key, value in e.record["source_sha256"].items() if key.startswith("rtl/")
    }, "Timing lint is not bound to current production RTL")
