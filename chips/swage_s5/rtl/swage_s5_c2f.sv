module swage_s5_c2f #(
    parameter integer MAX_WIDTH = 4096
) (
    input wire clk, rst_n,
    input wire [47:0] s_data,
    input wire [5:0] s_keep,
    input wire s_valid,
    output wire s_ready,
    input wire s_sof, s_eol, s_frame_last,
    input wire [12:0] s_x, s_y, image_width, image_height,
    input wire [215:0] b0_dw_weight, b1_dw_weight,
    input wire [71:0] b0_pw_weight, b1_pw_weight,
    input wire [95:0] b0_dw_bias, b0_pw_bias, b1_dw_bias, b1_pw_bias,
    input wire [14:0] b0_dw_shift, b0_pw_shift, b1_dw_shift, b1_pw_shift,
    input wire [575:0] fuse_weight,
    input wire [191:0] fuse_bias,
    input wire [29:0] fuse_shift,
    output wire [47:0] m_data,
    output wire [5:0] m_keep,
    output wire m_valid,
    input wire m_ready,
    output wire m_sof, m_eol, m_frame_last,
    output wire [12:0] m_x, m_y,
    output wire idle
);
    wire b0_ready, b0_valid, b0_out_ready, b0_sof, b0_eol, b0_last, b0_idle;
    wire b1_ready, b1_valid, b1_sof, b1_eol, b1_last, b1_idle;
    wire [23:0] b0_data, b1_data, saved_b1;
    wire [2:0] b0_keep, b1_keep;
    wire [12:0] b0_x, b0_y, b1_x, b1_y;
    wire original_ready, original_valid, original_idle;
    wire saved_ready, saved_valid, saved_idle, fuse_ready, fuse_idle;
    wire [47:0] original_data, masked_input;
    assign s_ready = b0_ready && original_ready;
    assign b0_out_ready = b1_ready && saved_ready;
    assign idle = rst_n && b0_idle && b1_idle && original_idle && saved_idle && fuse_idle;
    swage_s5_bottleneck #(.MAX_WIDTH(MAX_WIDTH)) u_bottleneck0 (
        .clk(clk),.rst_n(rst_n),.s_data(s_data[47:24]),.s_keep(s_keep[5:3]),
        .s_valid(s_valid && original_ready),.s_ready(b0_ready),
        .s_sof(s_sof),.s_eol(s_eol),.s_frame_last(s_frame_last),.s_x(s_x),.s_y(s_y),
        .image_width(image_width),.image_height(image_height),
        .dw_weight(b0_dw_weight),.pw_weight(b0_pw_weight),
        .dw_bias(b0_dw_bias),.pw_bias(b0_pw_bias),.dw_shift(b0_dw_shift),.pw_shift(b0_pw_shift),
        .m_data(b0_data),.m_keep(b0_keep),.m_valid(b0_valid),.m_ready(b0_out_ready),
        .m_sof(b0_sof),.m_eol(b0_eol),.m_frame_last(b0_last),
        .m_x(b0_x),.m_y(b0_y),.idle(b0_idle)
    );
    swage_s5_queue #(.WIDTH(48),.DEPTH(32)) u_original_queue (
        .clk(clk),.rst_n(rst_n),.s_data(masked_input),.s_valid(s_valid && b0_ready),
        .s_ready(original_ready),.m_data(original_data),.m_valid(original_valid),
        .m_ready(fuse_ready && b1_valid && saved_valid),.idle(original_idle)
    );
    swage_s5_bottleneck #(.MAX_WIDTH(MAX_WIDTH)) u_bottleneck1 (
        .clk(clk),.rst_n(rst_n),.s_data(b0_data),.s_keep(b0_keep),
        .s_valid(b0_valid && saved_ready),.s_ready(b1_ready),
        .s_sof(b0_sof),.s_eol(b0_eol),.s_frame_last(b0_last),.s_x(b0_x),.s_y(b0_y),
        .image_width(image_width),.image_height(image_height),
        .dw_weight(b1_dw_weight),.pw_weight(b1_pw_weight),
        .dw_bias(b1_dw_bias),.pw_bias(b1_pw_bias),.dw_shift(b1_dw_shift),.pw_shift(b1_pw_shift),
        .m_data(b1_data),.m_keep(b1_keep),.m_valid(b1_valid),
        .m_ready(fuse_ready && original_valid && saved_valid),
        .m_sof(b1_sof),.m_eol(b1_eol),.m_frame_last(b1_last),
        .m_x(b1_x),.m_y(b1_y),.idle(b1_idle)
    );
    swage_s5_queue #(.WIDTH(24),.DEPTH(16)) u_b1_queue (
        .clk(clk),.rst_n(rst_n),.s_data(b0_data),.s_valid(b0_valid && b1_ready),
        .s_ready(saved_ready),.m_data(saved_b1),.m_valid(saved_valid),
        .m_ready(fuse_ready && b1_valid && original_valid),.idle(saved_idle)
    );
    swage_s5_pointwise #(.CIN(12),.COUT(6)) u_fuse (
        .clk(clk),.rst_n(rst_n),.s_data({b1_data,saved_b1,original_data}),.s_keep(12'hfff),
        .s_valid(b1_valid && saved_valid && original_valid),.s_ready(fuse_ready),
        .s_sof(b1_sof),.s_eol(b1_eol),.s_frame_last(b1_last),.s_x(b1_x),.s_y(b1_y),
        .image_width(image_width),.image_height(image_height),
        .pw_weight(fuse_weight),.pw_bias(fuse_bias),.pw_shift(fuse_shift),
        .m_data(m_data),.m_keep(m_keep),.m_valid(m_valid),.m_ready(m_ready),
        .m_sof(m_sof),.m_eol(m_eol),.m_frame_last(m_frame_last),
        .m_x(m_x),.m_y(m_y),.idle(fuse_idle)
    );
    genvar c;
    generate for (c=0;c<6;c=c+1) begin : input_mask
        assign masked_input[c*8 +:8] = s_keep[c] ? s_data[c*8 +:8] : 8'd0;
    end endgenerate
endmodule
