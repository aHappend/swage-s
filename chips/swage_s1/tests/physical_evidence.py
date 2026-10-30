"""Read-only admission of physical evidence, never execution of native gates.

The shared runtime owns the two authoritative receipts. Its baseline status is
not product sign-off. Stronger product evidence must be explicitly linked by
``product_evidence`` entries (``id``, project-relative ``receipt``, ``sha256``)
to the existing admitted partition receipts. No glob/newest/root-level fallback
can establish that association. Missing bindings require Coordinator diagnosis;
this module cannot add them to runtime state.
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
    require(isinstance(summary, dict), "Missing timing summary")
    require(summary.get("netlist_mode") == "post_fit",
            "Post-map evidence cannot certify post-fit STA")
    require(type(summary.get("exit_code")) is int and summary["exit_code"] == 0,
            "STA execution did not complete successfully")
    for kind in ("setup", "hold"):
        slack = summary.get(f"wns_{kind}_ns")
        require(finite_number(slack) and slack >= 0,
                f"Nonnegative {kind} slack not demonstrated: {slack!r}")
        require(type(summary.get(f"failing_{kind}_count")) is int
                and summary[f"failing_{kind}_count"] == 0,
                f"Failing {kind} timing paths")
    for key in ("unconstrained_setup_paths", "unconstrained_functional_endpoints",
                "unapproved_timing_exceptions"):
        require(type(summary.get(key)) is int and summary[key] == 0,
                f"Missing or nonzero timing constraint accounting: {key}")
    clocks = summary.get("clocks")
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
    require(record.get("gate") == gate and record.get("applicable") is True
            and record.get("status") == "passed", "Native gate failed or is inapplicable")
    if gate == "quartus-sta":
        timing_summary(record.get("sta_summary"))
        require(record.get("clock_period_ns") == 10.0
                and record.get("top_module") == "swage_s1_top",
                "Native scaler timing target differs")
    else:
        require(native_scope(gate, record)["tier"] == SCALER_TIER,
                BRIDGE_TIER + ": register/heartbeat reads are not scaler workloads")
        require(record.get("requested_runs") == 3
                and record.get("deterministic_across_runs") is True,
                "Three deterministic native workload passes absent")
        require(isinstance(record.get("chip_id"), str) and record["chip_id"],
                "Missing actual physical chip identity")
    require(record.get("device") == DEVICE, "Native receipt exact target missing or different")


def configuration_inventory(actual, expected):
    def key(parameters):
        require(isinstance(parameters, dict)
                and all(type(value) is int for value in parameters.values()),
                "Configuration must contain explicit integer parameters")
        return tuple(sorted(parameters.items()))

    wanted = [key(parameters) for parameters in expected]
    observed = [key(parameters) for parameters in actual]
    require(len(observed) == len(set(observed)), "Duplicate physical configuration")
    require(len(wanted) == len(set(wanted)) and set(observed) == set(wanted),
            "Incomplete or different frozen physical configuration coverage")


def product_links(record, specs):
    links = record.get("product_evidence")
    require(isinstance(links, list) and links,
            "Native receipt lacks source/build/raw-report-bound product_evidence")
    require(all(isinstance(link, dict) and set(link) == {"id", "receipt", "sha256"}
                for link in links), "Malformed native product evidence binding")
    ids = [link["id"] for link in links]
    require(all(isinstance(ident, str) for ident in ids)
            and len(ids) == len(set(ids)), "Duplicate native product evidence binding")
    require(set(ids) == {spec["id"] for spec in specs},
            "Incomplete native physical variant/workload coverage")
    paths = []
    for link in links:
        name = Path(link["receipt"])
        require(not name.is_absolute() and ".." not in name.parts,
                "Native product receipt must be project-relative")
        require(re.fullmatch(r"[0-9a-f]{64}", link["sha256"]) is not None,
                "Invalid native product receipt hash")
        paths.append(name.as_posix())
    require(len(paths) == len(set(paths)), "One receipt cannot cover multiple configurations")
    return {link["id"]: link for link in links}


def validate_linked_products(e, gate, native, found, specs, validate, load):
    """Callbacks are the existing strict partition validators, not pass tokens."""
    links = product_links(native, specs)
    for spec in specs:
        ident = spec["id"]
        require(ident in found, f"Native product binding has no current admitted receipt: {ident}")
        path, _ = load(found, ident)
        require(links[ident]["receipt"] == e.relative(path)
                and links[ident]["sha256"] == file_hash(path),
                f"Stale/mismatched native product binding: {ident}")
    results = validate(e, found, [spec["id"] for spec in specs])
    configuration_inventory(
        [results[spec["id"]]["configuration"] for spec in specs],
        [spec["configuration"] for spec in specs],
    )
    expected_sources = {name: digest for name, digest in e.record["source_sha256"].items()
                        if not name.startswith("tests/")}
    require(native.get("source_sha256") == expected_sources,
            "Native source identity is missing, opaque, or stale")
    primary = native.get("execution_receipt")
    require(isinstance(primary, dict) and primary in list(links.values()),
            "Native raw execution is not bound to a validated product receipt")
    result = results[primary["id"]]
    timing = result if gate == "quartus-sta" else result["timing"]
    require(native.get("build_id") == timing["build_id"]
            and native.get("parameters") == result["configuration"],
            "Native build/configuration differs from its raw execution")
    if gate == "quartus-sta":
        for kind in ("setup", "hold"):
            require(native["sta_summary"][f"wns_{kind}_ns"]
                    == min(timing[f"wns_{kind}_ns"]),
                    "Native slack differs from the bound original post-fit reports")
    return [results[spec["id"]] for spec in specs]


def validate_native_gate(e, gate):
    from partitions import check_definitions, load_success, successes, validate_dependencies

    require(gate in ("quartus-sta", "board-hil"), "Unknown native physical gate")
    path = ROOT / NATIVE_DIRECTORY / f"{gate}.json"
    require(path.is_file() and path.resolve().is_relative_to((ROOT / NATIVE_DIRECTORY).resolve()),
            f"Authoritative runtime receipt unavailable: {e.relative(path)}")
    digest = file_hash(path)
    native = json.loads(path.read_text())
    binding = {
        "receipt": e.relative(path), "sha256": digest,
        "scope": native_scope(gate, native), "status": "not_validated",
    }
    e.record["authoritative_physical_evidence"] = binding
    e.save()
    native_requirements(gate, native)
    specs = [spec for spec in check_definitions() if spec["check"] == gate + "-shard"]
    # Discovery/validation only: native jobs are owned by the shared runtime.
    # Missing unadmitted physical variants must go through append-only admission.
    results = validate_linked_products(
        e, gate, native, successes(e), specs, validate_dependencies, load_success,
    )
    require(file_hash(path) == digest, "Authoritative receipt changed during validation")
    binding["status"] = "validated"
    binding["product_evidence"] = native["product_evidence"]
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
