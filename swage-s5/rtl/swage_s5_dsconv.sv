module swage_s5_dsconv #(
    parameter integer CIN = 3,
    parameter integer COUT = 6,
    parameter integer MAX_WIDTH = 4096
) (
    input wire clk, rst_n,
    input wire [8*CI-1:0] s_data,
    input wire [CI-1:0] s_keep,
    input wire s_valid,
    output wire s_ready,
    input wire s_sof, s_eol, s_frame_last,
    input wire [12:0] s_x, s_y, image_width, image_height,
    input wire [72*CI-1:0] dw_weight,
    input wire [8*CI*CO-1:0] pw_weight,
    input wire [32*CI-1:0] dw_bias,
    input wire [32*CO-1:0] pw_bias,
    input wire [5*CI-1:0] dw_shift,
    input wire [5*CO-1:0] pw_shift,
    output wire [8*CO-1:0] m_data,
    output wire [CO-1:0] m_keep,
    output wire m_valid,
    input wire m_ready,
    output wire m_sof, m_eol, m_frame_last,
    output wire [12:0] m_x, m_y,
    output wire idle
);
    localparam integer CI = CIN >= 1 && CIN <= 6 ? CIN : 1;
    localparam integer CO = COUT >= 1 && COUT <= 6 ? COUT : 1;
    wire [72*CI-1:0] window_data;
    wire [CI-1:0] window_keep;
    wire window_valid, window_sof, window_eol, window_last, window_idle;
    wire [12:0] window_x, window_y;
    reg [10:0] valid_pipe, sof_pipe, eol_pipe, last_pipe;
    reg [12:0] x_pipe [0:10], y_pipe [0:10];
    wire advance = rst_n && (!valid_pipe[10] || m_ready);
    wire [8*CI-1:0] depthwise6;
    assign m_valid = rst_n && valid_pipe[10];
    assign m_keep = m_valid ? {CO{1'b1}} : {CO{1'b0}};
    assign m_sof = m_valid && sof_pipe[10];
    assign m_eol = m_valid && eol_pipe[10];
    assign m_frame_last = m_valid && last_pipe[10];
    assign m_x = m_valid ? x_pipe[10] : 13'd0;
    assign m_y = m_valid ? y_pipe[10] : 13'd0;
    assign idle = rst_n && window_idle && valid_pipe == 0;
    swage_s5_window #(.CIN(CI),.MAX_WIDTH(MAX_WIDTH)) u_window (
        .clk(clk),.rst_n(rst_n),.s_data(s_data),.s_keep(s_keep),
        .s_valid(s_valid),.s_ready(s_ready),.s_sof(s_sof),.s_eol(s_eol),
        .s_frame_last(s_frame_last),.s_x(s_x),.s_y(s_y),
        .image_width(image_width),.image_height(image_height),
        .m_data(window_data),.m_keep(window_keep),.m_valid(window_valid),.m_ready(advance),
        .m_sof(window_sof),.m_eol(window_eol),.m_frame_last(window_last),
        .m_x(window_x),.m_y(window_y),.idle(window_idle)
    );
    genvar c, k, o;
    generate for (c=0;c<CI;c=c+1) begin : depthwise
        reg signed [15:0] products0 [0:8];
        wire signed [39:0] padded0 [0:9];
        reg signed [39:0] pairs1 [0:4];
        wire signed [39:0] padded1 [0:5];
        reg signed [39:0] pairs2 [0:2];
        reg signed [39:0] pairs3 [0:1];
        reg signed [39:0] sum4, biased5;
        reg [7:0] result6;
        wire [7:0] quantized5;
        wire signed [39:0] unused_rounded;
        wire signed [31:0] bias = dw_bias[c*32 +:32];
        wire signed [39:0] bias_extended = {{8{bias[31]}},bias};
        for (k=0;k<9;k=k+1) begin : product_extension
            assign padded0[k] = {{24{products0[k][15]}},products0[k]};
        end
        assign padded0[9] = 40'sd0;
        for (k=0;k<5;k=k+1) begin : pair_extension
            assign padded1[k] = pairs1[k];
        end
        assign padded1[5] = 40'sd0;
        swage_s5_quantize u_quantize (
            .value(biased5),.shift(dw_shift[c*5 +:5]),.relu(1'b0),
            .rounded(unused_rounded),.quantized(quantized5)
        );
        assign depthwise6[c*8 +:8] = result6;
        integer i;
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                for (i=0;i<9;i=i+1) products0[i] <= 0;
                for (i=0;i<5;i=i+1) pairs1[i] <= 0;
                for (i=0;i<3;i=i+1) pairs2[i] <= 0;
                pairs3[0] <= 0; pairs3[1] <= 0;
                sum4 <= 0; biased5 <= 0; result6 <= 0;
            end else if (advance) begin
                for (i=0;i<9;i=i+1)
                    products0[i] <= $signed(window_data[(c*9+i)*8 +:8]) *
                        $signed(dw_weight[(c*9+i)*8 +:8]);
                for (i=0;i<5;i=i+1) pairs1[i] <= padded0[2*i]+padded0[2*i+1];
                for (i=0;i<3;i=i+1) pairs2[i] <= padded1[2*i]+padded1[2*i+1];
                pairs3[0] <= pairs2[0]+pairs2[1]; pairs3[1] <= pairs2[2];
                sum4 <= pairs3[0]+pairs3[1];
                biased5 <= sum4+bias_extended;
                result6 <= quantized5;
            end
        end
    end
    for (o=0;o<CO;o=o+1) begin : pointwise
        reg signed [15:0] products7 [0:CI-1];
        wire signed [39:0] padded7 [0:11];
        reg signed [39:0] groups8 [0:2];
        reg signed [39:0] sum9;
        reg [7:0] result10;
        wire signed [31:0] bias = pw_bias[o*32 +:32];
        wire signed [39:0] bias_extended = {{8{bias[31]}},bias};
        wire signed [39:0] biased9 = sum9+bias_extended;
        wire [7:0] quantized9;
        wire signed [39:0] unused_rounded;
        for (k=0;k<12;k=k+1) begin : inputs
            if (k<CI) begin : active_lane
                assign padded7[k] = {{24{products7[k][15]}},products7[k]};
            end else begin : padding
                assign padded7[k] = 40'sd0;
            end
        end
        swage_s5_quantize u_quantize (
            .value(biased9),.shift(pw_shift[o*5 +:5]),.relu(1'b1),
            .rounded(unused_rounded),.quantized(quantized9)
        );
        assign m_data[o*8 +:8] = m_valid ? result10 : 8'd0;
        integer i;
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                for (i=0;i<CI;i=i+1) products7[i] <= 0;
                for (i=0;i<3;i=i+1) groups8[i] <= 0;
                sum9 <= 0; result10 <= 0;
            end else if (advance) begin
                for (i=0;i<CI;i=i+1)
                    products7[i] <= $signed(depthwise6[i*8 +:8]) *
                        $signed(pw_weight[(o*CI+i)*8 +:8]);
                for (i=0;i<3;i=i+1)
                    groups8[i] <= (padded7[4*i]+padded7[4*i+1]) +
                        (padded7[4*i+2]+padded7[4*i+3]);
                sum9 <= (groups8[0]+groups8[1])+groups8[2];
                result10 <= quantized9;
            end
        end
    end endgenerate
    integer i;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_pipe <= 0; sof_pipe <= 0; eol_pipe <= 0; last_pipe <= 0;
            for (i=0;i<11;i=i+1) begin x_pipe[i] <= 0; y_pipe[i] <= 0; end
        end else if (advance) begin
            valid_pipe <= {valid_pipe[9:0],window_valid};
            sof_pipe <= {sof_pipe[9:0],window_sof};
            eol_pipe <= {eol_pipe[9:0],window_eol};
            last_pipe <= {last_pipe[9:0],window_last};
            x_pipe[0] <= window_x; y_pipe[0] <= window_y;
            for (i=1;i<11;i=i+1) begin
                x_pipe[i] <= x_pipe[i-1]; y_pipe[i] <= y_pipe[i-1];
            end
        end
    end
    generate if (CIN < 1 || CIN > 6 || COUT < 1 || COUT > 6 ||
        MAX_WIDTH < 1 || MAX_WIDTH > 4096) begin : invalid_parameters
        initial $fatal(1,"SWAGE_S5_DSCONV PARAMETER ERROR");
    end endgenerate
endmodule
