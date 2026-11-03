module swage_s5_pointwise #(
    parameter integer CIN = 12,
    parameter integer COUT = 6
) (
    input wire clk, rst_n,
    input wire [8*CI-1:0] s_data,
    input wire [CI-1:0] s_keep,
    input wire s_valid,
    output wire s_ready,
    input wire s_sof, s_eol, s_frame_last,
    input wire [12:0] s_x, s_y, image_width, image_height,
    input wire [8*CI*CO-1:0] pw_weight,
    input wire [32*CO-1:0] pw_bias,
    input wire [5*CO-1:0] pw_shift,
    output wire [8*CO-1:0] m_data,
    output wire [CO-1:0] m_keep,
    output wire m_valid,
    input wire m_ready,
    output wire m_sof, m_eol, m_frame_last,
    output wire [12:0] m_x, m_y,
    output wire idle
);
    localparam integer CI = CIN >= 1 && CIN <= 12 ? CIN : 1;
    localparam integer CO = COUT >= 1 && COUT <= 8 ? COUT : 1;
    reg [4:0] valid_pipe, sof_pipe, eol_pipe, last_pipe;
    reg [12:0] x_pipe [0:4], y_pipe [0:4];
    wire advance = rst_n && (!valid_pipe[4] || m_ready);
    assign s_ready = advance;
    assign m_valid = rst_n && valid_pipe[4];
    assign m_keep = m_valid ? {CO{1'b1}} : {CO{1'b0}};
    assign m_sof = m_valid && sof_pipe[4];
    assign m_eol = m_valid && eol_pipe[4];
    assign m_frame_last = m_valid && last_pipe[4];
    assign m_x = m_valid ? x_pipe[4] : 13'd0;
    assign m_y = m_valid ? y_pipe[4] : 13'd0;
    assign idle = rst_n && valid_pipe == 0;
    genvar o, k;
    generate for (o=0;o<CO;o=o+1) begin : outputs
        reg signed [15:0] products0 [0:CI-1];
        wire signed [39:0] padded [0:11];
        reg signed [39:0] groups1 [0:2];
        reg signed [39:0] sum2, biased3;
        reg [7:0] result4;
        wire [7:0] quantized3;
        wire signed [39:0] unused_rounded;
        wire signed [31:0] bias = pw_bias[o*32 +:32];
        wire signed [39:0] bias_extended = {{8{bias[31]}},bias};
        for (k=0;k<12;k=k+1) begin : inputs
            if (k<CI) begin : active_lane
                assign padded[k] = {{24{products0[k][15]}},products0[k]};
            end else begin : padding
                assign padded[k] = 40'sd0;
            end
        end
        swage_s5_quantize u_quantize (
            .value(biased3),.shift(pw_shift[o*5 +:5]),.relu(1'b1),
            .rounded(unused_rounded),.quantized(quantized3)
        );
        assign m_data[o*8 +:8] = m_valid ? result4 : 8'd0;
        integer i;
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                for (i=0;i<CI;i=i+1) products0[i] <= 0;
                for (i=0;i<3;i=i+1) groups1[i] <= 0;
                sum2 <= 0; biased3 <= 0; result4 <= 0;
            end else if (advance) begin
                for (i=0;i<CI;i=i+1)
                    products0[i] <= $signed(s_keep[i] ? s_data[i*8 +:8] : 8'd0) *
                        $signed(pw_weight[(o*CI+i)*8 +:8]);
                for (i=0;i<3;i=i+1)
                    groups1[i] <= (padded[4*i]+padded[4*i+1]) +
                        (padded[4*i+2]+padded[4*i+3]);
                sum2 <= (groups1[0]+groups1[1])+groups1[2];
                biased3 <= sum2+bias_extended;
                result4 <= quantized3;
            end
        end
    end endgenerate
    integer i;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_pipe <= 0; sof_pipe <= 0; eol_pipe <= 0; last_pipe <= 0;
            for (i=0;i<5;i=i+1) begin x_pipe[i] <= 0; y_pipe[i] <= 0; end
        end else if (advance) begin
            valid_pipe <= {valid_pipe[3:0],s_valid};
            sof_pipe <= {sof_pipe[3:0],s_sof};
            eol_pipe <= {eol_pipe[3:0],s_eol};
            last_pipe <= {last_pipe[3:0],s_frame_last};
            x_pipe[0] <= s_x; y_pipe[0] <= s_y;
            for (i=1;i<5;i=i+1) begin
                x_pipe[i] <= x_pipe[i-1]; y_pipe[i] <= y_pipe[i-1];
            end
        end
    end
    generate if (CIN < 1 || CIN > 12 || COUT < 1 || COUT > 8) begin : invalid_parameters
        initial $fatal(1,"SWAGE_S5_POINTWISE PARAMETER ERROR");
    end endgenerate
endmodule
