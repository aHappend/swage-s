module swage_s5_top #(
    parameter integer SRC_WIDTH = 8,
    parameter integer SRC_HEIGHT = 8,
    parameter integer DST_WIDTH = 16,
    parameter integer DST_HEIGHT = 16,
    parameter integer PIXEL_BITS = 8,
    parameter integer CHANNELS = 3,
    parameter integer PHASE_FRAC = 8,
    parameter integer INTERPOLATION = 0,
    parameter integer MAX_FRAME_PIXELS = 4096,
    parameter integer CLAHE_TILE = 16,
    parameter integer S5_MAX_WIDTH = SRC_WIDTH > DST_WIDTH ? SRC_WIDTH : DST_WIDTH,
    parameter integer S5_NUM_CLASSES = 4,
    parameter integer S5_MAX_TOPK = 16,
    parameter integer S5_FIFO_DEPTH = 2
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
    input wire token_axis_tready,
    input wire [1:0] cfg_s5_enable,
    input wire [15:0] cfg_s5_threshold,
    input wire [4:0] cfg_s5_topk,
    input wire weight_valid,
    output wire weight_ready,
    output reg weight_error,
    input wire [15:0] weight_addr,
    input wire [31:0] weight_data,
    output wire [87:0] det_axis_tdata,
    output wire det_axis_tvalid,
    input wire det_axis_tready,
    output wire det_axis_tuser, det_axis_tlast, det_axis_keep,
    output wire [7:0] det_axis_rank, det_axis_count,
    output wire [31:0] det_axis_frame_id,
    output wire [47:0] roi_axis_tdata,
    output wire roi_axis_tvalid, roi_axis_tuser, roi_axis_tlast, roi_axis_frame_last,
    output wire [31:0] roi_axis_patch_id,
    output wire [12:0] roi_axis_patch_x, roi_axis_patch_y,
    output wire [7:0] roi_axis_element,
    output wire [2:0] roi_axis_keep,
    output wire [7:0] roi_axis_det_rank,
    output wire [31:0] roi_axis_frame_id,
    input wire roi_axis_tready
);
    localparam integer DATA_WIDTH = (PIXEL_BITS == 10 && CHANNELS == 1) ? 16 :
        ((PIXEL_BITS == 8 && CHANNELS == 3) ? 24 : 8);
    localparam integer SW = SRC_WIDTH >= 1 && SRC_WIDTH <= 4096 ? SRC_WIDTH : 1;
    localparam integer SH = SRC_HEIGHT >= 1 && SRC_HEIGHT <= 4096 ? SRC_HEIGHT : 1;
    localparam integer DW = DST_WIDTH >= 1 && DST_WIDTH <= 4096 ? DST_WIDTH : 1;
    localparam integer DH = DST_HEIGHT >= 1 && DST_HEIGHT <= 4096 ? DST_HEIGHT : 1;
    localparam integer NC = S5_NUM_CLASSES >= 1 && S5_NUM_CLASSES <= 8 ? S5_NUM_CLASSES : 1;
    localparam integer K = S5_MAX_TOPK >= 1 && S5_MAX_TOPK <= 16 ? S5_MAX_TOPK : 1;
    localparam integer CAP = MAX_FRAME_PIXELS >= 1 && MAX_FRAME_PIXELS <= 65536 ? MAX_FRAME_PIXELS : 1;
    localparam integer TILE = CLAHE_TILE == 8 ? 8 : 16;
    localparam [2:0] RUN=0, DRAIN=1, SNAPSHOT=2, COPY_WAIT=3, FINISH=4, DELIVERY=5;
    reg [2:0] state;
    reg [1:0] enable_reg;
    reg [15:0] threshold_reg;
    reg [4:0] topk_reg, patch_reg;
    reg bf16_reg, frame_active, input_closed, final_stripe, descriptors_done;
    reg [71:0] mean_reg, reciprocal_reg;
    reg [12:0] width_reg, height_reg, raster_x, raster_y;
    reg [12:0] stripe_base_y, stripe_grid_y;
    reg [4:0] stripe_row, snapshot_index;
    reg [16:0] stripe_addr;
    reg [24:0] source_count;
    reg [31:0] frame_id;
    reg start_frame;
    reg [DATA_WIDTH-1:0] input_data [0:1];
    reg [2:0] input_keep [0:1];
    reg [1:0] input_sof, input_eol;
    reg input_rd_ptr, input_wr_ptr;
    reg [1:0] input_count;
    reg [431:0] dw_weights [0:7];
    reg [575:0] pw_weights [0:7];
    reg [383:0] biases [0:7];
    reg [59:0] shifts [0:7];

    wire [12:0] requested_width = cfg_enable[4] ? 13'(DW) :
        (cfg_enable[0] ? cfg_roi_width : 13'(SW));
    wire [12:0] requested_height = cfg_enable[4] ? 13'(DH) :
        (cfg_enable[0] ? cfg_roi_height : 13'(SH));
    wire [25:0] requested_area = {13'd0,requested_width}*{13'd0,requested_height};
    wire [1:0] requested_format = cfg_enable[3] ?
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
            (cfg_hist_channel == 3 && (CHANNELS != 3 || requested_format != 0))));
    wire illegal_patch = !(cfg_patch_size == 8 || cfg_patch_size == 14 || cfg_patch_size == 16) ||
        cfg_norm_inv_std[23:0] == 0 || cfg_norm_inv_std[47:24] == 0 ||
        cfg_norm_inv_std[71:48] == 0;
    wire illegal_s4 = |cfg_s4_enable && (
        PIXEL_BITS != 8 || CHANNELS != 3 || requested_format != 0 || requested_area > CAP ||
        (cfg_s4_enable[0] && (cfg_temporal_alpha > 256 || cfg_motion_threshold > 256)) ||
        (cfg_s4_enable[1] && (cfg_clip_limit == 0 || cfg_clip_limit > TILE*TILE)) ||
        (cfg_s4_enable[2] && illegal_patch));
    wire illegal_s5 = cfg_s5_topk == 0 || cfg_s5_topk > S5_MAX_TOPK ||
        (cfg_s5_enable[1] && (!cfg_s5_enable[0] || illegal_patch)) ||
        (cfg_s5_enable[0] && (PIXEL_BITS != 8 || CHANNELS != 3 || requested_format != 0));
    wire isp_cfg_ready, isp_cfg_error, isp_input_ready;
    wire [DATA_WIDTH-1:0] isp_data, branch_data;
    wire [2:0] isp_keep, branch_keep;
    wire isp_valid, isp_sof, isp_eol, isp_ready;
    wire branch_valid, branch_sof, branch_eol, branch_ready, fork_idle;
    wire input_permitted = !(cfg_valid && cfg_ready) && !(weight_valid && weight_ready) &&
        (!enable_reg[0] || !input_closed);
    wire source_transfer = rst_n && s_axis_tvalid && s_axis_tready;
    wire input_push = source_transfer && enable_reg[0];
    wire buffered_valid = rst_n && enable_reg[0] && input_count != 0;
    wire input_pop = buffered_valid && isp_input_ready;
    wire [DATA_WIDTH-1:0] source_data = enable_reg[0] ?
        input_data[input_rd_ptr] : s_axis_tdata;
    wire [2:0] source_keep = enable_reg[0] ? input_keep[input_rd_ptr] : s_axis_cvalid;
    wire source_sof = enable_reg[0] ? input_sof[input_rd_ptr] : s_axis_tuser;
    wire source_eol = enable_reg[0] ? input_eol[input_rd_ptr] : s_axis_tlast;
    wire source_valid = enable_reg[0] ? buffered_valid : s_axis_tvalid && input_permitted;
    wire branch_transfer = branch_valid && branch_ready;
    wire branch_last = raster_x == width_reg-13'd1 && raster_y == height_reg-13'd1;
    assign cfg_ready = rst_n && isp_cfg_ready && !frame_active && input_count == 0;
    assign cfg_error = rst_n && isp_cfg_error;
    assign weight_ready = rst_n && isp_cfg_ready && !frame_active && input_count == 0 && !cfg_valid;
    assign s_axis_tready = rst_n && input_permitted &&
        (enable_reg[0] ? input_count < 2 : isp_input_ready);

    always @(posedge clk) begin
        if (rst_n && input_push) begin
            input_data[input_wr_ptr] <= s_axis_tdata;
            input_keep[input_wr_ptr] <= s_axis_cvalid;
        end
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            input_sof <= 0; input_eol <= 0;
            input_rd_ptr <= 0; input_wr_ptr <= 0; input_count <= 0;
        end else begin
            if (input_push) begin
                input_sof[input_wr_ptr] <= s_axis_tuser;
                input_eol[input_wr_ptr] <= s_axis_tlast;
                input_wr_ptr <= !input_wr_ptr;
            end
            if (input_pop) input_rd_ptr <= !input_rd_ptr;
            case ({input_push,input_pop})
                2'b10: input_count <= input_count+2'd1;
                2'b01: input_count <= input_count-2'd1;
                default: begin end
            endcase
        end
    end

    wire [3:0] weight_layer = weight_addr[15:12];
    wire [1:0] weight_kind = weight_addr[11:10];
    wire [9:0] weight_index = weight_addr[9:0];
    reg [9:0] dw_count, pw_count, bias_count;
    reg valid_weight;
    always @* begin
        dw_count = 0; pw_count = 0; bias_count = 0;
        case (weight_layer)
            0: begin dw_count = 27; pw_count = 18; bias_count = 9; end
            1,2: begin dw_count = 27; pw_count = 9; bias_count = 6; end
            3: begin pw_count = 72; bias_count = 6; end
            4,5: begin dw_count = 54; pw_count = 36; bias_count = 12; end
            6: begin pw_count = 10'(6*NC); bias_count = 10'(NC); end
            7: begin pw_count = 24; bias_count = 4; end
            default: begin end
        endcase
        valid_weight = 0;
        case (weight_kind)
            0: valid_weight = weight_index < dw_count &&
                weight_data[31:8] == {24{weight_data[7]}};
            1: valid_weight = weight_index < pw_count &&
                weight_data[31:8] == {24{weight_data[7]}};
            2: valid_weight = weight_index < bias_count;
            3: valid_weight = weight_index < bias_count && weight_data[31:5] == 0;
        endcase
    end

    swage_s4_top #(
        .SRC_WIDTH(SRC_WIDTH),.SRC_HEIGHT(SRC_HEIGHT),.DST_WIDTH(DST_WIDTH),.DST_HEIGHT(DST_HEIGHT),
        .PIXEL_BITS(PIXEL_BITS),.CHANNELS(CHANNELS),.PHASE_FRAC(PHASE_FRAC),
        .INTERPOLATION(INTERPOLATION),.MAX_FRAME_PIXELS(MAX_FRAME_PIXELS),.CLAHE_TILE(CLAHE_TILE)
    ) u_isp (
        .clk(clk),.rst_n(rst_n),.s_axis_tdata(source_data),.s_axis_cvalid(source_keep),
        .s_axis_tvalid(source_valid),.s_axis_tuser(source_sof),
        .s_axis_tlast(source_eol),.s_axis_tready(isp_input_ready),
        .m_axis_tdata(isp_data),.m_axis_cvalid(isp_keep),.m_axis_tvalid(isp_valid),
        .m_axis_tuser(isp_sof),.m_axis_tlast(isp_eol),.m_axis_tready(isp_ready),
        .cfg_valid(cfg_valid && !frame_active),.cfg_ready(isp_cfg_ready),.cfg_error(isp_cfg_error),
        .cfg_enable(cfg_enable),.cfg_border(cfg_border),.cfg_abs(cfg_abs),.cfg_shift(cfg_shift),
        .cfg_coeff(cfg_coeff),.cfg_roi_x(cfg_roi_x),.cfg_roi_y(cfg_roi_y),
        .cfg_roi_width(cfg_roi_width),.cfg_roi_height(cfg_roi_height),
        .cfg_input_format(illegal_s5 ? 2'd3 : cfg_input_format),
        .cfg_csc_mode(cfg_csc_mode),.cfg_output_420(cfg_output_420),.cfg_csc_coeff(cfg_csc_coeff),
        .cfg_csc_in_bias(cfg_csc_in_bias),.cfg_csc_out_bias(cfg_csc_out_bias),
        .cfg_hist_channel(cfg_hist_channel),.hist_req_valid(hist_req_valid),.hist_req_addr(hist_req_addr),
        .hist_req_ready(hist_req_ready),.hist_rsp_valid(hist_rsp_valid),.hist_rsp_data(hist_rsp_data),
        .hist_rsp_ready(hist_rsp_ready),.hist_done(hist_done),.cfg_s4_enable(cfg_s4_enable),
        .cfg_temporal_alpha(cfg_temporal_alpha),.cfg_motion_threshold(cfg_motion_threshold),
        .cfg_clip_limit(cfg_clip_limit),.cfg_patch_size(cfg_patch_size),.cfg_norm_bf16(cfg_norm_bf16),
        .cfg_norm_mean(cfg_norm_mean),.cfg_norm_inv_std(cfg_norm_inv_std),
        .token_axis_tdata(token_axis_tdata),.token_axis_tvalid(token_axis_tvalid),
        .token_axis_tuser(token_axis_tuser),.token_axis_tlast(token_axis_tlast),
        .token_axis_frame_last(token_axis_frame_last),.token_axis_patch_id(token_axis_patch_id),
        .token_axis_patch_x(token_axis_patch_x),.token_axis_patch_y(token_axis_patch_y),
        .token_axis_element(token_axis_element),.token_axis_keep(token_axis_keep),
        .token_axis_tready(token_axis_tready)
    );
    swage_s5_fork #(.DATA_WIDTH(DATA_WIDTH)) u_fork (
        .clk(clk),.rst_n(rst_n),.branch_enable(enable_reg[0]),
        .s_data(isp_data),.s_keep(isp_keep),.s_valid(isp_valid),.s_sof(isp_sof),.s_eol(isp_eol),
        .s_ready(isp_ready),.p_data(m_axis_tdata),.p_keep(m_axis_cvalid),
        .p_valid(m_axis_tvalid),.p_sof(m_axis_tuser),.p_eol(m_axis_tlast),.p_ready(m_axis_tready),
        .b_data(branch_data),.b_keep(branch_keep),.b_valid(branch_valid),
        .b_sof(branch_sof),.b_eol(branch_eol),.b_ready(branch_ready),.idle(fork_idle)
    );
    wire [23:0] rgb_data = 24'(branch_data);
    wire [23:0] centered_data;
    genvar c;
    generate for (c=0;c<3;c=c+1) begin : center_rgb
        assign centered_data[8*c +:8] = branch_keep[c] ? rgb_data[8*c +:8]^8'h80 : 8'd0;
    end endgenerate

    wire stem_ready, stem_valid, stem_sof, stem_eol, stem_last, stem_idle;
    wire c2f_ready, c2f_valid, c2f_sof, c2f_eol, c2f_last, c2f_idle;
    wire pyramid_ready, pyramid_valid, pyramid_sof, pyramid_eol, pyramid_last, pyramid_idle;
    wire head_ready, head_valid, head_eligible, head_last, head_idle;
    wire [47:0] stem_data, c2f_data, pyramid_data;
    wire [5:0] stem_keep, c2f_keep, pyramid_keep;
    wire [12:0] stem_x, stem_y, c2f_x, c2f_y, pyramid_x, pyramid_y, head_x, head_y;
    wire [1:0] pyramid_level, head_level;
    wire [87:0] head_data;
    assign branch_ready = rst_n && state == RUN && !start_frame && stem_ready;
    swage_s5_dsconv #(.CIN(3),.COUT(6),.MAX_WIDTH(S5_MAX_WIDTH)) u_stem (
        .clk(clk),.rst_n(rst_n),.s_data(centered_data),.s_keep(branch_keep),
        .s_valid(branch_valid && state == RUN && !start_frame),.s_ready(stem_ready),
        .s_sof(branch_sof),.s_eol(branch_eol),.s_frame_last(branch_last),
        .s_x(raster_x),.s_y(raster_y),.image_width(width_reg),.image_height(height_reg),
        .dw_weight(dw_weights[0][215:0]),.pw_weight(pw_weights[0][143:0]),
        .dw_bias(biases[0][95:0]),.pw_bias(biases[0][287:96]),
        .dw_shift(shifts[0][14:0]),.pw_shift(shifts[0][44:15]),
        .m_data(stem_data),.m_keep(stem_keep),.m_valid(stem_valid),.m_ready(c2f_ready),
        .m_sof(stem_sof),.m_eol(stem_eol),.m_frame_last(stem_last),
        .m_x(stem_x),.m_y(stem_y),.idle(stem_idle)
    );
    swage_s5_c2f #(.MAX_WIDTH(S5_MAX_WIDTH)) u_c2f (
        .clk(clk),.rst_n(rst_n),.s_data(stem_data),.s_keep(stem_keep),
        .s_valid(stem_valid),.s_ready(c2f_ready),.s_sof(stem_sof),.s_eol(stem_eol),
        .s_frame_last(stem_last),.s_x(stem_x),.s_y(stem_y),
        .image_width(width_reg),.image_height(height_reg),
        .b0_dw_weight(dw_weights[1][215:0]),.b1_dw_weight(dw_weights[2][215:0]),
        .b0_pw_weight(pw_weights[1][71:0]),.b1_pw_weight(pw_weights[2][71:0]),
        .b0_dw_bias(biases[1][95:0]),.b0_pw_bias(biases[1][191:96]),
        .b1_dw_bias(biases[2][95:0]),.b1_pw_bias(biases[2][191:96]),
        .b0_dw_shift(shifts[1][14:0]),.b0_pw_shift(shifts[1][29:15]),
        .b1_dw_shift(shifts[2][14:0]),.b1_pw_shift(shifts[2][29:15]),
        .fuse_weight(pw_weights[3]),.fuse_bias(biases[3][191:0]),.fuse_shift(shifts[3][29:0]),
        .m_data(c2f_data),.m_keep(c2f_keep),.m_valid(c2f_valid),.m_ready(pyramid_ready),
        .m_sof(c2f_sof),.m_eol(c2f_eol),.m_frame_last(c2f_last),
        .m_x(c2f_x),.m_y(c2f_y),.idle(c2f_idle)
    );
    swage_s5_pyramid #(.MAX_WIDTH(S5_MAX_WIDTH)) u_pyramid (
        .clk(clk),.rst_n(rst_n),.s_data(c2f_data),.s_keep(c2f_keep),
        .s_valid(c2f_valid),.s_ready(pyramid_ready),.s_sof(c2f_sof),.s_eol(c2f_eol),
        .s_frame_last(c2f_last),.s_x(c2f_x),.s_y(c2f_y),
        .image_width(width_reg),.image_height(height_reg),
        .l1_dw_weight(dw_weights[4]),.l2_dw_weight(dw_weights[5]),
        .l1_pw_weight(pw_weights[4][287:0]),.l2_pw_weight(pw_weights[5][287:0]),
        .l1_dw_bias(biases[4][191:0]),.l1_pw_bias(biases[4][383:192]),
        .l2_dw_bias(biases[5][191:0]),.l2_pw_bias(biases[5][383:192]),
        .l1_dw_shift(shifts[4][29:0]),.l1_pw_shift(shifts[4][59:30]),
        .l2_dw_shift(shifts[5][29:0]),.l2_pw_shift(shifts[5][59:30]),
        .m_data(pyramid_data),.m_keep(pyramid_keep),.m_valid(pyramid_valid),.m_ready(head_ready),
        .m_sof(pyramid_sof),.m_eol(pyramid_eol),.m_frame_last(pyramid_last),
        .m_x(pyramid_x),.m_y(pyramid_y),.m_level(pyramid_level),.idle(pyramid_idle)
    );
    wire queue_ready, queue_valid, queue_out_ready, queue_idle;
    wire [117:0] queued_candidate;
    swage_s5_head #(.NUM_CLASSES(S5_NUM_CLASSES)) u_head (
        .clk(clk),.rst_n(rst_n),.s_data(pyramid_data),.s_keep(pyramid_keep),
        .s_valid(pyramid_valid),.s_ready(head_ready),.s_sof(pyramid_sof),.s_eol(pyramid_eol),
        .s_frame_last(pyramid_last),.s_x(pyramid_x),.s_y(pyramid_y),
        .image_width(width_reg),.image_height(height_reg),.s_level(pyramid_level),
        .class_weight(pw_weights[6][48*NC-1:0]),.class_bias(biases[6][32*NC-1:0]),
        .class_shift(shifts[6][5*NC-1:0]),.reg_weight(pw_weights[7][191:0]),
        .reg_bias(biases[7][127:0]),.reg_shift(shifts[7][19:0]),.threshold(threshold_reg),
        .m_data(head_data),.m_eligible(head_eligible),.m_valid(head_valid),.m_ready(queue_ready),
        .m_level(head_level),.m_x(head_x),.m_y(head_y),.m_frame_last(head_last),.idle(head_idle)
    );
    swage_s5_queue #(.WIDTH(118),.DEPTH(S5_FIFO_DEPTH)) u_candidate_queue (
        .clk(clk),.rst_n(rst_n),.s_data({head_last,head_y,head_x,head_level,head_eligible,head_data}),
        .s_valid(head_valid),.s_ready(queue_ready),.m_data(queued_candidate),
        .m_valid(queue_valid),.m_ready(queue_out_ready),.idle(queue_idle)
    );
    wire topk_idle, finish_ready, topk_done, snapshot_valid;
    wire [87:0] snapshot_data, ranked_data;
    wire [12:0] snapshot_x, snapshot_y, ranked_x, ranked_y;
    wire [1:0] snapshot_level, ranked_level;
    wire ranked_valid, ranked_ready, ranked_keep, ranked_first, ranked_last;
    wire [7:0] ranked_rank, ranked_count;
    wire [4:0] ranked_slot, replace_slot;
    wire replace_valid;
    swage_s5_topk #(.MAX_TOPK(S5_MAX_TOPK)) u_topk (
        .clk(clk),.rst_n(rst_n),.topk(topk_reg),.start_frame(start_frame),
        .finish_valid(state == FINISH),.finish_ready(finish_ready),
        .s_data(queued_candidate[87:0]),.s_eligible(queued_candidate[88]),
        .s_level(queued_candidate[90:89]),.s_x(queued_candidate[103:91]),
        .s_y(queued_candidate[116:104]),.s_frame_last(queued_candidate[117]),
        .s_valid(queue_valid),.s_ready(queue_out_ready),.snapshot_index(snapshot_index),
        .snapshot_valid(snapshot_valid),.snapshot_data(snapshot_data),
        .snapshot_x(snapshot_x),.snapshot_y(snapshot_y),.snapshot_level(snapshot_level),
        .m_data(ranked_data),.m_valid(ranked_valid),.m_ready(ranked_ready),
        .m_keep(ranked_keep),.m_first(ranked_first),.m_last(ranked_last),
        .m_rank(ranked_rank),.m_count(ranked_count),.m_x(ranked_x),.m_y(ranked_y),
        .m_level(ranked_level),.m_slot(ranked_slot),.replace_valid(replace_valid),
        .replace_slot(replace_slot),.delivery_done(topk_done),.idle(topk_idle)
    );
    wire roi_idle, copy_ready, copy_done, cmd_ready;
    wire current_snapshot = snapshot_valid && snapshot_y >= stripe_base_y &&
        {1'b0,snapshot_y} < {1'b0,stripe_base_y}+{9'd0,patch_reg};
    wire copy_valid = state == SNAPSHOT && enable_reg[1] && current_snapshot;
    wire need_roi = enable_reg[1] && ranked_keep;
    assign ranked_ready = rst_n && det_axis_tready && (!need_roi || cmd_ready);
    assign det_axis_tvalid = rst_n && ranked_valid && (!need_roi || cmd_ready);
    assign det_axis_tdata = det_axis_tvalid ? ranked_data : 88'd0;
    assign det_axis_tuser = det_axis_tvalid && ranked_first;
    assign det_axis_tlast = det_axis_tvalid && ranked_last;
    assign det_axis_keep = det_axis_tvalid && ranked_keep;
    assign det_axis_rank = det_axis_tvalid ? ranked_rank : 8'd0;
    assign det_axis_count = det_axis_tvalid ? ranked_count : 8'd0;
    assign det_axis_frame_id = det_axis_tvalid ? frame_id : 32'd0;
    swage_s5_roi_tokens #(.MAX_WIDTH(S5_MAX_WIDTH),.MAX_TOPK(S5_MAX_TOPK)) u_roi_tokens (
        .clk(clk),.rst_n(rst_n),.start_frame(start_frame && enable_reg[1]),
        .image_width(width_reg),.image_height(height_reg),.patch_size(patch_reg),
        .mean(mean_reg),.inv_std(reciprocal_reg),.bf16(bf16_reg),.frame_id(frame_id),
        .wr_valid(branch_transfer && enable_reg[1]),.wr_addr(stripe_addr),
        .wr_data(rgb_data),.wr_keep(branch_keep),.replace_valid(replace_valid && enable_reg[1]),
        .replace_slot(replace_slot),.copy_valid(copy_valid),.copy_ready(copy_ready),
        .copy_slot(snapshot_index),.copy_anchor_x(snapshot_x),
        .copy_grid_y(stripe_grid_y),.copy_base_y(stripe_base_y),.copy_done(copy_done),
        .cmd_valid(ranked_valid && det_axis_tready && need_roi),.cmd_ready(cmd_ready),
        .cmd_slot(ranked_slot),.cmd_rank(ranked_rank),.cmd_last(ranked_last),
        .m_data(roi_axis_tdata),.m_valid(roi_axis_tvalid),.m_ready(roi_axis_tready),
        .m_first(roi_axis_tuser),.m_last(roi_axis_tlast),.m_frame_last(roi_axis_frame_last),
        .m_id(roi_axis_patch_id),.m_x(roi_axis_patch_x),.m_y(roi_axis_patch_y),
        .m_element(roi_axis_element),.m_keep(roi_axis_keep),
        .m_rank(roi_axis_det_rank),.m_frame_id(roi_axis_frame_id),.idle(roi_idle)
    );
    wire compute_drained = stem_idle && c2f_idle && pyramid_idle && head_idle &&
        queue_idle && topk_idle;
    integer i;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= RUN; enable_reg <= 0; threshold_reg <= 65535; topk_reg <= 1;
            patch_reg <= 16; bf16_reg <= 0; mean_reg <= {3{24'd32640}};
            reciprocal_reg <= {3{24'd512}}; width_reg <= 13'(DW); height_reg <= 13'(DH);
            frame_active <= 0; input_closed <= 0; source_count <= 0; frame_id <= 0;
            start_frame <= 0;
            raster_x <= 0; raster_y <= 0; stripe_row <= 0; stripe_addr <= 0;
            stripe_base_y <= 0; stripe_grid_y <= 0; snapshot_index <= 0;
            final_stripe <= 0; descriptors_done <= 0; weight_error <= 0;
            for (i=0;i<8;i=i+1) begin
                dw_weights[i] <= 0; pw_weights[i] <= 0; biases[i] <= 0; shifts[i] <= 0;
            end
        end else begin
            weight_error <= 0;
            start_frame <= enable_reg[0] && !frame_active && source_transfer;
            if (cfg_valid && cfg_ready && !illegal_legacy && !illegal_s4 && !illegal_s5) begin
                enable_reg <= cfg_s5_enable; threshold_reg <= cfg_s5_threshold; topk_reg <= cfg_s5_topk;
                patch_reg <= cfg_s5_enable[1] ? cfg_patch_size : 5'd16;
                bf16_reg <= cfg_norm_bf16;
                mean_reg <= cfg_norm_mean; reciprocal_reg <= cfg_norm_inv_std;
                width_reg <= requested_width; height_reg <= requested_height;
                raster_x <= 0; raster_y <= 0; stripe_row <= 0; stripe_addr <= 0;
                stripe_base_y <= 0; stripe_grid_y <= 0;
            end
            if (weight_valid && weight_ready) begin
                if (!valid_weight) weight_error <= 1;
                else case (weight_kind)
                    0: dw_weights[weight_layer][8*weight_index +:8] <= weight_data[7:0];
                    1: pw_weights[weight_layer][8*weight_index +:8] <= weight_data[7:0];
                    2: biases[weight_layer][32*weight_index +:32] <= weight_data;
                    3: shifts[weight_layer][5*weight_index +:5] <= weight_data[4:0];
                endcase
            end
            if (source_transfer && enable_reg[0]) begin
                source_count <= source_count+25'd1;
                if (source_count == 25'(SW*SH-1)) input_closed <= 1;
                if (!frame_active) begin frame_active <= 1; descriptors_done <= 0; end
            end
            if (branch_transfer) begin
                stripe_addr <= stripe_addr+17'd1;
                if (raster_x == width_reg-13'd1) begin
                    raster_x <= 0; raster_y <= raster_y+13'd1;
                    if (stripe_row == patch_reg-5'd1 || branch_last) begin
                        stripe_row <= 0; state <= DRAIN; final_stripe <= branch_last;
                    end else stripe_row <= stripe_row+5'd1;
                end else raster_x <= raster_x+13'd1;
            end
            case (state)
                DRAIN: if (compute_drained) begin snapshot_index <= 0; state <= SNAPSHOT; end
                SNAPSHOT: begin
                    if (copy_valid) begin
                        if (copy_ready) state <= COPY_WAIT;
                    end else if (snapshot_index == 5'(K-1)) begin
                        if (final_stripe) state <= FINISH;
                        else begin
                            state <= RUN; stripe_addr <= 0;
                            stripe_base_y <= stripe_base_y+{8'd0,patch_reg};
                            stripe_grid_y <= stripe_grid_y+13'd1;
                        end
                    end else snapshot_index <= snapshot_index+5'd1;
                end
                COPY_WAIT: if (copy_done) begin
                    if (snapshot_index == 5'(K-1)) begin
                        if (final_stripe) state <= FINISH;
                        else begin
                            state <= RUN; stripe_addr <= 0;
                            stripe_base_y <= stripe_base_y+{8'd0,patch_reg};
                            stripe_grid_y <= stripe_grid_y+13'd1;
                        end
                    end else begin snapshot_index <= snapshot_index+5'd1; state <= SNAPSHOT; end
                end
                FINISH: if (finish_ready) state <= DELIVERY;
                DELIVERY: begin
                    if (topk_done) descriptors_done <= 1;
                    if ((descriptors_done || topk_done) && roi_idle && isp_cfg_ready &&
                        fork_idle && input_count == 0) begin
                        state <= RUN; frame_active <= 0; input_closed <= 0;
                        source_count <= 0; frame_id <= frame_id+32'd1;
                        raster_x <= 0; raster_y <= 0; stripe_addr <= 0; stripe_row <= 0;
                        stripe_base_y <= 0; stripe_grid_y <= 0; final_stripe <= 0;
                        descriptors_done <= 0;
                    end
                end
                default: begin end
            endcase
        end
    end
    generate if (S5_MAX_WIDTH < 1 || S5_MAX_WIDTH > 4096 ||
        S5_MAX_WIDTH < SRC_WIDTH || S5_MAX_WIDTH < DST_WIDTH ||
        S5_NUM_CLASSES < 1 || S5_NUM_CLASSES > 8 || S5_MAX_TOPK < 1 || S5_MAX_TOPK > 16 ||
        S5_FIFO_DEPTH < 1 || S5_FIFO_DEPTH > 16) begin : invalid_parameters
        initial $fatal(1,"SWAGE_S5 PARAMETER ERROR");
    end endgenerate
endmodule
