"""Bounded public-interface diagnostic; not the complete admitted L1 suite.

Run from the project root: python3 tests/run_focused.py
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracle import detections, fixture, normalize, pack, scene, selftest
from synthesis import synthesize


ROOT = Path(__file__).resolve().parent.parent


def now():
    return datetime.now(timezone.utc).isoformat()


def hexfile(path, values):
    path.write_text("".join(f"{v:x}\n" for v in values))


def vectors(directory, width, height, seed, threshold, topk, patch=8, classes=3, bf16=False):
    rgb, masks = scene(width, height, seed)
    layers = fixture(classes)
    winners, eligible = detections(rgb, masks, layers, threshold, topk)
    weights = [(address << 32) | data for li, layer in enumerate(layers)
               for address, data in layer.writes(li)]
    hexfile(directory / "weights.hex", weights)
    hexfile(directory / "pixels.hex",
            [pack(p, 8) | masks[y][x] << 24
             for y, row in enumerate(rgb) for x, p in enumerate(row)])
    descriptors, tokens = [], []
    for rank, (_, data, x, y) in enumerate(winners):
        descriptors.append(data | int(rank == 0) << 88 |
                           int(rank == len(winners) - 1) << 89 | 1 << 90 |
                           rank << 91 | len(winners) << 99)
        patch_x, patch_y = x // patch, y // patch
        patch_id = patch_y * ((width + patch - 1) // patch) + patch_x
        for element in range(patch * patch):
            sx, sy = patch_x * patch + element % patch, patch_y * patch + element // patch
            inside = sx < width and sy < height
            p = rgb[sy][sx] if inside else [0, 0, 0]
            keep = masks[sy][sx] if inside else 0
            token = normalize(p, keep, [32640] * 3, [512] * 3, bf16)
            tokens.append(token | int(rank == 0 and element == 0) << 48 |
                          int(element == patch * patch - 1) << 49 |
                          int(rank == len(winners) - 1 and element == patch * patch - 1) << 50 |
                          patch_id << 51 | patch_x << 83 | patch_y << 96 |
                          element << 109 | keep << 117 | rank << 120)
    if not descriptors:
        descriptors = [(1 << 88) | (1 << 89)]
    hexfile(directory / "descriptors.hex", descriptors)
    hexfile(directory / "roi.hex", tokens)
    return {"weight_count": len(weights), "descriptor_count": len(descriptors),
            "roi_count": len(tokens), "eligible_count": eligible,
            "retained": [{"sort_key": key, "descriptor": f"{data:022x}", "x": x, "y": y}
                         for key, data, x, y in winners]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=13)
    parser.add_argument("--height", type=int, default=17)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--patch", type=int, choices=(8, 14, 16), default=8)
    parser.add_argument("--classes", type=int, choices=(1, 3, 8), default=3)
    parser.add_argument("--topk", type=int, choices=(1, 3, 16), default=3)
    parser.add_argument("--threshold", type=int, default=500)
    parser.add_argument("--bf16", action="store_true")
    parser.add_argument("--simulator", choices=("icarus", "verilator"), default="icarus")
    parser.add_argument("--repair-of")
    parser.add_argument("--synthesis", action="store_true")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "evidence" / "preparation" / f"focused-{stamp}-{uuid.uuid4().hex[:12]}"
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"argv": [sys.executable, *sys.argv], "cwd": ".",
               "started_at": now(), "repair_of": args.repair_of, "commands": [],
               "source_sha256": {}, "tool_versions": {}, "exit_code": None}
    sources = (sorted(ROOT.glob("rtl/*.sv")) + sorted(ROOT.glob("tests/*")) +
               [ROOT / "design" / name for name in ("SPEC.md", "ARCHITECTURE.md")])
    for source in sources:
        if not source.is_file():
            continue
        relative = source.relative_to(ROOT)
        receipt["source_sha256"][str(relative)] = hashlib.sha256(source.read_bytes()).hexdigest()
        snapshot = output / "sources" / relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, snapshot)

    def save():
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    def run(argv, label, timeout=3600):
        stdout, stderr = output / f"{label}.stdout.txt", output / f"{label}.stderr.txt"
        command = {"argv": argv, "cwd": ".", "started_at": now(), "timeout_s": timeout,
                   "stdout": str(stdout.relative_to(ROOT)), "stderr": str(stderr.relative_to(ROOT))}
        receipt["commands"].append(command)
        save()
        try:
            with stdout.open("wb") as out, stderr.open("wb") as err:
                result = subprocess.run(argv, cwd=ROOT, stdout=out, stderr=err, timeout=timeout)
            command["exit_code"] = result.returncode
            command["timed_out"] = False
        except subprocess.TimeoutExpired:
            command["exit_code"] = None
            command["timed_out"] = True
            raise
        finally:
            command["ended_at"] = now()
            save()
        if result.returncode:
            raise RuntimeError(f"{label} exited {result.returncode}: {command['stdout']}, {command['stderr']}")
        return stdout.read_text()

    save()
    try:
        selftest()
        if args.synthesis:
            receipt["tool_versions"]["yosys"] = run(["yosys", "-V"], "version-yosys").strip()
            receipt["synthesis"] = synthesize(ROOT, output, run)
            receipt["exit_code"] = 0
            return 0
        simulator = args.simulator
        if simulator == "icarus" and (args.width * args.height > 221):
            simulator = "verilator"
        tools = (("iverilog", "-V"), ("vvp", "-V")) if simulator == "icarus" else (("verilator", "--version"),)
        for i, tool in enumerate(tools):
            receipt["tool_versions"][tool[0]] = run(list(tool), f"version-{i}").splitlines()[:3]
        case = vectors(output, args.width, args.height, args.seed, args.threshold, args.topk,
                       args.patch, args.classes, args.bf16)
        receipt["case"] = {**vars(args), **case}
        rtl = [str(p.relative_to(ROOT)) for p in sorted(ROOT.glob("rtl/*.sv"))]
        rel = str(output.relative_to(ROOT))
        parameters = {"W": args.width, "H": args.height, "NCLASS": args.classes,
                      "K": args.topk, "PS": args.patch, "BF16": int(args.bf16),
                      "THRESHOLD": args.threshold, "SEED": args.seed}
        if simulator == "icarus":
            binary = f"{rel}/sim.vvp"
            run(["iverilog", "-g2012", "-Wall", "-s", "focused_tb",
                 *[f"-Pfocused_tb.{key}={value}" for key, value in parameters.items()],
                 "-o", binary, "tests/focused_tb.sv", *rtl], "compile")
            executable = ["vvp", binary]
        else:
            obj = f"{rel}/compile-obj"
            run(["verilator", "--binary", "--timing", "-Wno-fatal", "--top-module", "focused_tb",
                 *[f"-G{key}={value}" for key, value in parameters.items()], "--Mdir", obj,
                 "-o", "simulation", "-j", "2", "tests/focused_tb.sv", *rtl], "compile")
            executable = [f"{obj}/simulation"]
        for stalled in (0, 1):
            run([*executable, f"+ROOT={rel}", f"+WC={case['weight_count']}",
                 f"+DC={case['descriptor_count']}", f"+RC={case['roi_count']}",
                 f"+STALL={stalled}"], f"simulation-{stalled}",
                timeout=600 if simulator == "icarus" else 3600)
        receipt["exit_code"] = 0
    except Exception as error:
        receipt["error"] = f"{type(error).__name__}: {error}"
        receipt["exit_code"] = 1
    finally:
        receipt["ended_at"] = now()
        save()
        print(json.dumps({"receipt": str((output / "receipt.json").relative_to(ROOT)),
                          "exit_code": receipt["exit_code"], "error": receipt.get("error")}), flush=True)
    return receipt["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
