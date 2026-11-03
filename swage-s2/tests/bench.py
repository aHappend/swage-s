"""Generated public-pin transactors; no hierarchical DUT observations."""

PARAMETERS = ("SRC_WIDTH", "SRC_HEIGHT", "DST_WIDTH", "DST_HEIGHT",
              "PIXEL_BITS", "CHANNELS", "PHASE_FRAC", "INTERPOLATION")


def architectural(geometry):
    sw, sh, dw, dh, bits, channels, _, _ = geometry
    width = 16 if bits == 10 else channels * 8
    params = ", ".join(f".{n}({v})" for n, v in zip(PARAMETERS, geometry))
    template = r"""
module tb;
  localparam SW=@SW@, SH=@SH@, DW=@DW@, DH=@DH@, B=@B@;
  localparam N=SW*SH, M=DW*DH, LIMIT=200*(N+M+SH+DH)+20000;
  reg clk=0;
  always #5 clk=~clk;
  reg rst_n;
  reg [B-1:0] sd=0;
  reg sv=0, ss=0, sl=0, mr=0, cv=0;
  reg [1:0] cm=0;
  reg cb=0, ca=0;
  reg [4:0] cs=0;
  reg [143:0] cc=0;
  wire sr,mv,ms,ml,cr,ce;
  wire [B-1:0] md;
  swage_s2_top #(@PARAMS@) dut (
    .clk(clk),.rst_n(rst_n),.s_axis_tdata(sd),.s_axis_tvalid(sv),
    .s_axis_tuser(ss),.s_axis_tlast(sl),.s_axis_tready(sr),
    .m_axis_tdata(md),.m_axis_tvalid(mv),.m_axis_tuser(ms),.m_axis_tlast(ml),
    .m_axis_tready(mr),.cfg_valid(cv),.cfg_ready(cr),.cfg_error(ce),
    .cfg_mode(cm),.cfg_border(cb),.cfg_abs(ca),.cfg_shift(cs),.cfg_coeff(cc));
  reg pr=0,pv=0,ps=0,pl=0;
  reg [B-1:0] pd=0;
  wire pready,pvalid,psof,peol;
  wire [B-1:0] pout;
  parent_swage_s1_top #(@PARAMS@) parent (
    .clk(clk),.rst_n(pr),.s_axis_tdata(pd),.s_axis_tvalid(pv),
    .s_axis_tuser(ps),.s_axis_tlast(pl),.s_axis_tready(pready),
    .m_axis_tdata(pout),.m_axis_tvalid(pvalid),.m_axis_tuser(psof),
    .m_axis_tlast(peol),.m_axis_tready(mr));
  reg [B-1:0] pixels[0:N-1], expected[0:M-1];
  integer input_file,result_file,rc,frames,f,seed,mode,bd,ab,shift,use_defaults;
  integer si,oi,pi,po,cycles,bubbles,stalls,runstall,maxstall,sofstall,eolstall;
  integer cfgchanges,busycycles,illegal,fillreset,outputreset,stallreset;
  integer sofhold,eolhold,busy_held,first,got_error,abort_kind,abort_done;
  integer total_in,total_out,total_parent,random_value,j;
  reg [31:0] random_state;
  reg blocked,pblocked,sblocked,psblocked;
  reg [B+1:0] held,pheld,sheld,psheld;
  reg accepted_in,accepted_out,accepted_pi,accepted_po;
  reg in_frame=0;
  reg [B+1:0] observed,observed_parent;
  string stimulus_path,result_path;
  function automatic [31:0] rng(input [31:0] value);
    reg [31:0] x;
    begin x=value^(value<<13); x=x^(x>>17); rng=x^(x<<5); end
  endfunction
  task automatic reset_check;
    begin
      if ({sr,cr,ce,mv,ms,ml,md} !== 0)
        $fatal(1,"RESET_ZERO t=%0t pins=%h",$time,{sr,cr,ce,mv,ms,ml,md});
    end
  endtask
  always @(posedge clk or negedge clk) if (!rst_n) reset_check();
  task automatic sample_edge;
    begin
      @(posedge clk);
      if ((^{sr,cr,ce,mv,ms,ml}) === 1'bx)
        $fatal(1,"UNKNOWN_CONTROL pins=%b",{sr,cr,ce,mv,ms,ml});
      if (mv && $isunknown(md)) $fatal(1,"UNKNOWN_DATA");
      if (!mv && (ms||ml)) $fatal(1,"INVALID_OUTPUT_MARKER");
      if (pr && (^{pready,pvalid,psof,peol}) === 1'bx)
        $fatal(1,"PARENT_UNKNOWN_CONTROL");
      if (pvalid && (^pout) === 1'bx) $fatal(1,"PARENT_UNKNOWN_DATA");
      if (sblocked && {sv,sd,ss,sl} !== {1'b1,sheld}) $fatal(1,"SOURCE_STABILITY");
      if (psblocked && {pv,pd,ps,pl} !== {1'b1,psheld})
        $fatal(1,"PARENT_SOURCE_STABILITY");
      if (blocked && {mv,md,ms,ml} !== {1'b1,held}) $fatal(1,"STALL_STABILITY");
      if (pblocked && {pvalid,pout,psof,peol} !== {1'b1,pheld})
        $fatal(1,"PARENT_STALL_STABILITY");
      accepted_in=sv&&sr; accepted_out=mv&&mr;
      accepted_pi=pv&&pready; accepted_po=pvalid&&mr;
      observed={md,ms,ml}; observed_parent={pout,psof,peol};
      blocked=mv&&!mr; held={md,ms,ml};
      pblocked=pvalid&&!mr; pheld={pout,psof,peol};
      sblocked=sv&&!sr; sheld={sd,ss,sl};
      psblocked=pv&&!pready; psheld={pd,ps,pl};
      if (mv&&!mr) begin
        stalls=stalls+1; runstall=runstall+1;
        if (runstall>maxstall) maxstall=runstall;
        if (ms) sofstall=sofstall+1;
        if (ml) eolstall=eolstall+1;
      end else runstall=0;
      if (in_frame&&si<N&&sr&&!sv) bubbles=bubbles+1;
      #1;
    end
  endtask
  task automatic clear_reset;
    begin
      @(negedge clk); #1; rst_n=0; pr=0; cv=0; sv=0; pv=0; mr=0;
      blocked=0; pblocked=0;sblocked=0;psblocked=0;in_frame=0;
      #1; reset_check();
      // Assert and release entirely between edges to distinguish asynchronous reset.
      #1; rst_n=1;
      #1;
      if(mv || ms || ml || md!==0 || ce || !cr) $fatal(1,"ASYNC_RESET_RELEASE");
      rst_n=0;
      repeat(3) sample_edge();
      @(negedge clk); rst_n=1;
      sample_edge();
      if (mv || ce || !cr) $fatal(1,"RESET_RELEASE");
    end
  endtask
  task automatic configure(input integer requested, input integer expect_error);
    begin
      @(negedge clk); cv=1; cm=2'(requested);
      sv=1; sd=pixels[0]; ss=1; sl=(SW==1);
      #1;
      if (!cr || sr) $fatal(1,"CONFIG_PRIORITY");
      sample_edge();
      if (ce !== 1'(expect_error)) $fatal(1,"CONFIG_ERROR_PULSE got=%b",ce);
      if (expect_error) illegal=illegal+1;
      else cfgchanges=cfgchanges+1;
      // Retain the pending first pixel; following configurations still have priority.
    end
  endtask
  task automatic run_frame(input integer abort_at);
    begin
      si=0;oi=0;pi=0;po=0;cycles=0;sofhold=0;eolhold=0;busy_held=0;
      in_frame=1;
      blocked=0;pblocked=0;abort_done=0;
      @(negedge clk);
      pr=(mode==0);pv=0;mr=0;cv=0;accepted_in=0;accepted_pi=0;
      while ((si<N || oi<M || (mode==0&&(pi<N||po<M)) || !cr) && !abort_done) begin
        random_state=rng(random_state);
        random_value=integer'(random_state & 32'h7fffffff);
        if (!sv || accepted_in) begin
          sv=(si<N && (random_value%4!=0));
          if (sv) begin sd=pixels[si];ss=(si==0);sl=(si%SW==SW-1);end
        end
        if (!pv || accepted_pi) begin
          pv=(mode==0 && pi<N && (random_value%5!=0));
          if(pv) begin pd=pixels[pi];ps=(pi==0);pl=(pi%SW==SW-1);end
        end
        // Hold the first SOF and first EOL for twenty actual valid cycles each.
        mr=(random_value%5!=0);
        if (mv && ms && sofhold<20) begin mr=0;sofhold=sofhold+1;end
        else if (mv && ml && eolhold<20) begin mr=0;eolhold=eolhold+1;end
        if (abort_at==3 && mv) mr=0;
        if (si>0 && !cr && !busy_held) begin
          cv=1; cm=2'((mode+1)%4);
          busy_held=1;
        end
        // A held request is withdrawn before idle, never allowed to change this frame.
        if (si==N && oi==M) cv=0;
        #1;
        if (cv && busy_held) begin
          if(cr) $fatal(1,"BUSY_CONFIG_READY");
          busycycles=busycycles+1;
        end
        sample_edge();
        if(ce) $fatal(1,"BUSY_CONFIG_ERROR");
        if(accepted_in) begin si=si+1;total_in=total_in+1;end
        if(accepted_pi) pi=pi+1;
        if(accepted_out) begin
          if(oi>=M) $fatal(1,"EXTRA_OUTPUT");
          if(observed !== {expected[oi],1'(oi==0),1'(oi%DW==DW-1)})
            $fatal(1,"PIXEL_MISMATCH frame=%0d index=%0d got=%h expected=%h",f,oi,
                   observed,{expected[oi],1'(oi==0),1'(oi%DW==DW-1)});
          if(abort_at==0) $fdisplay(result_file,"O %0d %0d %h",f,oi,observed);
          oi=oi+1;total_out=total_out+1;
        end
        if(accepted_po) begin
          if(po>=M || observed_parent !== {expected[po],1'(po==0),1'(po%DW==DW-1)})
            $fatal(1,"PARENT_MISMATCH frame=%0d index=%0d",f,po);
          if(abort_at==0) $fdisplay(result_file,"P %0d %0d %h",f,po,observed_parent);
          po=po+1;total_parent=total_parent+1;
        end
        if(cr && (si!=0) && (si<N||oi<M)) $fatal(1,"EARLY_FRAME_RELEASE");
        if(si==N && !cr && sr) $fatal(1,"NEXT_FRAME_ADMISSION");
        if(abort_at==1 && si>0 && oi==0) begin fillreset=fillreset+1;abort_done=1;end
        if(abort_at==2 && accepted_out) begin outputreset=outputreset+1;abort_done=1;end
        if(abort_at==3 && blocked) begin stallreset=stallreset+1;abort_done=1;end
        cycles=cycles+1;
        if(cycles>LIMIT) $fatal(1,"PROGRESS frame=%0d si=%0d oi=%0d",f,si,oi);
        @(negedge clk);
      end
      in_frame=0;
      sv=0;pv=0;cv=0;mr=1;
      if(!abort_done) begin
        repeat(24) begin
          sample_edge();
          if(accepted_out || accepted_po || mv || pvalid) $fatal(1,"FINAL_DRAIN");
          @(negedge clk);
        end
        if(!cr || si!=N || oi!=M) $fatal(1,"FRAME_LENGTH");
        $fdisplay(result_file,"F %0d %0d %0d %0d %0d",f,si,oi,po,cycles);
        $fflush(result_file);
      end else begin
        $fdisplay(result_file,"A %0d %0d %0d %0d %0d",f,abort_at,si,oi,cycles);
        $fflush(result_file);
      end
    end
  endtask
  initial begin
    rst_n=0;
    bubbles=0;stalls=0;runstall=0;maxstall=0;sofstall=0;eolstall=0;
    cfgchanges=0;busycycles=0;illegal=0;fillreset=0;outputreset=0;stallreset=0;
    total_in=0;total_out=0;total_parent=0;
    accepted_in=0;accepted_pi=0;blocked=0;pblocked=0;sblocked=0;psblocked=0;
`ifndef VERILATOR
    #0;
`else
    #1;
`endif
    reset_check();
    if(!$value$plusargs("stimulus=%s",stimulus_path) ||
       !$value$plusargs("result=%s",result_path)) $fatal(1,"ARGS");
    input_file=$fopen(stimulus_path,"r");result_file=$fopen(result_path,"w");
    if(!input_file || !result_file) $fatal(1,"FILES");
    rc=$fscanf(input_file,"%d %d\n",frames,seed);
    if(rc!=2||frames<1) $fatal(1,"HEADER");
    random_state=32'(seed);
    clear_reset();
    for(f=0;f<frames;f=f+1) begin
      rc=$fscanf(input_file,"%d %d %d %d %h %d\n",mode,bd,ab,shift,cc,use_defaults);
      if(rc!=6) $fatal(1,"FRAME_HEADER");
      for(j=0;j<N;j=j+1) begin
        rc=$fscanf(input_file,"%h\n",pixels[j]);if(rc!=1)$fatal(1,"INPUT");
      end
      for(j=0;j<M;j=j+1) begin
        rc=$fscanf(input_file,"%h\n",expected[j]);if(rc!=1)$fatal(1,"EXPECTED");
      end
      cb=1'(bd);ca=1'(ab);cs=5'(shift);
      if(use_defaults) begin
        if(mode!=0)$fatal(1,"DEFAULT_FIXTURE_MODE");
        clear_reset();
        run_frame(0);
      end else begin
      configure(mode,0);
      if(f<=1 && @ILLEGAL@) begin
        cb=~cb;ca=~ca;cs=~cs;cc=~cc;
        @REJECT@
        cb=~cb;ca=~ca;cs=~cs;cc=~cc;
      end
      // Aborted epochs never count as completed frames.
      if(f==0 || f==1 || f==3) begin
        for(abort_kind=1;abort_kind<=3;abort_kind=abort_kind+1) begin
          if(!((abort_kind==1&&N==1)||(abort_kind==2&&M==1))) begin
            run_frame(abort_kind);
            if(!abort_done) $fatal(1,"RESET_SCENARIO_UNREACHED");
            clear_reset();
            cb=1'(bd);ca=1'(ab);cs=5'(shift);
            configure(mode,0);
          end
        end
      end
      run_frame(0);
      end
    end
    $fdisplay(result_file,"C %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d",
      bubbles,stalls,maxstall,sofstall,eolstall,cfgchanges,busycycles,illegal,
      fillreset,outputreset,stallreset,total_in,total_out);
    $fclose(input_file);$fclose(result_file);
    $display("COMPLETE frames=%0d outputs=%0d parent=%0d",frames,total_out,total_parent);
    $finish;
  end
endmodule
"""
    rejected = [1, 2, 3] if bits == 10 else [2, 3]
    values = dict(SW=sw, SH=sh, DW=dw, DH=dh, B=width, PARAMS=params,
                  ILLEGAL=int(bits == 10 or channels == 1),
                  REJECT=" ".join(f"configure({mode},1);" for mode in rejected))
    for name, value in values.items():
        template = template.replace(f"@{name}@", str(value))
    return template
