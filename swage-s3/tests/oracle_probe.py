"""Bounded model/parent diagnostic, not an S3 acceptance-suite replacement.

Run: python3 tests/oracle_probe.py
"""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback
from dataclasses import replace
from fractions import Fraction

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isp_oracle import (Config, convolution, demosaic, histogram_words, legal,
                        matrix_pixel, pipeline, preset, round_away, scale_plane)


ROOT = Path(__file__).resolve().parent.parent


def model_checks() -> dict[str, int]:
    assert [round_away(Fraction(n, 2)) for n in (-5, -3, -1, 0, 1, 3, 5)] == [
        -3, -2, -1, 0, 1, 2, 3]
    for mode in range(4):
        coeff, ib, ob = preset(mode)
        endpoints = ((0, 0, 0), (255, 255, 255)) if mode < 2 else (
            (16, 128, 128), (235, 128, 128))
        expected = ((16, 128, 128), (235, 128, 128)) if mode < 2 else (
            (0, 0, 0), (255, 255, 255))
        assert tuple(matrix_pixel(p, coeff, ib, ob) for p in endpoints) == expected
    identity = (8192, 0, 0, 0, 8192, 0, 0, 0, 8192)
    assert matrix_pixel((0, 1, 255), identity, (0, 0, 0), (0, 0, 0)) == (0, 1, 255)
    assert matrix_pixel((1, 1, 1), (4096, 0, 0, -4096, 0, 0, 32767, 0, 0),
                        (0, 0, 0), (0, 128, 0)) == (1, 127, 4)
    ramp = [[(10,), (20,)], [(30,), (40,)]]
    assert demosaic(ramp, False, (0, 0))[0][0] == (10, 25, 40)
    assert demosaic(ramp, False, (1, 1))[0][0] == (40, 25, 10)
    assert demosaic(ramp, True, (0, 0))[1][1] == (0, 0, 40)
    assert convolution(ramp, (0, 0, 0, 0, -1, 0, 0, 0, 0), 0,
                       False, False) == [[(0,), (0,)], [(0,), (0,)]]
    assert convolution(ramp, (0, 0, 0, 0, -1, 0, 0, 0, 0), 0,
                       True, False) == ramp
    for frac in (8, 16):
        assert scale_plane([[0, 100]], 4, 1, frac, False) == [[0, 50, 100, 100]]
        assert scale_plane([[0, 100]], 4, 1, frac, True) == [[0, 100, 100, 100]]
        assert scale_plane([[1023]], 3, 2, frac, False) == [[1023] * 3] * 2
    total = 0
    for value in range(256):
        beats, bins = pipeline([[(value,)]], Config(enable=32), 1, 1, channels=1)
        assert len(beats) == 1 and beats[0].data == value
        assert beats[0].mask == 1 and beats[0].sof and beats[0].eol
        assert bins[value] == 1 and sum(bins) == 1
        assert histogram_words(bins)[value // 8] == 1 << (32 * (value % 8))
        total += 1
    for value in range(1024):
        beats, bins = pipeline([[(value,)]], Config(enable=32), 1, 1,
                               bits=10, channels=1)
        assert beats[0].data == value and bins[value >> 2] == 1 and sum(bins) == 1
    sparse = [[(x + y * 3, 40 + x, 90 + y) if not (x & 1 or y & 1)
               else (x + y * 3, 0, 0) for x in range(3)] for y in range(3)]
    cfg = Config(enable=16 | 32, input_format=2, hist_channel=1)
    beats, bins = pipeline(sparse, cfg, 5, 5)
    assert sum(b.mask == 7 for b in beats) == 9 and sum(bins) == 9
    assert all(b.data >> 8 == 0 for b in beats if b.mask == 1)
    inverse = replace(cfg, enable=8, csc_mode=4, csc_coeff=identity)
    expanded, _ = pipeline(sparse, inverse, 3, 3)
    for i, beat in enumerate(expanded):
        y, x = divmod(i, 3)
        assert beat.mask == 7
        assert (beat.data >> 8) == (
            sparse[y & ~1][x & ~1][1] | sparse[y & ~1][x & ~1][2] << 8)
    assert not legal(replace(cfg, enable=1, roi=(1, 0, 2, 3)), 3, 3, 8, 3)
    assert not legal(replace(cfg, input_format=3), 3, 3, 8, 3)
    assert not legal(replace(cfg, enable=8, csc_mode=0), 3, 3, 8, 3)
    assert not legal(replace(cfg, enable=32, hist_channel=3), 3, 3, 8, 3)
    return {"mono8_singletons": total, "mono10_singletons": 1024,
            "preset_endpoints": 8, "inverse420_replication_pixels": 9,
            "scaled420_pixels": 25}


def parent_testbench(image, zero, corrupt_expected=False):
    expected = demosaic(image, zero, (0, 0))
    inputs = [p[0] for row in image for p in row]
    outputs = [sum(v << (8 * c) for c, v in enumerate(p)) for row in expected for p in row]
    if corrupt_expected:
        outputs[0] ^= 1
    initialization = "\n".join(
        f"src[{i}]=8'd{value}; expected[{i}]=24'd{outputs[i]};"
        for i, value in enumerate(inputs))
    return f"""
module tb;
  reg clk=0;
  always #5 clk=~clk;
  reg rst_n;
  reg [7:0] src[0:8];
  reg [23:0] expected[0:8];
  integer sent=0, received=0, cycles=0;
  reg [23:0] stalled_data;
  reg stalled_sof, stalled_eol, stalled=0;
  wire sr, mv, mu, ml;
  wire [23:0] md;
  reg sv=0, mr=0, su=0, sl=0, accepted=0;
  reg [7:0] sd=0;
  swage_s2_demosaic #(.SRC_WIDTH(3),.SRC_HEIGHT(3)) dut(
    .clk(clk),.rst_n(rst_n),.s_axis_tdata(sd),
    .s_axis_tvalid(sv),.s_axis_tuser(su),.s_axis_tlast(sl),
    .s_axis_tready(sr),.border(1'b{int(zero)}),
    .m_axis_tdata(md),.m_axis_tvalid(mv),.m_axis_tuser(mu),.m_axis_tlast(ml),
    .m_axis_tready(mr));
  initial begin
    {initialization}
    rst_n=0;
    #1;
    if ({{md,mv,mu,ml,sr}} !== 28'd0) $fatal(1,"RESET_OUTPUT");
    #1 rst_n=1;
    #1 rst_n=0;
    #18 rst_n=1;
  end
  always @(posedge clk) begin
    if (!rst_n) begin
      accepted=0;
      if ({{md,mv,mu,ml,sr}} !== 28'd0) $fatal(1,"RESET_EDGE");
    end else begin
      accepted=sv && sr;
      cycles=cycles+1;
      if (cycles>20000) $fatal(1,"FINITE_CYCLE_BOUND");
      if (mv !== 1'b0 && mv !== 1'b1) $fatal(1,"VALID_XZ");
      if (sr !== 1'b0 && sr !== 1'b1) $fatal(1,"READY_XZ");
      if (stalled && {{md,mu,ml,mv}} !== {{stalled_data,stalled_sof,stalled_eol,1'b1}})
        $fatal(1,"STALL_STABILITY");
      stalled=mv && !mr;
      stalled_data=md; stalled_sof=mu; stalled_eol=ml;
      if (mv && mr) begin
        if (received>=9) $fatal(1,"EXTRA_OUTPUT");
        if (md !== expected[received] || mu !== (received==0) ||
            ml !== (received%3==2))
          $fatal(1,"NUMERICAL_OR_MARKER index=%0d actual=%h expected=%h",
                 received,md,expected[received]);
        received=received+1;
      end
      if (accepted) sent=sent+1;
      if (received==9 && sent==9) begin
        $display("COMPARED pixels=9 border={int(zero)}"); $finish;
      end
    end
  end
  always @(negedge clk) begin
    if (!rst_n && {{md,mv,mu,ml,sr}} !== 28'd0) $fatal(1,"RESET_NEGEDGE");
    if (!sv || accepted) begin
      sv=rst_n && sent<9 && cycles%4!=1;
      sd=src[sent<9?sent:0]; su=sent==0; sl=sent%3==2;
    end
    mr=rst_n && cycles%5!=2 && !(cycles>=50 && cycles<85);
  end
endmodule
"""


def main():
    stamp = str(time.time_ns())
    directory = Path("evidence/preparation") / f"oracle-parent-{stamp}"
    out = ROOT / directory
    out.mkdir(parents=True, exist_ok=False)
    receipt = {"argv": ["python3", "tests/oracle_probe.py"], "cwd": ".",
               "started_at": time.time(), "timeout_s": 3600, "commands": [],
               "source_sha256": {}, "tool_versions": {}, "scope": "advisory preparation"}
    for pattern in ("tests/*.py", "swage_s2_reference/rtl/*.sv"):
        for path in sorted(ROOT.glob(pattern)):
            receipt["source_sha256"][path.relative_to(ROOT).as_posix()] = (
                hashlib.sha256(path.read_bytes()).hexdigest())

    def run(argv, name, expect_failure=False):
        stdout, stderr = directory / f"{name}.stdout.txt", directory / f"{name}.stderr.txt"
        record = {"argv": argv, "cwd": ".", "started_at": time.time(),
                  "timeout_s": 3600, "stdout": stdout.as_posix(), "stderr": stderr.as_posix()}
        receipt["commands"].append(record)
        try:
            with (ROOT / stdout).open("w") as so, (ROOT / stderr).open("w") as se:
                result = subprocess.run(argv, cwd=ROOT, stdout=so, stderr=se, timeout=3600)
            record["exit_code"] = result.returncode
            record["timed_out"] = False
        except subprocess.TimeoutExpired:
            record["exit_code"] = None
            record["timed_out"] = True
            raise
        finally:
            record["ended_at"] = time.time()
        if (result.returncode != 0) != expect_failure:
            raise RuntimeError(f"{name} exited {result.returncode}; see {stderr}")
        return (ROOT / stdout).read_text() + (ROOT / stderr).read_text()

    status = 1
    try:
        receipt["tool_versions"]["python3"] = sys.version
        receipt["tool_versions"]["iverilog"] = run(["iverilog", "-V"], "iverilog-version")
        receipt["tool_versions"]["vvp"] = run(["vvp", "-V"], "vvp-version")
        receipt["model_checks"] = model_checks()
        image = [[((17 + 71 * x + 37 * y) % 256,) for x in range(3)] for y in range(3)]
        for border in (False, True):
            name = f"parent-demosaic-{int(border)}"
            tb = directory / f"{name}.sv"
            (ROOT / tb).write_text(parent_testbench(image, border))
            receipt["source_sha256"][tb.as_posix()] = hashlib.sha256(
                (ROOT / tb).read_bytes()).hexdigest()
            binary = directory / f"{name}.vvp"
            run(["iverilog", "-g2012", "-Wall", "-s", "tb", "-o", binary.as_posix(),
                 tb.as_posix(), "swage_s2_reference/rtl/swage_s2_demosaic.sv",
                 "swage_s2_reference/rtl/swage_s2_window.sv"], f"{name}-compile")
            output = run(["vvp", binary.as_posix()], f"{name}-simulation")
            if f"COMPARED pixels=9 border={int(border)}" not in output:
                raise AssertionError("missing completed numerical comparisons")
        receipt["parent_compared_pixels"] = 18
        tb = directory / "corrupt-expectation.sv"
        (ROOT / tb).write_text(parent_testbench(image, False, corrupt_expected=True))
        receipt["source_sha256"][tb.as_posix()] = hashlib.sha256(
            (ROOT / tb).read_bytes()).hexdigest()
        binary = directory / "corrupt-expectation.vvp"
        run(["iverilog", "-g2012", "-Wall", "-s", "tb", "-o", binary.as_posix(),
             tb.as_posix(), "swage_s2_reference/rtl/swage_s2_demosaic.sv",
             "swage_s2_reference/rtl/swage_s2_window.sv"], "corrupt-expectation-compile")
        output = run(["vvp", binary.as_posix()], "corrupt-expectation-simulation",
                     expect_failure=True)
        if "NUMERICAL_OR_MARKER index=0" not in output:
            raise AssertionError("expected corruption did not reach intended comparison")
        receipt["corrupt_expected_output_detected"] = True
        status = 0
    except Exception:
        (out / "failure.stderr.txt").write_text(traceback.format_exc())
        receipt["failure_stderr"] = (directory / "failure.stderr.txt").as_posix()
    finally:
        receipt["exit_code"] = status
        receipt["ended_at"] = time.time()
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps({"exit_code": status, "receipt": (directory / "receipt.json").as_posix()}))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
