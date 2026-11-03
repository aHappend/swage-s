module swage_s5_bottleneck #(
    parameter integer MAX_WIDTH = 4096
) (
    input wire clk, rst_n,
    input wire [23:0] s_data,
    input wire [2:0] s_keep,
    input wire s_valid,
    output wire s_ready,
    input wire s_sof, s_eol, s_frame_last,
    input wire [12:0] s_x, s_y, image_width, image_height,
    input wire [215:0] dw_weight,
    input wire [71:0] pw_weight,
    input wire [95:0] dw_bias, pw_bias,
    input wire [14:0] dw_shift, pw_shift,
    output wire [23:0] m_data,
    output wire [2:0] m_keep,
    output wire m_valid,
    input wire m_ready,
    output wire m_sof, m_eol, m_frame_last,
    output wire [12:0] m_x, m_y,
    output wire idle
);
    wire ds_ready, ds_valid, ds_sof, ds_eol, ds_last, ds_idle;
    wire [23:0] ds_data, bypass_data, masked_input;
    wire [2:0] ds_keep;
    wire [12:0] ds_x, ds_y;
    wire bypass_ready, bypass_valid, bypass_idle;
    reg valid, sof, eol, last;
    reg [12:0] x, y;
    reg [23:0] result;
    wire advance = rst_n && (!valid || m_ready);
    assign s_ready = ds_ready && bypass_ready;
    assign m_valid = rst_n && valid;
    assign m_data = m_valid ? result : 24'd0;
    assign m_keep = m_valid ? 3'b111 : 3'd0;
    assign m_sof = m_valid && sof;
    assign m_eol = m_valid && eol;
    assign m_frame_last = m_valid && last;
    assign m_x = m_valid ? x : 13'd0;
    assign m_y = m_valid ? y : 13'd0;
    assign idle = rst_n && ds_idle && bypass_idle && !valid;
    swage_s5_dsconv #(.CIN(3),.COUT(3),.MAX_WIDTH(MAX_WIDTH)) u_transform (
        .clk(clk),.rst_n(rst_n),.s_data(s_data),.s_keep(s_keep),
        .s_valid(s_valid && bypass_ready),.s_ready(ds_ready),
        .s_sof(s_sof),.s_eol(s_eol),.s_frame_last(s_frame_last),
        .s_x(s_x),.s_y(s_y),.image_width(image_width),.image_height(image_height),
        .dw_weight(dw_weight),.pw_weight(pw_weight),.dw_bias(dw_bias),.pw_bias(pw_bias),
        .dw_shift(dw_shift),.pw_shift(pw_shift),
        .m_data(ds_data),.m_keep(ds_keep),.m_valid(ds_valid),
        .m_ready(advance && bypass_valid),.m_sof(ds_sof),.m_eol(ds_eol),
        .m_frame_last(ds_last),.m_x(ds_x),.m_y(ds_y),.idle(ds_idle)
    );
    swage_s5_queue #(.WIDTH(24),.DEPTH(16)) u_residual_queue (
        .clk(clk),.rst_n(rst_n),.s_data(masked_input),.s_valid(s_valid && ds_ready),
        .s_ready(bypass_ready),.m_data(bypass_data),.m_valid(bypass_valid),
        .m_ready(advance && ds_valid),.idle(bypass_idle)
    );
    genvar c;
    generate for (c=0;c<3;c=c+1) begin : residual
        wire signed [8:0] sum = $signed({bypass_data[c*8+7],bypass_data[c*8 +:8]}) +
            $signed({ds_data[c*8+7],ds_data[c*8 +:8]});
        wire [7:0] merged = sum > 9'sd127 ? 8'h7f : (sum < 0 ? 8'd0 : sum[7:0]);
        assign masked_input[c*8 +:8] = s_keep[c] ? s_data[c*8 +:8] : 8'd0;
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) result[c*8 +:8] <= 0;
            else if (advance) result[c*8 +:8] <= merged;
        end
    end endgenerate
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin valid <= 0; sof <= 0; eol <= 0; last <= 0; x <= 0; y <= 0; end
        else if (advance) begin
            valid <= ds_valid && bypass_valid;
            sof <= ds_sof; eol <= ds_eol; last <= ds_last; x <= ds_x; y <= ds_y;
        end
    end
endmodule
