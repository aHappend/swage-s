"""Generated, public-pin-only transactors; never read DUT internals."""

import json
import re

from evidence import ROOT, require
from reference import (
    Config, PATTERNS, STALLS,
    compare_models, configurations, image, integer_reference,
)


STREAM_TB = r"""
`timescale 1ns/1ps
module tb;
  parameter integer SRC_WIDTH=8, SRC_HEIGHT=8, DST_WIDTH=16, DST_HEIGHT=16,
    PIXEL_BITS=8, CHANNELS=1, PHASE_FRAC=8, INTERPOLATION=0;
  localparam integer DATA_WIDTH=(PIXEL_BITS==10)?16:PIXEL_BITS*CHANNELS;
  localparam integer NI=3*SRC_WIDTH*SRC_HEIGHT, NO=3*DST_WIDTH*DST_HEIGHT;
  reg clk=0;
  reg rst_n;
  reg [DATA_WIDTH-1:0] sd=0;
  reg sv=0, su=0, sl=0, mr=0;
  wire sr, mv, mu, ml;
  wire [DATA_WIDTH-1:0] md;
  integer source_pixels[0:NI-1], expected_pixels[0:NO-1];
  integer expected_sof[0:NO-1], expected_eol[0:NO-1];
  integer fd, scan, cases, case_id, stall_mode, seed, abort_mode;
  integer nsource, nresult, nframes, scenario, i, guard;
  integer ic, oc, cycles, sofs, eols, stall_cycles, longest_stall, streak;
  integer bubble_cycles, simultaneous, row_reuse, skipped_rows;
  integer hold_left, hold_started, aborted, prelude, abort_hits;
  integer quiet, last_input_at, last_output_at, observed_row;
  integer total_clocks=0, scenario_start_clocks;
  reg held_output;
  reg [DATA_WIDTH+1:0] held_payload;
  reg [31:0] random_state;
  string vectors;
  always #5 clk=~clk;
  always @(posedge clk) total_clocks <= total_clocks+1;

  swage_s1_top #(
    .SRC_WIDTH(SRC_WIDTH),.SRC_HEIGHT(SRC_HEIGHT),
    .DST_WIDTH(DST_WIDTH),.DST_HEIGHT(DST_HEIGHT),
    .PIXEL_BITS(PIXEL_BITS),.CHANNELS(CHANNELS),
    .PHASE_FRAC(PHASE_FRAC),.INTERPOLATION(INTERPOLATION)
  ) target(.clk(clk),.rst_n(rst_n),
    .s_axis_tdata(sd),.s_axis_tvalid(sv),.s_axis_tuser(su),.s_axis_tlast(sl),
    .s_axis_tready(sr),.m_axis_tdata(md),.m_axis_tvalid(mv),
    .m_axis_tuser(mu),.m_axis_tlast(ml),.m_axis_tready(mr));

  task check_reset;
    begin
      if ({sr,mv,mu,ml,md} !== {(DATA_WIDTH+4){1'b0}})
        $fatal(1,"PROTOCOL reset outputs nonzero/unknown time=%0t",$time);
    end
  endtask
  always @(posedge clk) if (rst_n === 1'b0) check_reset();
  always @(negedge clk) if (rst_n === 1'b0) check_reset();

  function [31:0] next_random(input [31:0] state);
    reg [31:0] t;
    begin
      t=state^(state<<13); t=t^(t>>17); next_random=t^(t<<5);
    end
  endfunction

  task reset_frame;
    begin
      @(negedge clk); #1; rst_n=0; sv=0; sd=0; su=0; sl=0; mr=0;
      #1; check_reset();
      repeat(3) @(posedge clk);
      @(negedge clk); #1; rst_n=1;
    end
  endtask

  task pulse_reset;
    begin
      @(negedge clk); #1; rst_n=0; sv=0; sd=0; su=0; sl=0; mr=0;
      #1; check_reset();
      #1; rst_n=1;
      #1; check_reset();
      $display("ASYNC_PULSE %0d",case_id);
    end
  endtask

  task run_frame_set(input integer is_prelude);
    begin
      ic=0; oc=0; cycles=0; sofs=0; eols=0; stall_cycles=0;
      bubble_cycles=0; simultaneous=0; longest_stall=0; streak=0;
      hold_left=0; hold_started=0; held_output=0; aborted=0;
      last_input_at=-1; last_output_at=-1;
      random_state=32'(seed); sv=0;
      while (((ic<nsource)||(oc<nresult)) && !aborted) begin
        @(negedge clk);
        random_state=next_random(random_state);
        if (!sv && ic<nsource && !(stall_mode==1 && cycles==0) &&
            (stall_mode!=1 || random_state[2:0]!=0)) begin
          sv=1; sd=DATA_WIDTH'(source_pixels[ic]);
          su=(ic%(SRC_WIDTH*SRC_HEIGHT)==0);
          sl=(ic%SRC_WIDTH==SRC_WIDTH-1);
        end
        if (stall_mode==2 && !hold_started && mv===1'b1) begin
          hold_left=40; hold_started=1;
        end
        if (stall_mode==1 && !hold_started && mv===1'b1) begin
          hold_left=2; hold_started=1;
        end
        if (is_prelude && abort_mode==3 && mv===1'b1) begin
          hold_left=40; hold_started=1;
        end
        mr=(stall_mode!=1 || random_state[6:4]!=0);
        if (hold_left>0) begin mr=0; hold_left=hold_left-1; end
        @(posedge clk);
        cycles=cycles+1;
        if ((sr!==1'b0 && sr!==1'b1) || (mv!==1'b0 && mv!==1'b1) ||
            (mu!==1'b0 && mu!==1'b1) || (ml!==1'b0 && ml!==1'b1))
          $fatal(1,"PROTOCOL unknown active control case=%0d",case_id);
        if (held_output && {mv,mu,ml,md} !== {1'b1,held_payload})
          $fatal(1,"PROTOCOL output changed under backpressure case=%0d",case_id);
        if (mv) begin
          if ((^md)===1'bx)
            $fatal(1,"MISMATCH active X/Z case=%0d pixel=%0d",case_id,oc);
          if (oc>=nresult)
            $fatal(1,"MISMATCH excess output case=%0d",case_id);
          if ({md,mu,ml} !== {DATA_WIDTH'(expected_pixels[oc]),
                 1'(expected_sof[oc]),1'(expected_eol[oc])})
            $fatal(1,"MISMATCH case=%0d pixel=%0d expected=%h/%0d/%0d got=%h/%b/%b",
              case_id,oc,expected_pixels[oc],expected_sof[oc],
              expected_eol[oc],md,mu,ml);
        end
        held_output=mv&&!mr;
        held_payload={mu,ml,md};
        if (mv&&!mr) begin
          stall_cycles=stall_cycles+1; streak=streak+1;
          if (streak>longest_stall) longest_stall=streak;
        end else streak=0;
        if (!sv && ic<nsource) bubble_cycles=bubble_cycles+1;
        if (sv&&sr&&mv&&mr) simultaneous=simultaneous+1;
        if (sv&&sr) begin ic=ic+1; last_input_at=cycles; end
        if (mv&&mr) begin
          if (!is_prelude && oc%DST_WIDTH==0)
            $display("ROW %0d %0d %0d",case_id,oc/DST_WIDTH,ic);
          oc=oc+1; sofs=sofs+integer'(mu); eols=eols+integer'(ml);
          last_output_at=cycles;
        end
        if (is_prelude) begin
          if (abort_mode==1 && ic>=((SRC_WIDTH>1)?SRC_WIDTH/2:1) &&
              ic<SRC_WIDTH*SRC_HEIGHT) aborted=1;
          if (abort_mode==2 && ic>=((INTERPOLATION==1)?1:2)*SRC_WIDTH && oc==0 &&
              cycles>=last_input_at+3 && !mv) aborted=1;
          if (abort_mode==3 && streak>=8) aborted=1;
          if (abort_mode==4 && oc>=DST_WIDTH*DST_HEIGHT &&
              ic>2*SRC_WIDTH && ic<SRC_WIDTH*SRC_HEIGHT) aborted=1;
          if (abort_mode==5 && oc>=DST_WIDTH-4 && oc<DST_WIDTH) aborted=1;
          if (abort_mode==6 && ic==SRC_WIDTH && oc==0) aborted=1;
          if (abort_mode==7 && oc==DST_WIDTH*DST_HEIGHT &&
              ic==SRC_WIDTH*SRC_HEIGHT && cycles>=last_output_at+2) aborted=1;
        end
        #1;
        // Retire the driven source only using the pre-edge acceptance.
        if (last_input_at==cycles) sv=0;
        if (cycles>100000+100*(nsource+nresult))
          $fatal(1,"PROTOCOL incomplete case=%0d in=%0d out=%0d",case_id,ic,oc);
      end
      if (is_prelude) begin
        if (!aborted) $fatal(1,"PROTOCOL reset event not reached %0d",abort_mode);
        $display("ABORT %0d %0d %0d %0d",case_id,abort_mode,ic,oc);
      end else begin
        if (ic!=nsource || oc!=nresult || sofs!=nframes || eols!=nframes*DST_HEIGHT)
          $fatal(1,"PROTOCOL counts case=%0d",case_id);
        if (stall_mode==2 && longest_stall<32)
          $fatal(1,"PROTOCOL directed stall missed case=%0d",case_id);
        if (stall_mode==1 && (stall_cycles==0 || bubble_cycles==0))
          $fatal(1,"PROTOCOL random stalls/bubbles missed case=%0d",case_id);
        $display("RESULT %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d",
          case_id,ic,oc,sofs,eols,stall_cycles,longest_stall,
          bubble_cycles,simultaneous,cycles);
        sv=0; mr=1;
        repeat(16) begin
          @(posedge clk);
          if (mv!==1'b0) $fatal(1,"MISMATCH extra output case=%0d",case_id);
        end
      end
    end
  endtask

  initial begin
    rst_n=0;
`ifdef __ICARUS__
    #0; check_reset();
`else
    #0.001; check_reset();
`endif
    if (!$value$plusargs("vectors=%s",vectors)) $fatal(1,"Missing vectors");
    fd=$fopen(vectors,"r");
    if (!fd) $fatal(1,"Cannot open vectors");
    scan=$fscanf(fd,"%d",cases);
    if (scan!=1 || cases<1) $fatal(1,"Invalid case count");
    for (scenario=0;scenario<cases;scenario=scenario+1) begin
      scan=$fscanf(fd,"%d %d %d %d %d %d %d",
        case_id,stall_mode,seed,abort_mode,nframes,nsource,nresult);
      if (scan!=7 || nsource>NI || nresult>NO) $fatal(1,"Invalid header");
      for(i=0;i<nsource;i=i+1) begin
        scan=$fscanf(fd,"%h",source_pixels[i]);
        if(scan!=1) $fatal(1,"Invalid source vector");
      end
      for(i=0;i<nresult;i=i+1) begin
        scan=$fscanf(fd,"%h %d %d",expected_pixels[i],expected_sof[i],expected_eol[i]);
        if(scan!=3) $fatal(1,"Invalid reference vector");
      end
      scenario_start_clocks=total_clocks;
      reset_frame();
      if (abort_mode!=0) begin
        prelude=1; run_frame_set(1);
        if (abort_mode==3) pulse_reset();
        else reset_frame();
      end
      prelude=0;
      run_frame_set(0);
      #1;
      $display("SCENARIO_CLOCKS %0d %0d",case_id,total_clocks-scenario_start_clocks);
    end
    $fclose(fd);
    $display("COMPLETE %0d",cases);
    $display("TOTAL_CLOCKS %0d",total_clocks);
    $finish;
  end
endmodule
"""

INVALID_TB = r"""
`timescale 1ns/1ps
module tb;
  parameter integer SRC_WIDTH=8,SRC_HEIGHT=8,DST_WIDTH=16,DST_HEIGHT=16,
    PIXEL_BITS=8,CHANNELS=1,PHASE_FRAC=8,INTERPOLATION=0;
  localparam integer W=(PIXEL_BITS==10&&CHANNELS==1)?16:
                       ((PIXEL_BITS==8&&CHANNELS==3)?24:8);
  reg clk=0,rst_n;
  wire sr,mv,mu,ml;
  wire [W-1:0] md;
  always #5 clk=~clk;
  swage_s1_top #(.SRC_WIDTH(SRC_WIDTH),.SRC_HEIGHT(SRC_HEIGHT),
    .DST_WIDTH(DST_WIDTH),.DST_HEIGHT(DST_HEIGHT),.PIXEL_BITS(PIXEL_BITS),
    .CHANNELS(CHANNELS),.PHASE_FRAC(PHASE_FRAC),.INTERPOLATION(INTERPOLATION))
    target(.clk(clk),.rst_n(rst_n),.s_axis_tdata({W{1'b0}}),
    .s_axis_tvalid(1'b0),.s_axis_tuser(1'b0),.s_axis_tlast(1'b0),
    .s_axis_tready(sr),.m_axis_tdata(md),.m_axis_tvalid(mv),
    .m_axis_tuser(mu),.m_axis_tlast(ml),.m_axis_tready(1'b0));
  initial begin rst_n=0; #20; $fatal(1,"Illegal configuration was accepted"); end
endmodule
"""


PIPELINE_TB = r"""
`timescale 1ns/1ps
module tb;
  parameter integer PIXEL_BITS=8, CHANNELS=1, PHASE_FRAC=8;
  localparam integer DATA_WIDTH=(PIXEL_BITS==10)?16:PIXEL_BITS*CHANNELS;
  reg clk=0, rst_n;
  reg iv=0, isu=0, isl=0, ready=0;
  reg [PHASE_FRAC-1:0] fx=0,fy=0;
  reg [DATA_WIDTH-1:0] a=0,b=0,c=0,d=0;
  reg [DATA_WIDTH-1:0] p00,p01,p10,p11;
  wire advance,empty,mv,mu,ml;
  wire [DATA_WIDTH-1:0] md;
  reg [5:0] qv=0,qu=0,ql=0;
  reg [DATA_WIDTH-1:0] qdata[0:5];
  reg [DATA_WIDTH-1:0] expected=0;
  integer fd,n,scan,index=0,tick=0,count=0,i,stall=0,simultaneous=0;
  string vectors;
  always #5 clk=~clk;
  always @(posedge clk)
    if(advance&&iv) begin p00<=a; p01<=b; p10<=c; p11<=d; end
  swage_s1_interp #(.PIXEL_BITS(PIXEL_BITS),.CHANNELS(CHANNELS),
    .PHASE_FRAC(PHASE_FRAC),.DATA_WIDTH(DATA_WIDTH)) target(
    .clk(clk),.rst_n(rst_n),.issue_valid(iv),.issue_sof(isu),.issue_eol(isl),
    .fx(fx),.fy(fy),.p00(p00),.p01(p01),.p10(p10),.p11(p11),
    .pipe_advance(advance),.pipe_empty(empty),.m_axis_tdata(md),
    .m_axis_tvalid(mv),.m_axis_tuser(mu),.m_axis_tlast(ml),.m_axis_tready(ready));
  task reset_check;
    if ({mv,mu,ml,md,advance} !== {(DATA_WIDTH+4){1'b0}})
      $fatal(1,"PROTOCOL pipeline reset");
  endtask
  always @(posedge clk) if(!rst_n) reset_check();
  always @(negedge clk) if(!rst_n) reset_check();
  initial begin
    rst_n=0;
`ifdef __ICARUS__
    #0; reset_check();
`else
    #0.001; reset_check();
`endif
    if(!$value$plusargs("vectors=%s",vectors)) $fatal(1,"vectors missing");
    fd=$fopen(vectors,"r"); if(!fd) $fatal(1,"vectors open");
    scan=$fscanf(fd,"%d",n); if(scan!=1) $fatal(1,"vector header");
    repeat(3) @(posedge clk);
    @(negedge clk); rst_n=1;
    while(index<n || qv!=0) begin
      @(negedge clk);
      tick=tick+1;
      ready=!(tick>=15&&tick<55) && (tick%7!=0);
      if(advance) begin
        iv=(index<n && tick%5!=0); isu=(index==0); isl=(index%7==6);
        if(iv) begin
          scan=$fscanf(fd,"%h %h %h %h %h %h %h",a,b,c,d,fx,fy,expected);
          if(scan!=7) $fatal(1,"pipeline vector malformed");
        end
      end
      @(posedge clk);
      if(mv!==qv[5] || mu!==(qv[5]&&qu[5]) || ml!==(qv[5]&&ql[5]))
        $fatal(1,"MISMATCH six-stage advancing-edge tags tick=%0d",tick);
      if(mv && md!==qdata[5])
        $fatal(1,"MISMATCH six-stage arithmetic tick=%0d expected=%h actual=%h",
          tick,qdata[5],md);
      if(mv&&ready) count=count+1;
      if(mv&&!ready) stall=stall+1;
      if(mv&&ready&&iv&&advance) simultaneous=simultaneous+1;
      if(advance) begin
        for(i=5;i>0;i=i-1) qdata[i]=qdata[i-1];
        qdata[0]=expected;
        qv={qv[4:0],iv}; qu={qu[4:0],isu}; ql={ql[4:0],isl};
        if(iv) index=index+1;
      end
      #1;
      if(tick>10000) $fatal(1,"pipeline incomplete");
    end
    if(count!=n || stall<32 || simultaneous==0)
      $fatal(1,"PROTOCOL pipeline event counts");
    $display("PIPELINE %0d %0d %0d",count,stall,simultaneous);
    $finish;
  end
endmodule
"""


def compile_sim(e, simulator, c, directory, tb_text=STREAM_TB, rtl=None, parameters=None):
    directory.mkdir(parents=True, exist_ok=False)
    tb = directory / "tb.sv"
    tb.write_text(tb_text, encoding="ascii")
    rtl = sorted((rtl or ROOT / "rtl").glob("*.sv"))
    params = parameters if parameters is not None else c.parameters()
    if simulator == "icarus":
        binary = directory / "simulation.vvp"
        e.run(["iverilog", "-g2012", "-Wall", "-s", "tb",
               *[f"-Ptb.{key}={value}" for key, value in params.items()],
               "-o", binary, *rtl, tb])
        return ["vvp", binary]
    obj = directory / "obj"
    e.run(["verilator", "--binary", "--timing", "--assert", "-Wno-fatal",
           "--top-module", "tb", "--Mdir", obj, "-o", "simulation",
           *[f"-G{key}={value}" for key, value in params.items()], *rtl, tb])
    return [obj / "simulation"]


def scenario_records(c, *, small=False, mutation=False):
    patterns = ("random", "vertical", "horizontal", "ties") if mutation else PATTERNS
    stalls = ((0, 17),) if (small or mutation) else STALLS
    for pattern in patterns:
        for stall, seed in stalls:
            yield {"pattern": pattern, "stall": stall, "seed": seed, "abort": 0}
    if not (small or mutation):
        yield {"pattern": "ties", "stall": 2, "seed": 89, "abort": 0}
        if (c.sw, c.sh, c.dw, c.dh) == (8, 8, 16, 16):
            for abort in (1, 2, 3, 5, 6, 7):
                yield {"pattern": "random", "stall": 0, "seed": 2026, "abort": abort}
        if (c.sw, c.sh, c.dw, c.dh) == (8, 16, 5, 1):
            yield {"pattern": "random", "stall": 0, "seed": 89, "abort": 4}


def write_vectors(c, directory, records, *, case_ids=None):
    case_ids = list(range(len(records))) if case_ids is None else list(case_ids)
    require(len(case_ids) == len(records) and len(set(case_ids)) == len(case_ids),
            "Scenario identities must be unique and complete")
    path = directory / "vectors.txt"
    model_records = []
    with path.open("x", encoding="ascii") as stream:
        stream.write(f"{len(records)}\n")
        for case_id, record in zip(case_ids, records):
            inputs, outputs, comparisons = [], [], []
            for frame in range(3):
                pixels = image(c, record["pattern"], record["seed"], frame)
                expected = integer_reference(c, pixels)
                comparisons.append(compare_models(c, pixels, expected))
                inputs.extend(pixels)
                outputs.extend(expected)
            stream.write(
                f"{case_id} {record['stall']} {record['seed']} {record['abort']} "
                f"3 {len(inputs)} {len(outputs)}\n"
            )
            stream.writelines(f"{pixel:x}\n" for pixel in inputs)
            stream.writelines(f"{pixel:x} {sof} {eol}\n" for pixel, sof, eol in outputs)
            model_records.append({"id": case_id, **record, "frames": 3,
                                  "model_comparisons": comparisons})
    (directory / "model.json").write_text(json.dumps(model_records, indent=2) + "\n")
    return path, model_records


def execute_config(e, simulator, c, directory, *, small=False, mutation=False, rtl=None):
    command = compile_sim(e, simulator, c, directory, rtl=rtl)
    records = list(scenario_records(c, small=small, mutation=mutation))
    path, models = write_vectors(c, directory, records)
    out, err, code = e.run(
        [*command, f"+vectors={path.name}"], cwd=directory,
        expected=(0, 1) if mutation else (0,),
    )
    if mutation:
        require(code != 0 and "MISMATCH" in out + err,
                "Mutation must execute and fail an unchanged numerical/protocol comparison")
        return {"configuration": c.parameters(), "mismatch": out + err}
    result = parse_stream_output(c, simulator, records, models, out)
    return {**result, "raw_output": e.record["commands"][-1]["stdout"],
            "raw_stderr": e.record["commands"][-1]["stderr"],
            "duration_s": e.record["commands"][-1]["duration_s"]}


def parse_stream_output(c, simulator, records, models, out):
    require(len(records) == len(models) > 0, "Scenario/model inventory differs")
    require(len({model["id"] for model in models}) == len(models),
            "Scenario model IDs are duplicated")
    matches = re.findall(
        r"^RESULT (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+)$",
        out, re.MULTILINE,
    )
    require(len(matches) == len(records), f"Incomplete scenarios for {c.label}/{simulator}")
    require(re.findall(r"^COMPLETE (\d+)$", out, re.MULTILINE) == [str(len(records))],
            "Simulator did not complete")
    parsed = []
    observed_rows = {}
    for ident, row, count in re.findall(r"^ROW (\d+) (\d+) (\d+)$", out, re.MULTILINE):
        observed_rows.setdefault(int(ident), []).append((int(row), int(count)))
    clocks = [(int(ident), int(count)) for ident, count in re.findall(
        r"^SCENARIO_CLOCKS (\d+) (\d+)$", out, re.MULTILINE)]
    require([ident for ident, _ in clocks] == [model["id"] for model in models],
            "Missing, duplicate or reordered complete scenario clock counters")
    total = re.findall(r"^TOTAL_CLOCKS (\d+)$", out, re.MULTILINE)
    require(len(total) == 1 and int(total[0]) == sum(count for _, count in clocks),
            "Scenario clock accounting does not cover the complete simulation")
    for record, model, values, (_, complete_cycles) in zip(records, models, matches, clocks):
        ident, ni, no, sof, eol, stalls, longest, bubbles, overlap, cycles = map(int, values)
        require(ident == model["id"], "Scenario duplication or reordering")
        require(complete_cycles > cycles, "Reset/quiet clocks missing from scenario accounting")
        require((ni, no, sof, eol) == (3*c.sw*c.sh, 3*c.dw*c.dh, 3, 3*c.dh),
                "Pixel or sideband counts incorrect")
        if record["abort"]:
            require(re.search(rf"^ABORT {ident} {record['abort']} \d+ \d+$",
                              out, re.MULTILINE) is not None, "Reset phase not observed")
            if record["abort"] == 3:
                require(re.search(rf"^ASYNC_PULSE {ident}$", out, re.MULTILINE) is not None,
                        "Sub-clock asynchronous reset pulse not observed")
        rows = observed_rows.get(ident, [])
        require(len(rows) == 3*c.dh, "Missing publicly observed output-row boundaries")
        reuse = skips = 0
        previous = None
        scale = 1 << c.frac
        for row_number, (row, source_count) in enumerate(rows):
            require(row == row_number, "Output-row observation duplicated or reordered")
            frame, y = divmod(row, c.dh)
            phase = y * (c.sh*scale//c.dh)
            y0 = min(c.sh-1, (phase+(scale//2 if c.mode else 0))//scale)
            y1 = y0 if c.mode else min(c.sh-1, phase//scale+1)
            require(source_count == (frame*c.sh+y1+1)*c.sw,
                    f"Unexpected source consumption at output row {row}: {source_count}")
            if previous is not None and y != 0:
                old_y0, old_y1, old_count = previous
                if (y0, y1) == (old_y0, old_y1):
                    require(source_count == old_count, "Repeated source rows unnecessarily consumed input")
                    reuse += 1
                skips += max(0, y0-old_y1-1)
            previous = y0, y1, source_count
        if record["stall"] == 2:
            require(longest >= 32, "Directed stall not observed")
        if record["stall"] == 1:
            require(stalls > 0 and bubbles > 0, "Random stalls/bubbles not observed")
        parsed.append({**model, "inputs": ni, "outputs": no, "sof": sof,
                       "eol": eol, "stalled_clocks": stalls, "longest_stall": longest,
                       "input_bubbles": bubbles, "simultaneous": overlap, "cycles": cycles,
                       "row_reuse": reuse, "skipped_source_rows": skips,
                       "complete_cycles": complete_cycles,
                       "observed_row_boundaries": [list(row) for row in rows]})
    return {"configuration": c.parameters(), "simulator": simulator,
            "cases": parsed, "complete_cycles": int(total[0])}


def execute_partition(e, simulator, c, directory):
    """Keep global scenario IDs when Icarus scenarios use separate processes."""
    if simulator != "icarus":
        return execute_config(e, simulator, c, directory)
    command = compile_sim(e, simulator, c, directory)
    cases, observations = [], []
    for ident, record in enumerate(scenario_records(c)):
        scenario = directory / f"scenario-{ident:02d}"
        scenario.mkdir()
        path, models = write_vectors(c, scenario, [record], case_ids=[ident])
        out, _, _ = e.run([*command, f"+vectors={path.name}"], cwd=scenario)
        child = e.record["commands"][-1]
        result = parse_stream_output(c, simulator, [record], models, out)
        observation = {
            "id": ident, "raw_output": child["stdout"], "raw_stderr": child["stderr"],
            "duration_s": child["duration_s"], "complete_cycles": result["complete_cycles"],
        }
        cases.extend(result["cases"])
        observations.append(observation)
        # Retain the completed scenario before diagnosing a tier violation.
        e.record["scenario_observations"] = observations
        e.save()
        require(child["duration_s"] < 60,
                f"Icarus tier violation at scenario {ident}: {child['duration_s']:.3f}s; "
                "retain this receipt and request scheduling diagnosis")
        require(result["complete_cycles"] <= 500000,
                f"Icarus tier violation at scenario {ident}: "
                f"{result['complete_cycles']} complete clocks; diagnosis required")
    return {"configuration": c.parameters(), "simulator": simulator,
            "cases": cases, "scenario_observations": observations}


def pipeline_checks(e, simulator, *, formats=None, fractions=(8, 16)):
    import random
    from reference import FORMATS

    results = []
    for bits, channels in FORMATS if formats is None else formats:
        for frac in fractions:
            c = Config(bits=bits, channels=channels, frac=frac)
            directory = e.path / f"pipeline-{simulator}-{bits}-{channels}-{frac}"
            params = dict(PIXEL_BITS=bits, CHANNELS=channels, PHASE_FRAC=frac)
            command = compile_sim(e, simulator, c, directory, PIPELINE_TB, parameters=params)
            rng = random.Random(2026)
            scale, maximum = 1 << frac, (1 << bits) - 1
            path = directory / "vectors.txt"
            with path.open("x") as stream:
                stream.write("256\n")
                for index in range(256):
                    values = [
                        [rng.randrange(maximum+1) for _ in range(channels)]
                        for _ in range(4)
                    ]
                    if index < 4:
                        values = [[maximum if index % 2 else 0] * channels for _ in range(4)]
                    fx = (0, scale//2, scale-1, 1)[index % 4]
                    fy = (scale//2, scale-1, 0, 1)[index % 4]
                    packed = [sum(v << (ch*bits) for ch, v in enumerate(row)) for row in values]
                    result = 0
                    for ch in range(channels):
                        a, b, cc, d = (row[ch] for row in values)
                        total = (a*(scale-fx)*(scale-fy) + b*fx*(scale-fy)
                                 + cc*(scale-fx)*fy + d*fx*fy)
                        result |= ((total+scale*scale//2)//(scale*scale)) << (ch*bits)
                    stream.write(" ".join(f"{v:x}" for v in [*packed, fx, fy, result])+"\n")
            out, _, _ = e.run([*command, f"+vectors={path.name}"], cwd=directory)
            match = re.search(r"^PIPELINE (\d+) (\d+) (\d+)$", out, re.MULTILINE)
            require(match is not None and int(match[1]) == 256 and int(match[2]) >= 32
                    and int(match[3]) > 0, "Six advancing stages not demonstrated")
            results.append({
                "simulator": simulator, "bits": bits, "channels": channels, "frac": frac,
                "compared": int(match[1]), "stalled": int(match[2]),
                "simultaneous": int(match[3]),
                "raw_output": e.record["commands"][-1]["stdout"],
            })
    return results


def parameter_cases():
    # Minimum and maximum of every independently configurable dimension.
    legal = [Config(1, 1, 1, 1), Config(4096, 4096, 4096, 4096)]
    for key in ("sw", "sh", "dw", "dh"):
        legal.extend(Config(**{key: value}) for value in (1, 4096))
    legal.extend(configurations(((1, 1, 1, 1),)))
    invalid = []
    for key in ("SRC_WIDTH", "SRC_HEIGHT", "DST_WIDTH", "DST_HEIGHT"):
        invalid.extend({key: value} for value in (0, -1, 4097))
    invalid.extend(
        {"PIXEL_BITS": bits, "CHANNELS": channels}
        for bits in (-1, 0, 7, 8, 9, 10, 11, 16, 24)
        for channels in (-1, 0, 1, 2, 3, 4)
        if (bits, channels) not in ((8, 1), (10, 1), (8, 3))
    )
    invalid.extend({"PHASE_FRAC": value} for value in (-1, 0, 7, 9, 15, 17))
    invalid.extend({"INTERPOLATION": value} for value in (-1, 2, 3))
    return [
        {"id": index, "legal": True, "parameters": c.parameters()}
        for index, c in enumerate(legal)
    ] + [
        {"id": len(legal)+index, "legal": False,
         "parameters": {**Config().parameters(), **overrides}}
        for index, overrides in enumerate(invalid)
    ]


def parameter_checks(e, simulator, *, shard=None):
    cases = parameter_cases()
    if shard is not None:
        require(0 <= shard < 7, "Parameter shard must be in [0,6]")
        cases = [case for case in cases if case["id"] % 7 == shard]
    directory = e.path / f"parameters-{simulator}"
    directory.mkdir()
    rtl = sorted((ROOT / "rtl").glob("*.sv"))
    results = []
    for case in cases:
        params = case["parameters"]
        start = len(e.record["commands"])
        if case["legal"]:
            if simulator == "icarus":
                e.run(["iverilog", "-g2012", "-Wall", "-t", "null", "-s", "swage_s1_top",
                       *[f"-Pswage_s1_top.{k}={v}" for k, v in params.items()], *rtl])
            else:
                e.run(["verilator", "--lint-only", "--timing", "-Wno-fatal",
                       "--top-module", "swage_s1_top",
                       *[f"-G{k}={v}" for k, v in params.items()], *rtl])
            results.append({**case, "command_indices": [start]})
            continue
        target = directory / f"invalid-{case['id']}"
        command = compile_sim(e, simulator, Config(),
                              target, INVALID_TB, parameters=params)
        out, err, code = e.run(command, expected=(1, -6, 134),
                              cwd=target)
        require(code != 0 and "SWAGE_S1 PARAMETER ERROR" in out + err,
                f"Invalid configuration missed explicit diagnostic: {params}")
        results.append({**case, "command_indices": list(range(start, len(e.record["commands"])))})
    return {"simulator": simulator, "cases": results,
            "legal_elaborations": sum(case["legal"] for case in cases),
            "invalid_diagnostics": sum(not case["legal"] for case in cases)}


def behavior(e, *, small=False):
    if not small:
        from partitions import aggregate
        return aggregate(e, "behavior")
    for name, version in (("iverilog", ("-V",)), ("vvp", ("-V",)),
                          ("verilator", ("--version",)), ("make", ("--version",)),
                          ("g++", ("--version",))):
        e.tool(name, version)
    ledger = []
    targets = [Config()]
    for simulator in ("icarus", "verilator"):
        for c in targets:
            result = execute_config(e, simulator, c,
                                    e.path / f"{simulator}-{c.label}", small=small)
            ledger.append(result)
            (e.path / "behavior-ledger.json").write_text(json.dumps(ledger, indent=2)+"\n")
    e.assertion("completed configuration/simulator pairs", len(ledger), 2*len(targets))
    return ledger


def coverage_totals(e, ledger):
    totals = {key: 0 for key in (
        "ties", "carry", "right_clamp", "bottom_clamp", "compared_channels",
        "reset_fill", "reset_compute", "reset_stall", "reset_tail",
        "reset_pipe_drain", "reset_row_boundary", "reset_frame_boundary",
        "directed_stall", "random_stall", "three_frames",
        "row_reuse", "skipped_source_rows", "maximum_arithmetic", "rgb_distinct",
    )}
    for run in ledger:
        for case in run["cases"]:
            totals["three_frames"] += int(case["sof"] == 3)
            totals["directed_stall"] += int(case["longest_stall"] >= 32)
            totals["random_stall"] += int(case["stall"] == 1 and case["stalled_clocks"] > 0)
            totals["row_reuse"] += case["row_reuse"]
            totals["skipped_source_rows"] += case["skipped_source_rows"]
            totals["maximum_arithmetic"] += (
                case["outputs"] if case["pattern"] == "full" else 0
            )
            if run["configuration"]["CHANNELS"] == 3 and case["pattern"] == "horizontal":
                totals["rgb_distinct"] += case["outputs"]
            if case["abort"]:
                totals[("reset_fill", "reset_compute", "reset_stall", "reset_tail",
                        "reset_pipe_drain", "reset_row_boundary", "reset_frame_boundary")
                       [case["abort"]-1]] += 1
            for record in case["model_comparisons"]:
                for key in ("ties", "carry", "right_clamp", "bottom_clamp", "compared_channels"):
                    totals[key] += record[key]
    for key, count in totals.items():
        require(count > 0, f"Required coverage category unexercised: {key}")
    e.record["coverage"] = totals
    e.save()
    return totals
