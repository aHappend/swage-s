"""Public-pin simulator bridge; generated HDL and fixtures stay in run directories."""

from pathlib import Path


def pack(values, width=16):
    return sum((value & ((1 << width) - 1)) << (width * i)
               for i, value in enumerate(values))


def beat_word(beat):
    return beat.data | beat.mask << 24 | int(beat.sof) << 27 | int(beat.eol) << 28


def write_cases(directory, cases):
    directory.mkdir(parents=True, exist_ok=False)
    records = []
    for index, case in enumerate(cases):
        config, image, expected, histogram = (
            case["config"], case["image"], case["expected"], case["histogram"])
        fields = [config.enable, int(config.border), int(config.absolute), config.shift,
                  pack(config.coeff), *config.roi, config.input_format, config.csc_mode,
                  int(config.output_420), pack(config.csc_coeff), pack(config.in_bias),
                  pack(config.out_bias), config.hist_channel, len(expected),
                  case.get("seed", 17), case.get("traffic", 2), case.get("flags", 0)]
        records.append(" ".join(f"{value:x}" for value in fields))
        source = []
        raw = len(image[0][0]) == 1 or bool(config.enable & 2)
        for y, row in enumerate(image):
            for x, pixel in enumerate(row):
                mask = 1 if raw or (config.input_format == 2 and (x & 1 or y & 1)) else 7
                source.append(pack(pixel, 10 if len(pixel) == 1 else 8) |
                              mask << 24 | int(x == y == 0) << 27 |
                              int(x == len(row) - 1) << 28)
        for kind, values, digits in (
                ("input", source, 8),
                ("expected", [beat_word(b) for b in expected], 8),
                ("histogram", histogram, 64)):
            (directory / f"case-{index}-{kind}.hex").write_text(
                "".join(f"{value:0{digits}x}\n" for value in values))
    (directory / "cases.txt").write_text("\n".join(records) + "\n")


def testbench(parameters):
    sw, sh, dw, dh, bits, channels, frac, interpolation = parameters
    data_width = 16 if bits == 10 else 8 * channels
    return f"""`timescale 1ns/1ps
module tb;
  localparam SW={sw},SH={sh},DW={dw},DH={dh},DATA_WIDTH={data_width};
  localparam MAX_OUT=(SW*SH>DW*DH ? SW*SH : DW*DH);
  reg clk=0;
  reg rst_n;
  reg [DATA_WIDTH-1:0] s_axis_tdata=0;
  reg [2:0] s_axis_cvalid=0;
  reg s_axis_tvalid=0,s_axis_tuser=0,s_axis_tlast=0,m_axis_tready=0;
  wire s_axis_tready;
  wire [DATA_WIDTH-1:0] m_axis_tdata;
  wire [2:0] m_axis_cvalid;
  wire m_axis_tvalid,m_axis_tuser,m_axis_tlast;
  reg cfg_valid=0;
  wire cfg_ready,cfg_error;
  reg [5:0] cfg_enable=0;
  reg cfg_border=0,cfg_abs=0,cfg_output_420=0;
  reg [4:0] cfg_shift=0;
  reg [143:0] cfg_coeff=0,cfg_csc_coeff=0;
  reg [47:0] cfg_csc_in_bias=0,cfg_csc_out_bias=0;
  reg [12:0] cfg_roi_x=0,cfg_roi_y=0,cfg_roi_width=0,cfg_roi_height=0;
  reg [1:0] cfg_input_format=0,cfg_hist_channel=0;
  reg [2:0] cfg_csc_mode=0;
  reg hist_req_valid=0,hist_rsp_ready=0;
  reg [4:0] hist_req_addr=0;
  wire hist_req_ready,hist_rsp_valid,hist_done;
  wire [255:0] hist_rsp_data;
  swage_s3_top #(.SRC_WIDTH(SW),.SRC_HEIGHT(SH),.DST_WIDTH(DW),.DST_HEIGHT(DH),
    .PIXEL_BITS({bits}),.CHANNELS({channels}),.PHASE_FRAC({frac}),
    .INTERPOLATION({interpolation})) dut(.*);
  reg [31:0] src[0:SW*SH-1], expected[0:MAX_OUT-1];
  reg [255:0] histogram[0:31];
  string base,filename;
  integer fd,n,index=0,nout,seed,traffic,flags,cycle=0,frame_cycle,sent,got;
  integer first_hold,eol_hold,final_hold,hold_remaining,stat_cycle,a,j;
  integer compared=0,statistics=0,configs=0,busy_checks=0,stall_checks=0;
  integer first_stalls=0,eol_stalls=0,final_stalls=0,response_stalls=0;
  integer source_bubbles=0,frame_bound;
  integer reset_fill=0,reset_stall=0,reset_response=0,snapshot_replaced=0;
  reg source_fire,output_fire,observed_cfg_ready,observed_req_ready;
  reg observed_rsp_valid,observed_done,stalled=0;
  reg [31:0] output_word,stall_word,rng;
  reg [255:0] response_word;
  reg [1:0] saved_format;
  reg [456:0] saved_configuration;

  task restore_configuration;
    begin
      {{cfg_enable,cfg_border,cfg_abs,cfg_shift,cfg_coeff,
        cfg_roi_x,cfg_roi_y,cfg_roi_width,cfg_roi_height,cfg_input_format,cfg_csc_mode,
        cfg_output_420,cfg_csc_coeff,cfg_csc_in_bias,cfg_csc_out_bias,cfg_hist_channel}}
        = saved_configuration;
    end
  endtask

  task reject_current;
    begin
      cfg_valid=1;tick();
      if (!observed_cfg_ready || source_fire || cfg_error !== 1)
        $fatal(1,"CONFIG_REJECT_CATEGORY case=%0d",index);
      restore_configuration();cfg_valid=0;
    end
  endtask

  task reset_check;
    begin
      if ({{m_axis_tdata,m_axis_cvalid,m_axis_tvalid,m_axis_tuser,m_axis_tlast,
             s_axis_tready,cfg_ready,cfg_error,hist_req_ready,hist_rsp_valid,
             hist_rsp_data,hist_done}} !== 0)
        $fatal(1,"RESET_OUTPUT case=%0d cycle=%0d",index,cycle);
    end
  endtask

  task tick;
    begin
      #5;
      if (!rst_n) reset_check();
      if ((^{{s_axis_tready,m_axis_tvalid,cfg_ready,cfg_error,hist_req_ready,
               hist_rsp_valid,hist_done}}) === 1'bx)
        $fatal(1,"ACTIVE_XZ case=%0d cycle=%0d",index,cycle);
      output_word={{3'd0,m_axis_tlast,m_axis_tuser,m_axis_cvalid,24'(m_axis_tdata)}};
      if (m_axis_tvalid && (^output_word) === 1'bx)
        $fatal(1,"PIXEL_XZ case=%0d",index);
      if (hist_rsp_valid && (^hist_rsp_data) === 1'bx)
        $fatal(1,"STATISTICS_XZ case=%0d",index);
      if (rst_n && stalled && (m_axis_tvalid !== 1'b1 || output_word !== stall_word))
        $fatal(1,"STALL_STABILITY case=%0d cycle=%0d",index,cycle);
      stalled=rst_n && m_axis_tvalid && !m_axis_tready;
      stall_word=output_word;
      if (stalled) stall_checks=stall_checks+1;
      source_fire=s_axis_tvalid && s_axis_tready;
      output_fire=m_axis_tvalid && m_axis_tready;
      observed_cfg_ready=cfg_ready;
      observed_req_ready=hist_req_ready;
      observed_rsp_valid=hist_rsp_valid;
      observed_done=hist_done;
      response_word=hist_rsp_data;
      clk=1;
      if (!rst_n) reset_check();
      #1;
      cycle=cycle+1;
      #4 clk=0;
      if (!rst_n) reset_check();
    end
  endtask

  task advance_random;
    begin
      rng=rng ^ (rng<<13); rng=rng ^ (rng>>17); rng=rng ^ (rng<<5);
    end
  endtask

  task configure;
    begin
      while (!cfg_ready) tick();
      cfg_valid=1; s_axis_tvalid=1;
      s_axis_tdata=DATA_WIDTH'(src[0][23:0]);s_axis_cvalid=src[0][26:24];
      s_axis_tuser=src[0][27];s_axis_tlast=src[0][28];
      tick();
      if (!observed_cfg_ready || source_fire || cfg_error !== 0)
        $fatal(1,"CONFIG_ACCEPT_PRIORITY case=%0d",index);
      cfg_valid=0;
      configs=configs+1;
      saved_configuration={{cfg_enable,cfg_border,cfg_abs,cfg_shift,cfg_coeff,
        cfg_roi_x,cfg_roi_y,cfg_roi_width,cfg_roi_height,cfg_input_format,cfg_csc_mode,
        cfg_output_420,cfg_csc_coeff,cfg_csc_in_bias,cfg_csc_out_bias,cfg_hist_channel}};
      {{cfg_enable,cfg_border,cfg_abs,cfg_shift,cfg_coeff,
        cfg_roi_x,cfg_roi_y,cfg_roi_width,cfg_roi_height,cfg_input_format,cfg_csc_mode,
        cfg_output_420,cfg_csc_coeff,cfg_csc_in_bias,cfg_csc_out_bias,cfg_hist_channel}}
        = ~saved_configuration;
      cfg_input_format=3;cfg_valid=1;s_axis_tvalid=1;
      tick();
      if (!observed_cfg_ready || source_fire || cfg_error !== 1)
        $fatal(1,"CONFIG_REJECT_PRIORITY case=%0d",index);
      cfg_valid=0;
      restore_configuration();
      cfg_enable=1;cfg_roi_width=0;reject_current();
      cfg_enable=1;cfg_roi_height=0;reject_current();
      cfg_enable=1;cfg_roi_x=8191;cfg_roi_width=8191;reject_current();
      cfg_enable=1;cfg_roi_y=8191;cfg_roi_height=8191;reject_current();
      cfg_enable=2;cfg_input_format=1;reject_current();
      cfg_enable=4;cfg_input_format=2;reject_current();
      cfg_enable=8;cfg_input_format=0;cfg_csc_mode=5;reject_current();
      cfg_enable=8;cfg_input_format=0;cfg_csc_mode=6;reject_current();
      cfg_enable=8;cfg_input_format=0;cfg_csc_mode=7;reject_current();
      cfg_enable=8;cfg_input_format=1;cfg_csc_mode=0;reject_current();
      cfg_enable=8;cfg_input_format=0;cfg_csc_mode=2;reject_current();
      cfg_enable=1;cfg_input_format=2;cfg_roi_x=1;cfg_roi_y=0;
      cfg_roi_width=1;cfg_roi_height=1;reject_current();
      cfg_enable=1;cfg_input_format=2;cfg_roi_x=0;cfg_roi_y=1;
      cfg_roi_width=1;cfg_roi_height=1;reject_current();
      cfg_enable=32;cfg_input_format=1;cfg_hist_channel=3;reject_current();
      if ({channels}!=3) begin
        cfg_enable=2;cfg_input_format=0;reject_current();
        cfg_enable=8;cfg_input_format=0;cfg_csc_mode=0;reject_current();
        cfg_enable=32;cfg_hist_channel=1;reject_current();
        cfg_enable=0;cfg_input_format=1;reject_current();
      end
      if ({bits}==10) begin cfg_enable=4;cfg_input_format=0;reject_current();end
    end
  endtask

  task reset_dut;
    begin
      rst_n=0;
      #1;reset_check();
      tick();
      s_axis_tvalid=0;cfg_valid=0;hist_req_valid=0;hist_rsp_ready=0;
      m_axis_tready=0;stalled=0;
      rst_n=1;tick();
      if (hist_done || hist_rsp_valid || hist_req_ready)
        $fatal(1,"RESET_SNAPSHOT_INVALIDATION case=%0d",index);
    end
  endtask

  task abort_probe;
    begin
      sent=0;frame_cycle=0;m_axis_tready=0;
      while ((flags[1] && sent==0) || (flags[2] && !m_axis_tvalid)) begin
        s_axis_tvalid=sent<SW*SH;
        if (sent<SW*SH) begin
          s_axis_tdata=DATA_WIDTH'(src[sent][23:0]);s_axis_cvalid=src[sent][26:24];
          s_axis_tuser=src[sent][27];s_axis_tlast=src[sent][28];
        end
        tick();
        if (source_fire) sent=sent+1;
        frame_cycle=frame_cycle+1;
        if (frame_cycle>2048+128*(SW*SH+nout+SW+SH+DW+DH))
          $fatal(1,"RESET_PROBE_BOUND case=%0d",index);
      end
      if (flags[1]) begin
        if (sent==0 || m_axis_tready) $fatal(1,"RESET_FILL_PRECONDITION");
        reset_fill=reset_fill+1;
      end
      if (flags[2]) begin
        if (!m_axis_tvalid) $fatal(1,"RESET_STALL_PRECONDITION");
        for (j=0;j<32;j=j+1) begin
          if (!s_axis_tvalid || source_fire) begin
            s_axis_tvalid=sent<SW*SH;
            if (sent<SW*SH) begin
              s_axis_tdata=DATA_WIDTH'(src[sent][23:0]);s_axis_cvalid=src[sent][26:24];
              s_axis_tuser=src[sent][27];s_axis_tlast=src[sent][28];
            end
          end
          tick();
          if (source_fire) sent=sent+1;
        end
        reset_stall=reset_stall+1;
      end
      reset_dut();
      configure();
    end
  endtask

  task frame;
    begin : frame_body
      sent=0;got=0;frame_cycle=0;rng=32'(seed);
      first_hold=0;eol_hold=0;final_hold=0;hold_remaining=0;
      // Finite work: clears, two 32-edge divisions, line scans, serialized bins,
      // plus at most 32 injected stall cycles per boundary and bounded bubbles.
      frame_bound=2048+128*(SW*SH+nout+SW+SH+DW+DH);
      while (sent<SW*SH || got<nout || !cfg_ready) begin
        advance_random();
        if (!s_axis_tvalid || source_fire) begin
          s_axis_tvalid=sent<SW*SH && (traffic==0 || rng[2:0]!=0);
          if (sent<SW*SH && !s_axis_tvalid) source_bubbles=source_bubbles+1;
          if (sent<SW*SH) begin
            s_axis_tdata=DATA_WIDTH'(src[sent][23:0]);
            s_axis_cvalid=src[sent][26:24];
            s_axis_tuser=src[sent][27];s_axis_tlast=src[sent][28];
          end
        end
        m_axis_tready=traffic==0 || (traffic==1 ? frame_cycle[0] : rng[4:3]!=0);
        if (traffic!=0 && m_axis_tvalid && hold_remaining==0) begin
          if (!first_hold && got==0) begin
            first_hold=1;hold_remaining=32;first_stalls=first_stalls+1;
          end else if (!eol_hold && m_axis_tlast) begin
            eol_hold=1;hold_remaining=32;eol_stalls=eol_stalls+1;
          end else if (!final_hold && got==nout-1) begin
            final_hold=1;hold_remaining=32;final_stalls=final_stalls+1;
          end
        end
        if (hold_remaining>0) begin
          m_axis_tready=0;hold_remaining=hold_remaining-1;
        end
        // A busy configuration cannot change the active frame or pulse error.
        cfg_valid=frame_cycle==4;
        tick();
        if (frame_cycle==0 && cfg_error !== 0)
          $fatal(1,"CONFIG_ERROR_PULSE case=%0d",index);
        if (cfg_valid) begin
          if (observed_cfg_ready || cfg_error !== 0)
            $fatal(1,"BUSY_CONFIG case=%0d",index);
          busy_checks=busy_checks+1;
        end
        if (output_fire) begin
          if (got>=nout) $fatal(1,"EXTRA_OUTPUT case=%0d",index);
          if (output_word !== expected[got])
            $fatal(1,"PIXEL_MISMATCH case=%0d pixel=%0d got=%h expected=%h enable=%h",
                   index,got,output_word,expected[got],cfg_enable);
          got=got+1;compared=compared+1;
        end
        if (source_fire) sent=sent+1;
        if (observed_done && sent>0 && (sent<SW*SH || got<nout))
          $fatal(1,"EARLY_HISTOGRAM_DONE case=%0d",index);
        if (!cfg_enable[5] && hist_done !== 0)
          $fatal(1,"BYPASSED_HISTOGRAM case=%0d",index);
        frame_cycle=frame_cycle+1;
        if (frame_cycle>frame_bound)
          $fatal(1,"FINITE_WORK_BOUND case=%0d sent=%0d got=%0d expected=%0d",
                 index,sent,got,nout);
      end
      cfg_valid=0;s_axis_tvalid=0;m_axis_tready=1;
      tick();
      if (output_fire) $fatal(1,"EXTRA_OUTPUT_AFTER_FRAME case=%0d",index);
      if (cfg_enable[5] && !flags[0]) begin
        if (!hist_done) $fatal(1,"MISSING_HISTOGRAM_DONE case=%0d",index);
        for (a=0;a<32;a=a+1) begin
          if (!hist_req_ready) $fatal(1,"STATISTICS_NOT_READY case=%0d",index);
          hist_req_addr=5'(a);hist_req_valid=1;hist_rsp_ready=0;
          tick();
          if (!observed_req_ready) $fatal(1,"STATISTICS_REQUEST case=%0d",index);
          hist_req_valid=0;
          for (j=1;j<=9;j=j+1) begin
            if (hist_rsp_valid) $fatal(1,"STATISTICS_EARLY_LATENCY case=%0d edge=%0d",index,j);
            tick();
          end
          if (!hist_rsp_valid) $fatal(1,"STATISTICS_LATE_LATENCY case=%0d",index);
          if (flags[3]) begin
            if (hist_rsp_data !== histogram[a]) $fatal(1,"RESET_RESPONSE_VALUE");
            reset_response=reset_response+1;
            reset_dut();
            disable frame_body;
          end
          for (stat_cycle=0;stat_cycle<32;stat_cycle=stat_cycle+1) begin
            cfg_valid=1;
            tick();
            if (!observed_rsp_valid || response_word !== histogram[a])
              $fatal(1,"HISTOGRAM_MISMATCH case=%0d address=%0d got=%h expected=%h",
                     index,a,response_word,histogram[a]);
            if (observed_cfg_ready || s_axis_tready || observed_req_ready || !hist_done)
              $fatal(1,"SNAPSHOT_RESERVATION case=%0d",index);
            response_stalls=response_stalls+1;
          end
          cfg_valid=0;hist_rsp_ready=1;
          tick();
          if (!observed_rsp_valid || response_word !== histogram[a])
            $fatal(1,"HISTOGRAM_MISMATCH case=%0d address=%0d",index,a);
          statistics=statistics+1;
          hist_rsp_ready=0;
          if (hist_rsp_valid) $fatal(1,"STATISTICS_RETIRE case=%0d",index);
        end
      end else if (cfg_enable[5]) begin
        if (!hist_done) $fatal(1,"SNAPSHOT_REPLACEMENT_PRECONDITION");
        snapshot_replaced=snapshot_replaced+1;
      end
    end
  endtask

  initial begin
    rst_n=0;
    #1;reset_check();
    #4;clk=1;reset_check();
    #1;reset_check();
    #4;clk=0;reset_check();
    rst_n=1;#1;rst_n=0;#1;reset_check();
    tick();rst_n=1;tick();
    if (!$value$plusargs("fixtures=%s",base)) $fatal(1,"FIXTURE_ARGUMENT");
    filename={{base,"/cases.txt"}};fd=$fopen(filename,"r");
    if (fd==0) $fatal(1,"FIXTURE_OPEN");
    while (!$feof(fd)) begin
      n=$fscanf(fd,"%h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h\\n",
        cfg_enable,cfg_border,cfg_abs,cfg_shift,cfg_coeff,
        cfg_roi_x,cfg_roi_y,cfg_roi_width,cfg_roi_height,cfg_input_format,cfg_csc_mode,
        cfg_output_420,cfg_csc_coeff,cfg_csc_in_bias,cfg_csc_out_bias,cfg_hist_channel,
        nout,seed,traffic,flags);
      if (n!=20) begin
        if (!(n==0 && $feof(fd))) $fatal(1,"MALFORMED_FIXTURE fields=%0d",n);
      end else begin
        if (nout<1 || nout>MAX_OUT) $fatal(1,"FIXTURE_OUTPUT_SIZE");
        filename=$sformatf("%s/case-%0d-input.hex",base,index);
        $readmemh(filename,src,0,SW*SH-1);
        filename=$sformatf("%s/case-%0d-expected.hex",base,index);
        $readmemh(filename,expected,0,nout-1);
        filename=$sformatf("%s/case-%0d-histogram.hex",base,index);
        $readmemh(filename,histogram,0,31);
        configure();
        if (flags[1] || flags[2]) abort_probe();
        frame();
        index=index+1;
      end
    end
    $fclose(fd);
    $display("SUMMARY cases=%0d pixels=%0d statistics=%0d configs=%0d busy=%0d stalls=%0d first=%0d eol=%0d final=%0d responses=%0d bubbles=%0d",
      index,compared,statistics,configs,busy_checks,stall_checks,first_stalls,
      eol_stalls,final_stalls,response_stalls,source_bubbles);
    $display("RESET_SUMMARY fill=%0d stall=%0d response=%0d unread=%0d",
      reset_fill,reset_stall,reset_response,snapshot_replaced);
    $finish;
  end
endmodule
"""
