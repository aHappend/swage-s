"""Generic memory-preserving synthesis and JSON structural assertions."""

import gzip
import hashlib
import json
from pathlib import Path


def synthesize(root, output, run, parameters=None, top_name="swage_s5_top"):
    rtl = [str(p.relative_to(root)) for p in sorted(root.glob("rtl/*.sv"))]
    relative = output.relative_to(root)
    script = output / "synthesis.ys"
    netlist = output / "netlist.json"
    parameters = ({"SRC_WIDTH": 3, "SRC_HEIGHT": 2, "DST_WIDTH": 3, "DST_HEIGHT": 2,
                   "MAX_FRAME_PIXELS": 16, "S5_MAX_WIDTH": 3, "S5_NUM_CLASSES": 3,
                   "S5_MAX_TOPK": 3, "S5_FIFO_DEPTH": 3} if parameters is None else parameters)
    script.write_text(
        "read_verilog -sv " + " ".join(rtl) + "\n"
        "chparam " + " ".join(f"-set {key} {value}" for key, value in parameters.items()) +
        f" {top_name}\n"
        f"hierarchy -check -top {top_name}\n"
        "proc\nopt_expr\nopt_clean\ncheck -noinit\n"
        "memory -nomap\nopt_clean\ncheck -assert\n"
        f"write_json {relative}/netlist.json\n"
    )
    run(["yosys", "-s", str(script.relative_to(root))], "synthesis")
    design = json.loads(netlist.read_text())
    modules = design["modules"]
    tops = [module for module in modules.values()
            if int(module.get("attributes", {}).get("top", "0"), 2) == 1]
    assert len(tops) == 1, "Expected exactly one synthesized top"
    top = tops[0]
    required = ("u_isp", "u_fork", "u_stem", "u_c2f", "u_pyramid", "u_head",
                "u_topk", "u_roi_tokens") if top_name == "swage_s5_top" else ()
    for name in required:
        assert name in top["cells"], f"Missing literal hierarchy: {name}"
    def child(parent, instance):
        return modules[parent["cells"][instance]["type"]]
    required_children = (((child(top, "u_c2f"), ("u_bottleneck0", "u_bottleneck1", "u_fuse")),
                          (child(top, "u_pyramid"), ("u_level1", "u_level2")),
                          (child(top, "u_roi_tokens"), ("u_roi_normalizer",)),
                          (child(top, "u_stem"), ("u_window",)))
                         if top_name == "swage_s5_top" else ())
    for parent, instances in required_children:
        for instance in instances:
            assert instance in parent["cells"], f"Missing required child: {instance}"
    for parent in modules.values():
        hdlname = parent.get("attributes", {}).get("hdlname", "").lstrip("\\").split()
        if hdlname and hdlname[0] == "swage_s5_dsconv":
            assert "u_window" in parent["cells"], "Spatial convolution without literal window"
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
    if top_name in ("swage_s5_top", "swage_s5_window", "swage_s5_dsconv", "swage_s1_top", "swage_s2_top"):
        assert memory_bits > 0, "No inferred image/line memory in synthesized graph"
    graph_edges = ((
            ("u_stem", "m_data", "u_c2f", "s_data"),
            ("u_c2f", "m_data", "u_pyramid", "s_data"),
            ("u_pyramid", "m_data", "u_head", "s_data"))
                   if top_name == "swage_s5_top" else ())
    for source, source_port, target, target_port in graph_edges:
        a = top["cells"][source]["connections"][source_port]
        b = top["cells"][target]["connections"][target_port]
        assert a == b and any(isinstance(bit, int) for bit in a), f"Disconnected graph: {source}->{target}"
    outputs = (("det_axis_tdata", "roi_axis_tdata") if top_name == "swage_s5_top" else
               ("m_axis_tdata",) if top_name in ("swage_s1_top", "swage_s2_top") else ("m_data",))
    for port in outputs:
        assert any(isinstance(bit, int) for bit in top["ports"][port]["bits"]), f"Constant output: {port}"
    if top_name == "swage_s5_top":
        assert (top["cells"]["u_head"]["connections"]["m_data"] ==
                top["cells"]["u_candidate_queue"]["connections"]["s_data"][:88])
        assert (top["cells"]["u_candidate_queue"]["connections"]["m_data"][:88] ==
                top["cells"]["u_topk"]["connections"]["s_data"])
        assert (top["cells"]["u_topk"]["connections"]["m_slot"] ==
                top["cells"]["u_roi_tokens"]["connections"]["cmd_slot"])
        assert (top["cells"]["u_roi_tokens"]["connections"]["m_data"] ==
                top["ports"]["roi_axis_tdata"]["bits"])
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
              "parameters": parameters, "top": top_name,
              "state_bits_unique_modules": state_bits,
              "memory_bits_unique_modules": memory_bits,
              "cell_types_unique_modules": counts,
              "netlist": str(compressed.relative_to(root)), "uncompressed_sha256": digest,
              "uncompressed_bytes": size}
    (output / "synthesis-summary.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
