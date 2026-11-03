module swage_s4_top #(
    parameter integer SRC_WIDTH = 8,
    parameter integer SRC_HEIGHT = 8,
    parameter integer DST_WIDTH = 16,
    parameter integer DST_HEIGHT = 16,
    parameter integer PIXEL_BITS = 8,
    parameter integer CHANNELS = 3,
    parameter integer PHASE_FRAC = 8,
    parameter integer INTERPOLATION = 0,
    parameter integer MAX_FRAME_PIXELS = 4096,
    parameter integer CLAHE_TILE = 16
) (
    input wire clk, rst_n,
    input wire [DATA_WIDTH-1:0] s_axis_tdata,
    input wire [2:0] s_axis_cvalid,
    input wire s_axis_tvalid, s_axis_tuser, s_axis_tlast,
    output wire s_axis_tready,
    output wire [DATA_WIDTH-1:0] m_axis_tdata,
    output wire [2:0] m_axis_cvalid,
    output wire m_axis_tvalid, m_axis_tuser, m_axis_tlast,
    input wire m_axis_tready,
    input wire cfg_valid,
    output wire cfg_ready, cfg_error,
    input wire [5:0] cfg_enable,
    input wire cfg_border, cfg_abs,
    input wire [4:0] cfg_shift,
    input wire [143:0] cfg_coeff,
    input wire [12:0] cfg_roi_x, cfg_roi_y, cfg_roi_width, cfg_roi_height,
    input wire [1:0] cfg_input_format,
    input wire [2:0] cfg_csc_mode,
    input wire cfg_output_420,
    input wire [143:0] cfg_csc_coeff,
    input wire [47:0] cfg_csc_in_bias, cfg_csc_out_bias,
    input wire [1:0] cfg_hist_channel,
    input wire hist_req_valid,
    input wire [4:0] hist_req_addr,
    output wire hist_req_ready,
    output wire hist_rsp_valid,
    output wire [255:0] hist_rsp_data,
    input wire hist_rsp_ready,
    output wire hist_done,
    input wire [2:0] cfg_s4_enable,
    input wire [8:0] cfg_temporal_alpha, cfg_motion_threshold, cfg_clip_limit,
    input wire [4:0] cfg_patch_size,
    input wire cfg_norm_bf16,
    input wire [71:0] cfg_norm_mean, cfg_norm_inv_std,
    output wire [47:0] token_axis_tdata,
    output wire token_axis_tvalid, token_axis_tuser, token_axis_tlast,
    output wire token_axis_frame_last,
    output wire [31:0] token_axis_patch_id,
    output wire [12:0] token_axis_patch_x, token_axis_patch_y,
    output wire [7:0] token_axis_element,
    output wire [2:0] token_axis_keep,
    input wire token_axis_tready
);
    localparam integer DATA_WIDTH = (PIXEL_BITS == 10 && CHANNELS == 1) ? 16 :
        ((PIXEL_BITS == 8 && CHANNELS == 3) ? 24 : 8);
    localparam integer SW = SRC_WIDTH >= 1 && SRC_WIDTH <= 4096 ? SRC_WIDTH : 1;
    localparam integer SH = SRC_HEIGHT >= 1 && SRC_HEIGHT <= 4096 ? SRC_HEIGHT : 1;
    localparam integer DW = DST_WIDTH >= 1 && DST_WIDTH <= 4096 ? DST_WIDTH : 1;
    localparam integer DH = DST_HEIGHT >= 1 && DST_HEIGHT <= 4096 ? DST_HEIGHT : 1;
    localparam integer CAP = MAX_FRAME_PIXELS >= 1 && MAX_FRAME_PIXELS <= 65536 ?
        MAX_FRAME_PIXELS : 1;
    localparam integer TILE = CLAHE_TILE == 8 ? 8 : 16;
    localparam [2:0] IDLE=0, CAPTURE=1, TONE_START=2, TONE=3,
        PIXELS=4, TOKEN_START=5, TOKENS=6;
    localparam [2:0] CAP_WAIT=0, CAP_HISTORY=1, CAP_INPUT=2, CAP_RESULT=3, CAP_COMMIT=4;
    localparam [1:0] PIX_REQUEST=0, PIX_RESPONSE=1, PIX_SEND=2;
    reg [2:0] phase, capture_state, s4_enable;
    reg [1:0] pixel_state;
    reg [8:0] alpha_reg, threshold_reg, clip_reg;
    reg [4:0] patch_reg;
    reg bf16_reg, history_valid;
    reg [71:0] mean_reg, reciprocal_reg;
    reg [12:0] width_reg, height_reg, pixel_x, pixel_y;
    reg [16:0] capture_count, pixel_count;
    reg [24:0] source_count;
    reg [16:0] area_reg;
    reg [23:0] capture_data, history_data, temporal_result, pixel_data;
    reg [2:0] capture_mask, pixel_mask;
    wire enhanced = |s4_enable;
    wire [12:0] requested_width = cfg_enable[4] ? 13'(DW) :
        (cfg_enable[0] ? cfg_roi_width : 13'(SW));
    wire [12:0] requested_height = cfg_enable[4] ? 13'(DH) :
        (cfg_enable[0] ? cfg_roi_height : 13'(SH));
    wire [25:0] requested_area = {13'd0,requested_width} * {13'd0,requested_height};
    wire [1:0] requested_output_format = cfg_enable[3] ?
        (cfg_input_format != 0 ? 2'd0 : (cfg_output_420 ? 2'd2 : 2'd1)) : cfg_input_format;
    wire [13:0] roi_right = {1'b0,cfg_roi_x}+{1'b0,cfg_roi_width};
    wire [13:0] roi_bottom = {1'b0,cfg_roi_y}+{1'b0,cfg_roi_height};
    wire illegal_legacy = cfg_input_format == 3 ||
        (CHANNELS != 3 && cfg_input_format != 0) ||
        (cfg_enable[0] && (cfg_roi_width == 0 || cfg_roi_height == 0 ||
            roi_right > SW || roi_bottom > SH ||
            (cfg_input_format == 2 && (cfg_roi_x[0] || cfg_roi_y[0])))) ||
        (cfg_enable[1] && (CHANNELS != 3 || PIXEL_BITS != 8 || cfg_input_format != 0)) ||
        (cfg_enable[2] && (PIXEL_BITS != 8 || cfg_input_format != 0)) ||
        (cfg_enable[3] && (CHANNELS != 3 || cfg_csc_mode > 4 ||
            (cfg_csc_mode < 2 && cfg_input_format != 0) ||
            ((cfg_csc_mode == 2 || cfg_csc_mode == 3) && cfg_input_format == 0))) ||
        (cfg_enable[5] && ((CHANNELS == 1 && cfg_hist_channel != 0) ||
            (cfg_hist_channel == 3 && (CHANNELS != 3 || requested_output_format != 0))));
    wire illegal_s4 = |cfg_s4_enable && (
        PIXEL_BITS != 8 || CHANNELS != 3 || requested_output_format != 0 ||
        requested_area > CAP ||
        (cfg_s4_enable[0] && (cfg_temporal_alpha > 256 || cfg_motion_threshold > 256)) ||
        (cfg_s4_enable[1] && (cfg_clip_limit == 0 || cfg_clip_limit > TILE*TILE)) ||
        (cfg_s4_enable[2] && (!(cfg_patch_size == 8 || cfg_patch_size == 14 ||
            cfg_patch_size == 16) || cfg_norm_inv_std[23:0] == 0 ||
            cfg_norm_inv_std[47:24] == 0 || cfg_norm_inv_std[71:48] == 0)));
    wire legacy_cfg_ready, legacy_cfg_error, legacy_input_ready;
    wire [DATA_WIDTH-1:0] legacy_data;
    wire [2:0] legacy_mask;
    wire legacy_valid, legacy_sof, legacy_eol;
    wire allow_input = !enhanced || phase == IDLE ||
        (phase == CAPTURE && source_count < 25'(SW*SH));
    wire use_history = s4_enable[0] && history_valid;
    wire legacy_output_ready;
    wire begin_enhanced = enhanced && phase == IDLE && !cfg_valid &&
        legacy_cfg_ready && s_axis_tvalid;

    reg mem_wr_valid, mem_wr_history, mem_rd_valid, mem_rd_history, mem_rsp_ready;
    reg [16:0] mem_wr_addr, mem_rd_addr;
    reg [23:0] mem_wr_data;
    reg [2:0] mem_wr_mask;
    wire mem_rd_ready, mem_rsp_valid;
    wire [23:0] mem_rsp_data;
    wire [2:0] mem_rsp_mask;
    wire temporal_ready, temporal_valid;
    wire [23:0] temporal_data;
    wire tone_busy, tone_done, tone_rd_valid, tone_rsp_ready, tone_wr_valid;
    wire [16:0] tone_rd_addr, tone_wr_addr;
    wire [23:0] tone_wr_data;
    wire [2:0] tone_wr_mask;
    wire patch_busy, patch_done, patch_rd_valid, patch_rsp_ready;
    wire [16:0] patch_rd_addr;
    wire sample_valid, sample_ready, sample_first, sample_last, sample_final;
    wire [23:0] sample_data;
    wire [2:0] sample_keep;
    wire [31:0] sample_id;
    wire [12:0] sample_x, sample_y;
    wire [7:0] sample_element;

    assign cfg_ready = rst_n && legacy_cfg_ready && phase == IDLE;
    assign cfg_error = rst_n && legacy_cfg_error;
    assign s_axis_tready = rst_n && allow_input && legacy_input_ready;
    assign legacy_output_ready = !enhanced ? m_axis_tready :
        (phase == CAPTURE && capture_state == CAP_WAIT && (!use_history || mem_rd_ready));
    assign m_axis_tvalid = rst_n && (enhanced ?
        (phase == PIXELS && pixel_state == PIX_SEND) : legacy_valid);
    assign m_axis_tdata = !rst_n ? {DATA_WIDTH{1'b0}} :
        (enhanced ? (m_axis_tvalid ? DATA_WIDTH'(pixel_data) : {DATA_WIDTH{1'b0}}) : legacy_data);
    assign m_axis_cvalid = !rst_n ? 3'd0 :
        (enhanced ? (m_axis_tvalid ? pixel_mask : 3'd0) : legacy_mask);
    assign m_axis_tuser = m_axis_tvalid && (enhanced ?
        (pixel_count == 0) : legacy_sof);
    assign m_axis_tlast = m_axis_tvalid && (enhanced ?
        (pixel_x == width_reg-13'd1) : legacy_eol);

    swage_s3_top #(
        .SRC_WIDTH(SRC_WIDTH),.SRC_HEIGHT(SRC_HEIGHT),
        .DST_WIDTH(DST_WIDTH),.DST_HEIGHT(DST_HEIGHT),
        .PIXEL_BITS(PIXEL_BITS),.CHANNELS(CHANNELS),
        .PHASE_FRAC(PHASE_FRAC),.INTERPOLATION(INTERPOLATION)
    ) u_legacy (
        .clk(clk),.rst_n(rst_n),.s_axis_tdata(s_axis_tdata),.s_axis_cvalid(s_axis_cvalid),
        .s_axis_tvalid(s_axis_tvalid && allow_input),.s_axis_tuser(s_axis_tuser),
        .s_axis_tlast(s_axis_tlast),.s_axis_tready(legacy_input_ready),
        .m_axis_tdata(legacy_data),.m_axis_cvalid(legacy_mask),.m_axis_tvalid(legacy_valid),
        .m_axis_tuser(legacy_sof),.m_axis_tlast(legacy_eol),.m_axis_tready(legacy_output_ready),
        .cfg_valid(cfg_valid && phase == IDLE),.cfg_ready(legacy_cfg_ready),
        .cfg_error(legacy_cfg_error),.cfg_enable(cfg_enable),
        .cfg_border(cfg_border),.cfg_abs(cfg_abs),.cfg_shift(cfg_shift),.cfg_coeff(cfg_coeff),
        .cfg_roi_x(cfg_roi_x),.cfg_roi_y(cfg_roi_y),
        .cfg_roi_width(cfg_roi_width),.cfg_roi_height(cfg_roi_height),
        .cfg_input_format(illegal_s4 ? 2'd3 : cfg_input_format),
        .cfg_csc_mode(cfg_csc_mode),.cfg_output_420(cfg_output_420),
        .cfg_csc_coeff(cfg_csc_coeff),.cfg_csc_in_bias(cfg_csc_in_bias),
        .cfg_csc_out_bias(cfg_csc_out_bias),.cfg_hist_channel(cfg_hist_channel),
        .hist_req_valid(hist_req_valid),.hist_req_addr(hist_req_addr),
        .hist_req_ready(hist_req_ready),.hist_rsp_valid(hist_rsp_valid),
        .hist_rsp_data(hist_rsp_data),.hist_rsp_ready(hist_rsp_ready),.hist_done(hist_done)
    );
    swage_s4_frame_store #(.MAX_FRAME_PIXELS(CAP)) u_frame_store (
        .clk(clk),.rst_n(rst_n),.wr_valid(mem_wr_valid),.wr_history(mem_wr_history),
        .wr_addr(mem_wr_addr),.wr_data(mem_wr_data),.wr_mask(mem_wr_mask),
        .rd_valid(mem_rd_valid),.rd_ready(mem_rd_ready),.rd_history(mem_rd_history),
        .rd_addr(mem_rd_addr),.rsp_valid(mem_rsp_valid),.rsp_ready(mem_rsp_ready),
        .rsp_data(mem_rsp_data),.rsp_mask(mem_rsp_mask)
    );
    swage_s4_temporal u_temporal (
        .clk(clk),.rst_n(rst_n),.s_valid(phase == CAPTURE && capture_state == CAP_INPUT),
        .s_ready(temporal_ready),.s_data(capture_data),.h_data(history_data),
        .history_valid(use_history),.temporal_enable(s4_enable[0]),
        .alpha(alpha_reg),.threshold(threshold_reg),.m_valid(temporal_valid),
        .m_ready(phase == CAPTURE && capture_state == CAP_RESULT),.m_data(temporal_data)
    );
    swage_s4_clahe #(.CLAHE_TILE(TILE)) u_clahe (
        .clk(clk),.rst_n(rst_n),.start(phase == TONE_START),
        .width(width_reg),.height(height_reg),.clip_limit(clip_reg),
        .busy(tone_busy),.done(tone_done),.rd_valid(tone_rd_valid),
        .rd_ready(mem_rd_ready && phase == TONE),.rd_addr(tone_rd_addr),
        .rsp_valid(mem_rsp_valid && phase == TONE),.rsp_ready(tone_rsp_ready),
        .rsp_data(mem_rsp_data),.rsp_mask(mem_rsp_mask),
        .wr_valid(tone_wr_valid),.wr_addr(tone_wr_addr),.wr_data(tone_wr_data),
        .wr_mask(tone_wr_mask)
    );
    swage_s4_patch_tokenizer u_patch_tokenizer (
        .clk(clk),.rst_n(rst_n),.start(phase == TOKEN_START),
        .width(width_reg),.height(height_reg),.patch_size(patch_reg),
        .busy(patch_busy),.done(patch_done),.rd_valid(patch_rd_valid),
        .rd_ready(mem_rd_ready && phase == TOKENS),.rd_addr(patch_rd_addr),
        .rsp_valid(mem_rsp_valid && phase == TOKENS),.rsp_ready(patch_rsp_ready),
        .rsp_data(mem_rsp_data),.sample_valid(sample_valid),.sample_ready(sample_ready),
        .sample_data(sample_data),.sample_keep(sample_keep),
        .sample_first(sample_first),.sample_last(sample_last),.sample_frame_last(sample_final),
        .sample_id(sample_id),.sample_x(sample_x),.sample_y(sample_y),.sample_element(sample_element)
    );
    swage_s4_normalizer u_normalizer (
        .clk(clk),.rst_n(rst_n),.s_valid(sample_valid),.s_ready(sample_ready),
        .s_data(sample_data),.s_keep(sample_keep),.s_first(sample_first),
        .s_last(sample_last),.s_frame_last(sample_final),.s_id(sample_id),
        .s_x(sample_x),.s_y(sample_y),.s_element(sample_element),
        .mean(mean_reg),.inv_std(reciprocal_reg),.bf16(bf16_reg),
        .m_valid(token_axis_tvalid),.m_ready(token_axis_tready),
        .m_data(token_axis_tdata),.m_keep(token_axis_keep),.m_first(token_axis_tuser),
        .m_last(token_axis_tlast),.m_frame_last(token_axis_frame_last),
        .m_id(token_axis_patch_id),.m_x(token_axis_patch_x),.m_y(token_axis_patch_y),
        .m_element(token_axis_element)
    );
    always @* begin
        mem_wr_valid = 0; mem_wr_history = 0; mem_wr_addr = 0;
        mem_wr_data = 0; mem_wr_mask = 0;
        mem_rd_valid = 0; mem_rd_history = 0; mem_rd_addr = 0; mem_rsp_ready = 0;
        if (rst_n) begin
            case (phase)
                CAPTURE: begin
                    mem_wr_addr = capture_count; mem_wr_mask = capture_mask;
                    if (capture_state == CAP_WAIT && use_history) begin
                        mem_rd_valid = legacy_valid;
                        mem_rd_history = 1; mem_rd_addr = capture_count;
                    end
                    if (capture_state == CAP_HISTORY) mem_rsp_ready = 1;
                    if (capture_state == CAP_RESULT) begin
                        mem_wr_valid = temporal_valid; mem_wr_data = temporal_data;
                    end
                    if (capture_state == CAP_COMMIT && s4_enable[0]) begin
                        mem_wr_valid = 1; mem_wr_history = 1; mem_wr_data = temporal_result;
                    end
                end
                TONE: begin
                    mem_rd_valid = tone_rd_valid; mem_rd_addr = tone_rd_addr;
                    mem_rsp_ready = tone_rsp_ready;
                    mem_wr_valid = tone_wr_valid; mem_wr_addr = tone_wr_addr;
                    mem_wr_data = tone_wr_data; mem_wr_mask = tone_wr_mask;
                end
                PIXELS: begin
                    mem_rd_valid = pixel_state == PIX_REQUEST; mem_rd_addr = pixel_count;
                    mem_rsp_ready = pixel_state == PIX_RESPONSE;
                end
                TOKENS: begin
                    mem_rd_valid = patch_rd_valid; mem_rd_addr = patch_rd_addr;
                    mem_rsp_ready = patch_rsp_ready;
                end
                default: begin end
            endcase
        end
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase <= IDLE; capture_state <= CAP_WAIT; pixel_state <= PIX_REQUEST;
            s4_enable <= 0; alpha_reg <= 128; threshold_reg <= 16; clip_reg <= 4;
            patch_reg <= 16; bf16_reg <= 0; history_valid <= 0;
            mean_reg <= {3{24'd32640}}; reciprocal_reg <= {3{24'd512}};
            width_reg <= 13'(DW); height_reg <= 13'(DH); area_reg <= 17'(DW*DH);
            capture_count <= 0; pixel_count <= 0; pixel_x <= 0; pixel_y <= 0;
            source_count <= 0;
            capture_data <= 0; capture_mask <= 0; history_data <= 0;
            temporal_result <= 0; pixel_data <= 0; pixel_mask <= 0;
        end else begin
            if (enhanced && s_axis_tvalid && s_axis_tready)
                source_count <= source_count+25'd1;
            if (cfg_valid && cfg_ready && !illegal_legacy && !illegal_s4) begin
                s4_enable <= cfg_s4_enable;
                alpha_reg <= cfg_temporal_alpha; threshold_reg <= cfg_motion_threshold;
                clip_reg <= cfg_clip_limit; patch_reg <= cfg_patch_size;
                bf16_reg <= cfg_norm_bf16; mean_reg <= cfg_norm_mean;
                reciprocal_reg <= cfg_norm_inv_std;
                width_reg <= requested_width; height_reg <= requested_height;
                area_reg <= requested_area[16:0]; history_valid <= 0;
            end
            case (phase)
                IDLE: if (begin_enhanced) begin
                    phase <= CAPTURE; capture_state <= CAP_WAIT; capture_count <= 0;
                    source_count <= 0;
                end
                CAPTURE: case (capture_state)
                    CAP_WAIT: if (legacy_valid && legacy_output_ready) begin
                        capture_data <= 24'(legacy_data); capture_mask <= legacy_mask;
                        if (use_history) capture_state <= CAP_HISTORY;
                        else begin history_data <= 0; capture_state <= CAP_INPUT; end
                    end
                    CAP_HISTORY: if (mem_rsp_valid) begin
                        history_data <= mem_rsp_data; capture_state <= CAP_INPUT;
                    end
                    CAP_INPUT: if (temporal_ready) capture_state <= CAP_RESULT;
                    CAP_RESULT: if (temporal_valid) begin
                        temporal_result <= temporal_data; capture_state <= CAP_COMMIT;
                    end
                    CAP_COMMIT: begin
                        if (capture_count == area_reg-17'd1) begin
                            history_valid <= s4_enable[0];
                            pixel_count <= 0; pixel_x <= 0; pixel_y <= 0;
                            pixel_state <= PIX_REQUEST;
                            if (s4_enable[1]) phase <= TONE_START;
                            else phase <= PIXELS;
                        end else begin capture_count <= capture_count+17'd1; capture_state <= CAP_WAIT; end
                    end
                    default: capture_state <= CAP_WAIT;
                endcase
                TONE_START: phase <= TONE;
                TONE: if (tone_done) phase <= PIXELS;
                PIXELS: case (pixel_state)
                    PIX_REQUEST: if (mem_rd_ready) pixel_state <= PIX_RESPONSE;
                    PIX_RESPONSE: if (mem_rsp_valid) begin
                        pixel_data <= mem_rsp_data; pixel_mask <= mem_rsp_mask; pixel_state <= PIX_SEND;
                    end
                    PIX_SEND: if (m_axis_tready) begin
                        if (pixel_count == area_reg-17'd1) begin
                            if (s4_enable[2]) phase <= TOKEN_START;
                            else phase <= IDLE;
                        end else begin
                            pixel_count <= pixel_count+17'd1;
                            if (pixel_x == width_reg-13'd1) begin
                                pixel_x <= 0; pixel_y <= pixel_y+13'd1;
                            end else pixel_x <= pixel_x+13'd1;
                            pixel_state <= PIX_REQUEST;
                        end
                    end
                    default: pixel_state <= PIX_REQUEST;
                endcase
                TOKEN_START: phase <= TOKENS;
                TOKENS: if (token_axis_tvalid && token_axis_tready && token_axis_frame_last)
                    phase <= IDLE;
                default: phase <= IDLE;
            endcase
        end
    end
    generate if (SRC_WIDTH < 1 || SRC_WIDTH > 4096 || SRC_HEIGHT < 1 || SRC_HEIGHT > 4096 ||
        DST_WIDTH < 1 || DST_WIDTH > 4096 || DST_HEIGHT < 1 || DST_HEIGHT > 4096 ||
        !((PIXEL_BITS == 8 && CHANNELS == 1) || (PIXEL_BITS == 10 && CHANNELS == 1) ||
          (PIXEL_BITS == 8 && CHANNELS == 3)) ||
        !(PHASE_FRAC == 8 || PHASE_FRAC == 16) || !(INTERPOLATION == 0 || INTERPOLATION == 1) ||
        MAX_FRAME_PIXELS < 1 || MAX_FRAME_PIXELS > 65536 ||
        !(CLAHE_TILE == 8 || CLAHE_TILE == 16)) begin : invalid_parameters
        initial $fatal(1,"SWAGE_S4 PARAMETER ERROR");
    end endgenerate
endmodule
