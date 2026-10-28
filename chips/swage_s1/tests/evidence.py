"""Exclusive, source-bound evidence for admitted checks and bounded probes."""

import datetime
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
SELECTORS = ("rtl/*.sv", "design/*.md", "fpga/*", "tests/*.py")


def sources(root=ROOT):
    return sorted({p for selector in SELECTORS for p in root.glob(selector) if p.is_file()})


def hashes(root=ROOT):
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
    def __init__(self, check, preparation=False):
        parent = ROOT / "evidence" / ("preparation" if preparation else "checks")
        self.path = parent / f"{check}-{uuid.uuid4().hex}"
        self.path.mkdir(parents=True, exist_ok=False)
        self.record = {
            "schema": 1, "check": check, "acceptance_evidence": not preparation,
            "argv": ["python3", *sys.argv], "cwd": ".", "start": stamp(),
            "source_sha256": hashes(), "commands": [], "assertions": [],
            "status": "running",
        }
        for source in sources():
            target = self.path / "sources" / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        self.save()
        print(self.relative(self.path / "receipt.json"), flush=True)

    def relative(self, path):
        return Path(path).relative_to(ROOT).as_posix()

    def save(self):
        # Only this still-open receipt is updated. Completed receipts are immutable.
        (self.path / "receipt.json").write_text(
            json.dumps(self.record, indent=2, sort_keys=True) + "\n", encoding="ascii"
        )

    def assertion(self, name, actual, expected):
        self.record["assertions"].append(
            {"name": name, "actual": actual, "expected": expected}
        )
        self.save()
        require(actual == expected, f"{name}: actual={actual!r}, expected={expected!r}")

    def run(self, argv, *, cwd=ROOT, timeout=3600, expected=(0,), name=None):
        argv = [str(arg) for arg in argv]
        requested_argv = list(argv)
        env = execution_environment()
        if Path(argv[0]).name.startswith(("quartus_", "qsys-")):
            located = shutil.which(argv[0], path=env.get("PATH"))
            if located:
                argv[0] = str(Path(located).resolve())
        require(timeout >= 3600, "Child timeout must be at least 3600 seconds")
        index = len(self.record["commands"])
        stem = f"{index:05d}-{name or Path(argv[0]).name}"
        stdout = self.path / f"{stem}.stdout"
        stderr = self.path / f"{stem}.stderr"
        command = {
            "argv": argv, "cwd": self.relative(cwd), "start": stamp(),
            "requested_argv": requested_argv,
            "timeout_s": timeout, "stdout": self.relative(stdout),
            "stderr": self.relative(stderr), "exit_status": None,
            "expected_exit_status": list(expected),
            "timed_out": False,
            "core_dump_limit_bytes": 0,
        }
        self.record["commands"].append(command)
        self.save()
        started = time.monotonic()
        try:
            with stdout.open("xb") as out, stderr.open("xb") as err:
                result = subprocess.run(
                    argv, cwd=cwd, stdout=out, stderr=err,
                    timeout=timeout, check=False,
                    env=env,
                    preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)),
                )
            command["exit_status"] = result.returncode
        except subprocess.TimeoutExpired:
            command["timed_out"] = True
            raise
        except OSError as error:
            command["launch_error"] = str(error)
            raise
        finally:
            command["end"] = stamp()
            command["duration_s"] = time.monotonic() - started
            self.save()
        require(result.returncode in expected,
                f"Command exit {result.returncode}: {argv}; "
                f"see {command['stdout']} and {command['stderr']}")
        return stdout.read_text(errors="replace"), stderr.read_text(errors="replace"), result.returncode

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
