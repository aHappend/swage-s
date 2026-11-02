"""Memory-preserving real Yosys synthesis and executable parameter diagnostics."""

import json


PARAMETERS = ("SRC_WIDTH", "SRC_HEIGHT", "DST_WIDTH", "DST_HEIGHT",
              "PIXEL_BITS", "CHANNELS", "PHASE_FRAC", "INTERPOLATION")


def boundaries(run, root):
    baseline = (3, 5, 7, 3, 8, 3, 8, 0)
    tuples = [(4096, 4096, 4096, 4096, 8, 3, 16, 1)]
    for index in range(4):
        for value in (0, 4097):
            item = list(baseline)
            item[index] = value
            tuples.append(tuple(item))
    for index, value in ((4, 0), (4, 9), (5, 0), (5, 2), (6, 0), (6, 9),
                         (7, -1), (7, 2)):
        item = list(baseline)
        item[index] = value
        tuples.append(tuple(item))
    tuples.extend([(1, 1, 1, 1, 10, 3, 8, 0), (1, 1, 1, 1, 10, 1, 16, 1)])
    sources = [run.path(p) for p in sorted((root / "rtl").glob("*.sv"))]
    for i, values in enumerate(tuples):
        binary = run.directory / f"parameters-{i}.vvp"
        declarations = ",".join(f"parameter integer {key}={value}"
                                for key, value in zip(PARAMETERS, values))
        connections = ",".join(f".{key}({key})" for key in PARAMETERS)
        current = run.directory / f"parameters-{i}.sv"
        current.write_text(f"module parameter_tb #({declarations})(); "
                           f"swage_s3_top #({connections}) dut(); "
                           'initial begin #1; $display("LEGAL_PARAMETERS"); $finish; end endmodule\n')
        run.command(["iverilog", "-g2012", "-Wall", "-s", "parameter_tb",
                     "-o", run.path(binary), run.path(current), *sources],
                    f"parameters-{i}-compile")
        legal = i in (0, len(tuples)-1)
        text = run.command(["vvp", run.path(binary)], f"parameters-{i}-execute",
                           failure=not legal)
        required = "LEGAL_PARAMETERS" if legal else "SWAGE_S3 PARAMETER ERROR"
        if required not in text:
            raise AssertionError(f"parameter tuple {values} missing {required}")
        run.record["runs"].append({"name": f"parameters-{i}", "parameters": values,
                                   "kind": "elaboration" if legal else "explicit rejection",
                                   "diagnostic": required})


def synthesize(run, root, compact=False):
    run.version("yosys", ["-V"])
    configs = [(1, 1, 1, 1, 8, 1, 8, 0)] if compact else [
        (1, 1, 1, 1, 8, 1, 8, 0), (3, 5, 7, 3, 8, 1, 16, 1),
        (3, 5, 7, 3, 10, 1, 8, 0), (1, 1, 1, 1, 8, 3, 16, 1),
        (3, 5, 7, 3, 8, 3, 8, 0)]
    sources = " ".join(run.path(p) for p in sorted((root / "rtl").glob("*.sv")))
    for i, values in enumerate(configs):
        name = f"yosys-{i}"
        script = run.directory / f"{name}.ys"
        netlist = run.directory / f"{name}.json"
        verilog = run.directory / f"{name}.v"
        parameters = " ".join(f"-chparam {key} {value}"
                              for key, value in zip(PARAMETERS, values))
        script.write_text(
            f"read_verilog -sv {sources}\n"
            f"hierarchy -check -top swage_s3_top {parameters}\n"
            "proc\nopt_expr\nopt_clean\ncheck -assert\n"
            "memory -nomap\nopt_clean\n"
            "synth -top swage_s3_top -run begin:coarse -noabc\n"
            "techmap\nopt -fast\n"
            "check -assert\n"
            "select -assert-none t:$dlatch t:$adlatch t:$_DLATCH* t:$div t:$mod\n"
            f"write_json {run.path(netlist)}\nwrite_verilog {run.path(verilog)}\nstat\n")
        run.command(["yosys", "-s", run.path(script)], name)
        modules = json.loads(netlist.read_text())["modules"]
        counts = {}
        memory_bits = 0
        for module in modules.values():
            if int(str(module.get("attributes", {}).get("blackbox", "0")), 2):
                raise AssertionError(f"{name}: unresolved blackbox")
            for cell in module["cells"].values():
                kind = cell["type"]
                if any(term in kind.lower() for term in ("latch", "$div", "$mod")):
                    raise AssertionError(f"{name}: forbidden cell {kind}")
                counts[kind] = counts.get(kind, 0) + 1
                if kind.startswith("$mem"):
                    params = cell["parameters"]
                    memory_bits += int(params.get("WIDTH", "0"), 2) * int(
                        params.get("SIZE", "0"), 2)
        if not memory_bits:
            raise AssertionError(f"{name}: no preserved RAM")
        ports = modules["swage_s3_top"]["ports"]
        required_inputs = ("cfg_enable", "cfg_csc_coeff", "cfg_roi_width", "s_axis_cvalid",
                           "hist_req_addr")
        for port in required_inputs:
            if ports[port]["direction"] != "input" or any(
                    not isinstance(bit, int) for bit in ports[port]["bits"]):
                raise AssertionError(f"{name}: disconnected configuration {port}")
        run.record["runs"].append({"name": name, "parameters": values,
                                   "kind": "Yosys synthesis", "cells": counts,
                                   "memory_bits": memory_bits, "netlist": run.path(netlist),
                                   "zero_latches": True, "zero_div_mod": True})
