"""Exclusive, source-bound evidence for admitted checks and bounded probes.

Internal prerequisites inherit the parent's source identity and outer budget;
their outputs are exclusive children, not new shared-runtime allocations.
The 2700-second ceiling reserves 60 seconds for settlement and receipt writing.
Each delegated level leaves a further five seconds for its parent to settle it.
Requested tool timeouts remain recorded separately from this outer deadline.
"""

import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import shutil
import subprocess
import sys
import time
import threading
import uuid


ROOT = Path(__file__).resolve().parents[1]
SELECTORS = ("rtl/*.sv", "design/*.md", "fpga/*", "tests/*.py")
ADMITTED_PARTITION_OUTPUTS = {
    ("behavior-shard", 154, "icarus"): "behavior-shard",
    ("behavior-shard", 0, "icarus"): "recovery-v2-behavior-shard-icarus-0",
    ("quartus-sta-shard", 178, None): "quartus-sta-shard",
    ("quartus-sta-shard", 166, None): "recovery-v2-quartus-sta-shard-166",
}
ADMITTED_PARTITION_OUTPUTS.update({
    ("behavior-shard", index, "icarus"): f"recovery-v2-behavior-shard-icarus-{index}"
    for index in range(168) if index != 154
})
ADMITTED_PARTITION_OUTPUTS.update({
    (check, index, "icarus"): f"recovery-v2-{check}-icarus-{index}"
    for check, count in (("parameter-shard", 7), ("pipeline-shard", 6))
    for index in range(count)
})
ADMITTED_PARTITION_OUTPUTS.update({
    ("behavior-shard", index, "verilator"): f"recovery-v2-behavior-shard-verilator-{index}"
    for index in range(168)
})
ADMITTED_PARTITION_OUTPUTS.update({
    (check, index, "verilator"): f"recovery-v2-{check}-verilator-{index}"
    for check, count in (("parameter-shard", 7), ("pipeline-shard", 6))
    for index in range(count)
})
ADMITTED_PARTITION_OUTPUTS.update({
    ("quartus-sta-shard", index, None): f"recovery-v2-quartus-sta-shard-{index}"
    for index in range(192) if index not in (178, 166)
})


def partition_output_prefix(check, shard, simulator=None):
    return ADMITTED_PARTITION_OUTPUTS.get((check, shard, simulator))


def partition_receipts(directory):
    # Keep historical receipts visible for explicit exclusion, not reuse.
    patterns = {"*-shard-*/receipt.json"}
    patterns.update(f"{prefix}-*/receipt.json"
                    for prefix in ADMITTED_PARTITION_OUTPUTS.values()
                    if "-shard-" not in f"{prefix}-")
    patterns.add("*/prerequisites/**/receipt.json")
    return sorted({path for pattern in patterns for path in directory.glob(pattern)})


def sources(root=None):
    root = ROOT if root is None else root
    return sorted({p for selector in SELECTORS for p in root.glob(selector) if p.is_file()})


def hashes(root=None):
    root = ROOT if root is None else root
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sources(root)
    }


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def execution_environment():
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    launcher = shutil.which("quartus_sh")
    if launcher:
        native_bin = Path(launcher).resolve().parent
        native_root = native_bin.parent
        if (native_root / "adm" / "qenv.sh").is_file():
            # Intel launchers locate their runtime relative to argv[0].
            # Use the existing installation, not a symlink in a foreign bin.
            dirs = [native_bin, native_root / "sopc_builder" / "bin"]
            env["PATH"] = os.pathsep.join(
                [str(path) for path in dirs if path.is_dir()] + [env.get("PATH", "")]
            )
            env["QUARTUS_ROOTDIR"] = str(native_root)
    return env


class Evidence:
    def __init__(self, check, preparation=False, *, output_prefix=None,
                 parent_receipt=None, child_output=None, argv=None):
        self._lock = threading.RLock()
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        prefix = check if output_prefix is None else output_prefix
        require(re.fullmatch(r"[a-z0-9][a-z0-9-]*", prefix) is not None,
                "Evidence output prefix must be a plain check name")
        parent = ROOT / "evidence" / ("preparation" if preparation else "checks")
        invocation = ["python3", *sys.argv]
        binding = None
        deadline = time.time() + 2640
        if parent_receipt is not None or child_output is not None:
            require(parent_receipt is not None and child_output is not None,
                    "Internal execution requires both parent receipt and child output")
            parent_path, output, parent_record, entry = child_contract(
                parent_receipt, child_output, invocation,
            )
            require(parent_record["status"] == "running",
                    "Internal execution parent is not running")
            require(parent_record["source_sha256"] == hashes(),
                    "Internal execution parent has stale sources")
            self.path = output
            preparation = not parent_record["acceptance_evidence"]
            deadline = parent_record["execution_budget"]["deadline_epoch"] - 5
            binding = {
                "receipt": parent_path.relative_to(ROOT).as_posix(),
                "id": entry["id"], "output": entry["output"],
                "argv": entry["argv"], "runtime_allocation": False,
            }
        else:
            self.path = parent / f"{prefix}-{uuid.uuid4().hex}"
        self.path.mkdir(parents=True, exist_ok=False)
        self.deadline = deadline
        self.record = {
            "schema": 1, "check": check, "acceptance_evidence": not preparation,
            "argv": invocation if argv is None else argv,
            "invocation_argv": invocation, "cwd": ".", "start": stamp(),
            "source_sha256": hashes(), "commands": [], "assertions": [],
            "status": "running",
            "execution_budget": {"limit_s": 2700, "reserve_s": 60,
                                 "deadline_epoch": deadline},
        }
        if binding:
            self.record["parent_execution"] = binding
        for source in sources():
            target = self.path / "sources" / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        self.save()
        print(self.relative(self.path / "receipt.json"), flush=True)

    def remaining(self):
        remaining = self.deadline - time.time()
        require(remaining > 0, "Outer check collection budget exhausted; "
                "completed source-bound prerequisites retained, coverage incomplete")
        return remaining

    def relative(self, path):
        return Path(path).relative_to(ROOT).as_posix()

    def save(self):
        # Only this still-open receipt is updated. Completed receipts are immutable.
        with self._lock:
            temporary = self.path / "receipt.json.tmp"
            temporary.write_text(
                json.dumps(self.record, indent=2, sort_keys=True) + "\n", encoding="ascii"
            )
            temporary.replace(self.path / "receipt.json")

    def assertion(self, name, actual, expected):
        self.record["assertions"].append(
            {"name": name, "actual": actual, "expected": expected}
        )
        self.save()
        require(actual == expected, f"{name}: actual={actual!r}, expected={expected!r}")

    def run(self, argv, *, cwd=None, timeout=3600, expected=(0,), name=None):
        cwd = ROOT if cwd is None else cwd
        argv = [str(arg) for arg in argv]
        requested_argv = list(argv)
        env = execution_environment()
        if Path(argv[0]).name.startswith(("quartus_", "qsys-")):
            located = shutil.which(argv[0], path=env.get("PATH"))
            if located:
                argv[0] = str(Path(located).resolve())
        require(timeout >= 3600, "Child timeout must be at least 3600 seconds")
        effective_timeout = min(timeout, self.remaining())
        with self._lock:
            index = len(self.record["commands"])
            stem = f"{index:05d}-{name or Path(argv[0]).name}"
            stdout = self.path / f"{stem}.stdout"
            stderr = self.path / f"{stem}.stderr"
            command = {
                "argv": argv, "cwd": self.relative(cwd), "start": stamp(),
                "requested_argv": requested_argv,
                "timeout_s": timeout, "stdout": self.relative(stdout),
                "effective_timeout_s": effective_timeout,
                "deadline_epoch": self.deadline,
                "stderr": self.relative(stderr), "exit_status": None,
                "expected_exit_status": list(expected),
                "timed_out": False, "core_dump_limit_bytes": 0,
            }
            self.record["commands"].append(command)
            self.save()
        started = time.monotonic()
        try:
            with stdout.open("xb") as out, stderr.open("xb") as err:
                process = subprocess.Popen(
                    argv, cwd=cwd, stdout=out, stderr=err,
                    env=env, start_new_session=True,
                )
                try:
                    process.wait(timeout=effective_timeout)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    raise
            with self._lock:
                command["exit_status"] = process.returncode
        except subprocess.TimeoutExpired:
            with self._lock:
                command["timed_out"] = True
                command["exit_status"] = process.returncode
            raise
        except OSError as error:
            with self._lock:
                command["launch_error"] = str(error)
            raise
        finally:
            with self._lock:
                command["end"] = stamp()
                command["duration_s"] = time.monotonic() - started
                self.save()
        require(process.returncode in expected,
                f"Command exit {process.returncode}: {argv}; "
                f"see {command['stdout']} and {command['stderr']}")
        return stdout.read_text(errors="replace"), stderr.read_text(errors="replace"), process.returncode

    def tool(self, executable, version_args=("--version",)):
        located = shutil.which(executable, path=execution_environment().get("PATH"))
        require(located is not None,
                f"Unavailable prerequisite: {executable}")
        path = Path(located).resolve()
        if executable.startswith("quartus_"):
            with path.open("rb") as stream:
                launcher = stream.read(65536)
            if launcher.startswith(b"#!") and b"qenv.sh" in launcher:
                roots = [path.parent.parent]
                roots.extend(Path(os.environ[name]) for name in (
                    "QUARTUS_ROOTDIR", "QUARTUS_ROOTDIR_OVERRIDE"
                ) if os.environ.get(name))
                valid_runtime = any((root / "adm" / "qenv.sh").is_file() for root in roots)
                self.record.setdefault("tool_preflight", {})[executable] = {
                    "launcher_found": True, "required_component": "adm/qenv.sh",
                    "component_found": valid_runtime,
                }
                self.save()
                require(valid_runtime, f"Unavailable prerequisite: {executable} launcher "
                        "exists but its required Quartus adm/qenv.sh runtime is absent")
        out, err, _ = self.run([executable, *version_args], name=f"version-{executable}")
        identity = {
            "executable_sha256": file_hash(path),
            "version_args": list(version_args),
            "stdout_sha256": hashlib.sha256(out.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(err.encode()).hexdigest(),
        }
        previous = self.record.setdefault("tools", {}).get(executable)
        require(previous is None or previous == identity,
                f"Tool identity changed during execution: {executable}")
        self.record["tools"][executable] = identity
        self.record.setdefault("tool_commands", {})[executable] = len(self.record["commands"])-1
        self.save()

    def finish(self, error=None):
        if error is None and time.time() >= self.deadline:
            error = AssertionError("Outer check budget exhausted; coverage incomplete")
        self.record["end"] = stamp()
        self.record["status"] = "failed" if error else "passed"
        if error:
            self.record["error"] = f"{type(error).__name__}: {error}"
        self.record["sources_unchanged"] = hashes() == self.record["source_sha256"]
        if not self.record["sources_unchanged"]:
            self.record["status"] = "failed"
            self.record["source_change_error"] = "Product sources changed during execution"
        self.save()
        require(self.record["sources_unchanged"], "Sources changed during check")
        if error and self.record.get("error", "").startswith(
                "AssertionError: Outer check budget exhausted"):
            raise error


def child_contract(parent_receipt, child_output, invocation):
    """Validate a project-owned child destination, not a runtime allocation."""
    for name in (parent_receipt, child_output):
        path = Path(name)
        require(not path.is_absolute() and ".." not in path.parts,
                "Internal execution paths must be project-relative")
    parent_path = ROOT / parent_receipt
    require(parent_path.name == "receipt.json" and parent_path.is_file()
            and parent_path.resolve() == parent_path
            and any(parent_path.is_relative_to(ROOT / "evidence" / kind)
                    for kind in ("checks", "preparation")),
            "Internal execution parent must be an existing exclusive evidence receipt")
    output = ROOT / child_output
    require(output.parent == parent_path.parent / "prerequisites"
            and output.parent.resolve() == output.parent
            and re.fullmatch(r"[a-z0-9][a-z0-9-]*-[0-9a-f]{32}", output.name),
            "Internal execution destination is outside parent-owned prerequisites")
    record = json.loads(parent_path.read_text())
    entries = record.get("internal_prerequisites", [])
    matches = [entry for entry in entries if entry["output"] == child_output]
    require(len(matches) == 1 and len({entry["output"] for entry in entries}) == len(entries),
            "Internal execution destination is missing or duplicated in parent contract")
    entry = matches[0]
    require(entry["argv"] == invocation and entry["runtime_allocation"] is False,
            "Internal execution argv differs from parent contract")
    return parent_path, output, record, entry
