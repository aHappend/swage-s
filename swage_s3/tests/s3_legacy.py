"""Real parent S1/S2 comparison, independently scored and compared with S3.

Parent modules run in a separate executable. Both implementations consume the
same image/configuration and are independently compared with the integer oracle;
retained accepted-output files also support a direct differential comparison.
"""

import re
import time

from isp_oracle import Config, pipeline
from s3_sim import pack


def legacy_bench(parameters):
    sw, sh, dw, dh, bits, channels, frac, interpolation = parameters
    width = 16 if bits == 10 else bits * channels
    return f"""`timescale 1ns/1ps
module legacy_tb;
  localparam SW={sw},SH={sh},DW={dw},DH={dh},W={width};
  reg clk=0,rst_n;
  reg [1:0][W-1:0] sd;
  reg [1:0] sv=0,su=0,sl=0,mr=0;
  wire [1:0] sr,mv,mu,ml;
  wire [1:0][W-1:0] md;
  reg cv=0;
  wire cr,ce;
  reg [1:0] mode=0;
  reg [1:0] saved_mode;
  reg border=0,absolute=0;
  reg [4:0] shift=0;
  reg [143:0] coeff=0;
  swage_s2_top #(.SRC_WIDTH(SW),.SRC_HEIGHT(SH),.DST_WIDTH(DW),
    .DST_HEIGHT(DH),.PIXEL_BITS({bits}),.CHANNELS({channels}),
    .PHASE_FRAC({frac}),.INTERPOLATION({interpolation})) parent_s2 (
    .clk(clk),.rst_n(rst_n),.s_axis_tdata(sd[0]),.s_axis_tvalid(sv[0]),
    .s_axis_tuser(su[0]),.s_axis_tlast(sl[0]),.s_axis_tready(sr[0]),
    .m_axis_tdata(md[0]),.m_axis_tvalid(mv[0]),.m_axis_tuser(mu[0]),
    .m_axis_tlast(ml[0]),.m_axis_tready(mr[0]),.cfg_valid(cv),
    .cfg_ready(cr),.cfg_error(ce),.cfg_mode(mode),.cfg_border(border),
    .cfg_abs(absolute),.cfg_shift(shift),.cfg_coeff(coeff));
  swage_s1_top #(.SRC_WIDTH(SW),.SRC_HEIGHT(SH),.DST_WIDTH(DW),
    .DST_HEIGHT(DH),.PIXEL_BITS({bits}),.CHANNELS({channels}),
    .PHASE_FRAC({frac}),.INTERPOLATION({interpolation})) parent_s1 (
    .clk(clk),.rst_n(rst_n),.s_axis_tdata(sd[1]),.s_axis_tvalid(sv[1]),
    .s_axis_tuser(su[1]),.s_axis_tlast(sl[1]),.s_axis_tready(sr[1]),
    .m_axis_tdata(md[1]),.m_axis_tvalid(mv[1]),.m_axis_tuser(mu[1]),
    .m_axis_tlast(ml[1]),.m_axis_tready(mr[1]));
  reg [31:0] src[0:SW*SH-1],expect_s2[0:DW*DH-1],expect_s1[0:DW*DH-1];
  reg [31:0] word,old_word[0:1];
  reg [1:0] sf=0,of=0,old_stall=0;
  reg [1:0][W-1:0] sampled_data,sampled_source;
  reg [1:0] sampled_valid,sampled_ready,sampled_user,sampled_last,sampled_su,sampled_sl;
  integer sent[0:1],got[0:1];
  integer c=0,k,i,seed,cycle=0,fd,n,total=0,parent_bad=0,observed_fd=0,bound;
  string base,filename,simulator;
  task tick;
    begin
      #5;
      if (!rst_n && {{sr,mv,mu,ml,md,cr,ce}} !== 0) $fatal(1,"LEGACY_RESET");
      sf=sr & sv;of=mv & mr;
      if ((^{{sr,mv}}) === 1'bx) $fatal(1,"LEGACY_ACTIVE_XZ");
      sampled_data=md;sampled_source=sd;sampled_valid=mv;sampled_ready=mr;
      sampled_user=mu;sampled_last=ml;sampled_su=su;sampled_sl=sl;
      clk=1;#1;
      for (k=0;k<2;k=k+1) begin
        word={{6'd0,sampled_last[k],sampled_user[k],24'(sampled_data[k])}};
        if (sampled_valid[k] && (^word) === 1'bx) $fatal(1,"LEGACY_PIXEL_XZ");
        if (rst_n && old_stall[k] && (!sampled_valid[k] || word !== old_word[k]))
          $fatal(1,"LEGACY_STABILITY engine=%0d",k);
        old_stall[k]=rst_n && sampled_valid[k] && !sampled_ready[k];old_word[k]=word;
        if (of[k]) begin
          if (got[k]>=DW*DH) $fatal(1,"LEGACY_EXTRA_OUTPUT engine=%0d",k);
          if (word !== (k==0 ? expect_s2[got[k]] : expect_s1[got[k]])) begin
            parent_bad=parent_bad+1;
            if (parent_bad<=4)
              $display("PARENT_PREEXISTING_MISMATCH case=%0d engine=%0d pixel=%0d got=%h expected=%h",
                c,k,got[k],word,k==0 ? expect_s2[got[k]] : expect_s1[got[k]]);
          end
          if (k==0) $fwrite(observed_fd,"%08h\\n",word);
          got[k]=got[k]+1;total=total+1;
        end
        if (sf[k]) begin
          if (sent[k]>=SW*SH || sampled_source[k] !== W'(src[sent[k]][23:0]) ||
              sampled_su[k] !== (sent[k]==0) || sampled_sl[k] !== (sent[k]%SW==SW-1))
            $fatal(1,"LEGACY_SOURCE_DRIVER engine=%0d source=%0d",k,sent[k]);
          sent[k]=sent[k]+1;
        end
      end
      #4 clk=0;cycle=cycle+1;
      if (cycle>500000) $fatal(1,"LEGACY_CYCLE_BUDGET");
    end
  endtask
  initial begin
    rst_n=0;
    for (i=0;i<2;i=i+1) begin sd[i]=0;sent[i]=0;got[i]=0;end
    tick();rst_n=1;#1;rst_n=0;tick();rst_n=1;tick();
    if (!$value$plusargs("fixtures=%s",base)) $fatal(1,"LEGACY_ARGUMENT");
    if (!$value$plusargs("simulator=%s",simulator)) $fatal(1,"LEGACY_SIMULATOR_ARGUMENT");
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
        filename=$sformatf("%s/case-%0d-observed-%s.hex",base,c,simulator);
        observed_fd=$fopen(filename,"w");
        if (!observed_fd) $fatal(1,"LEGACY_OBSERVATION_OPEN");
        sv=0;mr=3;bound=0;
        while (!cr) begin
          tick();bound=bound+1;
          if (bound>2048) $fatal(1,"LEGACY_CONFIG_BOUND");
        end
        cv=1;tick();cv=0;
        if (ce!==0) $fatal(1,"LEGACY_CONFIG_ACCEPT");
        if ({channels}!=3) begin
          saved_mode=mode;mode=({bits}==10 ? 1:2);cv=1;tick();cv=0;
          if (ce!==1) $fatal(1,"LEGACY_CONFIG_REJECT");
          mode=saved_mode;tick();
          if (ce!==0) $fatal(1,"LEGACY_CONFIG_ERROR_PULSE");
        end
        for (i=0;i<2;i=i+1) begin sent[i]=0;got[i]=0;end
        bound=0;
        while (got[0]<DW*DH || got[1]<DW*DH || sent[0]<SW*SH || sent[1]<SW*SH) begin
          for (i=0;i<2;i=i+1) begin
            if (!sv[i] || sf[i]) begin
              sv[i]=sent[i]<SW*SH && (cycle+i+seed)%7!=1;
              if (sent[i]<SW*SH) begin
                sd[i]=W'(src[sent[i]][23:0]);su[i]=sent[i]==0;sl[i]=sent[i]%SW==SW-1;
              end
            end
            mr[i]=(cycle+i)%5!=1 && !(got[i]==0 && bound<32);
          end
          cv=bound==4;
          tick();bound=bound+1;
          if (cv && (cr || ce)) $fatal(1,"LEGACY_BUSY_CONFIG");
          cv=0;
          if (bound>2048+128*(SW*SH+DW*DH+SW+SH+DW+DH))
            $fatal(1,"LEGACY_FINITE_WORK_BOUND case=%0d",c);
        end
        $fclose(observed_fd);observed_fd=0;
        sv=0;mr=3;repeat(16) tick();c=c+1;
      end
    end
    $display("LEGACY_SUMMARY cases=%0d pixels=%0d parent_mismatches=%0d",c,total,parent_bad);
    $finish;
  end
endmodule
"""


def legacy(run, root, make_case, simulate, compact=False):
    run.version("verilator", ["--version"])
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
        bench.write_text(legacy_bench(parameters))
        obj = build_root / "compile-obj"
        sources = [run.path(p) for p in sorted((root / "swage_s2_reference/rtl").glob("*.sv"))]
        name = f"legacy-{index}"
        if compact:
            binary = build_root / "sim.vvp"
            run.command(["iverilog", "-g2012", "-Wall", "-s", "legacy_tb",
                         "-o", run.path(binary), run.path(bench), *sources],
                        f"{name}-iverilog-compile")
            run.command(["vvp", run.path(binary), f"+fixtures={run.path(build_root)}",
                         "+simulator=iverilog"],
                        f"{name}-iverilog-simulation")
        run.command(["verilator", "--binary", "--timing", "-Wno-fatal",
                     "--top-module", "legacy_tb", "-j", "2", "--Mdir", run.path(obj),
                     "-o", "sim", run.path(bench), *sources], f"{name}-compile")
        text = run.command([run.path(obj / "sim"), f"+fixtures={run.path(build_root)}",
                            "+simulator=verilator"],
                           f"{name}-simulation")
        match = re.search(r"LEGACY_SUMMARY cases=(\d+) pixels=(\d+) parent_mismatches=(\d+)", text)
        if not match:
            raise AssertionError(f"{name}: missing completed legacy comparisons")
        count, pixels, parent_bad = map(int, match.groups())
        if count != len(scenarios) or pixels != count * parameters[2] * parameters[3] * 2:
            raise AssertionError(f"{name}: incomplete legacy comparison")
        s3_name = f"legacy-s3-{index}"
        simulate(run, parameters, scenarios, s3_name)
        s3_runs = sorted(run.directory.glob(f"{s3_name}-*/fixtures"))
        if len(s3_runs) != 1:
            raise AssertionError("ambiguous S3 differential output directory")
        differential, differences = 0, 0
        for i in range(len(scenarios)):
            parent = [int(line, 16) for line in
                      (build_root / f"case-{i}-observed-verilator.hex").read_text().splitlines()]
            current = [int(line, 16) for line in
                       (s3_runs[0] / f"case-{i}-observed.hex").read_text().splitlines()]
            current = [(v & 0xFFFFFF) | ((v >> 27) & 3) << 24 for v in current]
            if len(parent) != parameters[2]*parameters[3] or len(current) != len(parent):
                raise AssertionError("incomplete retained differential output")
            differential += sum(a == b for a, b in zip(parent, current))
            differences += sum(a != b for a, b in zip(parent, current))
        if differences and not parent_bad:
            raise AssertionError("parent/S3 differential mismatch without a parent oracle failure")
        run.record["runs"].append({"name": name, "parameters": parameters, "cases": count,
            "pixels": pixels, "parent_s3_equal_pixels": differential,
            "parent_s3_different_pixels": differences, "parent_numerical_failures": parent_bad})
        for pattern in ("*.o", "*.a", "*.gch"):
            for path in obj.glob(pattern):
                path.unlink()
