"""Independently activated numerical defects in disposable source copies."""

import copy
from dataclasses import replace
import hashlib
import time

from isp_oracle import Config


def sensitivity(run, root, make_case, simulate, compact=False):
    run.version("verilator", ["--version"])
    p = (3, 3, 5, 3, 8, 3, 8, 0)
    entries = [
        ("roi-off-by-one", "swage_s3_crop.sv",
         "x >= roi_x", "x + 13'd1 >= roi_x",
         Config(enable=33, roi=(1, 1, 2, 2)), "ramp", "PIXEL_MISMATCH"),
        ("bayer-parity", "swage_s3_demosaic.sv",
         "w_x[0] ^ origin_x[0]", "w_x[0] ^ origin_x[0] ^ 1'b1",
         Config(enable=34), "ramp", "PIXEL_MISMATCH"),
        ("filter-rounding", "swage_s3_convolution.sv",
         "rounded_s6 <= magnitude_s5 + rounding_bias;",
         "rounded_s6 <= magnitude_s5;",
         Config(enable=36, shift=1), "1", "PIXEL_MISMATCH"),
        ("csc-rounding", "swage_s3_csc.sv",
         "(magnitude + 36'd4096) >> 13", "(magnitude + 36'd0) >> 13",
         Config(enable=40, csc_mode=4,
                csc_coeff=(4096, 0, 0, -4096, 0, 0, 8192, 0, 0),
                out_bias=(0, 128, 0)), "1", "PIXEL_MISMATCH"),
        ("chroma420-mask", "swage_s3_csc.sv",
         "(subsample && (x[0] || y[0]))", "(subsample && (!x[0] || y[0]))",
         Config(enable=56, output_420=True), "ramp", "PIXEL_MISMATCH"),
        ("scaler-phase", "swage_s3_plane_scaler.sv",
         "step_x <= quotient[PW-1:0];", "step_x <= quotient[PW-1:0] + (PW'(1)<<F);",
         Config(enable=48), "ramp", "PIXEL_MISMATCH"),
        ("histogram-lost-update", "swage_s3_histogram.sv",
         "increment_reg<=counter_q+32'd1", "increment_reg<=counter_q+32'd0",
         Config(enable=32), "77", "HISTOGRAM_MISMATCH"),
    ]
    if compact:
        entries = [entries[3]]
    for label, file, old, new, config, pattern, assertion in entries:
        case = make_case(p, config, 17, pattern, name=label)
        simulate(run, p, [case], f"{label}-baseline")
        directory = run.directory / f"mutation-{label}-{time.time_ns()}"
        directory.mkdir(exist_ok=False)
        changes = {}
        for source in sorted((root / "rtl").glob("*.sv")):
            data = source.read_text()
            if source.name == file:
                if data.count(old) != 1:
                    raise AssertionError(f"{label}: mutation target not unique")
                data = data.replace(old, new)
            target = directory / source.name
            target.write_text(data)
            changes[source.name] = hashlib.sha256(target.read_bytes()).hexdigest()
        run.record.setdefault("mutations", []).append({
            "class": label, "file": file, "old": old, "new": new,
            "copy_sha256": changes, "expected_assertion": assertion})
        simulate(run, p, [case], f"{label}-defect", rtl=directory, failure=assertion)
    case = make_case(p, Config(enable=32), 17, "77", name="comparison-path-baseline")
    simulate(run, p, [case], "comparison-path-baseline")
    for kind in ("output", "histogram"):
        changed = copy.deepcopy(case)
        if kind == "output":
            changed["expected"][0] = replace(changed["expected"][0],
                                              data=changed["expected"][0].data ^ 1)
            assertion = "PIXEL_MISMATCH"
        else:
            changed["histogram"][0] ^= 1
            assertion = "HISTOGRAM_MISMATCH"
        simulate(run, p, [changed], f"corrupted-expected-{kind}", failure=assertion)
