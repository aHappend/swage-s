"""Seven independent disposable RTL defects; compiler failures are never kills."""

import hashlib
import json
import shutil

from cases import Case
from leaves import arithmetic


MUTATIONS = (
    ("temporal-threshold-equality", "swage_s4_temporal.sv",
     "motion < threshold1", "motion <= threshold1", "temporal"),
    ("temporal-rounding", "swage_s4_temporal.sv",
     "+ 18'd128", "+ 18'd0", "temporal"),
    ("clahe-clipping-redistribution", "swage_s4_clahe.sv",
     "(state == HIST_WRITE ? histogram_q+9'd1 : clipped)",
     "(state == HIST_WRITE ? histogram_q+9'd1 : histogram_q)", "clahe"),
    ("clahe-cdf-lut", "swage_s4_clahe.sv",
     "lut[index_reg] <= quotient[7:0]", "lut[index_reg] <= quotient[7:0] ^ 8'd1", "clahe"),
    ("patch-padding-tagging", "swage_s4_patch_tokenizer.sv",
     "? 3'b111 : 3'b000", "? 3'b001 : 3'b000", "patch"),
    ("q88-signed-rounding", "swage_s4_normalizer.sv",
     "product_magnitude[15:0] > 16'h8000", "product_magnitude[15:0] >= 16'h8000", "normalizer"),
    ("bf16-ties-even", "swage_s4_normalizer.sv",
     "bf_remainder == bf_half && bf_floor[0]", "bf_remainder == bf_half && !bf_floor[0]", "normalizer"),
)


def run_mutations(evidence, selected=None):
    from verify import PROJECT, unique, relative, simulation
    pristine = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (PROJECT/"rtl").glob("*.sv")}
    rows = []
    for name, filename, old, new, kind in MUTATIONS:
        if selected is not None and name != selected:
            continue
        tree = evidence.folder / unique("mutation-"+name)
        tree.mkdir()
        rtl = tree/"rtl"
        rtl.mkdir()
        for path in (PROJECT/"rtl").glob("*.sv"):
            shutil.copyfile(path, rtl/path.name)
        path = rtl/filename
        text = path.read_text()
        assert text.count(old) == 1, f"mutation target not unique {name}"
        path.write_text(text.replace(old, new))
        (tree/"mutation.json").write_text(json.dumps(
            dict(name=name, file=filename, original=old, mutated=new,
                 pristine_sha256=pristine, changed_sha256=hashlib.sha256(path.read_bytes()).hexdigest()),
            indent=2)+"\n")
        first = len(evidence.receipt["commands"])
        try:
            if kind in ("temporal", "normalizer"):
                arithmetic(evidence, kind, source=rtl, simulator="verilator", label=name)
            else:
                case = Case("sensitivity-"+name, (3, 5, 3, 5, 8, 3, 8, 0, 257, 8),
                            dict(s4=2 if kind=="clahe" else 4, clip=1, patch=8), pattern="constant")
                simulation(evidence, [case], source=rtl, label=name)
        except AssertionError as exc:
            commands = evidence.receipt["commands"][first:]
            assert len(commands) >= 2 and commands[-2]["exit_code"] == 0, "mutant failed to compile"
            simulated = commands[-1]
            assert simulated["argv"][0].endswith("/simulation"), "mutant did not execute compiled simulation"
            assert not simulated["timed_out"] and simulated["exit_code"] != 0, "timeout is not sensitivity"
            stdout = (PROJECT/simulated["stdout"]).read_text(errors="replace")
            mismatches = [line for line in stdout.splitlines() if "_MISMATCH" in line]
            assert mismatches, f"mutant failed without independent numerical mismatch: {exc}"
            row = dict(name=name, outcome="rejected by unchanged independent comparison",
                       first_mismatch=mismatches[0], simulation=simulated,
                       mutation=relative(tree/"mutation.json"),
                       parent_receipt=relative(evidence.folder/"receipt.json"),
                       tool_versions=evidence.receipt["tool_versions"])
            (tree/"receipt.json").write_text(json.dumps(row, indent=2)+"\n")
            rows.append(row)
        else:
            raise AssertionError(f"DEFECT_NOT_DETECTED {name}")
    assert len(rows) == (1 if selected is not None else 7)
    assert pristine == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (PROJECT/"rtl").glob("*.sv")}
    (evidence.folder/"defect-sensitivity.json").write_text(json.dumps(rows, indent=2)+"\n")
