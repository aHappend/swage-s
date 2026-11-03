"""Source-bound execution records shared by L1 and bounded diagnostics."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def fresh(parent, stem):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = parent / f"{stem}-{stamp}-{uuid.uuid4().hex[:12]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


class Execution:
    def __init__(self, root, output, argv, repair_of=None):
        self.root, self.output = root.resolve(), output.resolve()
        self.record = {"argv": argv, "cwd": ".", "started_at": now(),
                       "repair_of": repair_of, "commands": [], "source_sha256": {},
                       "tool_versions": {}, "exit_code": None}
        for pattern in ("rtl/*.sv", "tests/*", "design/SPEC.md",
                        "design/ARCHITECTURE.md", "swage_s4_reference/rtl/*.sv"):
            for source in sorted(self.root.glob(pattern)):
                if not source.is_file():
                    continue
                relative = source.relative_to(self.root)
                self.record["source_sha256"][str(relative)] = hashlib.sha256(
                    source.read_bytes()).hexdigest()
                target = self.output / "sources" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        provenance = self.root / "design" / "GENERATIONAL_HANDOFF.md"
        if provenance.is_file():
            self.record["source_sha256"]["design/GENERATIONAL_HANDOFF.md"] = hashlib.sha256(
                provenance.read_bytes()).hexdigest()
        self.save()

    def relative(self, path):
        return str(Path(path).resolve().relative_to(self.root))

    def save(self):
        (self.output / "receipt.json").write_text(json.dumps(self.record, indent=2) + "\n")

    def run(self, argv, label, timeout=3600, cwd=None, expected_exit=0):
        working = self.root if cwd is None else Path(cwd).resolve()
        logs = fresh(self.output, label)
        stdout, stderr = logs / "stdout.txt", logs / "stderr.txt"
        command = {"argv": list(map(str, argv)), "cwd": self.relative(working),
                   "started_at": now(), "timeout_s": timeout,
                   "stdout": self.relative(stdout), "stderr": self.relative(stderr),
                   "exit_code": None, "timed_out": False}
        self.record["commands"].append(command)
        self.save()
        try:
            with stdout.open("wb") as out, stderr.open("wb") as err:
                with subprocess.Popen(argv, cwd=working, stdout=out, stderr=err) as process:
                    deadline = time.monotonic() + timeout
                    while True:
                        try:
                            code = process.wait(timeout=min(30, max(0.01, deadline - time.monotonic())))
                            break
                        except subprocess.TimeoutExpired:
                            if time.monotonic() >= deadline:
                                process.kill()
                                process.wait()
                                command["timed_out"] = True
                                raise
                            print(f"PROGRESS {label}; logs={self.relative(logs)}", flush=True)
            command["exit_code"] = code
            if expected_exit is not None and code != expected_exit:
                raise RuntimeError(f"{label}: exit={code}; stdout={command['stdout']}; "
                                   f"stderr={command['stderr']}")
            return stdout.read_text()
        finally:
            command["ended_at"] = now()
            self.save()

    def versions(self, tools):
        for tool, flag in tools:
            text = self.run([tool, flag], f"version-{tool}")
            if not text.strip():
                text = (self.root / self.record["commands"][-1]["stderr"]).read_text()
            assert text.strip(), f"Missing real tool version for {tool}"
            self.record["tool_versions"][tool] = text.splitlines()[:3]
        self.save()

    def finish(self, code, **fields):
        self.record.update(fields, exit_code=code, ended_at=now())
        self.save()


def prune_compile(directory):
    for path in directory.iterdir():
        if path.is_file() and path.suffix in (".o", ".a", ".gch", ".d"):
            path.unlink()
