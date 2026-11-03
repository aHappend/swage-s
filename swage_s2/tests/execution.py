"""Fresh, source-bound execution receipts shared by L1 and advisory probes."""

import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid


ROOT = Path(__file__).resolve().parent.parent
SELECTORS = ("rtl/*.sv", "tests/*.py", "design/SPEC.md",
             "design/ARCHITECTURE.md", "swage_s1_reference/rtl/*.sv")


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def fresh(parent, stem):
    name = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = parent / f"{stem}-{name}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def hashes(root=ROOT):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for selector in SELECTORS for p in sorted(root.glob(selector))}


class Ledger:
    def __init__(self, preparation=False, repair_of=None):
        self.path = fresh(ROOT / "evidence" / "preparation" if preparation
                          else ROOT / "evidence", "probe" if preparation else "l1")
        self.data = dict(argv=["python3", "tests/verify.py", *os.sys.argv[1:]], cwd=".",
                         start=timestamp(), source_sha256=hashes(),
                         timeout_s=None, timed_out=False, commands=[], versions={},
                         repair_of=repair_of)
        snapshot = self.path / "sources"
        for name in self.data["source_sha256"]:
            destination = snapshot / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        self.started = time.monotonic()
        self.finish(None)

    def run(self, argv, stem, cwd=ROOT, allowed_failure=False, clean_environment=False):
        prefix = self.path / f"{len(self.data['commands']):03d}-{stem}"
        record = dict(argv=[str(x) for x in argv], cwd=os.path.relpath(cwd, ROOT),
                      start=timestamp(), timeout_s=None, timed_out=False,
                      stdout=str(prefix.with_suffix(".stdout.txt").relative_to(ROOT)),
                      stderr=str(prefix.with_suffix(".stderr.txt").relative_to(ROOT)))
        self.data["commands"].append(record)
        self.finish(None)
        environment = None
        if clean_environment:
            environment = dict(os.environ)
            for name in ("PYTHONPATH", "PYTHONHOME"):
                environment.pop(name, None)
            environment.update(PYTHONSAFEPATH="1", CCACHE_DISABLE="1", OBJCACHE="")
            record["environment_removed"] = ["PYTHONPATH", "PYTHONHOME"]
            record["environment_overrides"] = dict(PYTHONSAFEPATH="1", CCACHE_DISABLE="1",
                                                   OBJCACHE="")
        with (ROOT / record["stdout"]).open("wb") as out, (
                ROOT / record["stderr"]).open("wb") as err:
            process = subprocess.Popen(record["argv"], cwd=cwd, stdout=out, stderr=err,
                                       env=environment)
            # Heartbeats report actual child liveness, not fabricated tool progress.
            while process.poll() is None:
                try:
                    process.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    print(f"L1 running: {stem} pid={process.pid}", flush=True)
            record.update(exit=process.returncode, end=timestamp())
        self.finish(None)
        if process.returncode and not allowed_failure:
            tail = (ROOT / record["stderr"]).read_text(errors="replace")[-2000:]
            raise RuntimeError(f"{stem}: exit {process.returncode}; "
                               f"{record['stdout']}, {record['stderr']}\n{tail}")
        return record

    def tools(self):
        for tool, option in (("python3", "--version"), ("iverilog", "-V"),
                             ("vvp", "-V"), ("verilator", "--version"),
                             ("yosys", "-V"), ("make", "--version"), ("g++", "--version")):
            if not shutil.which(tool):
                raise RuntimeError(f"Missing required tool: {tool}")
            record = self.run([tool, option], f"version-{tool}")
            self.data["versions"][tool] = dict(stdout=record["stdout"], stderr=record["stderr"])

    def finish(self, exit_code, error=None):
        self.data.update(end=timestamp(), duration_s=time.monotonic() - self.started,
                         exit=exit_code, error=error)
        dump(self.path / "receipt.json", self.data)


def relative(path):
    return str(path.relative_to(ROOT))
