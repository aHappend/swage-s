"""Compact IEEE-1800 leaf tests with independently derived arithmetic and queues."""

import json
import os
import random
from fractions import Fraction

from harness import hexfile
from reference import normalize, pack, temporal


def temporal_vectors():
    result = []
    for alpha in (0, 1, 128, 255, 256):
        for threshold in (0, 1, 16, 255, 256):
            for motion in sorted({0, 1, 15, 16, 17, 254, 255,
                                  max(0, threshold-1), min(255, threshold), min(255, threshold+1)}):
                for valid_history, enabled in ((0, 1), (1, 1), (1, 0)):
                    history = (0, 0, 0)
                    pixel = (motion, motion//2, motion//3)
                    expected = temporal([[pixel]], [[history]] if valid_history and enabled else None,
                                        alpha, threshold)[0][0]
                    source = (pack(pixel, 8) | pack(history, 8) << 24 |
                              valid_history << 48 | enabled << 49 | alpha << 50 | threshold << 59)
                    result.append((source, pack(expected, 8)))
    # Both signs of a half-byte temporal result and unequal channel motion classification.
    for pixel, history in (((1, 3, 5), (0, 0, 0)), ((0, 0, 0), (1, 3, 5)),
                           ((25, 12, 8), (9, 11, 7))):
        expected = temporal([[pixel]], [[history]], 128, 256)[0][0]
        source = pack(pixel, 8) | pack(history, 8) << 24 | 3 << 48 | 128 << 50 | 256 << 59
        result.append((source, pack(expected, 8)))
    return result


def normalizer_vectors():
    values = []
    # Products immediately below/at/above Q ties, odd/even quotients and signs.
    for sign in (-1, 1):
        for integer in (0, 1, 2, 127, 128, 32767, 32768):
            for offset in (-1, 0, 1):
                product = sign * (integer * 65536 + 32768 + offset)
                # Reciprocal=1, representable exact difference for small Q tie cases.
                if abs(product) <= (1 << 23)-1:
                    values.append(((0, 0, 0), [-product]*3, [1]*3))
    # BF16 ties with even/odd significands, neighboring products, and mantissa carry.
    for significand in (128, 129, 254, 255):
        for offset in (-1, 0, 1):
            product = significand*65536 + 32768 + offset
            for sign in (-1, 1):
                values.append(((0, 0, 0), [-sign*product//2]*3, [2]*3))
                # Exact unit products within the signed mean range at a smaller exponent.
                p = significand*16384 + 8192 + offset
                values.append(((0, 0, 0), [-sign*p]*3, [1]*3))
    for means in ([0, 0, 0], [32640]*3, [-8388608, 8388607, 0],
                  [8388607, -8388608, 65535]):
        for reciprocal in (1, 512, 65536, 16777215):
            for pixel in ((0, 0, 0), (255, 255, 255), (127, 128, 129)):
                values.append((pixel, means, [reciprocal]*3))
    rng = random.Random(203)
    for _ in range(128):
        values.append((tuple(rng.randrange(256) for _ in range(3)),
                       [rng.randrange(-8388608, 8388608) for _ in range(3)],
                       [rng.randrange(1, 16777216) for _ in range(3)]))
    result = []
    for bf in (0, 1):
        for index, (pixel, means, reciprocals) in enumerate(values):
            for keep in (0, 7):
                # All public tags independently varied, including flags which may coincide.
                first, last, final = index & 1, (index >> 1) & 1, (index >> 2) & 1
                pid, x, y, element = index*31, index % 29, index % 17, index % 256
                tags = (pid << 48 | x << 80 | y << 93 | element << 106 |
                        keep << 114 | first << 117 | last << 118 | final << 119)
                expected = (normalize(pixel, means, reciprocals, bf) if keep else 0) | tags
                source = (pack(pixel, 8) | pack(means, 24) << 24 |
                          pack(reciprocals, 24) << 96 | bf << 168 | tags << 169)
                result.append((source, expected))
    return result


def arithmetic_coverage(kind, vectors):
    counts = {}
    def count(name):
        counts[name] = counts.get(name, 0) + 1
    for source, expected in vectors:
        if kind == "temporal":
            pixel = [(source >> (8*c)) & 255 for c in range(3)]
            history = [(source >> (24+8*c)) & 255 for c in range(3)]
            alpha, threshold = (source >> 50) & 511, (source >> 59) & 511
            usable = (source >> 48) & 3 == 3
            motion = max(abs(a-b) for a, b in zip(pixel, history))
            count(f"alpha_{alpha}")
            count(f"threshold_{threshold}")
            count("blend" if usable and motion < threshold else "current")
            count("history_valid" if usable else "history_unused")
            if usable:
                if motion == threshold:
                    count("motion_equal")
                if motion+1 == threshold:
                    count("motion_immediately_below")
                if motion == threshold+1:
                    count("motion_immediately_above")
        else:
            keep = (source >> (169+114)) & 7
            count("padding" if not keep else "real")
            if not keep:
                assert expected & ((1 << 48)-1) == 0
                continue
            fmt = (source >> 168) & 1
            for c in range(3):
                pixel = (source >> (8*c)) & 255
                mean = (source >> (24+24*c)) & 16777215
                if mean & (1 << 23):
                    mean -= 1 << 24
                reciprocal = (source >> (96+24*c)) & 16777215
                product = (256*pixel-mean)*reciprocal
                lane = (expected >> (16*c)) & 65535
                count(("bf16_" if fmt else "q88_") +
                      ("negative" if product < 0 else ("positive" if product > 0 else "zero")))
                if mean in (-8388608, 8388607):
                    count(f"mean_{mean}")
                if reciprocal in (1, 16777215):
                    count(f"reciprocal_{reciprocal}")
                if not fmt:
                    if product > 0 and lane == 32767:
                        count("q88_positive_saturation")
                    if product < 0 and lane == 32768:
                        count("q88_negative_saturation")
                    integer, remainder = divmod(abs(product), 65536)
                    if remainder == 32768:
                        count("q88_tie_odd" if integer & 1 else "q88_tie_even")
                    if remainder in (32767, 32769):
                        count(f"q88_neighbor_{remainder}")
                elif product:
                    # Exact mantissa scaling for a denominator of 2^24.
                    leading = abs(product).bit_length()-1
                    if leading >= 7:
                        integer, remainder = divmod(abs(product), 1 << (leading-7))
                        divisor = 1 << (leading-7)
                        if 2*remainder == divisor:
                            count("bf16_tie_odd" if integer & 1 else "bf16_tie_even")
                        if 2*remainder == divisor-2:
                            count("bf16_neighbor_below")
                        if 2*remainder == divisor+2:
                            count("bf16_neighbor_above")
                        if ((lane >> 7) & 255)-127 > leading-24:
                            count("bf16_mantissa_carry")
    required = (("blend", "current", "motion_equal", "motion_immediately_below",
                 "motion_immediately_above", "history_valid", "history_unused")
                if kind == "temporal" else
                ("padding", "real", "q88_positive", "q88_negative", "q88_zero",
                 "bf16_positive", "bf16_negative", "bf16_zero",
                 "q88_positive_saturation", "q88_negative_saturation",
                 "q88_tie_even", "q88_tie_odd", "q88_neighbor_32767", "q88_neighbor_32769",
                 "bf16_tie_even", "bf16_tie_odd", "bf16_mantissa_carry",
                 "bf16_neighbor_below", "bf16_neighbor_above",
                 "mean_-8388608", "mean_8388607", "reciprocal_1", "reciprocal_16777215"))
    assert all(counts.get(name, 0) > 0 for name in required), f"uncovered accepted arithmetic bins {required}"
    return counts


def arithmetic(evidence, kind, source=None, simulator="iverilog", label=None):
    from verify import PROJECT, unique, relative
    label = label or "leaf-"+kind
    folder = evidence.folder / unique(label)
    folder.mkdir()
    vectors = temporal_vectors() if kind == "temporal" else normalizer_vectors()
    hexfile(folder/"input.hex", [v[0] for v in vectors], 289)
    hexfile(folder/"expected.hex", [v[1] for v in vectors], 120)
    if kind == "temporal":
        stages, bits = 5, 24
        inst = """
swage_s4_temporal dut(.clk(clk),.rst_n(rst_n),.s_valid(sv),.s_ready(sr),
    .s_data(vector[23:0]),.h_data(vector[47:24]),.history_valid(vector[48]),
    .temporal_enable(vector[49]),.alpha(vector[58:50]),.threshold(vector[67:59]),
    .m_valid(mv),.m_ready(mr),.m_data(result[23:0]));
assign result[119:24] = 0;
"""
    else:
        stages, bits = 8, 120
        inst = """
wire [119:0] tags=vector[288:169];
swage_s4_normalizer dut(.clk(clk),.rst_n(rst_n),.s_valid(sv),.s_ready(sr),
    .s_data(vector[23:0]),.mean(vector[95:24]),.inv_std(vector[167:96]),
    .bf16(vector[168]),.s_keep(tags[116:114]),.s_first(tags[117]),
    .s_last(tags[118]),.s_frame_last(tags[119]),.s_id(tags[79:48]),
    .s_x(tags[92:80]),.s_y(tags[105:93]),.s_element(tags[113:106]),
    .m_valid(mv),.m_ready(mr),.m_data(result[47:0]),.m_id(result[79:48]),
    .m_x(result[92:80]),.m_y(result[105:93]),.m_element(result[113:106]),
    .m_keep(result[116:114]),.m_first(result[117]),.m_last(result[118]),
    .m_frame_last(result[119]));
"""
    tb = r"""
module tb;
reg clk=0, rst_n=0;
always #5 clk=~clk;
reg sv=0,mr=0;
reg [288:0] vector=0;
wire sr,mv;
wire [119:0] result;
@INSTANCE@
reg [288:0] inputs[0:@N@-1];
reg [119:0] expected[0:@N@-1];
integer due[0:@N@-1];
integer sent=0,offered=-1,received=0,advances=0,cycles=0,stalls=0,gaps=0;
integer saturation_positive=0,saturation_negative=0,positive=0,negative=0,zero=0;
reg stalled=0;
reg [119:0] held;
task automatic reset_check;
begin
    if ({sr,mv,result} !== 0) $fatal(1,"LEAF_RESET_XZ");
end
endtask
initial begin
`ifndef VERILATOR
    #0; reset_check();
`endif
    #1; reset_check();
end
always @(negedge clk) if(!rst_n) reset_check();
always @(posedge clk) begin
    if(!rst_n) reset_check();
    else begin
        cycles=cycles+1;
        if(cycles>32*@N@+100) $fatal(1,"LEAF_DEADLOCK");
        if ((^{sr,mv,result}) === 1'bx) $fatal(1,"LEAF_ACTIVE_XZ");
        if(sr !== (!mv || mr)) $fatal(1,"LEAF_ADVANCE_HANDSHAKE");
        if(stalled && (!mv || result !== held)) $fatal(1,"LEAF_STALL_CHANGED");
        stalled=mv && !mr; held=result;
        if(stalled) stalls=stalls+1;
        if(mv && mr) begin
            if(received>=sent || result !== expected[received])
                $fatal(1,"@KIND@_MISMATCH index=%0d expected=%h actual=%h",
                    received,expected[received],result);
            received=received+1;
        end
        if(sr) begin
            advances=advances+1;
            if(sv) begin due[sent]=advances+@LATENCY@; sent=sent+1; end
        end
        #1;
        if(mv !== (received<sent && due[received]<=advances))
            $fatal(1,"@KIND@_LATENCY_MISMATCH advances=%0d received=%0d sent=%0d",
                advances,received,sent);
        if(mv && result !== expected[received])
            $fatal(1,"@KIND@_MISMATCH index=%0d expected=%h actual=%h",
                received,expected[received],result);
    end
end
initial begin
    $readmemh("input.hex",inputs);
    $readmemh("expected.hex",expected);
    repeat(2) @(negedge clk);
    rst_n=1;
    while(received<@N@) begin
        @(negedge clk);
        mr=cycles%17>=5;
        if(!sv || sent!=offered) begin
            sv=sent<@N@ && cycles%13!=4 && cycles%13!=5;
            if(sv) begin vector=inputs[sent]; offered=sent; end
            else gaps=gaps+1;
        end
    end
    @(negedge clk); sv=0; mr=1;
    repeat(12) @(negedge clk);
    if(sent!=@N@ || received!=@N@ || !stalls || !gaps) $fatal(1,"LEAF_COVERAGE_MISSING");
    $display("LEAF_RESULT @KIND@ %0d %0d %0d %0d",received,cycles,stalls,gaps);
    $finish;
end
endmodule
"""
    for key, value in {"INSTANCE": inst, "N": len(vectors), "LATENCY": stages-1,
                       "KIND": kind.upper()}.items():
        tb = tb.replace("@"+key+"@", str(value))
    (folder/"tb.sv").write_text(tb)
    rtl = (source or PROJECT/"rtl") / f"swage_s4_{kind}.sv"
    sources = [os.path.relpath(rtl, folder), "tb.sv"]
    if simulator == "iverilog":
        evidence.command(["iverilog", "-g2012", "-Wall", "-s", "tb", "-o", "simulation.vvp", *sources],
                         folder, label+"-compile")
        record, out, _ = evidence.command(["vvp", "simulation.vvp"], folder, label+"-simulate")
    else:
        evidence.command(["verilator", "--binary", "--timing", "-Wno-fatal", "--top-module", "tb",
                          "--Mdir", "compile-obj", "-j", "2", "-o", "simulation", *sources],
                         folder, label+"-compile")
        record, out, _ = evidence.command(["./compile-obj/simulation"], folder, label+"-simulate")
    fields = [line.split() for line in out.splitlines() if line.startswith("LEAF_RESULT ")]
    assert len(fields) == 1 and int(fields[0][2]) == len(vectors), "missing actual leaf comparisons"
    result = dict(kind=kind, compared=len(vectors), cycles=int(fields[0][3]),
                  stalls=int(fields[0][4]), gaps=int(fields[0][5]),
                  observed_coverage=arithmetic_coverage(kind, vectors),
                  stdout=record["stdout"], vector_sha256={
                      name: __import__("hashlib").sha256((folder/name).read_bytes()).hexdigest()
                      for name in ("input.hex", "expected.hex", "tb.sv")})
    (folder/"comparison.json").write_text(json.dumps(result, indent=2)+"\n")
    return result


def storage(evidence, depth):
    from verify import PROJECT, unique
    folder = evidence.folder / unique(f"storage-depth-{depth}")
    folder.mkdir()
    tb = r"""
module tb;
reg clk=0,rst_n=0;
always #5 clk=~clk;
reg [7:0] sd=0;
reg sv=0,mr=0;
wire sr,mv,empty;
wire [7:0] md;
swage_s3_fifo #(.WIDTH(8),.DEPTH(@DEPTH@)) fifo(
    .clk(clk),.rst_n(rst_n),.s_data(sd),.s_valid(sv),.s_ready(sr),
    .m_data(md),.m_valid(mv),.m_ready(mr),.empty(empty));
reg wr=0,wh=0,rv=0,rh=0,rr=0;
reg [16:0] wa=0,ra=0;
reg [23:0] wd=0;
reg [2:0] wm=0;
wire ready,valid;
wire [23:0] data;
wire [2:0] mask;
swage_s4_frame_store #(.MAX_FRAME_PIXELS(@DEPTH@)) store(
    .clk(clk),.rst_n(rst_n),.wr_valid(wr),.wr_history(wh),.wr_addr(wa),
    .wr_data(wd),.wr_mask(wm),.rd_valid(rv),.rd_ready(ready),.rd_history(rh),
    .rd_addr(ra),.rsp_valid(valid),.rsp_ready(rr),.rsp_data(data),.rsp_mask(mask));
integer head=0,tail=0,cycle=0,full=0,simultaneous=0,outputs=0,wraps=0;
reg [7:0] queue[0:2047];
reg held_valid=0;
reg [7:0] held_data;
task automatic reset_check;
begin
    if({sr,mv,md,empty,ready,valid,data,mask} !== 0) $fatal(1,"STORAGE_RESET_XZ");
end
endtask
initial begin
`ifndef VERILATOR
    #0; reset_check();
`endif
    #1; reset_check();
end
always @(negedge clk) if(!rst_n) reset_check();
always @(posedge clk) begin
    if(!rst_n) begin head=0;tail=0;held_valid=0;reset_check();end
    else begin
        if((^{sr,mv,md,empty,ready,valid,data,mask})===1'bx) $fatal(1,"STORAGE_ACTIVE_XZ");
        if(held_valid && (!mv || md !== held_data)) $fatal(1,"FIFO_STALL_CHANGED");
        held_valid=mv&&!mr;held_data=md;
        if(mv&&mr) begin
            if(head==tail || md !== queue[head]) $fatal(1,"FIFO_MISMATCH index=%0d",head);
            head=head+1;outputs=outputs+1;
        end
        if(sv&&sr) begin queue[tail]=sd;tail=tail+1;if(tail%@DEPTH@==0) wraps=wraps+1;end
        if(sv&&!sr) full=full+1;
        if(sv&&sr&&mv&&mr) simultaneous=simultaneous+1;
    end
end
task automatic read_word(input integer addr,history,input reg[23:0] expected,input reg[2:0] expected_mask);
begin
    @(negedge clk);ra=addr;rh=history;rv=1;rr=0;
    if(!ready) $fatal(1,"FRAME_STORE_NOT_READY");
    @(posedge clk);#1;
    if(valid) $fatal(1,"FRAME_STORE_EARLY_RESPONSE");
    @(negedge clk);rv=0;
    @(posedge clk);#1;
    if(!valid || {mask,data} !== {expected_mask,expected}) $fatal(1,"FRAME_STORE_MISMATCH");
    repeat(4) begin
        @(negedge clk);
        if(!valid || {mask,data} !== {expected_mask,expected} || ready)
            $fatal(1,"FRAME_STORE_STALL_CHANGED");
    end
    rr=1;@(posedge clk);@(negedge clk);rr=0;
end
endtask
integer i;
initial begin
    repeat(2) @(negedge clk);rst_n=1;
    // Fill without draining, then deterministic concurrent traffic, then drain.
    for(i=0;i<600;i=i+1) begin
        @(negedge clk);
        if(!sv||sr) begin sv=1;sd=tail;end
        mr=i>30 && i%11>=3;
    end
    @(negedge clk);sv=0;mr=1;
    while(head<tail) @(negedge clk);
    repeat(5) @(negedge clk);
    // Hold one output with no stored traffic, then accept it alongside a new input.
    mr=0;sv=1;sd=tail;
    @(posedge clk);while(!sr) @(posedge clk);
    @(negedge clk);sv=0;
    while(!mv) @(negedge clk);
    if(!sr) $fatal(1,"FIFO_DIRECTED_PUSH_POP_PRECONDITION");
    sv=1;sd=tail;mr=1;
    @(posedge clk);
    if(!(sv&&sr&&mv&&mr)) $fatal(1,"FIFO_DIRECTED_PUSH_POP_NOT_COINCIDENT");
    @(negedge clk);sv=0;
    while(head<tail) @(negedge clk);
    repeat(5) @(negedge clk);
    if(!empty || !full || !simultaneous || wraps<4 || outputs<20) $fatal(1,"FIFO_COVERAGE_MISSING");
    for(i=0;i<@DEPTH@;i=i+1) begin
        @(negedge clk);wr=1;wh=0;wa=i;wd=24'h123400+i;wm=7;
        @(negedge clk);wh=1;wd=24'hfedc00+i;
    end
    @(negedge clk);wr=0;
    for(i=0;i<@DEPTH@;i=i+1) begin
        read_word(i,0,24'h123400+i,7);
        read_word(i,1,24'hfedc00+i,0);
    end
    @(negedge clk);#1;rst_n=0;#1;reset_check();
    repeat(2) @(negedge clk);rst_n=1;
    repeat(5) @(negedge clk);
    if(mv||valid) $fatal(1,"STORAGE_RESET_PHANTOM");
    $display("STORAGE_RESULT @DEPTH@ %0d %0d %0d %0d",outputs,full,simultaneous,wraps);
    $finish;
end
initial begin repeat(5000) @(posedge clk);$fatal(1,"STORAGE_DEADLOCK");end
endmodule
""".replace("@DEPTH@", str(depth))
    (folder/"tb.sv").write_text(tb)
    sources = [os.path.relpath(PROJECT/"rtl"/name, folder)
               for name in ("swage_s3_fifo.sv", "swage_s4_frame_store.sv")]
    evidence.command(["iverilog", "-g2012", "-Wall", "-s", "tb", "-o", "simulation.vvp",
                      *sources, "tb.sv"], folder, f"storage-{depth}-compile")
    record, out, _ = evidence.command(["vvp", "simulation.vvp"], folder, f"storage-{depth}-simulate")
    rows = [line.split() for line in out.splitlines() if line.startswith("STORAGE_RESULT ")]
    assert len(rows) == 1 and all(int(v)>0 for v in rows[0][2:])
    return dict(depth=depth, observed=list(map(int, rows[0][2:])), stdout=record["stdout"])


def run_leaves(evidence):
    results = [arithmetic(evidence, kind) for kind in ("temporal", "normalizer")]
    results += [storage(evidence, depth) for depth in (1, 3)]
    (evidence.folder/"leaf-results.json").write_text(json.dumps(results, indent=2)+"\n")
