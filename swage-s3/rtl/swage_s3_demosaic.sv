module swage_s3_demosaic #(
    parameter integer MAX_WIDTH = 8,
    parameter integer DATA_WIDTH = 24
) (
    input wire clk, rst_n,
    input wire [DATA_WIDTH-1:0] s_axis_tdata,
    input wire [2:0] s_axis_cvalid,
    input wire s_axis_tvalid, s_axis_tuser, s_axis_tlast,
    output wire s_axis_tready,
    input wire border,
    input wire enable,
    input wire [12:0] image_width, image_height, origin_x, origin_y,
    output wire [DATA_WIDTH-1:0] m_axis_tdata,
    output wire [2:0] m_axis_cvalid,
    output wire m_axis_tvalid, m_axis_tuser, m_axis_tlast,
    input wire m_axis_tready
);
    wire [71:0] w_data;
    wire w_valid, w_sof, w_eol, w_ready;
    wire [12:0] w_x, w_y;
    reg [1:0] valid_pipe, sof_pipe, eol_pipe;
    reg [7:0] red_s0, blue_s0;
    reg [8:0] green_sum_s0;
    reg [23:0] output_data;
    wire window_input_ready;
    wire advance = rst_n && enable && (!valid_pipe[1] || m_axis_tready);
    wire parity_x = w_x[0] ^ origin_x[0];
    wire parity_y = w_y[0] ^ origin_y[0];
    wire [7:0] q00 = w_data[32 +: 8];
    wire [7:0] q01 = w_data[40 +: 8];
    wire [7:0] q10 = w_data[56 +: 8];
    wire [7:0] q11 = w_data[64 +: 8];
    wire [7:0] selected_red = parity_y ?
        (parity_x ? q11 : q10) : (parity_x ? q01 : q00);
    wire [7:0] selected_blue = parity_y ?
        (parity_x ? q00 : q01) : (parity_x ? q10 : q11);
    wire [7:0] green_a = (parity_x ^ parity_y) ? q00 : q01;
    wire [7:0] green_b = (parity_x ^ parity_y) ? q11 : q10;
    wire [8:0] rounded_green = green_sum_s0 + 9'd1;

    assign w_ready = advance;
    assign s_axis_tready = rst_n && (enable ? window_input_ready : m_axis_tready);
    assign m_axis_tdata = !rst_n ? {DATA_WIDTH{1'b0}} :
        (enable ? DATA_WIDTH'(output_data) : s_axis_tdata);
    assign m_axis_cvalid = !rst_n ? 3'd0 : (enable ? 3'b111 : s_axis_cvalid);
    assign m_axis_tvalid = rst_n && (enable ? valid_pipe[1] : s_axis_tvalid);
    assign m_axis_tuser = m_axis_tvalid && (enable ? sof_pipe[1] : s_axis_tuser);
    assign m_axis_tlast = m_axis_tvalid && (enable ? eol_pipe[1] : s_axis_tlast);

    swage_s3_window #(
        .MAX_WIDTH(MAX_WIDTH), .DATA_WIDTH(8)
    ) u_window (
        .clk(clk), .rst_n(rst_n),
        .s_axis_tdata(s_axis_tdata[7:0]), .s_axis_tvalid(enable && s_axis_tvalid),
        .s_axis_tuser(s_axis_tuser), .s_axis_tlast(s_axis_tlast),
        .s_axis_tready(window_input_ready), .border(border),
        .image_width(image_width), .image_height(image_height),
        .origin_x(origin_x), .origin_y(origin_y),
        .w_data(w_data), .w_valid(w_valid), .w_sof(w_sof), .w_eol(w_eol),
        .w_x(w_x), .w_y(w_y), .w_ready(w_ready)
    );

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_pipe <= 0;
            sof_pipe <= 0;
            eol_pipe <= 0;
            red_s0 <= 0;
            blue_s0 <= 0;
            green_sum_s0 <= 0;
            output_data <= 0;
        end else if (advance) begin
            valid_pipe <= {valid_pipe[0], w_valid};
            sof_pipe <= {sof_pipe[0], w_valid && w_sof};
            eol_pipe <= {eol_pipe[0], w_valid && w_eol};
            if (w_valid) begin
                red_s0 <= selected_red;
                blue_s0 <= selected_blue;
                green_sum_s0 <= {1'b0, green_a} + {1'b0, green_b};
            end
            if (valid_pipe[0])
                output_data <= {blue_s0, rounded_green[8:1], red_s0};
            else
                output_data <= 0;
        end
    end
endmodule
