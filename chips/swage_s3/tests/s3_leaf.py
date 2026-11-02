"""Public-interface arithmetic latency checks, distinct from frame scheduling."""

import random
import re
import time

from isp_oracle import Config
from s3_sim import testbench, write_cases


def csc_latency(run, root, make_case):
    p = (3, 3, 3, 3, 8, 3, 8, 0)
    cases = []
    for mode in range(4):
        for sparse in ((False, True) if mode >= 2 else (False,)):
            config = Config(enable=8, csc_mode=mode,
                            input_format=(2 if sparse else 1) if mode >= 2 else 0,
                            output_420=mode < 2)
            cases.append(make_case(p, config, 17, "ramp",
                                   name=f"leaf-csc-{mode}-sparse-{int(sparse)}"))
    cases.append(make_case(p, Config(enable=8, csc_mode=4,
        csc_coeff=(4096, 0, 0, -4096, 0, 0, 32767, 0, 0), out_bias=(0, 128, 0)),
        43, "1", name="leaf-custom-half-ties"))
    directory = run.directory / f"leaf-csc-{time.time_ns()}"
    directory.mkdir(exist_ok=False)
    fixtures = directory / "fixtures"
    write_cases(fixtures, cases)
    base = testbench(p)
    instance = """  swage_s3_csc #(.MAX_WIDTH(SW),.DATA_WIDTH(DATA_WIDTH)) dut(
    .clk(clk),.rst_n(rst_n),.enable(1'b1),
    .image_width(13'(SW)),.image_height(13'(SH)),.origin_x(13'd0),.origin_y(13'd0),
    .input_format(cfg_input_format),.mode(cfg_csc_mode),.output_420(cfg_output_420),
    .coeff(cfg_csc_coeff),.in_bias(cfg_csc_in_bias),.out_bias(cfg_csc_out_bias),
    .s_axis_tdata(s_axis_tdata),.s_axis_cvalid(s_axis_cvalid),
    .s_axis_tvalid(s_axis_tvalid),.s_axis_tuser(s_axis_tuser),.s_axis_tlast(s_axis_tlast),
    .s_axis_tready(s_axis_tready),.m_axis_tdata(m_axis_tdata),.m_axis_cvalid(m_axis_cvalid),
    .m_axis_tvalid(m_axis_tvalid),.m_axis_tuser(m_axis_tuser),.m_axis_tlast(m_axis_tlast),
    .m_axis_tready(m_axis_tready));
  assign cfg_ready=1'b0;assign cfg_error=1'b0;assign hist_req_ready=1'b0;
  assign hist_rsp_valid=1'b0;assign hist_rsp_data=256'd0;assign hist_done=1'b0;
"""
    base = re.sub(r"  swage_s3_top #\(.*?dut\(\.\*\);\n", instance, base, count=1, flags=re.S)
    prefix = base.split("\n  initial begin")[0]
    bench = directory / "leaf.sv"
    bench.write_text(prefix + """
  integer accepted_at[0:SW*SH-1];
  integer latency,latency_checks=0;
  initial begin
    rst_n=0;#1;reset_check();tick();rst_n=1;#1;rst_n=0;tick();rst_n=1;tick();
    if (!$value$plusargs("fixtures=%s",base)) $fatal(1,"LEAF_ARGUMENT");
    filename={base,"/cases.txt"};fd=$fopen(filename,"r");
    if (!fd) $fatal(1,"LEAF_OPEN");
    m_axis_tready=1;
    while (!$feof(fd)) begin
      n=$fscanf(fd,"%h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h\\n",
        cfg_enable,cfg_border,cfg_abs,cfg_shift,cfg_coeff,
        cfg_roi_x,cfg_roi_y,cfg_roi_width,cfg_roi_height,cfg_input_format,cfg_csc_mode,
        cfg_output_420,cfg_csc_coeff,cfg_csc_in_bias,cfg_csc_out_bias,cfg_hist_channel,
        nout,seed,traffic,flags);
      if (n!=20) begin
        if (!(n==0 && $feof(fd))) $fatal(1,"LEAF_FORMAT");
      end else begin
        filename=$sformatf("%s/case-%0d-input.hex",base,index);$readmemh(filename,src);
        filename=$sformatf("%s/case-%0d-expected.hex",base,index);$readmemh(filename,expected);
        sent=0;got=0;frame_cycle=0;latency=cfg_input_format==2 ? 9:7;
        source_fire=0;
        while (sent<SW*SH || got<nout) begin
          if (!s_axis_tvalid || source_fire) begin
            s_axis_tvalid=sent<SW*SH && frame_cycle%4!=1;
            if (sent<SW*SH) begin
              s_axis_tdata=src[sent][23:0];s_axis_cvalid=src[sent][26:24];
              s_axis_tuser=src[sent][27];s_axis_tlast=src[sent][28];
            end
          end
          tick();
          if (source_fire) begin accepted_at[sent]=cycle;sent=sent+1;end
          if (output_fire) begin
            if (got>=nout || output_word !== expected[got])
              $fatal(1,"LEAF_CSC_VALUE case=%0d pixel=%0d",index,got);
            if (cycle-accepted_at[got] != latency)
              $fatal(1,"LEAF_CSC_LATENCY case=%0d pixel=%0d got=%0d expected=%0d",
                     index,got,cycle-accepted_at[got],latency);
            latency_checks=latency_checks+1;got=got+1;
          end
          frame_cycle=frame_cycle+1;
          if (frame_cycle>2048) $fatal(1,"LEAF_CSC_BOUND");
        end
        s_axis_tvalid=0;repeat(2) begin
          tick();if (output_fire) $fatal(1,"LEAF_CSC_EXTRA_OUTPUT");
        end
        index=index+1;
      end
    end
    $display("LEAF_SUMMARY cases=%0d latency_checks=%0d",index,latency_checks);$finish;
  end
endmodule
""")
    binary = directory / "sim.vvp"
    name = "leaf-csc"
    run.command(["iverilog", "-g2012", "-Wall", "-s", "tb", "-o", run.path(binary),
                 run.path(bench), *[run.path(path) for path in sorted((root / "rtl").glob("*.sv"))]],
                f"{name}-compile")
    text = run.command(["vvp", run.path(binary), f"+fixtures={run.path(fixtures)}"],
                       f"{name}-simulation")
    expected_checks = sum(len(c["expected"]) for c in cases)
    if f"LEAF_SUMMARY cases={len(cases)} latency_checks={expected_checks}" not in text:
        raise AssertionError("incomplete CSC leaf latency checks")
    run.record["runs"].append({"name": name, "parameters": p,
                               "counts": {"cases": len(cases), "pixels": expected_checks,
                                          "csc_latency_checks": expected_checks}})


def interpolation_latency(run, root):
    rng = random.Random(89)
    for bits, channels in ((8, 1), (10, 1), (8, 3)):
        for frac in (8, 16):
            name = f"leaf-interpolation-{bits}-{channels}-{frac}"
            directory = run.directory / f"{name}-{time.time_ns()}"
            directory.mkdir(exist_ok=False)
            samples = []
            expected = []
            for i in range(21):
                pixels = [[rng.randrange(1 << bits) for _ in range(channels)] for _ in range(4)]
                fx, fy = (rng.randrange(1 << frac) for _ in range(2))
                unit = 1 << frac
                result = [sum(pixels[k][c] * (fx if k & 1 else unit-fx) *
                              (fy if k & 2 else unit-fy) for k in range(4))
                          for c in range(channels)]
                values = [(v+(1 << (2*frac-1))) >> (2*frac) for v in result]
                words = [sum(v << (bits*c) for c, v in enumerate(pixel)) for pixel in pixels]
                samples.append((*words, fx, fy))
                expected.append(sum(v << (bits*c) for c, v in enumerate(values)))
            initialization = "\n".join(
                f"p0[{i}]={words[0]};p1[{i}]={words[1]};p2[{i}]={words[2]};"
                f"p3[{i}]={words[3]};xs[{i}]={words[4]};ys[{i}]={words[5]};gold[{i}]={expected[i]};"
                for i, words in enumerate(samples))
            width = 16 if bits == 10 else bits*channels
            bench = directory / "interpolation.sv"
            bench.write_text(f"""`timescale 1ns/1ps
module interpolation_tb;
  localparam W={width},F={frac},N=21;
  reg clk=0,rst_n,iv=0,isu=0,isl=0,mr=0;
  reg [F-1:0] fx=0,fy=0;
  reg [W-1:0] a=0,b=0,c=0,d=0;
  wire advance,empty,mv,mu,ml;
  wire [W-1:0] md;
  swage_s1_interp #(.PIXEL_BITS({bits}),.CHANNELS({channels}),
    .DATA_WIDTH(W),.PHASE_FRAC(F)) dut(
    .clk(clk),.rst_n(rst_n),.issue_valid(iv),.issue_sof(isu),.issue_eol(isl),
    .fx(fx),.fy(fy),.p00(a),.p01(b),.p10(c),.p11(d),
    .pipe_advance(advance),.pipe_empty(empty),.m_axis_tdata(md),.m_axis_tvalid(mv),
    .m_axis_tuser(mu),.m_axis_tlast(ml),.m_axis_tready(mr));
  reg [W-1:0] p0[0:N-1],p1[0:N-1],p2[0:N-1],p3[0:N-1],gold[0:N-1];
  reg [F-1:0] xs[0:N-1],ys[0:N-1];
  reg [5:0] valid_model=0;
  integer data_model[0:5];
  integer sent=0,got=0,cycle=0,j,hold_remaining=0,held=0,checks=0;
  reg issue,step,transfer;
  reg [W+1:0] observed,stall_word;
  reg stalled=0;
  task tick;
    begin
      #5;
      if (!rst_n && {{md,mv,mu,ml,advance}} !== 0) $fatal(1,"INTERP_RESET");
      if (rst_n && mv !== valid_model[5]) $fatal(1,"INTERP_SIX_STAGE_VALID");
      observed={{ml,mu,md}};step=advance;issue=iv&&advance;transfer=mv&&mr;
      if (mv && (^observed) === 1'bx) $fatal(1,"INTERP_ACTIVE_XZ");
      if (rst_n && stalled && (!mv || observed !== stall_word))
        $fatal(1,"INTERP_STALL");
      stalled=rst_n&&mv&&!mr;stall_word=observed;
      if (transfer) begin
        j=data_model[5];
        if (j!=got || md !== gold[j] || mu !== (j==0) || ml !== (j%7==6))
          $fatal(1,"INTERP_NUMERICAL_OR_MARKER index=%0d",got);
        got=got+1;checks=checks+1;
      end
      clk=1;#1;
      if (step) begin
        valid_model={{valid_model[4:0],iv}};
        for (j=5;j>0;j=j-1) data_model[j]=data_model[j-1];
        data_model[0]=sent;
      end
      if (issue) begin
        a=p0[sent];b=p1[sent];c=p2[sent];d=p3[sent];sent=sent+1;
      end
      #4 clk=0;cycle=cycle+1;
    end
  endtask
  initial begin
    {initialization}
    for (j=0;j<6;j=j+1) data_model[j]=0;
    rst_n=0;tick();rst_n=1;#1;rst_n=0;tick();rst_n=1;
    while (got<N) begin
      if (!iv || advance) iv=sent<N && cycle%4!=1;
      isu=sent==0;isl=sent%7==6;
      if (sent<N) begin fx=xs[sent];fy=ys[sent];end
      mr=cycle%3!=1;
      if (mv && !held) begin held=1;hold_remaining=32;end
      if (hold_remaining>0) begin mr=0;hold_remaining=hold_remaining-1;end
      tick();
      if (cycle>2048) $fatal(1,"INTERP_BOUND");
    end
    iv=0;mr=1;repeat(8) tick();
    if (checks!=N || !empty) $fatal(1,"INTERP_COMPLETION");
    $display("INTERPOLATION_SUMMARY pixels=%0d latency=6 held=32",checks);$finish;
  end
endmodule
""")
            binary = directory / "sim.vvp"
            run.command(["iverilog", "-g2012", "-Wall", "-s", "interpolation_tb", "-o",
                         run.path(binary), run.path(bench), "rtl/swage_s1_interp.sv"],
                        f"{name}-compile")
            text = run.command(["vvp", run.path(binary)], f"{name}-simulation")
            if "INTERPOLATION_SUMMARY pixels=21 latency=6 held=32" not in text:
                raise AssertionError("incomplete interpolation leaf check")
            run.record["runs"].append({"name": name, "counts": {"pixels": 21,
                "interpolation_latency_checks": 21}, "pixel_bits": bits,
                "channels": channels, "phase_frac": frac})
