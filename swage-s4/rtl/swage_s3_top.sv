module swage_s3_top #(
    parameter integer SRC_WIDTH = 8,
    parameter integer SRC_HEIGHT = 8,
    parameter integer DST_WIDTH = 16,
    parameter integer DST_HEIGHT = 16,
    parameter integer PIXEL_BITS = 8,
    parameter integer CHANNELS = 1,
    parameter integer PHASE_FRAC = 8,
    parameter integer INTERPOLATION = 0
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
    output wire hist_done
);
    localparam integer DATA_WIDTH = (PIXEL_BITS == 10 && CHANNELS == 1) ? 16 :
        ((PIXEL_BITS == 8 && CHANNELS == 3) ? 24 : 8);
    localparam integer SW = SRC_WIDTH >= 1 && SRC_WIDTH <= 4096 ? SRC_WIDTH : 1;
    localparam integer SH = SRC_HEIGHT >= 1 && SRC_HEIGHT <= 4096 ? SRC_HEIGHT : 1;
    localparam integer DW = DST_WIDTH >= 1 && DST_WIDTH <= 4096 ? DST_WIDTH : 1;
    localparam integer DH = DST_HEIGHT >= 1 && DST_HEIGHT <= 4096 ? DST_HEIGHT : 1;
    localparam integer PB = PIXEL_BITS == 10 && CHANNELS == 1 ? 10 : 8;
    localparam integer CH = PIXEL_BITS == 8 && CHANNELS == 3 ? 3 : 1;
    localparam integer F = PHASE_FRAC == 16 ? 16 : 8;
    localparam integer MODE = INTERPOLATION == 1 ? 1 : 0;
    localparam [24:0] SOURCE_BEATS = 25'(SW*SH);
    localparam [24:0] DESTINATION_BEATS = 25'(DW*DH);
    generate if (SRC_WIDTH < 1 || SRC_WIDTH > 4096 ||
        SRC_HEIGHT < 1 || SRC_HEIGHT > 4096 ||
        DST_WIDTH < 1 || DST_WIDTH > 4096 ||
        DST_HEIGHT < 1 || DST_HEIGHT > 4096 ||
        !((PIXEL_BITS == 8 && CHANNELS == 1) ||
          (PIXEL_BITS == 10 && CHANNELS == 1) ||
          (PIXEL_BITS == 8 && CHANNELS == 3)) ||
        !(PHASE_FRAC == 8 || PHASE_FRAC == 16) ||
        !(INTERPOLATION == 0 || INTERPOLATION == 1)) begin : invalid_parameters
        initial $fatal(1,"SWAGE_S3 PARAMETER ERROR");
    end endgenerate

    reg [5:0] enable_reg;
    reg border_reg, abs_reg, output_420_reg, error_reg, frame_active;
    reg [4:0] shift_reg;
    reg [143:0] coeff_reg, csc_coeff_reg;
    reg [47:0] in_bias_reg, out_bias_reg;
    reg [12:0] roi_x_reg, roi_y_reg, roi_width_reg, roi_height_reg;
    reg [12:0] width_reg, height_reg;
    reg [1:0] input_format_reg, hist_channel_reg;
    reg [2:0] csc_mode_reg;
    reg [24:0] source_count, destination_count, output_beats_reg;
    wire [13:0] roi_right = {1'b0,cfg_roi_x}+{1'b0,cfg_roi_width};
    wire [13:0] roi_bottom = {1'b0,cfg_roi_y}+{1'b0,cfg_roi_height};
    wire requested_inverse = cfg_input_format != 0;
    wire [1:0] requested_output_format = cfg_enable[3] ?
        (requested_inverse ? 2'd0 : (cfg_output_420 ? 2'd2 : 2'd1)) : cfg_input_format;
    wire illegal_configuration = cfg_input_format == 3 ||
        (CH != 3 && cfg_input_format != 0) ||
        (cfg_enable[0] && (cfg_roi_width == 0 || cfg_roi_height == 0 ||
            roi_right > SW || roi_bottom > SH ||
            (cfg_input_format == 2 && (cfg_roi_x[0] || cfg_roi_y[0])))) ||
        (cfg_enable[1] && (CH != 3 || PB != 8 || cfg_input_format != 0)) ||
        (cfg_enable[2] && (PB != 8 || cfg_input_format != 0)) ||
        (cfg_enable[3] && (CH != 3 || cfg_csc_mode > 4 ||
            (cfg_csc_mode < 2 && cfg_input_format != 0) ||
            ((cfg_csc_mode == 2 || cfg_csc_mode == 3) && cfg_input_format == 0))) ||
        (cfg_enable[5] && ((CH == 1 && cfg_hist_channel != 0) ||
            (cfg_hist_channel == 3 && (CH != 3 || requested_output_format != 0))));
    wire [12:0] origin_x = enable_reg[0] ? roi_x_reg : 13'd0;
    wire [12:0] origin_y = enable_reg[0] ? roi_y_reg : 13'd0;
    wire [1:0] output_format = enable_reg[3] ?
        (input_format_reg != 0 ? 2'd0 : (output_420_reg ? 2'd2 : 2'd1)) : input_format_reg;
    wire hist_frame_ready, hist_frame_complete, hist_reserved, scaler_idle;
    wire request_priority = hist_req_valid && hist_done;
    wire begin_frame = rst_n && !frame_active && !cfg_valid &&
        !hist_reserved && !request_priority && s_axis_tvalid;
    wire input_enabled = rst_n && frame_active && hist_frame_ready && source_count < SOURCE_BEATS;
    wire frame_drained = frame_active && source_count == SOURCE_BEATS &&
        destination_count == output_beats_reg && scaler_idle;
    wire [DATA_WIDTH-1:0] crop_data, demosaic_data, convolution_data, csc_data, scale_data;
    wire [2:0] crop_mask, demosaic_mask, convolution_mask, csc_mask, scale_mask;
    wire crop_valid,crop_sof,crop_eol,crop_ready;
    wire demosaic_valid,demosaic_sof,demosaic_eol,demosaic_ready;
    wire convolution_valid,convolution_sof,convolution_eol,convolution_ready;
    wire csc_valid,csc_sof,csc_eol,csc_ready;
    wire scale_valid,scale_sof,scale_eol,scale_ready,hist_ready;
    wire source_transfer = s_axis_tvalid && s_axis_tready;
    wire destination_transfer = m_axis_tvalid && m_axis_tready;
    assign cfg_ready = rst_n && !frame_active && !hist_reserved && !request_priority;
    assign cfg_error = rst_n && error_reg;
    assign s_axis_tready = input_enabled && crop_ready;

    swage_s3_crop #(.SRC_WIDTH(SW),.SRC_HEIGHT(SH),.DATA_WIDTH(DATA_WIDTH)) u_crop (
        .clk(clk),.rst_n(rst_n),.enable(enable_reg[0]),
        .roi_x(roi_x_reg),.roi_y(roi_y_reg),.roi_width(roi_width_reg),.roi_height(roi_height_reg),
        .s_axis_tdata(s_axis_tdata),.s_axis_cvalid(s_axis_cvalid),
        .s_axis_tvalid(input_enabled && s_axis_tvalid),
        .s_axis_tuser(s_axis_tuser),.s_axis_tlast(s_axis_tlast),.s_axis_tready(crop_ready),
        .m_axis_tdata(crop_data),.m_axis_cvalid(crop_mask),.m_axis_tvalid(crop_valid),
        .m_axis_tuser(crop_sof),.m_axis_tlast(crop_eol),.m_axis_tready(demosaic_ready)
    );
    swage_s3_demosaic #(.MAX_WIDTH(SW),.DATA_WIDTH(DATA_WIDTH)) u_demosaic (
        .clk(clk),.rst_n(rst_n),.enable(enable_reg[1]),.border(border_reg),
        .image_width(width_reg),.image_height(height_reg),.origin_x(origin_x),.origin_y(origin_y),
        .s_axis_tdata(crop_data),.s_axis_cvalid(crop_mask),.s_axis_tvalid(crop_valid),
        .s_axis_tuser(crop_sof),.s_axis_tlast(crop_eol),.s_axis_tready(demosaic_ready),
        .m_axis_tdata(demosaic_data),.m_axis_cvalid(demosaic_mask),.m_axis_tvalid(demosaic_valid),
        .m_axis_tuser(demosaic_sof),.m_axis_tlast(demosaic_eol),.m_axis_tready(convolution_ready)
    );
    swage_s3_convolution #(.MAX_WIDTH(SW),.DATA_WIDTH(DATA_WIDTH),.CHANNELS(CH)) u_convolution (
        .clk(clk),.rst_n(rst_n),.enable(enable_reg[2]),.border(border_reg),
        .image_width(width_reg),.image_height(height_reg),.origin_x(origin_x),.origin_y(origin_y),
        .cfg_abs(abs_reg),.cfg_shift(shift_reg),.cfg_coeff(coeff_reg),
        .s_axis_tdata(demosaic_data),.s_axis_cvalid(demosaic_mask),.s_axis_tvalid(demosaic_valid),
        .s_axis_tuser(demosaic_sof),.s_axis_tlast(demosaic_eol),.s_axis_tready(convolution_ready),
        .m_axis_tdata(convolution_data),.m_axis_cvalid(convolution_mask),
        .m_axis_tvalid(convolution_valid),.m_axis_tuser(convolution_sof),
        .m_axis_tlast(convolution_eol),.m_axis_tready(csc_ready)
    );
    swage_s3_csc #(.MAX_WIDTH(SW),.DATA_WIDTH(DATA_WIDTH)) u_csc (
        .clk(clk),.rst_n(rst_n),.enable(enable_reg[3]),
        .image_width(width_reg),.image_height(height_reg),.origin_x(origin_x),.origin_y(origin_y),
        .input_format(input_format_reg),.mode(csc_mode_reg),.output_420(output_420_reg),
        .coeff(csc_coeff_reg),.in_bias(in_bias_reg),.out_bias(out_bias_reg),
        .s_axis_tdata(convolution_data),.s_axis_cvalid(convolution_mask),
        .s_axis_tvalid(convolution_valid),.s_axis_tuser(convolution_sof),
        .s_axis_tlast(convolution_eol),.s_axis_tready(csc_ready),
        .m_axis_tdata(csc_data),.m_axis_cvalid(csc_mask),.m_axis_tvalid(csc_valid),
        .m_axis_tuser(csc_sof),.m_axis_tlast(csc_eol),.m_axis_tready(scale_ready)
    );
    swage_s3_scaler #(
        .MAX_WIDTH(SW),.DST_WIDTH(DW),.DST_HEIGHT(DH),.DATA_WIDTH(DATA_WIDTH),
        .PIXEL_BITS(PB),.CHANNELS(CH),.PHASE_FRAC(F),.INTERPOLATION(MODE)
    ) u_scaler (
        .clk(clk),.rst_n(rst_n),.enable(enable_reg[4]),.is_420(output_format == 2),
        .image_width(width_reg),.image_height(height_reg),.origin_x(origin_x),.origin_y(origin_y),
        .s_axis_tdata(csc_data),.s_axis_cvalid(csc_mask),.s_axis_tvalid(csc_valid),
        .s_axis_tuser(csc_sof),.s_axis_tlast(csc_eol),.s_axis_tready(scale_ready),
        .m_axis_tdata(scale_data),.m_axis_cvalid(scale_mask),.m_axis_tvalid(scale_valid),
        .m_axis_tuser(scale_sof),.m_axis_tlast(scale_eol),.m_axis_tready(hist_ready),.idle(scaler_idle)
    );
    swage_s3_histogram #(
        .DATA_WIDTH(DATA_WIDTH),.PIXEL_BITS(PB),.CHANNELS(CH)
    ) u_histogram (
        .clk(clk),.rst_n(rst_n),.enable(enable_reg[5]),
        .frame_begin(begin_frame),.frame_drained(frame_drained),.frame_idle(!frame_active),
        .frame_ready(hist_frame_ready),.frame_complete(hist_frame_complete),.reserved(hist_reserved),
        .hist_channel(hist_channel_reg),.rgb_output(output_format == 0),
        .s_axis_tdata(scale_data),.s_axis_cvalid(scale_mask),.s_axis_tvalid(scale_valid),
        .s_axis_tuser(scale_sof),.s_axis_tlast(scale_eol),.s_axis_tready(hist_ready),
        .m_axis_tdata(m_axis_tdata),.m_axis_cvalid(m_axis_cvalid),.m_axis_tvalid(m_axis_tvalid),
        .m_axis_tuser(m_axis_tuser),.m_axis_tlast(m_axis_tlast),.m_axis_tready(m_axis_tready),
        .hist_req_valid(hist_req_valid),.hist_req_addr(hist_req_addr),.hist_req_ready(hist_req_ready),
        .hist_rsp_valid(hist_rsp_valid),.hist_rsp_data(hist_rsp_data),
        .hist_rsp_ready(hist_rsp_ready),.hist_done(hist_done)
    );

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            enable_reg<=6'b010000; border_reg<=0; abs_reg<=0;
            shift_reg<=0; coeff_reg<=144'd1<<64;
            roi_x_reg<=0;roi_y_reg<=0;roi_width_reg<=13'(SW);roi_height_reg<=13'(SH);
            width_reg<=13'(SW);height_reg<=13'(SH);
            input_format_reg<=0;csc_mode_reg<=0;output_420_reg<=0;
            csc_coeff_reg<=0;in_bias_reg<=0;out_bias_reg<=0;hist_channel_reg<=0;
            error_reg<=0;frame_active<=0;source_count<=0;destination_count<=0;
            output_beats_reg<=DESTINATION_BEATS;
        end else begin
            error_reg<=0;
            if (cfg_valid && cfg_ready) begin
                if (illegal_configuration) error_reg<=1;
                else begin
                    enable_reg<=cfg_enable;border_reg<=cfg_border;abs_reg<=cfg_abs;
                    shift_reg<=cfg_shift;coeff_reg<=cfg_coeff;
                    roi_x_reg<=cfg_roi_x;roi_y_reg<=cfg_roi_y;
                    roi_width_reg<=cfg_roi_width;roi_height_reg<=cfg_roi_height;
                    width_reg<=cfg_enable[0] ? cfg_roi_width : 13'(SW);
                    height_reg<=cfg_enable[0] ? cfg_roi_height : 13'(SH);
                    input_format_reg<=cfg_input_format;csc_mode_reg<=cfg_csc_mode;
                    output_420_reg<=cfg_output_420;csc_coeff_reg<=cfg_csc_coeff;
                    in_bias_reg<=cfg_csc_in_bias;out_bias_reg<=cfg_csc_out_bias;
                    hist_channel_reg<=cfg_hist_channel;
                    if (cfg_enable[4]) output_beats_reg<=DESTINATION_BEATS;
                    else if (cfg_enable[0])
                        output_beats_reg<=25'(cfg_roi_width)*25'(cfg_roi_height);
                    else output_beats_reg<=SOURCE_BEATS;
                end
            end
            if (begin_frame) begin
                frame_active<=1;source_count<=0;destination_count<=0;
            end else if (frame_active) begin
                if (source_transfer) source_count<=source_count+25'd1;
                if (destination_transfer) destination_count<=destination_count+25'd1;
                if (frame_drained && hist_frame_complete) begin
                    frame_active<=0;source_count<=0;destination_count<=0;
                end
            end
        end
    end
endmodule
