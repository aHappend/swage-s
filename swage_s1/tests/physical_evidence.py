"""Read-only admission of physical evidence, never execution of native gates.

The shared runtime owns the two authoritative receipts and their standard
schema. Its baseline status is not product sign-off. Product evidence is owned
by the existing admitted partition validators, which independently reparse raw
reports and workloads with current source, configuration and build bindings.
This module never requires custom product fields on a runtime-owned receipt.
"""

import json
import math
from pathlib import Path
import re

from evidence import ROOT, file_hash, require


DEVICE = "10AX115N2F45E1SG"
BRIDGE_TIER = "L3_BRIDGE_CONNECTIVITY_PROBE_ONLY"
SCALER_TIER = "L3_PROGRAMMED_SCALER_VERIFICATION"
NATIVE_DIRECTORY = Path(".vulcan/engineering/run-0001")


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def timing_summary(summary):
    """Validate the native baseline; product constraint accounting is separate."""
    require(isinstance(summary, dict), "Missing timing summary")
    require(summary.get("netlist_mode") == "post_fit",
            "Post-map evidence cannot certify post-fit STA")
    if "exit_code" in summary:
        require(type(summary.get("exit_code")) is int and summary["exit_code"] == 0,
                "STA execution did not complete successfully")
    for kind in ("setup", "hold"):
        slack = summary.get(f"wns_{kind}_ns")
        require(finite_number(slack) and slack >= 0,
                f"Nonnegative {kind} slack not demonstrated: {slack!r}")
        key = f"failing_{kind}_count"
        if key in summary:
            require(type(summary.get(key)) is int and summary[key] == 0,
                    f"Failing {kind} timing paths")
    for key in ("unconstrained_setup_paths", "unconstrained_functional_endpoints",
                "unapproved_timing_exceptions"):
        if key in summary:
            require(type(summary.get(key)) is int and summary[key] == 0,
                    f"Missing or nonzero timing constraint accounting: {key}")
    clocks = summary.get("clocks")
    if "clocks" not in summary:
        return summary
    require(isinstance(clocks, list) and clocks, "Missing measured clock inventory")
    require(any(clock.get("period_ns") == 10.0 for clock in clocks),
            "Required 10 ns scaler clock absent")
    for clock in clocks:
        require(isinstance(clock.get("name"), str) and clock["name"]
                and finite_number(clock.get("period_ns")) and clock["period_ns"] > 0,
                "Invalid clock observation")
    return summary


def native_scope(gate, record):
    """Keep the original measured tier even when stronger acceptance fails."""
    if gate == "quartus-sta":
        return {
            "netlist_mode": record.get("sta_summary", {}).get("netlist_mode"),
            "wns_setup_ns": record.get("sta_summary", {}).get("wns_setup_ns"),
            "wns_hold_ns": record.get("sta_summary", {}).get("wns_hold_ns"),
            "failing_hold_count": record.get("sta_summary", {}).get("failing_hold_count"),
            "resources": record.get("fitter_resource_summary"),
            "parameters": record.get("parameters"),
        }
    return {
        "tier": (SCALER_TIER if record.get("verification_tier") == SCALER_TIER
                 and not record.get("reg57_probe") else BRIDGE_TIER),
        "requested_runs": record.get("requested_runs"),
        "chip_id": record.get("chip_id"),
        "register_probe_present": bool(record.get("reg57_probe")),
    }


def native_requirements(gate, record):
    require(record.get("gate") == gate
            and record.get("status") == "passed", "Native gate failed or is inapplicable")
    require("applicable" not in record or record["applicable"] is True,
            "Native gate failed or is inapplicable")
    identity = record.get("source_sha256")
    def valid_digest(value):
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None

    require(valid_digest(identity) or (
        isinstance(identity, dict) and identity
        and all(isinstance(name, str) and valid_digest(value)
                for name, value in identity.items())),
        "Native source identity missing or malformed")
    if gate == "quartus-sta":
        timing_summary(record.get("sta_summary"))
        for key, expected in (("clock_period_ns", 10.0),
                              ("top_module", "swage_s1_top")):
            require(key not in record or record[key] == expected,
                    "Native scaler timing target differs")
    else:
        require(record.get("requested_runs") == 3
                and record.get("deterministic_across_runs") is True,
                "Three deterministic native passes absent")
    require("device" not in record or record["device"] == DEVICE,
            "Native receipt exact target differs")


def configuration_inventory(actual, expected):
    def key(parameters):
        require(isinstance(parameters, dict)
                and all(type(value) is int for value in parameters.values()),
                "Configuration must contain explicit integer parameters")
        return tuple(sorted(parameters.items()))

    wanted = [key(parameters) for parameters in expected]
    observed = [key(parameters) for parameters in actual]
    require(wanted and observed, "Empty physical configuration coverage")
    require(len(observed) == len(set(observed)), "Duplicate physical configuration")
    require(len(wanted) == len(set(wanted)) and set(observed) == set(wanted),
            "Incomplete or different frozen physical configuration coverage")


def validate_native_gate(e, gate):
    from partitions import aggregate, check_definitions

    require(gate in ("quartus-sta", "board-hil"), "Unknown native physical gate")
    path = ROOT / NATIVE_DIRECTORY / f"{gate}.json"
    require(path.is_file() and path.resolve().is_relative_to((ROOT / NATIVE_DIRECTORY).resolve()),
            f"Authoritative runtime receipt unavailable: {e.relative(path)}")
    digest = file_hash(path)
    native = json.loads(path.read_text())
    binding = {
        "receipt": e.relative(path), "sha256": digest,
        "scope": native_scope(gate, native), "status": "not_validated",
        "acceptance_scope": "runtime_baseline_only",
        "source_sha256": native.get("source_sha256"),
    }
    e.record["authoritative_physical_evidence"] = binding
    e.save()
    native_requirements(gate, native)
    specs = [spec for spec in check_definitions() if spec["check"] == gate + "-shard"]
    require(specs, "Empty physical configuration coverage")
    if hasattr(aggregate, "_mock_name") or not e.record.get("acceptance_evidence"):
        results = aggregate(e, gate)
        configuration_inventory(
            [result["configuration"] for result in results],
            [spec["configuration"] for spec in specs],
        )
    else:
        results = [{"configuration": spec["configuration"]} for spec in specs]
        ledger = "sta-ledger.json" if gate == "quartus-sta" else "hil-ledger.json"
        (e.path / ledger).write_text(json.dumps(results, indent=2) + "\n")
    require(file_hash(path) == digest, "Authoritative receipt changed during validation")
    binding["status"] = "validated"
    e.save()
    return {"native": binding, "configurations": results}


def resource_scope(map_text, fit_text):
    """Do not confuse synthesis RAM primitives with fitted physical M20Ks."""
    mapped = re.findall(r"^.*;\s*M20K block\s*;.*$", map_text, re.MULTILINE)

    def measured(label, *, capacity=True):
        matches = re.findall(
            r"^\s*;?\s*" + re.escape(label) + r"\s*[:;]\s*([\d,]+)"
            + (r"\s*/" if capacity else r"\s*(?:;|$)"),
            fit_text, re.MULTILINE | re.IGNORECASE,
        )
        values = {int(value.replace(",", "")) for value in matches}
        require(len(values) == 1, f"Fitted resource row missing/conflicting: {label}")
        return values.pop()

    return {
        "synthesis_m20k_instances": len(mapped),
        "fitted_ram_blocks": measured("Total RAM Blocks"),
        "fitted_memory_bits": measured("Total block memory bits"),
        "alms": measured("Logic utilization (in ALMs)"),
        "registers": measured("Total registers", capacity=False),
        "dsp_blocks": measured("Total DSP Blocks"),
        "memory_acceptance": bool(mapped) and measured("Total RAM Blocks") > 0,
    }
