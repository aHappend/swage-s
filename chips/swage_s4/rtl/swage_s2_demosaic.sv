module swage_s2_demosaic #(
    parameter integer SRC_WIDTH = 8,
    parameter integer SRC_HEIGHT = 8
) (
    input wire clk, rst_n,
    input wire [7:0] s_axis_tdata,
    input wire s_axis_tvalid, s_axis_tuser, s_axis_tlast,
    output wire s_axis_tready,
    input wire border,
    output wire [23:0] m_axis_tdata,
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
    wire advance = rst_n && (!valid_pipe[1] || m_axis_tready);
    wire [7:0] q00 = w_data[32 +: 8];
    wire [7:0] q01 = w_data[40 +: 8];
    wire [7:0] q10 = w_data[56 +: 8];
    wire [7:0] q11 = w_data[64 +: 8];
    wire [7:0] selected_red = w_y[0] ?
        (w_x[0] ? q11 : q10) : (w_x[0] ? q01 : q00);
    wire [7:0] selected_blue = w_y[0] ?
        (w_x[0] ? q00 : q01) : (w_x[0] ? q10 : q11);
    wire [7:0] green_a = (w_x[0] ^ w_y[0]) ? q00 : q01;
    wire [7:0] green_b = (w_x[0] ^ w_y[0]) ? q11 : q10;
    wire [8:0] rounded_green = green_sum_s0 + 9'd1;

    assign w_ready = advance;
    assign m_axis_tdata = rst_n ? output_data : 24'd0;
    assign m_axis_tvalid = rst_n && valid_pipe[1];
    assign m_axis_tuser = m_axis_tvalid && sof_pipe[1];
    assign m_axis_tlast = m_axis_tvalid && eol_pipe[1];

    swage_s2_window #(
        .SRC_WIDTH(SRC_WIDTH), .SRC_HEIGHT(SRC_HEIGHT), .DATA_WIDTH(8)
    ) u_window (
        .clk(clk), .rst_n(rst_n),
        .s_axis_tdata(s_axis_tdata), .s_axis_tvalid(s_axis_tvalid),
        .s_axis_tuser(s_axis_tuser), .s_axis_tlast(s_axis_tlast),
        .s_axis_tready(s_axis_tready), .border(border),
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
