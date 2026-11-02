module swage_s2_convolution #(
    parameter integer SRC_WIDTH = 8,
    parameter integer SRC_HEIGHT = 8,
    parameter integer DATA_WIDTH = 8,
    parameter integer CHANNELS = 1
) (
    input wire clk, rst_n,
    input wire [DATA_WIDTH-1:0] s_axis_tdata,
    input wire s_axis_tvalid, s_axis_tuser, s_axis_tlast,
    output wire s_axis_tready,
    input wire border,
    input wire cfg_abs,
    input wire [4:0] cfg_shift,
    input wire [143:0] cfg_coeff,
    output wire [DATA_WIDTH-1:0] m_axis_tdata,
    output wire m_axis_tvalid, m_axis_tuser, m_axis_tlast,
    input wire m_axis_tready
);
    wire [9*DATA_WIDTH-1:0] w_data;
    wire w_valid, w_sof, w_eol, w_ready;
    wire [12:0] w_x, w_y;
    reg [8:0] valid_pipe, sof_pipe, eol_pipe;
    reg [7:0] abs_pipe;
    reg [4:0] shift0, shift1, shift2, shift3, shift4, shift5, shift6;
    wire [CHANNELS*8-1:0] packed_result;
    wire advance = rst_n && (!valid_pipe[8] || m_axis_tready);

    assign w_ready = advance;
    assign m_axis_tvalid = rst_n && valid_pipe[8];
    assign m_axis_tuser = m_axis_tvalid && sof_pipe[8];
    assign m_axis_tlast = m_axis_tvalid && eol_pipe[8];
    assign m_axis_tdata = rst_n ?
        {{(DATA_WIDTH-CHANNELS*8){1'b0}}, packed_result} : {DATA_WIDTH{1'b0}};

    swage_s2_window #(
        .SRC_WIDTH(SRC_WIDTH), .SRC_HEIGHT(SRC_HEIGHT), .DATA_WIDTH(DATA_WIDTH)
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
            abs_pipe <= 0;
            shift0 <= 0;
            shift1 <= 0;
            shift2 <= 0;
            shift3 <= 0;
            shift4 <= 0;
            shift5 <= 0;
            shift6 <= 0;
        end else if (advance) begin
            valid_pipe <= {valid_pipe[7:0], w_valid};
            sof_pipe <= {sof_pipe[7:0], w_valid && w_sof};
            eol_pipe <= {eol_pipe[7:0], w_valid && w_eol};
            abs_pipe <= {abs_pipe[6:0], cfg_abs};
            shift0 <= cfg_shift;
            shift1 <= shift0;
            shift2 <= shift1;
            shift3 <= shift2;
            shift4 <= shift3;
            shift5 <= shift4;
            shift6 <= shift5;
        end
    end

    genvar channel, tap;
    generate for (channel = 0; channel < CHANNELS; channel = channel + 1) begin : channels
        wire signed [31:0] product_s0 [0:8];
        reg signed [31:0] pair0_s1, pair1_s1, pair2_s1, pair3_s1, pair4_s1;
        reg signed [31:0] triple0_s2, triple1_s2, triple2_s2;
        reg signed [31:0] half0_s3, half1_s3;
        reg signed [31:0] sum_s4;
        reg [32:0] magnitude_s5, rounded_s6, shifted_s7;
        reg negative_s5, negative_s6, negative_s7;
        reg [7:0] result_s8;
        wire [32:0] magnitude_value = sum_s4[31] ?
            {1'b0, ~sum_s4} + 33'd1 : {1'b0, sum_s4};
        wire [32:0] rounding_bias = shift5 == 0 ? 33'd0 :
            (33'd1 << (shift5 - 5'd1));
        assign packed_result[channel*8 +: 8] = result_s8;

        for (tap = 0; tap < 9; tap = tap + 1) begin : taps
            wire signed [15:0] coefficient = cfg_coeff[tap*16 +: 16];
            wire signed [8:0] sample_value = {1'b0, w_data[tap*DATA_WIDTH+channel*8 +: 8]};
            wire signed [24:0] product_value = coefficient * sample_value;
            reg signed [31:0] product_reg;
            assign product_s0[tap] = product_reg;
            always @(posedge clk or negedge rst_n) begin
                if (!rst_n)
                    product_reg <= 0;
                else if (advance && w_valid)
                    product_reg <= {{7{product_value[24]}}, product_value};
            end
        end

        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                pair0_s1 <= 0;
                pair1_s1 <= 0;
                pair2_s1 <= 0;
                pair3_s1 <= 0;
                pair4_s1 <= 0;
                triple0_s2 <= 0;
                triple1_s2 <= 0;
                triple2_s2 <= 0;
                half0_s3 <= 0;
                half1_s3 <= 0;
                sum_s4 <= 0;
                magnitude_s5 <= 0;
                rounded_s6 <= 0;
                shifted_s7 <= 0;
                negative_s5 <= 0;
                negative_s6 <= 0;
                negative_s7 <= 0;
                result_s8 <= 0;
            end else if (advance) begin
                if (valid_pipe[0]) begin
                    pair0_s1 <= product_s0[0] + product_s0[1];
                    pair1_s1 <= product_s0[2] + product_s0[3];
                    pair2_s1 <= product_s0[4] + product_s0[5];
                    pair3_s1 <= product_s0[6] + product_s0[7];
                    pair4_s1 <= product_s0[8];
                end
                if (valid_pipe[1]) begin
                    triple0_s2 <= pair0_s1 + pair1_s1;
                    triple1_s2 <= pair2_s1 + pair3_s1;
                    triple2_s2 <= pair4_s1;
                end
                if (valid_pipe[2]) begin
                    half0_s3 <= triple0_s2 + triple1_s2;
                    half1_s3 <= triple2_s2;
                end
                if (valid_pipe[3])
                    sum_s4 <= half0_s3 + half1_s3;
                if (valid_pipe[4]) begin
                    magnitude_s5 <= magnitude_value;
                    negative_s5 <= sum_s4[31];
                end
                if (valid_pipe[5]) begin
                    rounded_s6 <= magnitude_s5 + rounding_bias;
                    negative_s6 <= negative_s5;
                end
                if (valid_pipe[6]) begin
                    shifted_s7 <= rounded_s6 >> shift6;
                    negative_s7 <= negative_s6;
                end
                if (valid_pipe[7]) begin
                    if (negative_s7 && !abs_pipe[7])
                        result_s8 <= 0;
                    else if (shifted_s7 > 33'd255)
                        result_s8 <= 8'd255;
                    else
                        result_s8 <= shifted_s7[7:0];
                end else begin
                    result_s8 <= 0;
                end
            end
        end
    end endgenerate
endmodule
