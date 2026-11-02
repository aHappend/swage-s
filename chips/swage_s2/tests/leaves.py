"""Independent pin-level window, spatial arithmetic and scaler latency checks."""

from execution import ROOT, dump, relative
from reference import convolve, demosaic, image_for, pack, taps


SPATIAL = r"""
module tb;
  localparam W=@W@,H=@H@,N=W*H,L=@L@,B=@B@;
  reg clk=0,rst_n;
  always #5 clk=~clk;
  reg [7:0] sd=0;
  reg sv=0,ss=0,sl=0,mr=0,border=0;
  wire sr,mv,ms,ml,wsr,wv,ws,wl;
  wire [B-1:0] md;
  wire [71:0] wd;
  wire [12:0] wx,wy;
  wire advance=rst_n&&(!mv||mr);
  @INSTANCE@
  swage_s2_window #(.SRC_WIDTH(W),.SRC_HEIGHT(H),.DATA_WIDTH(8)) witness (
    .clk(clk),.rst_n(rst_n),.s_axis_tdata(sd),.s_axis_tvalid(sv),
    .s_axis_tuser(ss),.s_axis_tlast(sl),.s_axis_tready(wsr),.border(border),
    .w_data(wd),.w_valid(wv),.w_sof(ws),.w_eol(wl),.w_x(wx),.w_y(wy),
    .w_ready(advance));
  reg [7:0] pixels[0:N-1];
  reg [71:0] windows[0:N-1];
  reg [B-1:0] values[0:N-1],queue_data[0:L-1];
  reg [L-1:0] qv=0,qs=0,ql=0;
  reg held_valid=0;
  reg [B+1:0] held;
  reg window_stalled=0;
  reg [99:0] window_held;
  integer file_in,file_out,rc,j,i,si,wi,oi,cycles,epoch,stalls,windows_checked,outputs;
  reg take,scan_take,adv,previous_take=0;
  string inp,outp;
  task automatic reset_check;
    begin
      if({sr,mv,ms,ml,md,wsr,wv,ws,wl,wd,wx,wy} !== 0)
        $fatal(1,"LEAF_RESET_ZERO");
    end
  endtask
  always @(posedge clk or negedge clk) if(!rst_n) reset_check();
  initial begin
    rst_n=0;
`ifndef VERILATOR
    #0;
`else
    #1;
`endif
    reset_check();
    if(!$value$plusargs("stimulus=%s",inp)||!$value$plusargs("result=%s",outp))
      $fatal(1,"LEAF_ARGS");
    file_in=$fopen(inp,"r");file_out=$fopen(outp,"w");
    if(!file_in||!file_out)$fatal(1,"LEAF_FILES");
    stalls=0;windows_checked=0;outputs=0;
    for(epoch=0;epoch<2;epoch=epoch+1)begin
      @(negedge clk);#1;rst_n=0;sv=0;mr=0;held_valid=0;window_stalled=0;qv=0;
      repeat(3)@(negedge clk);
      border=1'(epoch);
      for(j=0;j<N;j=j+1) begin
        rc=$fscanf(file_in,"%h %h %h\n",pixels[j],windows[j],values[j]);
        if(rc!=3)$fatal(1,"LEAF_STIMULUS");
      end
      rst_n=1;si=0;wi=0;oi=0;cycles=0;previous_take=0;
      while(oi<N || si<N || wi<N || |qv) begin
        if(!sv||previous_take)begin
          sv=si<N&&(cycles%4!=0);
          if(sv)begin sd=pixels[si];ss=si==0;sl=si%W==W-1;end
        end
        mr=!(cycles%53>=23&&cycles%53<43);
        @(posedge clk);
        if((^{sr,wsr,wv,mv})===1'bx)$fatal(1,"LEAF_XZ");
        if(sr!==wsr)$fatal(1,"LEAF_SOURCE_HANDSHAKE");
        if(held_valid&&{mv,md,ms,ml}!=={1'b1,held})$fatal(1,"LEAF_STALL_STABILITY");
        if(window_stalled&&{wv,wd,ws,wl,wx,wy}!=={1'b1,window_held})
          $fatal(1,"WINDOW_STALL_STABILITY");
        held_valid=mv&&!mr;held={md,ms,ml};
        window_stalled=wv&&!advance;window_held={wd,ws,wl,wx,wy};
        if(held_valid)stalls=stalls+1;
        if(mv&&mr)begin
          if(oi>=N || md!==values[oi] || ms!==1'(oi==0) || ml!==1'(oi%W==W-1))
            $fatal(1,"LEAF_NUMERICAL index=%0d data=%h expected=%h",oi,md,values[oi]);
          $fdisplay(file_out,"O %0d %0d %h",epoch,oi,{md,ms,ml});
          oi=oi+1;outputs=outputs+1;
        end
        take=sv&&sr;scan_take=wv&&advance;adv=advance;
        if(scan_take)begin
          if(wi>=N || wd!==windows[wi] || wx!==13'(wi%W) || wy!==13'(wi/W) ||
             ws!==1'(wi==0) || wl!==1'(wi%W==W-1))
            $fatal(1,"WINDOW_NUMERICAL index=%0d got=%h expected=%h",wi,wd,windows[wi]);
          windows_checked=windows_checked+1;
          $fdisplay(file_out,"W %0d %0d %h",epoch,wi,wd);
        end
        if(adv)begin
          for(i=L-1;i>0;i=i-1)begin
            qv[i]=qv[i-1];qs[i]=qs[i-1];ql[i]=ql[i-1];queue_data[i]=queue_data[i-1];
          end
          qv[0]=scan_take;
          if(scan_take)begin
            queue_data[0]=values[wi];qs[0]=wi==0;ql[0]=wi%W==W-1;
          end else begin qs[0]=0;ql[0]=0;end
        end
        if(take)si=si+1;
        if(scan_take)wi=wi+1;
        previous_take=take;
        #1;
        if(mv!==qv[L-1])$fatal(1,"LEAF_LATENCY_VALID cycles=%0d",cycles);
        if(mv&&{md,ms,ml}!=={queue_data[L-1],qs[L-1],ql[L-1]})
          $fatal(1,"LEAF_LATENCY_DATA cycles=%0d",cycles);
        cycles=cycles+1;
        if(cycles>200*N+2000)$fatal(1,"LEAF_PROGRESS");
        @(negedge clk);
      end
      sv=0;mr=1;
      repeat(24)begin
        @(posedge clk);if(mv||wv)$fatal(1,"LEAF_EXTRA_OUTPUT");
        @(negedge clk);
      end
    end
    $fdisplay(file_out,"C %0d %0d %0d",windows_checked,outputs,stalls);
    $fclose(file_in);$fclose(file_out);$finish;
  end
endmodule
"""

INTERP = r"""
module tb;
  localparam F=@F@,B=@B@,P=@P@,C=@C@;
  reg clk=0,rst_n;
  always #5 clk=~clk;
  reg issue=0,sof=0,eol=0,mr=0;
  reg [F-1:0] fx=0,fy=0;
  reg [B-1:0] p00=0,p01=0,p10=0,p11=0;
  wire advance,empty,mv,ms,ml;
  wire [B-1:0] md;
  swage_s1_interp #(.PIXEL_BITS(P),.CHANNELS(C),.DATA_WIDTH(B),.PHASE_FRAC(F)) dut(
    .clk(clk),.rst_n(rst_n),.issue_valid(issue),.issue_sof(sof),.issue_eol(eol),
    .fx(fx),.fy(fy),.p00(p00),.p01(p01),.p10(p10),.p11(p11),
    .pipe_advance(advance),.pipe_empty(empty),.m_axis_tdata(md),.m_axis_tvalid(mv),
    .m_axis_tuser(ms),.m_axis_tlast(ml),.m_axis_tready(mr));
  reg [B-1:0] samples[0:3],wanted,qd[0:5];
  reg [5:0] qv=0,qs=0,ql=0;
  reg [B+1:0] held;
  reg stalled=0;
  integer fi,fo,rc,i,n,cycle,accepted,retired,total;
  reg adv,take;
  string inp,outp;
  initial begin
    rst_n=0;
    if(!$value$plusargs("stimulus=%s",inp)||!$value$plusargs("result=%s",outp))
      $fatal(1,"INTERP_ARGS");
    fi=$fopen(inp,"r");fo=$fopen(outp,"w");
    if(!fi||!fo)$fatal(1,"INTERP_FILES");
    rc=$fscanf(fi,"%d\n",total);if(rc!=1||total<1)$fatal(1,"INTERP_HEADER");
    repeat(3)@(negedge clk);
    rst_n=1;n=0;cycle=0;accepted=0;retired=0;
    while(n<total || |qv)begin
      mr=!(cycle%47>=20&&cycle%47<38);
      #1;
      if(advance)begin
        issue=n<total&&cycle%4!=0;
        if(issue)begin
          rc=$fscanf(fi,"%h %h %h %h %h %h %h\n",
                     fx,fy,samples[0],samples[1],samples[2],samples[3],wanted);
          if(rc!=7)$fatal(1,"INTERP_INPUT");
          sof=n%7==0;eol=n%3==0;
        end else begin sof=0;eol=0;end
      end
      @(posedge clk);
      if(stalled&&{mv,md,ms,ml}!=={1'b1,held})$fatal(1,"INTERP_STALL");
      stalled=mv&&!mr;held={md,ms,ml};
      if(mv&&mr)begin retired=retired+1;$fdisplay(fo,"O %h",{md,ms,ml});end
      adv=advance;take=issue&&advance;
      if(adv)begin
        for(i=5;i>0;i=i-1)begin qv[i]=qv[i-1];qs[i]=qs[i-1];ql[i]=ql[i-1];qd[i]=qd[i-1];end
        qv[0]=take;qs[0]=take&&sof;ql[0]=take&&eol;qd[0]=wanted;
      end
      if(take)begin
        n=n+1;accepted=accepted+1;
        // The parent line RAM delivers samples after the issue edge.
        p00<=samples[0];p01<=samples[1];p10<=samples[2];p11<=samples[3];
      end
      #1;
      if(mv!==qv[5])$fatal(1,"INTERP_LATENCY");
      if(mv&&{md,ms,ml}!=={qd[5],qs[5],ql[5]})$fatal(1,"INTERP_NUMERICAL");
      cycle=cycle+1;if(cycle>10000)$fatal(1,"INTERP_PROGRESS");
      @(negedge clk);
    end
    if(accepted!=total||retired!=total)$fatal(1,"INTERP_COUNTS");
    repeat(10)begin @(posedge clk);if(mv)$fatal(1,"INTERP_EXTRA");end
    $fdisplay(fo,"C %0d %0d",accepted,retired);$fclose(fo);$finish;
  end
endmodule
"""


def simulate(ledger, directory, source, stimulus, name):
    tb, inp, out, binary = (directory / x for x in ("tb.sv", "stimulus.txt",
                                                  "results.txt", "simulator.vvp"))
    tb.write_text(source)
    inp.write_text(stimulus)
    ledger.run(["iverilog", "-g2012", "-Wall", "-s", "tb", "-o", relative(binary),
                relative(tb), *[relative(p) for p in sorted(ROOT.glob("rtl/*.sv"))]],
               f"{name}-compile")
    ledger.run(["vvp", relative(binary), f"+stimulus={relative(inp)}",
                f"+result={relative(out)}"], name)
    return out


def verify_leaves(ledger):
    import random
    results = []
    for w, h in ((1, 1), (3, 2), (5, 4)):
        for operation, latency, bits in (("convolution", 9, 8), ("demosaic", 2, 24)):
            name = f"leaf-{operation}-{w}x{h}"
            directory = ledger.path / name
            directory.mkdir()
            text = SPATIAL
            coeff = [-1, 2, 0, 3, 1, -2, 0, 1, -1]
            packed_coeff = sum((v & 65535) << (16 * i) for i, v in enumerate(coeff))
            ports = """
    .clk(clk),.rst_n(rst_n),.s_axis_tdata(sd),.s_axis_tvalid(sv),
    .s_axis_tuser(ss),.s_axis_tlast(sl),.s_axis_tready(sr),.border(border),
    .m_axis_tdata(md),.m_axis_tvalid(mv),.m_axis_tuser(ms),.m_axis_tlast(ml),
    .m_axis_tready(mr)"""
            if operation == "convolution":
                ports += f",.cfg_abs(1'b1),.cfg_shift(5'd4),.cfg_coeff(144'h{packed_coeff:036x})"
            instance = (f"swage_s2_{operation} #(.SRC_WIDTH(W),.SRC_HEIGHT(H)) dut("
                        + ports + ");")
            for key, value in dict(W=w, H=h, L=latency, B=bits, INSTANCE=instance).items():
                text = text.replace(f"@{key}@", str(value))
            geometry = (w, h, w, h, 8, 1, 8, 0)
            image = image_for(geometry, "rggb", 89)
            rows, expected = [], []
            for border in (0, 1):
                result = convolve(image, coeff, 4, 1, border) if operation == "convolution" else (
                    demosaic(image, border))
                for y in range(h):
                    for x in range(w):
                        window = sum(pack(p) << (8 * i)
                                     for i, p in enumerate(taps(image, x, y, border)))
                        value = pack(result[y][x])
                        rows.append(f"{pack(image[y][x]):x} {window:018x} {value:x}")
                        expected.append((border, y * w + x, window, value))
            path = simulate(ledger, directory, text, "\n".join(rows) + "\n", name)
            seen_w, seen_o, counters = [], [], None
            for line in path.read_text().splitlines():
                fields = line.split()
                if fields[0] in ("O", "W") and len(fields) == 4:
                    epoch, index, value = int(fields[1]), int(fields[2]), int(fields[3], 16)
                    target = seen_w if fields[0] == "W" else seen_o
                    if len(target) >= len(expected):
                        raise AssertionError("Extra leaf record")
                    e, n, win, pixel = expected[len(target)]
                    wanted = win if fields[0] == "W" else (
                        pixel << 2 | ((n == 0) << 1) | (n % w == w - 1))
                    if (epoch, index, value) != (e, n, wanted):
                        raise AssertionError("Independent leaf result mismatch")
                    target.append(value)
                elif fields[0] == "C" and len(fields) == 4 and counters is None:
                    counters = list(map(int, fields[1:]))
                else:
                    raise AssertionError("Malformed leaf results")
            if len(seen_w) != 2 * w * h or len(seen_o) != 2 * w * h or counters is None:
                raise AssertionError("Missing complete leaf comparisons")
            results.append(dict(name=name, latency=latency, windows=len(seen_w),
                                outputs=len(seen_o), stalls=counters[2]))
    for pixel_bits, channels, frac in ((8, 1, 8), (10, 1, 16), (8, 3, 16)):
        width = 16 if pixel_bits == 10 else channels * 8
        name = f"leaf-interp-{pixel_bits}-{channels}-{frac}"
        directory = ledger.path / name
        directory.mkdir()
        text = INTERP
        for key, value in dict(F=frac, B=width, P=pixel_bits, C=channels).items():
            text = text.replace(f"@{key}@", str(value))
        rng = random.Random(203)
        rows, expected = ["64"], []
        unit = 1 << frac
        for i in range(64):
            fx, fy = rng.randrange(unit), rng.randrange(unit)
            samples = [tuple(rng.randrange(1 << pixel_bits) for _ in range(channels))
                       for _ in range(4)]
            weights = ((unit - fx) * (unit - fy), fx * (unit - fy),
                       (unit - fx) * fy, fx * fy)
            wanted = tuple((sum(p[c] * w for p, w in zip(samples, weights)) +
                            unit * unit // 2) // (unit * unit) for c in range(channels))
            packed = pack(wanted, pixel_bits)
            rows.append(" ".join(f"{v:x}" for v in
                                 [fx, fy, *[pack(p, pixel_bits) for p in samples], packed]))
            expected.append(packed << 2 | ((i % 7 == 0) << 1) | (i % 3 == 0))
        path = simulate(ledger, directory, text, "\n".join(rows) + "\n", name)
        lines = path.read_text().splitlines()
        actual = [int(line.split()[1], 16) for line in lines if line.startswith("O ")]
        if actual != expected or lines[-1] != "C 64 64":
            raise AssertionError("Incomplete independent interpolation comparison")
        results.append(dict(name=name, outputs=64, latency=6))
    dump(ledger.path / "leaves.json", results)
    return results
