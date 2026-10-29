"""Reusable compiled simulators, bound to actual sources, tools and commands.

Only a completed successful build is reused. Copy the executable into each
new run; retain the original compiler receipt and raw-log hashes, never treat
build reuse as reuse of numerical comparisons. No generated output is shipped.
"""

import json
from pathlib import Path
import shutil

from evidence import ROOT, file_hash, require


def build_key(e, rtl, tb, parameters):
    return {
        "rtl": {path.relative_to(ROOT).as_posix(): file_hash(path) for path in rtl},
        "testbench_sha256": file_hash(tb),
        "parameters": parameters,
        "tools": {name: e.record["tools"][name] for name in ("verilator", "make", "g++")},
        "flags": ["--binary", "--timing", "--assert", "-Wno-fatal", "--top-module", "tb"],
    }


def project_file(name):
    path = Path(name)
    require(not path.is_absolute() and ".." not in path.parts, "Unsafe build-cache path")
    path = ROOT / path
    require(path.is_file() and path.resolve().is_relative_to(ROOT / "evidence"),
            f"Missing or escaped compiled-build artifact: {name}")
    return path


def validate_build(build, key, *, open_evidence=None):
    require(build["key"] == key, "Compiled simulator source/configuration/tool key differs")
    receipt = project_file(build["receipt"])
    record = (open_evidence.record if open_evidence is not None
              and receipt.parent == open_evidence.path else json.loads(receipt.read_text()))
    require(record["status"] == "passed" or (
        open_evidence is not None and receipt.parent == open_evidence.path
        and record["status"] == "running"),
        "Compiled simulator origin has not completed successfully")
    require(record.get("sources_unchanged") is True or (
        open_evidence is not None and receipt.parent == open_evidence.path
        and record["status"] == "running"),
            "Compiled simulator origin changed sources")
    for name, digest in key["rtl"].items():
        require(record["source_sha256"].get(name) == digest
                and file_hash(receipt.parent / "sources" / name) == digest,
                "Compiled simulator source snapshot differs")
    require({name: record["tools"][name] for name in key["tools"]} == key["tools"],
            "Compiled simulator origin tool identity differs")
    for name, digest in build["files"].items():
        path = project_file(name)
        require(path.resolve().is_relative_to(receipt.parent.resolve())
                and file_hash(path) == digest, "Compiled simulator artifact changed")
    tb = project_file(build["testbench"])
    require(build["testbench"] in build["files"]
            and file_hash(tb) == key["testbench_sha256"],
            "Compiled simulator transactor changed")
    binary = project_file(build["binary"])
    command = record["commands"][build["command_index"]]
    argv = command["requested_argv"]
    prefix = ["verilator", *key["flags"], "--Mdir", str(binary.parent), "-o", "simulation"]
    suffix = [str(ROOT / name) for name in sorted(key["rtl"])] + [str(tb)]
    require(argv[:len(prefix)] == prefix
            and argv[-len(suffix):] == suffix
            and sorted(argv[len(prefix):-len(suffix)]) == sorted(
                f"-G{name}={value}" for name, value in key["parameters"].items())
            and command["exit_status"] == 0 and not command["timed_out"]
            and command["cwd"] == "."
            and command.get("end") and command.get("duration_s", -1) >= 0
            and all(command[name] in build["files"] for name in ("stdout", "stderr")),
            "Compiled simulator lacks a successful source-bound compiler command")
    require(build["binary"] in build["files"] and binary.stat().st_mode & 0o111,
            "Compiled simulator executable binding absent")
    return binary


def reuse_build(e, key, directory):
    cache = getattr(e, "_simulator_builds", None)
    if cache is None:
        cache = []
        for parent in ("checks", "preparation"):
            for path in sorted((ROOT / "evidence" / parent).glob("*/compiled-builds.json")):
                # The index is only a locator. The origin command and every
                # bound file are validated before executing a matching binary.
                cache.extend(json.loads(path.read_text()))
        e._simulator_builds = cache
    for build in cache:
        if build.get("key") != key:
            continue
        origin = project_file(build["receipt"])
        if origin.parent != e.path:
            record = json.loads(origin.read_text())
            if record.get("status") != "passed":
                continue
        binary = validate_build(build, key, open_evidence=e)
        target = directory / "simulation"
        shutil.copy2(binary, target)
        reuse = {
            "target": e.relative(target), "sha256": file_hash(target), "origin": build,
            "origin_receipt_sha256": None if origin.parent == e.path else file_hash(origin),
        }
        e.record.setdefault("reused_compilations", []).append(reuse)
        e.save()
        return [target]
    return None


def retain_build(e, key, tb, binary, command_index):
    command = e.record["commands"][command_index]
    files = [tb, binary, project_file(command["stdout"]), project_file(command["stderr"])]
    build = {
        "key": key, "receipt": e.relative(e.path / "receipt.json"),
        "command_index": command_index, "testbench": e.relative(tb),
        "binary": e.relative(binary),
        "files": {e.relative(path): file_hash(path) for path in files},
    }
    e._simulator_builds.append(build)
    retained = getattr(e, "_retained_simulator_builds", [])
    retained.append(build)
    e._retained_simulator_builds = retained
    (e.path / "compiled-builds.json").write_text(json.dumps(retained, indent=2) + "\n")
    return build


def validate_reuse(path, record, parameters, tb):
    target = tb.parent / "simulation"
    matches = [item for item in record.get("reused_compilations", [])
               if item["target"] == target.relative_to(ROOT).as_posix()]
    require(len(matches) == 1, "Missing or duplicate compiled simulator reuse binding")
    reuse = matches[0]
    key = reuse["origin"]["key"]
    require(key["parameters"] == parameters and key["testbench_sha256"] == file_hash(tb)
            and key["rtl"] == {
                source.relative_to(ROOT).as_posix(): record["source_sha256"].get(
                    source.relative_to(ROOT).as_posix())
                for source in sorted((ROOT / "rtl").glob("*.sv"))
            }
            and key["tools"] == {name: record["tools"][name] for name in key["tools"]},
            "Reused build differs from current receipt sources/configuration/tools")
    origin = project_file(reuse["origin"]["receipt"])
    require((origin == path and reuse["origin_receipt_sha256"] is None)
            or (origin != path and reuse["origin_receipt_sha256"] == file_hash(origin)),
            "Compiled simulator origin receipt changed")
    binary = validate_build(reuse["origin"], key)
    require(file_hash(target) == reuse["sha256"] == file_hash(binary),
            "Reused simulator executable changed")


def cleanup_intermediates(e, directory):
    require(directory.resolve().is_relative_to(e.path.resolve()),
            "Compilation cleanup must stay in the new evidence output")
    removed = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.is_symlink() and path.suffix in (".o", ".a", ".gch"):
            removed.append({"path": e.relative(path), "bytes": path.stat().st_size})
            path.unlink()
    e.record.setdefault("removed_compilation_intermediates", []).extend(removed)
    e.save()
