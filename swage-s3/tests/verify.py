"""Portable, source-bound SWAGE-S3 verification entry point.

The value oracle is independent of RTL scheduling. Simulation observes only top
ports, sampling pre-edge transfers and checking held transactions exactly.
Generated HDL, fixtures, raw tool logs and immutable receipts are per-run.
"""

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isp_oracle import Config, histogram_words, pipeline
from s3_sim import testbench, write_cases


ROOT = Path(__file__).resolve().parent.parent
PATTERNS = ("rtl/*.sv", "tests/*.py", "swage_s2_reference/rtl/*.sv", "design/*.md")


class Run:
    def __init__(self, suite, preparation=False):
        prefix = Path("evidence/preparation") if preparation else Path("evidence")
        self.relative = prefix / f"{suite}-{time.time_ns()}"
        self.directory = ROOT / self.relative
        self.directory.mkdir(parents=True, exist_ok=False)
        self.record = {
            "argv": [sys.executable, *sys.argv], "cwd": ".",
            "started_at": time.time(), "commands": [], "source_sha256": {},
            "tool_versions": {}, "runs": [],
            "scope": "advisory preparation" if preparation else suite,
        }
        for pattern in PATTERNS:
            for path in sorted(ROOT.glob(pattern)):
                self.record["source_sha256"][path.relative_to(ROOT).as_posix()] = (
                    hashlib.sha256(path.read_bytes()).hexdigest())

    def path(self, path):
        return path.relative_to(ROOT).as_posix()

    def command(self, argv, name, timeout=600, failure=False, cwd=None):
        stdout, stderr = (self.directory / f"{name}.stdout.txt",
                          self.directory / f"{name}.stderr.txt")
        working_directory = ROOT if cwd is None else cwd
        entry = {"argv": [str(a) for a in argv],
                 "cwd": "." if cwd is None else self.path(cwd), "started_at": time.time(),
                 "timeout_s": timeout, "stdout": self.path(stdout), "stderr": self.path(stderr)}
        self.record["commands"].append(entry)
        print(json.dumps({"tool_execution": name, "phase": "start"}), flush=True)
        settled = threading.Event()

        def heartbeat():
            while not settled.wait(30):
                print(json.dumps({"tool_execution": name, "phase": "running",
                                  "elapsed_s": round(time.time()-entry["started_at"])}),
                      flush=True)

        threading.Thread(target=heartbeat, daemon=True).start()
        try:
            with stdout.open("w") as so, stderr.open("w") as se:
                result = subprocess.run(entry["argv"], cwd=working_directory, stdout=so, stderr=se,
                                        timeout=timeout)
            entry["exit_code"] = result.returncode
            entry["timed_out"] = False
        except subprocess.TimeoutExpired:
            entry["exit_code"] = None
            entry["timed_out"] = True
            raise
        finally:
            settled.set()
            entry["ended_at"] = time.time()
        text = stdout.read_text() + stderr.read_text()
        if failure is not None and (result.returncode != 0) != failure:
            raise AssertionError(f"{name}: exit {result.returncode}; "
                                 f"logs {self.path(stdout)}, {self.path(stderr)}")
        return text

    def version(self, tool, args):
        if tool in self.record["tool_versions"]:
            return
        self.record["tool_versions"][tool] = self.command([tool, *args], f"version-{tool}")

    def finish(self, status):
        self.record["generated_sha256"] = {
            self.path(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for pattern in ("*.sv", "*.ys", "*.hex", "cases.txt", "legacy.txt")
            for path in self.directory.rglob(pattern)}
        self.record["coverage"] = {
            "completed_runs": len(self.record["runs"]),
            "compared_pixels": sum(row.get("counts", {}).get("pixels", row.get("pixels", 0))
                                   for row in self.record["runs"]),
            "compared_histogram_words": sum(row.get("counts", {}).get("statistics", 0)
                                            for row in self.record["runs"]),
            "detected_defects": sum("detected" in row.get("counts", {})
                                    for row in self.record["runs"]),
        }
        self.record["exit_code"] = status
        self.record["ended_at"] = time.time()
        receipt = self.directory / "receipt.json"
        receipt.write_text(json.dumps(self.record, indent=2) + "\n")
        print(json.dumps({"exit_code": status, "receipt": self.path(receipt)}), flush=True)


def image_for(width, height, bits, channels, seed, pattern="random", sparse=False):
    rng = random.Random(seed)
    maximum = (1 << bits) - 1
    primaries = [(0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0),
                 (0, 0, 255), (16, 128, 128), (235, 16, 240), (1, 255, 0)]
    image = []
    for y in range(height):
        row = []
        for x in range(width):
            if pattern == "random":
                pixel = tuple(rng.randrange(maximum + 1) for _ in range(channels))
            elif pattern == "ramp":
                pixel = tuple((37*x + 71*y + 53*c) & maximum for c in range(channels))
            elif pattern == "checkerboard":
                pixel = (maximum if (x + y) & 1 else 0,) * channels
            elif pattern == "impulse":
                pixel = (maximum if (x, y) == (width//2, height//2) else 0,) * channels
            elif pattern == "primaries":
                pixel = primaries[(y*width+x) % len(primaries)][:channels]
            else:
                pixel = (int(pattern) & maximum,) * channels
            if sparse and (x & 1 or y & 1):
                pixel = (pixel[0], 0, 0)
            row.append(pixel)
        image.append(row)
    return image


def make_case(parameters, config, seed=17, pattern="random", traffic=2, name=""):
    sw, sh, dw, dh, bits, channels, frac, interpolation = parameters
    image = image_for(sw, sh, bits, channels, seed, pattern, config.input_format == 2)
    expected, bins = pipeline(image, config, dw, dh, bits=bits, channels=channels,
                              frac=frac, nearest=bool(interpolation))
    return {"config": config, "image": image, "expected": expected,
            "histogram": histogram_words(bins), "seed": seed, "traffic": traffic,
            "name": name or f"enable-{config.enable:02x}-{pattern}"}


def simulate(run, parameters, cases, name, simulator="verilator", rtl=None,
             failure=None):
    directory = run.directory / f"{name}-{time.time_ns()}"
    directory.mkdir(exist_ok=False)
    fixtures = directory / "fixtures"
    write_cases(fixtures, cases)
    bench = directory / "tb.sv"
    bench.write_text(testbench(parameters))
    sources = sorted((ROOT / "rtl" if rtl is None else rtl).glob("*.sv"))
    relative_sources = [run.path(p) for p in sources]
    if simulator == "iverilog":
        binary = directory / "sim.vvp"
        run.command(["iverilog", "-g2012", "-Wall", "-s", "tb", "-o",
                     run.path(binary), run.path(bench), *relative_sources], f"{name}-compile")
        argv = ["vvp", run.path(binary), f"+fixtures={run.path(fixtures)}",
                "+cycle_limit=500000"]
    else:
        build = directory / "compile-obj"
        run.command(["verilator", "--binary", "--timing", "-Wno-fatal",
                     "--top-module", "tb", "-j", "2", "--Mdir", run.path(build),
                     "-o", "sim", run.path(bench), *relative_sources], f"{name}-compile")
        argv = [run.path(build / "sim"), f"+fixtures={run.path(fixtures)}"]
    text = run.command(argv, f"{name}-simulation", failure=bool(failure))
    if failure:
        if failure not in text:
            raise AssertionError(f"{name}: unrelated failure, expected {failure}")
        counts = {"detected": failure}
    else:
        match = re.search(r"SUMMARY cases=(\d+) pixels=(\d+) statistics=(\d+) configs=(\d+)"
                          r" busy=(\d+) stalls=(\d+) first=(\d+) eol=(\d+) final=(\d+)"
                          r" responses=(\d+) bubbles=(\d+)", text)
        if not match:
            raise AssertionError(f"{name}: missing completed comparison summary")
        counts = dict(zip(("cases", "pixels", "statistics", "configs", "busy", "stalls",
                           "first", "eol", "final", "responses", "bubbles"),
                          map(int, match.groups())))
        if counts["cases"] != len(cases) or counts["pixels"] != sum(
                len(c["expected"]) for c in cases):
            raise AssertionError(f"{name}: incomplete pixel comparisons")
        expected_configs = sum(not bool(c.get("flags", 0) & 16) for c in cases) + sum(
            bool(c.get("flags", 0) & 6) for c in cases)
        if counts["configs"] != expected_configs:
            raise AssertionError(f"{name}: incomplete accepted configurations")
        if counts["statistics"] != 32 * sum(bool(c["config"].enable & 32) and
                                           not c.get("flags", 0) & 9 for c in cases):
            raise AssertionError(f"{name}: incomplete histogram comparisons")
        reset_match = re.search(r"RESET_SUMMARY fill=(\d+) stall=(\d+) response=(\d+) unread=(\d+)", text)
        if not reset_match:
            raise AssertionError(f"{name}: missing reset coverage")
        for key, value, flag in zip(("reset_fill", "reset_stall", "reset_response", "unread"),
                                   map(int, reset_match.groups()), (2, 4, 8, 1)):
            counts[key] = value
            if value != sum(bool(c.get("flags", 0) & flag) for c in cases):
                raise AssertionError(f"{name}: missing {key} event")
        directed = sum(c.get("traffic", 2) not in (0, 4) for c in cases)
        if any(counts[key] != directed for key in ("first", "eol", "final")):
            raise AssertionError(f"{name}: missing directed boundary stall")
        if counts["responses"] != 32 * counts["statistics"]:
            raise AssertionError(f"{name}: missing 32-cycle histogram response stalls")
    run.record["runs"].append({"name": name, "parameters": parameters,
                               "simulator": simulator, "counts": counts,
                               "scenarios": [c["name"] for c in cases]})
    if simulator == "verilator":
        for pattern in ("*.o", "*.a", "*.gch"):
            for path in build.glob(pattern):
                path.unlink()
    return counts


def behavior(run, seeds, compact=False, focus=None):
    run.version("iverilog", ["-V"])
    run.version("vvp", ["-V"])
    run.version("verilator", ["--version"])
    from oracle_probe import model_checks
    run.record["oracle_analytical_checks"] = model_checks()
    if focus == "legacy":
        from s3_legacy import legacy
        legacy(run, ROOT, make_case, simulate, compact)
        return
    if focus == "color":
        from s3_cases import color_cases
        p = (3, 5, 7, 3, 8, 3, 16, 0)
        simulate(run, p, color_cases(p, seeds, make_case), "focused-color")
        return
    if focus == "scaler":
        p = (3, 3, 5, 3, 8, 3, 8, 0)
        cases = [make_case(p, Config(enable=16), seed=offset, pattern="ramp", traffic=4,
                           name=f"scale-readiness-phase-{offset}") for offset in range(35)]
        for sim in ("iverilog", "verilator"):
            simulate(run, p, cases, f"focused-scaler-{sim}", sim)
        return
    if focus == "leaf":
        from s3_leaf import csc_latency, interpolation_latency
        csc_latency(run, ROOT, make_case)
        interpolation_latency(run, ROOT)
        return
    parameters = (3, 3, 5, 3, 8, 3, 8, 0)
    compact_cases = [
        *[make_case(parameters, Config(enable=16), pattern="ramp", traffic=t,
                    name=f"scale-no-histogram-traffic-{t}") for t in (0, 1, 2)],
        make_case(parameters, Config(enable=63, roi=(1, 1, 2, 2)), name="compact444"),
        make_case(parameters, Config(enable=56, output_420=True), name="compact420"),
        make_case(parameters, Config(enable=56, input_format=2, csc_mode=2),
                  name="compact-inverse420"),
    ]
    compact_cases[0]["flags"] = 16
    for flags in (2, 4, 8, 1, 0):
        case = make_case(parameters, Config(enable=63, roi=(0, 0, 2, 2) if flags==2 else (1, 1, 2, 2)),
                         seed=89 if flags==0 else 43, pattern="0" if flags==0 else "random",
                         name=f"reset-and-snapshot-{flags}")
        case["flags"] = flags
        compact_cases.append(case)
    if compact_cases[-1]["histogram"] == compact_cases[-2]["histogram"]:
        raise AssertionError("snapshot replacement stimulus must change the expected histogram")
    for sim in ("iverilog", "verilator"):
        simulate(run, parameters, compact_cases, f"compact-{sim}", sim)
    from s3_leaf import csc_latency, interpolation_latency
    csc_latency(run, ROOT, make_case)
    interpolation_latency(run, ROOT)
    if compact:
        return
    parameters = (8, 6, 11, 9, 8, 3, 8, 0)
    cases = [make_case(parameters, Config(enable=enable, roi=(1, 1, 3, 3),
                                         border=bool(seed & 2), absolute=True, shift=2,
                                         coeff=(-1, 0, 1, -2, 0, 2, -1, 0, 1)), seed,
                       name=f"bypass-{enable:02x}-seed-{seed}")
             for seed in seeds for enable in range(64)]
    simulate(run, parameters, cases, "bypass-matrix")
    from s3_cases import color_cases, sparse_cases
    color_parameters = (3, 5, 7, 3, 8, 3, 16, 0)
    simulate(run, color_parameters, color_cases(color_parameters, seeds, make_case),
             "color-numerical")
    geometries = [(1, 1, 1, 1), (1, 7, 3, 5), (7, 1, 5, 3), (2, 2, 1, 1),
                  (3, 5, 7, 3), (8, 6, 8, 6), (17, 9, 5, 13), (64, 33, 37, 19)]
    for i, geometry in enumerate(geometries):
        for bits, channels in ((8, 1), (10, 1), (8, 3)):
            p = (*geometry, bits, channels, 16 if i & 1 else 8, (i >> 1) & 1)
            configs = [Config(enable=48), Config(enable=32),
                       Config(enable=49, roi=(0, 0, 1, 1)),
                       Config(enable=49, roi=(p[0]-1, p[1]-1, 1, 1)),
                       Config(enable=49, roi=(0, 0, p[0], p[1]))]
            simulate(run, p, [make_case(p, c, seed, ("ramp", "checkerboard",
                         "impulse")[j % 3], name=f"geometry-{i}-{j}-seed-{seed}")
                         for seed in seeds for j, c in enumerate(configs)],
                     f"geometry-{i}-{bits}-{channels}")
        p = (*geometry, 8, 3, 16 if i & 1 else 8, (i >> 1) & 1)
        simulate(run, p, sparse_cases(p, seeds, make_case), f"sparse-geometry-{i}")
    for bits, values in ((8, range(256)), (10, range(1024))):
        p = (1, 1, 1, 1, bits, 1, 16, 1)
        cases = [make_case(p, Config(enable=enable), 17, str(value), traffic=0,
                           name=f"singleton-{bits}-{value}-enable-{enable}")
                 for value in values for enable in (0, 32)]
        cases.append(make_case(p, Config(enable=0, hist_channel=3, csc_mode=7,
                                        roi=(8191, 8191, 0, 0), shift=31,
                                        coeff=(-32768,)*9),
                               89, "255", traffic=0, name="mono-ignored-bypass-fields"))
        simulate(run, p, cases, f"singleton-exhaustive-{bits}")
    from s3_legacy import legacy
    legacy(run, ROOT, make_case, simulate)


def portable(run, staged=False):
    if staged:
        behavior(run, [17], compact=True)
        from s3_legacy import legacy
        from s3_synthesis import synthesize
        legacy(run, ROOT, make_case, simulate, compact=True)
        synthesize(run, ROOT, compact=True)
        return
    staging = run.directory / f"staging-{time.time_ns()}"
    staging.mkdir(exist_ok=False)
    copied = {}
    for pattern in PATTERNS:
        for source in sorted(ROOT.glob(pattern)):
            relative = source.relative_to(ROOT)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    run.record["clean_reproduction_sources"] = copied
    run.command(["python3", "tests/verify.py", "--suite", "portable", "--staged"],
                "clean-reproduction", timeout=2400, cwd=staging)
    receipts = list(staging.glob("evidence/portable-*/receipt.json"))
    if len(receipts) != 1 or json.loads(receipts[0].read_text())["exit_code"] != 0:
        raise AssertionError("clean reproduction did not retain one successful child receipt")
    run.record["clean_reproduction_receipt"] = run.path(receipts[0])


def source_audit(run):
    findings = []
    for pattern in PATTERNS:
        for path in ROOT.glob(pattern):
            text = path.read_text()
            # Do not match the audit's own regular-expression source literals.
            forbidden = (r"/(?:home|data|Users)/[A-Za-z0-9_-]+/",
                         r"gh[pousr]_[A-Za-z0-9]{30,}",
                         r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
            for expression in forbidden:
                if re.search(expression, text):
                    findings.append({"file": path.relative_to(ROOT).as_posix(),
                                     "category": expression})
    run.record["source_audit"] = {"files": len(run.record["source_sha256"]),
                                 "findings": findings}
    if findings:
        raise AssertionError("nonportable path or credential in declared deliverable sources")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("behavior", "sensitivity", "synthesis", "portable"),
                        required=True)
    parser.add_argument("--seeds", default="17,43,89")
    parser.add_argument("--compact", action="store_true", help="bounded diagnostic subset")
    parser.add_argument("--preparation", action="store_true", help="retain advisory probe receipt")
    parser.add_argument("--repair-of", help="project-relative retained original receipt")
    parser.add_argument("--focus", choices=("color", "legacy", "scaler", "leaf", "parameters"),
                        help="bounded diagnostic subset")
    parser.add_argument("--staged", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    run = Run(args.suite, args.preparation)
    if args.repair_of:
        run.record["repair_of"] = args.repair_of
    status = 1
    try:
        run.record["tool_versions"]["python3"] = sys.version
        source_audit(run)
        if args.suite == "behavior":
            behavior(run, [int(s) for s in args.seeds.split(",")], args.compact, args.focus)
        elif args.suite == "synthesis":
            from s3_synthesis import boundaries, synthesize
            run.version("iverilog", ["-V"])
            run.version("vvp", ["-V"])
            if not args.compact or args.focus == "parameters":
                boundaries(run, ROOT)
            if args.focus != "parameters":
                synthesize(run, ROOT, args.compact)
        elif args.suite == "sensitivity":
            from s3_sensitivity import sensitivity
            sensitivity(run, ROOT, make_case, simulate, args.compact)
        elif args.suite == "portable":
            portable(run, args.staged)
        else:
            raise NotImplementedError(f"{args.suite} suite is not authored yet")
        status = 0
    except Exception:
        error = run.directory / "failure.stderr.txt"
        error.write_text(traceback.format_exc())
        run.record["failure_stderr"] = run.path(error)
    finally:
        run.finish(status)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
