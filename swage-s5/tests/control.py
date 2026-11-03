"""Public transaction models for Top-K and legal FIFO depth boundaries."""

from oracle import pack
from receipts import prune_compile


def topk_check(root, output, run, topk=3, simulator="icarus", empty=False):
    candidates, vectors = [], []
    for index in range(96):
        score = 500 + (index // 3) * 7
        cls, level, y, x = index % 3, (index // 3) % 3, index // 12, index % 12
        if index >= 72:
            score = 900
        eligible = not empty and index % 11 != 5
        data = pack([0, 0, x+1, y+1], 16) | cls << 64 | score << 72
        key = (-score, cls, level, y, x)
        if eligible:
            candidates.append((key, data, x, y, level))
        vectors.append(data | level << 88 | x << 90 | y << 103 | int(eligible) << 116)
    winners = sorted(candidates)[:topk]
    expected = []
    for rank, (_, data, x, y, level) in enumerate(winners):
        expected.append(data | 1 << 88 | int(rank == 0) << 89 |
                        int(rank == len(winners)-1) << 90 | rank << 91 |
                        len(winners) << 99 | level << 107 | x << 109 | y << 122)
    if not winners:
        expected = [1 << 89 | 1 << 90]
    for name, values in (("input", vectors), ("expected", expected)):
        (output / f"topk-{name}.hex").write_text("".join(f"{v:x}\n" for v in values))
    relative = str(output.relative_to(root))
    rtl = [str(p.relative_to(root)) for p in sorted(root.glob("rtl/*.sv"))]
    if simulator == "icarus":
        binary = f"{relative}/simulation.vvp"
        run(["iverilog", "-g2012", "-Wall", "-s", "topk_tb", f"-Ptopk_tb.K={topk}",
             "-o", binary, "tests/topk_tb.sv", *rtl], "topk-compile")
        executable = ["vvp", binary]
    else:
        obj = output / "compile-obj"
        run(["verilator", "--binary", "--timing", "-Wno-fatal", "--top-module", "topk_tb",
             f"-GK={topk}", "--Mdir", str(obj.relative_to(root)), "-o", "simulation", "-j", "2",
             "tests/topk_tb.sv", *rtl], "topk-compile")
        prune_compile(obj)
        executable = [str((obj / "simulation").relative_to(root))]
    for stalled in (0, 1):
        text = run([*executable, f"+ROOT={relative}", f"+DC={len(expected)}", f"+STALL={stalled}"],
                   f"topk-simulation-{stalled}", timeout=600 if simulator == "icarus" else 3600)
        assert f"descriptors={len(expected)}" in text
    return {"topk": topk, "empty": empty, "compared_descriptors_per_replay": len(expected)}


def queue_check(root, output, run, depth=3, inherited=False):
    relative = str(output.relative_to(root))
    rtl = [str(p.relative_to(root)) for p in sorted(root.glob("rtl/*.sv"))]
    binary = f"{relative}/simulation.vvp"
    run(["iverilog", "-g2012", "-Wall", "-s", "queue_tb", f"-Pqueue_tb.DEPTH={depth}",
         f"-Pqueue_tb.KIND={int(inherited)}", "-o", binary, "tests/queue_tb.sv", *rtl], "queue-compile")
    text = run(["vvp", binary], "queue-simulation", timeout=600)
    assert f"depth={depth}" in text and "COMPARED " in text
    return {"depth": depth, "inherited": inherited, "ledger": text.strip()}
