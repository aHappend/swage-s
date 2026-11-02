"""Generic memory-preserving synthesis and JSON structural assertions."""

import gzip
import hashlib
import json
from pathlib import Path


def synthesize(root, output, run):
    rtl = [str(p.relative_to(root)) for p in sorted(root.glob("rtl/*.sv"))]
    relative = output.relative_to(root)
    script = output / "synthesis.ys"
    netlist = output / "netlist.json"
    script.write_text(
        "read_verilog -sv " + " ".join(rtl) + "\n"
        "chparam -set SRC_WIDTH 3 -set SRC_HEIGHT 2 "
        "-set DST_WIDTH 3 -set DST_HEIGHT 2 "
        "-set MAX_FRAME_PIXELS 16 -set S5_MAX_WIDTH 3 "
        "-set S5_NUM_CLASSES 3 -set S5_MAX_TOPK 3 "
        "-set S5_FIFO_DEPTH 3 swage_s5_top\n"
        "hierarchy -check -top swage_s5_top\n"
        "proc\nopt_expr\nopt_clean\ncheck -noinit\n"
        "memory -nomap\nopt_clean\ncheck -assert\n"
        f"write_json {relative}/netlist.json\n"
    )
    run(["yosys", "-s", str(script.relative_to(root))], "synthesis")
    design = json.loads(netlist.read_text())
    modules = design["modules"]
    top = modules["swage_s5_top"]
    required = ("u_isp", "u_fork", "u_stem", "u_c2f", "u_pyramid", "u_head",
                "u_topk", "u_roi_tokens")
    for name in required:
        assert name in top["cells"], f"Missing literal hierarchy: {name}"
    counts = {}
    state_bits, memory_bits = 0, 0
    for name, module in modules.items():
        for cell in module.get("cells", {}).values():
            kind = cell["type"]
            counts[kind] = counts.get(kind, 0) + 1
            assert "latch" not in kind.lower(), f"Latch cell in {name}: {kind}"
            assert not kind.startswith(("$div", "$mod")), f"Combinational quotient/remainder: {kind}"
            assert kind.startswith("$") or kind in modules, f"Unresolved module: {kind}"
            if "dff" in kind.lower():
                state_bits += len(cell["connections"].get("Q", []))
            if kind.startswith("$mem"):
                p = cell["parameters"]
                memory_bits += int(p.get("WIDTH", "0"), 2) * int(p.get("SIZE", "0"), 2)
    assert state_bits > 0, "No actual state in synthesized graph"
    assert memory_bits > 0, "No inferred image/line memory in synthesized graph"
    for source, source_port, target, target_port in (
            ("u_stem", "m_data", "u_c2f", "s_data"),
            ("u_c2f", "m_data", "u_pyramid", "s_data"),
            ("u_pyramid", "m_data", "u_head", "s_data")):
        a = top["cells"][source]["connections"][source_port]
        b = top["cells"][target]["connections"][target_port]
        assert a == b and any(isinstance(bit, int) for bit in a), f"Disconnected graph: {source}->{target}"
    for port in ("det_axis_tdata", "roi_axis_tdata"):
        assert any(isinstance(bit, int) for bit in top["ports"][port]["bits"]), f"Constant output: {port}"
    digest = hashlib.sha256(netlist.read_bytes()).hexdigest()
    size = netlist.stat().st_size
    compressed = output / "netlist.json.gz"
    with netlist.open("rb") as source, gzip.open(compressed, "wb") as target:
        while chunk := source.read(1024 * 1024):
            target.write(chunk)
    recovered_hash = hashlib.sha256()
    recovered_size = 0
    with gzip.open(compressed, "rb") as source:
        while chunk := source.read(1024 * 1024):
            recovered_hash.update(chunk)
            recovered_size += len(chunk)
    assert recovered_hash.hexdigest() == digest and recovered_size == size, "Netlist retention failure"
    netlist.unlink()
    result = {"stage": "generic inferred memories, not physical mapping or timing",
              "state_bits_unique_modules": state_bits,
              "memory_bits_unique_modules": memory_bits,
              "cell_types_unique_modules": counts,
              "netlist": str(compressed.relative_to(root)), "uncompressed_sha256": digest,
              "uncompressed_bytes": size}
    (output / "synthesis-summary.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
