"""Real parent S1/S2, S3 and independent mathematical comparisons.

Parent modules are name-qualified in disposable copies solely to coexist with
the preserved public legacy modules used by S3. No logic is changed.
"""

import hashlib
import re
import time

from isp_oracle import Config, pipeline
from s3_sim import beat_word, pack


def legacy_bench(parameters, cases):
    sw, sh, dw, dh, bits, channels, frac, interpolation = parameters
    width = 16 if bits == 10 else bits * channels
    return f"""`timescale 1ns/1ps
module legacy_tb;
  localparam SW={sw},SH={sh},DW={dw},DH={dh},W={width};
  reg clk=0,rst_n;
  reg [2:0][W-1:0] sd;
  reg [2:0] sm=0;
  reg [2:0] sv=0,su=0,sl=0,mr=0;
  wire [2:0] sr,mv,mu,ml;
  wire [2:0][W-1:0] md;
  wire [2:0] out_mask;
  reg cv=0;
  wire [1:0] cr,ce;
  reg [1:0] mode=0;
  reg border=0,absolute=0;
  reg [4:0] shift=0;
  reg [143:0] coeff=0;
  wire [5:0] enable={{1'b0,1'b1,1'b0,mode[0],mode[1],1'b0}};
  wire hr,hv,hd;
  wire [255:0] hdata;
  parent_swage_s2_top #(.SRC_WIDTH(SW),.SRC_HEIGHT(SH),.DST_WIDTH(DW),
    .DST_HEIGHT(DH),.PIXEL_BITS({bits}),.CHANNELS({channels}),
    .PHASE_FRAC({frac}),.INTERPOLATION({interpolation})) parent_s2 (
    .clk(clk),.rst_n(rst_n),.s_axis_tdata(sd[0]),.s_axis_tvalid(sv[0]),
    .s_axis_tuser(su[0]),.s_axis_tlast(sl[0]),.s_axis_tready(sr[0]),
    .m_axis_tdata(md[0]),.m_axis_tvalid(mv[0]),.m_axis_tuser(mu[0]),
    .m_axis_tlast(ml[0]),.m_axis_tready(mr[0]),.cfg_valid(cv),
    .cfg_ready(cr[0]),.cfg_error(ce[0]),.cfg_mode(mode),.cfg_border(border),
    .cfg_abs(absolute),.cfg_shift(shift),.cfg_coeff(coeff));
  swage_s3_top #(.SRC_WIDTH(SW),.SRC_HEIGHT(SH),.DST_WIDTH(DW),
    .DST_HEIGHT(DH),.PIXEL_BITS({bits}),.CHANNELS({channels}),
    .PHASE_FRAC({frac}),.INTERPOLATION({interpolation})) current_s3 (
    .clk(clk),.rst_n(rst_n),.s_axis_tdata(sd[1]),.s_axis_cvalid(sm),
    .s_axis_tvalid(sv[1]),.s_axis_tuser(su[1]),.s_axis_tlast(sl[1]),
    .s_axis_tready(sr[1]),.m_axis_tdata(md[1]),.m_axis_cvalid(out_mask),
    .m_axis_tvalid(mv[1]),.m_axis_tuser(mu[1]),.m_axis_tlast(ml[1]),
    .m_axis_tready(mr[1]),.cfg_valid(cv),.cfg_ready(cr[1]),.cfg_error(ce[1]),
    .cfg_enable(enable),.cfg_border(border),.cfg_abs(absolute),.cfg_shift(shift),
    .cfg_coeff(coeff),.cfg_roi_x(13'd0),.cfg_roi_y(13'd0),
    .cfg_roi_width(13'(SW)),.cfg_roi_height(13'(SH)),.cfg_input_format(2'd0),
    .cfg_csc_mode(3'd0),.cfg_output_420(1'b0),.cfg_csc_coeff(144'd0),
    .cfg_csc_in_bias(48'd0),.cfg_csc_out_bias(48'd0),.cfg_hist_channel(2'd0),
    .hist_req_valid(1'b0),.hist_req_addr(5'd0),.hist_req_ready(hr),
    .hist_rsp_valid(hv),.hist_rsp_data(hdata),.hist_rsp_ready(1'b0),.hist_done(hd));
  parent_swage_s1_top #(.SRC_WIDTH(SW),.SRC_HEIGHT(SH),.DST_WIDTH(DW),
    .DST_HEIGHT(DH),.PIXEL_BITS({bits}),.CHANNELS({channels}),
    .PHASE_FRAC({frac}),.INTERPOLATION({interpolation})) parent_s1 (
    .clk(clk),.rst_n(rst_n),.s_axis_tdata(sd[2]),.s_axis_tvalid(sv[2]),
    .s_axis_tuser(su[2]),.s_axis_tlast(sl[2]),.s_axis_tready(sr[2]),
    .m_axis_tdata(md[2]),.m_axis_tvalid(mv[2]),.m_axis_tuser(mu[2]),
    .m_axis_tlast(ml[2]),.m_axis_tready(mr[2]));
  reg [31:0] src[0:SW*SH-1],expect_s2[0:DW*DH-1],expect_s1[0:DW*DH-1];
  reg [31:0] result_s2[0:DW*DH-1],result_s3[0:DW*DH-1];
  reg [31:0] word,old_word[0:2];
  reg [2:0] sf,of,old_stall=0;
  integer sent[0:2],got[0:2];
  integer c=0,k,i,seed,cycle=0,fd,n,total=0,differential=0,parent_bad=0;
  integer bound;
  string base,filename;
  task tick;
    begin
      #5;
      if (!rst_n && {{sr,mv,mu,ml,md[0],md[1],md[2],cr,ce,out_mask,
                     hr,hv,hd,hdata}} !== 0) $fatal(1,"LEGACY_RESET");
      sf=sr & sv;of=mv & mr;
      if ((^{{sr,mv}}) === 1'bx) $fatal(1,"LEGACY_ACTIVE_XZ");
      for (k=0;k<3;k=k+1) begin
        word={{6'd0,ml[k],mu[k],24'(md[k])}};
        if (rst_n && old_stall[k] && (!mv[k] || word !== old_word[k]))
          $fatal(1,"LEGACY_STABILITY engine=%0d",k);
        old_stall[k]=rst_n && mv[k] && !mr[k];old_word[k]=word;
        if (of[k]) begin
          if (got[k]>=DW*DH) $fatal(1,"LEGACY_EXTRA_OUTPUT engine=%0d",k);
          if (k==1) begin
            if (word !== expect_s2[got[k]] || out_mask !== ({channels}==1 ? 3'b001:3'b111))
              $fatal(1,"S3_LEGACY_ORACLE_MISMATCH case=%0d pixel=%0d got=%h expected=%h",
                     c,got[k],word,expect_s2[got[k]]);
            result_s3[got[k]]=word;
          end else begin
            if (word !== (k==0 ? expect_s2[got[k]] : expect_s1[got[k]])) begin
              parent_bad=parent_bad+1;
              if (parent_bad<=4)
                $display("PARENT_PREEXISTING_MISMATCH case=%0d engine=%0d pixel=%0d got=%h expected=%h",
                  c,k,got[k],word,k==0 ? expect_s2[got[k]] : expect_s1[got[k]]);
            end
            if (k==0) result_s2[got[k]]=word;
          end
          got[k]=got[k]+1;total=total+1;
        end
        if (sf[k]) begin
          if (sent[k]>=SW*SH || sd[k] !== W'(src[sent[k]][23:0]) ||
              su[k] !== (sent[k]==0) || sl[k] !== (sent[k]%SW==SW-1))
            $fatal(1,"LEGACY_SOURCE_DRIVER engine=%0d source=%0d",k,sent[k]);
          sent[k]=sent[k]+1;
        end
      end
      clk=1;#1;#4 clk=0;cycle=cycle+1;
    end
  endtask
  initial begin
    rst_n=0;
    for (i=0;i<3;i=i+1) begin sd[i]=0;sent[i]=0;got[i]=0;end
    tick();rst_n=1;#1;rst_n=0;tick();rst_n=1;tick();
    if (!$value$plusargs("fixtures=%s",base)) $fatal(1,"LEGACY_ARGUMENT");
    filename={{base,"/legacy.txt"}};fd=$fopen(filename,"r");
    if (!fd) $fatal(1,"LEGACY_FIXTURE_OPEN");
    while (!$feof(fd)) begin
      n=$fscanf(fd,"%h %h %h %h %h %h\\n",mode,border,absolute,shift,coeff,seed);
      if (n!=6) begin
        if (!(n==0 && $feof(fd))) $fatal(1,"LEGACY_FIXTURE_FORMAT");
      end else begin
        filename=$sformatf("%s/case-%0d-input.hex",base,c);$readmemh(filename,src);
        filename=$sformatf("%s/case-%0d-s2.hex",base,c);$readmemh(filename,expect_s2);
        filename=$sformatf("%s/case-%0d-s1.hex",base,c);$readmemh(filename,expect_s1);
        sv=0;mr=7;
        bound=0;
        while (cr!==2'b11) begin
          tick();bound=bound+1;
          if (bound>2048) $fatal(1,"LEGACY_CONFIG_BOUND");
        end
        cv=1;tick();cv=0;
        if (ce!==0) $fatal(1,"LEGACY_CONFIG_ACCEPT");
        for (i=0;i<3;i=i+1) begin sent[i]=0;got[i]=0;end
        bound=0;
        while (got[0]<DW*DH || got[1]<DW*DH || got[2]<DW*DH ||
               sent[0]<SW*SH || sent[1]<SW*SH || sent[2]<SW*SH) begin
          for (i=0;i<3;i=i+1) begin
            if (!sv[i] || sf[i]) begin
              sv[i]=sent[i]<SW*SH && (cycle+i+seed)%7!=1;
              if (sent[i]<SW*SH) begin
                sd[i]=W'(src[sent[i]][23:0]);su[i]=sent[i]==0;sl[i]=sent[i]%SW==SW-1;
              end
            end
            mr[i]=(cycle+i)%5!=1 && !(got[i]==0 && bound<32);
          end
          sm=({channels}==1 || mode[1]) ? 3'b001 : 3'b111;
          tick();bound=bound+1;
          if (bound>2048+128*(SW*SH+DW*DH+SW+SH+DW+DH))
            $fatal(1,"LEGACY_FINITE_WORK_BOUND case=%0d sent=%0d,%0d,%0d got=%0d,%0d,%0d",
                   c,sent[0],sent[1],sent[2],got[0],got[1],got[2]);
        end
        for (i=0;i<DW*DH;i=i+1) begin
          if (result_s2[i]===result_s3[i]) differential=differential+1;
          else if (result_s2[i]===expect_s2[i])
            $fatal(1,"PARENT_S3_DIFFERENTIAL case=%0d pixel=%0d",c,i);
        end
        sv=0;mr=7;
        repeat(16) tick();
        c=c+1;
      end
    end
    $display("LEGACY_SUMMARY cases=%0d pixels=%0d differential=%0d parent_mismatches=%0d",
             c,total,differential,parent_bad);
    $finish;
  end
endmodule
"""


def legacy(run, root, make_case, compact=False):
    run.version("verilator", ["--version"])
    directory = run.directory / f"legacy-sources-{time.time_ns()}"
    directory.mkdir(exist_ok=False)
    copied = []
    for source in sorted((root / "swage_s2_reference/rtl").glob("*.sv")):
        target = directory / source.name
        target.write_text(re.sub(r"\bswage_s[12]_[A-Za-z0-9_]+\b",
                                 lambda match: "parent_" + match.group(), source.read_text()))
        copied.append(target)
    configs = [(3, 3, 5, 3, 8, 3, 8, 0)] if compact else [
        (3, 5, 7, 3, bits, channels, frac, mode)
        for bits, channels in ((8, 1), (10, 1), (8, 3))
        for frac in (8, 16) for mode in (0, 1)]
    for index, parameters in enumerate(configs):
        bits, channels = parameters[4:6]
        scenarios = []
        for mode in ((0,) if bits == 10 else (range(4) if channels == 3 else range(2))):
            for border in (False, True):
                cfg = Config(enable=16 | (2 if mode & 2 else 0) | (4 if mode & 1 else 0),
                             border=border, coeff=(-1, 0, 1, -2, 0, 2, -1, 0, 1),
                             absolute=True, shift=2)
                scenarios.append(make_case(parameters, cfg, 43, "ramp",
                                           name=f"legacy-mode-{mode}-border-{int(border)}"))
        build_root = run.directory / f"legacy-{index}-{time.time_ns()}"
        build_root.mkdir(exist_ok=False)
        records = []
        for i, case in enumerate(scenarios):
            cfg = case["config"]
            mode = (2 if cfg.enable & 2 else 0) | (1 if cfg.enable & 4 else 0)
            records.append(" ".join(f"{v:x}" for v in (
                mode, int(cfg.border), int(cfg.absolute), cfg.shift, pack(cfg.coeff), 43)))
            source = [pack(pixel, 10 if channels == 1 else 8)
                      for row in case["image"] for pixel in row]
            s1, _ = pipeline(case["image"], Config(enable=16), parameters[2], parameters[3],
                             bits=bits, channels=channels, frac=parameters[6],
                             nearest=bool(parameters[7]))
            for label, values in (("input", source),
                    ("s2", [b.data | int(b.sof) << 24 | int(b.eol) << 25
                            for b in case["expected"]]),
                    ("s1", [b.data | int(b.sof) << 24 | int(b.eol) << 25 for b in s1])):
                (build_root / f"case-{i}-{label}.hex").write_text(
                    "".join(f"{v:08x}\n" for v in values))
        (build_root / "legacy.txt").write_text("\n".join(records) + "\n")
        bench = build_root / "legacy_tb.sv"
        bench.write_text(legacy_bench(parameters, scenarios))
        obj = build_root / "compile-obj"
        sources = [*sorted((root / "rtl").glob("*.sv")), *copied]
        name = f"legacy-{index}"
        if compact:
            binary = build_root / "sim.vvp"
            run.command(["iverilog", "-g2012", "-Wall", "-s", "legacy_tb",
                         "-o", run.path(binary), run.path(bench),
                         *[run.path(p) for p in sources]], f"{name}-iverilog-compile")
            run.command(["vvp", run.path(binary), f"+fixtures={run.path(build_root)}"],
                        f"{name}-iverilog-simulation")
        run.command(["verilator", "--binary", "--timing", "-Wno-fatal",
                     "--top-module", "legacy_tb", "-j", "2", "--Mdir", run.path(obj),
                     "-o", "sim", run.path(bench), *[run.path(p) for p in sources]],
                    f"{name}-compile")
        text = run.command([run.path(obj / "sim"), f"+fixtures={run.path(build_root)}"],
                           f"{name}-simulation")
        match = re.search(r"LEGACY_SUMMARY cases=(\d+) pixels=(\d+) differential=(\d+) "
                          r"parent_mismatches=(\d+)", text)
        if not match:
            raise AssertionError(f"{name}: missing completed legacy comparisons")
        count, pixels, differential, parent_bad = map(int, match.groups())
        if count != len(scenarios) or pixels != count * parameters[2] * parameters[3] * 3:
            raise AssertionError(f"{name}: incomplete legacy comparison")
        run.record["runs"].append({"name": name, "parameters": parameters, "cases": count,
            "pixels": pixels, "parent_s3_equal_pixels": differential,
            "parent_numerical_failures": parent_bad,
            "parent_copy_sha256": {run.path(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in copied}})
        for pattern in ("*.o", "*.a", "*.gch"):
            for path in obj.glob(pattern):
                path.unlink()
