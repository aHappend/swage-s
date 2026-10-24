module swage_s1_interp #(
    parameter integer PIXEL_BITS = 8,
    parameter integer CHANNELS = 1,
    parameter integer DATA_WIDTH = 8,
    parameter integer PHASE_FRAC = 8
) (
    input wire clk, rst_n,
    input wire issue_valid, issue_sof, issue_eol,
    input wire [PHASE_FRAC-1:0] fx, fy,
    input wire [DATA_WIDTH-1:0] p00, p01, p10, p11,
    output wire pipe_advance, pipe_empty,
    output wire [DATA_WIDTH-1:0] m_axis_tdata,
    output wire m_axis_tvalid, m_axis_tuser, m_axis_tlast,
    input wire m_axis_tready
);
    localparam integer F = PHASE_FRAC;
    localparam integer HW = PIXEL_BITS + F + 1;
    localparam integer VW = PIXEL_BITS + 2*F + 3;
    localparam [F:0] SCALE = {1'b1, {F{1'b0}}};
    localparam [VW-1:0] ROUND = {{(VW-1){1'b0}}, 1'b1} << (2*F-1);
    reg [5:0] valid_pipe, sof_pipe, eol_pipe;
    reg [F:0] wx0_s1, wx1_s1, wy0_s1, wy1_s1;
    reg [F:0] wy0_s2, wy1_s2, wy0_s3, wy1_s3;
    wire [CHANNELS*PIXEL_BITS-1:0] packed_result;
    reg [DATA_WIDTH-1:0] output_data;
    assign pipe_advance = rst_n && (!valid_pipe[5] || m_axis_tready);
    assign pipe_empty = !(|valid_pipe);
    assign m_axis_tvalid = rst_n && valid_pipe[5];
    assign m_axis_tuser = rst_n && valid_pipe[5] && sof_pipe[5];
    assign m_axis_tlast = rst_n && valid_pipe[5] && eol_pipe[5];
    assign m_axis_tdata = rst_n ? output_data : {DATA_WIDTH{1'b0}};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_pipe <= 0;
            sof_pipe <= 0;
            eol_pipe <= 0;
            wx0_s1 <= 0;
            wx1_s1 <= 0;
            wy0_s1 <= 0;
            wy1_s1 <= 0;
            wy0_s2 <= 0;
            wy1_s2 <= 0;
            wy0_s3 <= 0;
            wy1_s3 <= 0;
            output_data <= 0;
        end else if (pipe_advance) begin
            valid_pipe <= {valid_pipe[4:0], issue_valid};
            sof_pipe <= {sof_pipe[4:0], issue_valid && issue_sof};
            eol_pipe <= {eol_pipe[4:0], issue_valid && issue_eol};
            wx0_s1 <= SCALE - {1'b0, fx};
            wx1_s1 <= {1'b0, fx};
            wy0_s1 <= SCALE - {1'b0, fy};
            wy1_s1 <= {1'b0, fy};
            wy0_s2 <= wy0_s1;
            wy1_s2 <= wy1_s1;
            wy0_s3 <= wy0_s2;
            wy1_s3 <= wy1_s2;
            if (valid_pipe[4])
                output_data <= {{(DATA_WIDTH-CHANNELS*PIXEL_BITS){1'b0}}, packed_result};
            else
                output_data <= 0;
        end
    end

    genvar channel;
    generate for (channel = 0; channel < CHANNELS; channel = channel + 1) begin : channels
        reg [HW-1:0] h00_s2, h01_s2, h10_s2, h11_s2;
        reg [HW-1:0] h0_s3, h1_s3;
        reg [VW-1:0] v0_s4, v1_s4;
        reg [PIXEL_BITS-1:0] result_s5;
        wire [VW-1:0] sum_bits = v0_s4 ^ v1_s4 ^ ROUND;
        wire [VW-1:0] carry_bits =
            ((v0_s4 & v1_s4) | (v0_s4 & ROUND) | (v1_s4 & ROUND)) << 1;
        wire [VW-1:0] rounded_sum = sum_bits + carry_bits;
        assign packed_result[channel*PIXEL_BITS +: PIXEL_BITS] = result_s5;

        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                h00_s2 <= 0;
                h01_s2 <= 0;
                h10_s2 <= 0;
                h11_s2 <= 0;
                h0_s3 <= 0;
                h1_s3 <= 0;
                v0_s4 <= 0;
                v1_s4 <= 0;
                result_s5 <= 0;
            end else if (pipe_advance) begin
                if (valid_pipe[0]) begin
                    h00_s2 <= p00[channel*PIXEL_BITS +: PIXEL_BITS] * wx0_s1;
                    h01_s2 <= p01[channel*PIXEL_BITS +: PIXEL_BITS] * wx1_s1;
                    h10_s2 <= p10[channel*PIXEL_BITS +: PIXEL_BITS] * wx0_s1;
                    h11_s2 <= p11[channel*PIXEL_BITS +: PIXEL_BITS] * wx1_s1;
                end
                if (valid_pipe[1]) begin
                    h0_s3 <= h00_s2 + h01_s2;
                    h1_s3 <= h10_s2 + h11_s2;
                end
                if (valid_pipe[2]) begin
                    v0_s4 <= h0_s3 * wy0_s3;
                    v1_s4 <= h1_s3 * wy1_s3;
                end
                if (valid_pipe[3])
                    result_s5 <= rounded_sum[2*F +: PIXEL_BITS];
            end
        end
    end endgenerate
endmodule
