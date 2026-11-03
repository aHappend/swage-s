"""Generate disposable public-pin SystemVerilog transactors and comparisons."""

import json
from pathlib import Path

from reference import clahe, legacy, pack, pixels, temporal, tokens
from cases import Case, image


PORTS = {
    "s_axis_tdata": 24, "s_axis_cvalid": 3, "s_axis_tvalid": 1,
    "s_axis_tuser": 1, "s_axis_tlast": 1, "m_axis_tready": 1,
    "cfg_valid": 1, "cfg_enable": 6, "cfg_border": 1, "cfg_abs": 1,
    "cfg_shift": 5, "cfg_coeff": 144, "cfg_roi_x": 13, "cfg_roi_y": 13,
    "cfg_roi_width": 13, "cfg_roi_height": 13, "cfg_input_format": 2,
    "cfg_csc_mode": 3, "cfg_output_420": 1, "cfg_csc_coeff": 144,
    "cfg_csc_in_bias": 48, "cfg_csc_out_bias": 48, "cfg_hist_channel": 2,
    "hist_req_valid": 1, "hist_req_addr": 5, "hist_rsp_ready": 1,
    "cfg_s4_enable": 3, "cfg_temporal_alpha": 9, "cfg_motion_threshold": 9,
    "cfg_clip_limit": 9, "cfg_patch_size": 5, "cfg_norm_bf16": 1,
    "cfg_norm_mean": 72, "cfg_norm_inv_std": 72, "token_axis_tready": 1,
}
OUTPUTS = {
    "s_axis_tready": 1, "m_axis_tdata": 24, "m_axis_cvalid": 3,
    "m_axis_tvalid": 1, "m_axis_tuser": 1, "m_axis_tlast": 1,
    "cfg_ready": 1, "cfg_error": 1, "hist_req_ready": 1, "hist_rsp_valid": 1,
    "hist_rsp_data": 256, "hist_done": 1, "token_axis_tdata": 48,
    "token_axis_tvalid": 1, "token_axis_tuser": 1, "token_axis_tlast": 1,
    "token_axis_frame_last": 1, "token_axis_patch_id": 32,
    "token_axis_patch_x": 13, "token_axis_patch_y": 13,
    "token_axis_element": 8, "token_axis_keep": 3,
}
PARAMETERS = ("SRC_WIDTH", "SRC_HEIGHT", "DST_WIDTH", "DST_HEIGHT", "PIXEL_BITS",
              "CHANNELS", "PHASE_FRAC", "INTERPOLATION", "MAX_FRAME_PIXELS", "CLAHE_TILE")


def configuration(cfg):
    fields = {
        "cfg_enable": cfg["enable"], "cfg_border": cfg["border"], "cfg_abs": cfg["abs"],
        "cfg_shift": cfg["shift"], "cfg_coeff": pack(cfg["coeff"], 16),
        "cfg_roi_x": cfg["rx"], "cfg_roi_y": cfg["ry"],
        "cfg_roi_width": cfg["rw"], "cfg_roi_height": cfg["rh"],
        "cfg_input_format": cfg["format"], "cfg_csc_mode": cfg["mode"],
        "cfg_output_420": cfg["out420"], "cfg_csc_coeff": pack(cfg["csc"], 16),
        "cfg_csc_in_bias": pack(cfg["ib"], 16), "cfg_csc_out_bias": pack(cfg["ob"], 16),
        "cfg_hist_channel": cfg["hist"], "cfg_s4_enable": cfg["s4"],
        "cfg_temporal_alpha": cfg["alpha"], "cfg_motion_threshold": cfg["threshold"],
        "cfg_clip_limit": cfg["clip"], "cfg_patch_size": cfg["patch"],
        "cfg_norm_bf16": cfg["bf16"], "cfg_norm_mean": pack(cfg["means"], 24),
        "cfg_norm_inv_std": pack(cfg["reciprocals"], 24),
    }
    return "\n".join(f"{name} = {PORTS[name]}'h{value:x};" for name, value in fields.items())


def bound(params, cfg, n_tokens):
    sw, sh, dw, dh, _, _, _, _, _, tile = params
    w = dw if cfg["enable"] & 16 else (cfg["rw"] if cfg["enable"] & 1 else sw)
    h = dh if cfg["enable"] & 16 else (cfg["rh"] if cfg["enable"] & 1 else sh)
    tiles = ((w+tile-1)//tile) * ((h+tile-1)//tile)
    # Parent line scheduling/dividers, synchronous handshakes, and all CLAHE iterations.
    return (20000 + 512*(sw*sh+w*h) + 32*n_tokens +
            (3*tiles*(256*(32+16)+256*4+64*tile*tile) if cfg["s4"] & 2 else 0))


def hexfile(path, values, width):
    path.write_text("".join(f"{v:0{(width+3)//4}x}\n" for v in values), encoding="ascii")


def generate(root, cases):
    params = cases[0].params
    assert all(c.params == params for c in cases)
    sw, sh, dw, dh, bits, channels, frac, nearest, capacity, tile = params
    width = 24 if channels == 3 else (16 if bits == 10 else 8)
    ports = dict(PORTS, s_axis_tdata=width)
    outs = dict(OUTPUTS, m_axis_tdata=width)
    declarations = "\n".join(
        f"{kind} [{n-1}:0] {name}" + (" = 0;" if kind == "reg" else ";")
        for kind, mapping in (("reg", ports), ("wire", outs)) for name, n in mapping.items())
    legacy_outs = {k: v for k, v in outs.items() if not k.startswith("token_")}
    declarations += "\n" + "\n".join(f"wire [{n-1}:0] parent_{name};" for name, n in legacy_outs.items())
    legacy_inputs = [name for name in ports if not name.startswith("cfg_s4") and
                     name not in ("cfg_temporal_alpha", "cfg_motion_threshold", "cfg_clip_limit",
                                  "cfg_patch_size", "cfg_norm_bf16", "cfg_norm_mean",
                                  "cfg_norm_inv_std", "token_axis_tready")]
    top_inst = "swage_s4_top #(" + ",".join(f".{k}({v})" for k, v in zip(PARAMETERS, params)) + ") dut (\n"
    top_inst += ".clk(clk),.rst_n(rst_n)," + ",".join(f".{k}({k})" for k in (*ports, *outs)) + ");"
    parent_inst = "reference_swage_s3_top #(" + ",".join(
        f".{k}({v})" for k, v in zip(PARAMETERS[:8], params[:8])) + ") parent (\n"
    parent_inst += ".clk(clk),.rst_n(rst_n)," + ",".join(
        [f".{k}({k})" for k in legacy_inputs] +
        [f".{k}(parent_{k})" for k in legacy_outs]) + ");"
    public = "{" + ",".join(outs) + "}"
    parent_public = "{" + ",".join("parent_"+k for k in legacy_outs) + "}"
    legacy_public = "{" + ",".join(legacy_outs) + "}"
    program, ledger, max_pixels, max_tokens = [], [], 1, 1
    for ci, case in enumerate(cases):
        effective = Case("retained", params).cfg if case.reject else case.cfg
        program.append(f'case_name = "{case.name}"; reset_dut(); differential = 1;')
        if case.reject:
            program.append(configuration(effective) + "\nconfigure(0); differential = 0;")
            program.append(configuration(case.cfg) + "\nconfigure(1);")
        else:
            program.append(configuration(case.cfg) + f"\ndifferential = {int(case.cfg['s4']==0)}; configure(0);")
        if case.abort:
            program.append("abort_capture(); reset_dut();")
            program.append(configuration(case.cfg) + "\nconfigure(0);")
        history = None
        for fi in range(case.frames):
            if case.history_reconfiguration and fi == 2:
                rejected = dict(effective, alpha=257)
                program.append(configuration(rejected) + "\nconfigure(1);")
                program.append(configuration(effective))
            if case.history_reconfiguration and fi == 3:
                program.append(configuration(effective) + "\nconfigure(0);")
                history = None
            actual_case = Case(case.name, params, effective, seed=case.seed, pattern=case.pattern)
            source, source_masks = image(actual_case, fi)
            raw, masks, bins = legacy(source, source_masks, effective, params)
            untoned = temporal(raw, history, effective["alpha"], effective["threshold"]) if effective["s4"] & 1 else raw
            history = untoned if effective["s4"] & 1 else None
            final = clahe(untoned, tile, effective["clip"]) if effective["s4"] & 2 else untoned
            expected_pixels = pixels(final, masks, bits)
            expected_tokens = tokens(final, effective["patch"], effective["means"], effective["reciprocals"],
                                     effective["bf16"]) if effective["s4"] & 4 else []
            stem = f"case-{ci:04d}-frame-{fi:02d}"
            src = [pack(p, bits if channels == 1 else 8) | mask << 24
                   for row, mm in zip(source, source_masks) for p, mask in zip(row, mm)]
            for suffix, data, nbits in (("input", src, 27), ("pixels", expected_pixels, 29),
                                        ("tokens", expected_tokens or [0], 120),
                                        ("hist", [pack(bins[i:i+8], 32) for i in range(0, 256, 8)], 256)):
                hexfile(root / f"{stem}-{suffix}.hex", data, nbits)
            n, nt = len(expected_pixels), len(expected_tokens)
            max_pixels, max_tokens = max(max_pixels, n, sw*sh), max(max_tokens, nt)
            frame_bound = bound(params, effective, nt)
            program.append(f'run_frame("{stem}", {n}, {nt}, {frame_bound}, '
                           f'{int(case.stalls)}, {case.seed}, {int(case.busy)}, {int(bool(effective["enable"] & 32))});')
            ledger.append(dict(case=case.name, frame=fi, seed=case.seed, params=params,
                               cfg=effective, labels=case.labels, expected_pixels=n,
                               expected_tokens=nt, histogram_bins=256 if effective["enable"] & 32 else 0,
                               cycle_bound=frame_bound, comparison_files={
                                   s: f"{stem}-{s}.hex" for s in ("input", "pixels", "tokens", "hist")}))
    (root / "expectations.json").write_text(json.dumps(ledger, indent=2) + "\n")
    tb = r"""
module tb;
reg clk = 0;
reg rst_n = 0;
always #5 clk = ~clk;
@DECLARATIONS@
@TOP@
@PARENT@
reg differential = 0;
string case_name;
reg active = 0, last_pixel_seen = 0;
integer cycle = 0, start_cycle, deadline = 100000000, frame_index = 0;
integer pi = 0, ti = 0, np = 0, nt = 0;
integer pixel_stalls = 0, token_stalls = 0, input_gaps = 0;
integer config_accepts = 0, config_rejects = 0, busy_observed = 0;
integer hist_comparisons = 0, priority_observed = 0;
integer isolated_gaps = 0, sustained_gaps = 0;
integer isolated_stalls = 0, sustained_stalls = 0;
integer raster_stall_run = 0, token_stall_run = 0;
reg [26:0] source [0:@MAX_PIXELS@-1];
reg [28:0] expected_pixels [0:@MAX_PIXELS@-1];
reg [119:0] expected_tokens [0:@MAX_TOKENS@-1];
reg [255:0] expected_hist [0:31];
wire [23:0] raster_data = m_axis_tdata;
wire [28:0] raster = {m_axis_tlast,m_axis_tuser,m_axis_cvalid,raster_data};
wire [119:0] token = {token_axis_frame_last,token_axis_tlast,token_axis_tuser,
    token_axis_keep,token_axis_element,token_axis_patch_y,token_axis_patch_x,
    token_axis_patch_id,token_axis_tdata};
reg raster_stalled = 0, token_stalled = 0, hist_stalled = 0;
reg [28:0] previous_raster;
reg [119:0] previous_token;
reg [255:0] previous_hist;
task automatic reset_outputs;
begin
    if (@PUBLIC@ !== 0) $fatal(1,"RESET_XZ_OR_NONZERO %s t=%0t",case_name,$time);
end
endtask
initial begin
`ifndef VERILATOR
    #0;
    reset_outputs();
`endif
    #1; reset_outputs();
end
always @(negedge clk) if (!rst_n) reset_outputs();
always @(posedge clk) begin
    if (!rst_n) reset_outputs();
    else begin
        cycle = cycle + 1;
        if (cycle > deadline) $fatal(1,"DEADLOCK_BOUND %s pi=%0d/%0d ti=%0d/%0d",case_name,pi,np,ti,nt);
        if ((^@PUBLIC@) === 1'bx) $fatal(1,"ACTIVE_XZ %s cycle=%0d",case_name,cycle);
        if (differential && @LEGACY@ !== @PARENT_PUBLIC@)
            $fatal(1,"BYPASS_CYCLE_MISMATCH %s cycle=%0d actual=%h parent=%h",
                case_name,cycle,@LEGACY@,@PARENT_PUBLIC@);
        if (raster_stalled && (!m_axis_tvalid || raster !== previous_raster))
            $fatal(1,"PIXEL_STALL_CHANGED %s",case_name);
        if (token_stalled && (!token_axis_tvalid || token !== previous_token))
            $fatal(1,"TOKEN_STALL_CHANGED %s",case_name);
        if (hist_stalled && (!hist_rsp_valid || hist_rsp_data !== previous_hist))
            $fatal(1,"HIST_STALL_CHANGED %s",case_name);
        raster_stalled = m_axis_tvalid && !m_axis_tready;
        token_stalled = token_axis_tvalid && !token_axis_tready;
        hist_stalled = hist_rsp_valid && !hist_rsp_ready;
        previous_raster = raster; previous_token = token; previous_hist = hist_rsp_data;
        if (raster_stalled) pixel_stalls = pixel_stalls + 1;
        if (token_stalled) token_stalls = token_stalls + 1;
        if (raster_stalled) raster_stall_run = raster_stall_run + 1;
        if (token_stalled) token_stall_run = token_stall_run + 1;
        if (m_axis_tvalid && m_axis_tready && raster_stall_run) begin
            if(raster_stall_run==1) isolated_stalls=isolated_stalls+1;
            else sustained_stalls=sustained_stalls+1;
            raster_stall_run=0;
        end
        if (token_axis_tvalid && token_axis_tready && token_stall_run) begin
            if(token_stall_run==1) isolated_stalls=isolated_stalls+1;
            else sustained_stalls=sustained_stalls+1;
            token_stall_run=0;
        end
        if (active && m_axis_tvalid && m_axis_tready) begin
            if (pi >= np) $fatal(1,"EXTRA_PIXEL %s",case_name);
            if (raster !== expected_pixels[pi])
                $fatal(1,"PIXEL_MISMATCH %s frame=%0d index=%0d expected=%h actual=%h",
                    case_name,frame_index,pi,expected_pixels[pi],raster);
            pi = pi + 1;
            if (pi == np) last_pixel_seen = 1;
        end
        if (active && token_axis_tvalid && token_axis_tready) begin
            if (!last_pixel_seen) $fatal(1,"TOKEN_BEFORE_RASTER_DRAIN %s",case_name);
            if (ti >= nt) $fatal(1,"EXTRA_TOKEN %s",case_name);
            if (token !== expected_tokens[ti])
                $fatal(1,"TOKEN_MISMATCH %s frame=%0d index=%0d expected=%h actual=%h",
                    case_name,frame_index,ti,expected_tokens[ti],token);
            ti = ti + 1;
        end
    end
end
task automatic reset_dut;
begin
    @(negedge clk); #1; rst_n = 0; active = 0; cfg_valid = 0;
    s_axis_tvalid = 0; hist_req_valid = 0; hist_rsp_ready = 0;
    m_axis_tready = 0; token_axis_tready = 0;
    raster_stalled = 0; token_stalled = 0; hist_stalled = 0;
    raster_stall_run = 0; token_stall_run = 0;
    #1; reset_outputs();
    repeat (2) @(negedge clk);
    rst_n = 1; m_axis_tready = 1; token_axis_tready = 1;
    repeat(4) begin
        @(negedge clk);
        if(m_axis_tvalid || token_axis_tvalid || hist_rsp_valid || hist_done || cfg_error)
            $fatal(1,"RESET_RELEASE_PHANTOM %s",case_name);
    end
end
endtask
task automatic configure(input integer reject);
begin
    while (!cfg_ready) @(negedge clk);
    cfg_valid = 1; s_axis_tvalid = 1;
    @(posedge clk);
    if (!cfg_ready || s_axis_tready) $fatal(1,"CONFIG_PRIORITY %s",case_name);
    #1;
    if (cfg_error !== (reject != 0)) $fatal(1,"CONFIG_ERROR %s expected=%0d actual=%b",case_name,reject,cfg_error);
    if (reject) config_rejects = config_rejects + 1;
    else config_accepts = config_accepts + 1;
    @(negedge clk); cfg_valid = 0; s_axis_tvalid = 0;
    @(posedge clk); #1;
    if (cfg_error !== 0) $fatal(1,"CONFIG_ERROR_NOT_PULSE %s",case_name);
    @(negedge clk);
end
endtask
task automatic abort_capture;
integer i;
begin
    for (i=0;i<@SW@;i=i+1) begin
        @(negedge clk); s_axis_tvalid=1; s_axis_tdata=i;
        s_axis_cvalid=@MASK@; s_axis_tuser=(i==0); s_axis_tlast=(i==@SW@-1);
        @(posedge clk);
        while (!s_axis_tready) @(posedge clk);
    end
    @(negedge clk); #1; rst_n=0; s_axis_tvalid=0;
    #1; reset_outputs();
end
endtask
task automatic run_frame(input string stem, input integer pixel_count, token_count,
    cycle_limit, stalls, seed, busy, histogram);
integer si, k, si_gap, addr;
integer saved_ps, saved_ts, saved_gaps, saved_busy;
reg final_p_stalled, final_t_stalled;
begin
    $readmemh({stem,"-input.hex"},source,0,@SW@*@SH@-1);
    $readmemh({stem,"-pixels.hex"},expected_pixels,0,pixel_count-1);
    if (token_count) $readmemh({stem,"-tokens.hex"},expected_tokens,0,token_count-1);
    $readmemh({stem,"-hist.hex"},expected_hist,0,31);
    np=pixel_count; nt=token_count; pi=0; ti=0; last_pixel_seen=0; active=1;
    start_cycle=cycle; deadline=cycle+cycle_limit;
    saved_ps=pixel_stalls; saved_ts=token_stalls; saved_gaps=input_gaps; saved_busy=busy_observed;
    final_p_stalled=0; final_t_stalled=0;
    fork
        begin : source_driver
            for (si=0;si<@SW@*@SH@;si=si+1) begin
                @(negedge clk);
                s_axis_tvalid=0;
                si_gap = !stalls ? 0 : ((si+seed)%7==0 ? 3 : ((si+seed)%11==0 ? 1 : 0));
                repeat (si_gap) begin input_gaps=input_gaps+1; @(negedge clk); end
                s_axis_tdata=source[si][23:0]; s_axis_cvalid=source[si][26:24];
                s_axis_tuser=(si==0); s_axis_tlast=(si%@SW@==@SW@-1); s_axis_tvalid=1;
                @(posedge clk);
                while (!s_axis_tready) @(posedge clk);
                if(si_gap==1) isolated_gaps=isolated_gaps+1;
                if(si_gap>1) sustained_gaps=sustained_gaps+1;
            end
            @(negedge clk); s_axis_tvalid=0;
        end
        begin : sink_driver
            k=0;
            while (pi<np || ti<nt) begin
                @(negedge clk); k=k+1;
                m_axis_tready=!stalls || (k%19>=4 && k%31!=7);
                token_axis_tready=!stalls || (k%23>=5 && k%29!=11);
                if (stalls && pi==np-1 && m_axis_tvalid && !final_p_stalled) begin
                    m_axis_tready=0; repeat(4) @(negedge clk);
                    if (!m_axis_tvalid || pi!=np-1) $fatal(1,"FINAL_PIXEL_STALL_NOT_HELD %s",case_name);
                    if (cfg_ready) $fatal(1,"CONFIG_READY_DURING_FINAL_PIXEL_STALL %s",case_name);
                    final_p_stalled=1; m_axis_tready=1;
                end
                if (stalls && nt && ti==nt-1 && token_axis_tvalid && !final_t_stalled) begin
                    token_axis_tready=0; repeat(4) @(negedge clk);
                    if (!token_axis_tvalid || ti!=nt-1 || !token_axis_frame_last)
                        $fatal(1,"FINAL_TOKEN_STALL_NOT_HELD %s",case_name);
                    if (cfg_ready) $fatal(1,"CONFIG_READY_DURING_FINAL_TOKEN_STALL %s",case_name);
                    final_t_stalled=1; token_axis_tready=1;
                end
            end
            @(negedge clk); m_axis_tready=1; token_axis_tready=1;
        end
        begin : busy_driver
            if (busy) begin
                // Preserve output reservation while sampling the public busy condition.
                wait(s_axis_tready);
                @(negedge clk); cfg_valid=1;
                repeat(2) begin
                    @(posedge clk);
                    if (cfg_ready || cfg_error) $fatal(1,"BUSY_CONFIG_ACCEPTED %s",case_name);
                    busy_observed=busy_observed+1;
                end
                @(negedge clk); cfg_valid=0;
            end
        end
    join
    while (!cfg_ready) @(negedge clk);
    // Continue comparing to reject trailing duplicate beats before the next frame.
    repeat(12) @(negedge clk);
    if (pi!=np || ti!=nt) $fatal(1,"STREAM_LENGTH %s",case_name);
    if (stalls && !final_p_stalled) $fatal(1,"MISSING_FINAL_PIXEL_STALL %s",case_name);
    if (stalls && nt && !final_t_stalled) $fatal(1,"MISSING_FINAL_TOKEN_STALL %s",case_name);
    if (busy && busy_observed==saved_busy) $fatal(1,"MISSING_BUSY_CONFIG %s",case_name);
    if (histogram) begin
        if (!hist_done) $fatal(1,"HIST_DONE_MISSING %s",case_name);
        for(addr=0;addr<32;addr=addr+1) begin
            @(negedge clk); hist_req_valid=1; hist_req_addr=addr; hist_rsp_ready=0;
            if(addr==0) begin
                s_axis_tvalid=1; cfg_valid=1;
                #1;
                if(cfg_ready || s_axis_tready) $fatal(1,"HIST_REQUEST_PRIORITY %s",case_name);
                priority_observed=priority_observed+1;
            end
            @(posedge clk);
            while(!hist_req_ready) @(posedge clk);
            @(negedge clk); hist_req_valid=0; s_axis_tvalid=0; cfg_valid=0;
            while(!hist_rsp_valid) @(negedge clk);
            repeat(3) @(negedge clk);
            if(cfg_ready || !hist_done || hist_req_ready)
                $fatal(1,"HIST_RESERVATION_LIFETIME %s",case_name);
            if(hist_rsp_data !== expected_hist[addr])
                $fatal(1,"HISTOGRAM_MISMATCH %s address=%0d expected=%h actual=%h",
                    case_name,addr,expected_hist[addr],hist_rsp_data);
            hist_rsp_ready=1;
            @(posedge clk);
            if(!hist_rsp_valid) $fatal(1,"HIST_RESPONSE_DISAPPEARED %s",case_name);
            hist_comparisons=hist_comparisons+8;
            @(negedge clk); hist_rsp_ready=0;
        end
    end
    $display("FRAME_RESULT %0d %0d %0d %0d %0d %0d %0d %0d %0d",
        frame_index,pi,ti,cycle-start_cycle,pixel_stalls-saved_ps,token_stalls-saved_ts,
        input_gaps-saved_gaps,final_p_stalled,final_t_stalled);
    frame_index=frame_index+1; active=0; deadline=cycle+100000000;
end
endtask
initial begin
    #2;
    @PROGRAM@
    $display("CONTROL_RESULT %0d %0d %0d %0d %0d %0d %0d %0d %0d",
        config_accepts,config_rejects,busy_observed,hist_comparisons,priority_observed,
        isolated_gaps,sustained_gaps,isolated_stalls,sustained_stalls);
    $finish;
end
endmodule
"""
    substitutions = {
        "DECLARATIONS": declarations, "TOP": top_inst, "PARENT": parent_inst,
        "PUBLIC": public, "LEGACY": legacy_public, "PARENT_PUBLIC": parent_public,
        "MAX_PIXELS": str(max_pixels), "MAX_TOKENS": str(max_tokens),
        "PROGRAM": "\n".join(program), "SW": str(sw), "SH": str(sh),
        "MASK": "7" if channels == 3 else "1",
    }
    for name, value in substitutions.items():
        tb = tb.replace("@"+name+"@", value)
    (root / "tb.sv").write_text(tb, encoding="ascii")
    return ledger


def reference_sources(project, output):
    result = []
    for path in sorted((project / "swage_s3_reference/rtl").glob("*.sv")):
        # Module renaming is solely namespace isolation for literal differential comparison.
        text = path.read_text()
        import re
        text = re.sub(r"\bswage_(s[123]_[A-Za-z0-9_]+)\b", r"reference_swage_\1", text)
        target = output / ("reference_" + path.name)
        target.write_text(text, encoding="ascii")
        result.append(target)
    return result
