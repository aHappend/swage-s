module swage_s5_head #(
    parameter integer NUM_CLASSES = 4
) (
    input wire clk, rst_n,
    input wire [47:0] s_data,
    input wire [5:0] s_keep,
    input wire s_valid,
    output wire s_ready,
    input wire s_sof, s_eol, s_frame_last,
    input wire [12:0] s_x, s_y, image_width, image_height,
    input wire [1:0] s_level,
    input wire [48*NC-1:0] class_weight,
    input wire [191:0] reg_weight,
    input wire [32*NC-1:0] class_bias,
    input wire [127:0] reg_bias,
    input wire [5*NC-1:0] class_shift,
    input wire [19:0] reg_shift,
    input wire [15:0] threshold,
    output wire [87:0] m_data,
    output wire m_eligible, m_valid,
    input wire m_ready,
    output wire [1:0] m_level,
    output wire [12:0] m_x, m_y,
    output wire m_frame_last,
    output wire idle
);
    localparam integer NC = NUM_CLASSES >= 1 && NUM_CLASSES <= 8 ? NUM_CLASSES : 1;
    reg [7:0] valid_pipe, last_pipe;
    reg [12:0] x_pipe [0:7], y_pipe [0:7];
    reg [1:0] level_pipe [0:7];
    wire advance = rst_n && (!valid_pipe[7] || m_ready);
    wire signed [39:0] projections5 [0:NC+3];
    wire [15:0] scores5 [0:7];
    wire [15:0] pair_scores [0:3], quad_scores [0:1];
    wire [7:0] pair_classes [0:3], quad_classes [0:1];
    wire [15:0] selected_score;
    wire [7:0] selected_class;
    reg [15:0] confidence6;
    reg [7:0] class6;
    reg [12:0] distances6 [0:3];
    reg [87:0] descriptor7;
    reg eligible7;
    wire [12:0] anchor_x6 = x_pipe[6] << level_pipe[6];
    wire [12:0] anchor_y6 = y_pipe[6] << level_pipe[6];
    wire [13:0] right6 = {1'b0,anchor_x6}+14'd1+{1'b0,distances6[2]};
    wire [13:0] bottom6 = {1'b0,anchor_y6}+14'd1+{1'b0,distances6[3]};
    wire [15:0] xmin6 = distances6[0] > anchor_x6 ? 16'd0 :
        {3'd0,anchor_x6}-{3'd0,distances6[0]};
    wire [15:0] ymin6 = distances6[1] > anchor_y6 ? 16'd0 :
        {3'd0,anchor_y6}-{3'd0,distances6[1]};
    wire [15:0] xmax6 = right6 > {1'b0,image_width} ? {3'd0,image_width} : {2'd0,right6};
    wire [15:0] ymax6 = bottom6 > {1'b0,image_height} ? {3'd0,image_height} : {2'd0,bottom6};
    assign s_ready = advance;
    assign m_valid = rst_n && valid_pipe[7];
    assign m_data = m_valid ? descriptor7 : 88'd0;
    assign m_eligible = m_valid && eligible7;
    assign m_level = m_valid ? level_pipe[7] : 2'd0;
    assign m_x = m_valid ? x_pipe[7] << level_pipe[7] : 13'd0;
    assign m_y = m_valid ? y_pipe[7] << level_pipe[7] : 13'd0;
    assign m_frame_last = m_valid && last_pipe[7];
    assign idle = rst_n && valid_pipe == 0;

    genvar o, k;
    generate for (o=0;o<NC+4;o=o+1) begin : projections
        wire [47:0] weight;
        wire signed [31:0] bias;
        wire [4:0] shift;
        if (o<NC) begin : classification
            assign weight = class_weight[o*48 +:48];
            assign bias = class_bias[o*32 +:32];
            assign shift = class_shift[o*5 +:5];
        end else begin : regression
            assign weight = reg_weight[(o-NC)*48 +:48];
            assign bias = reg_bias[(o-NC)*32 +:32];
            assign shift = reg_shift[(o-NC)*5 +:5];
        end
        reg signed [15:0] products0 [0:5];
        wire signed [39:0] extended0 [0:5];
        reg signed [39:0] pairs1 [0:2];
        reg signed [39:0] pairs2 [0:1];
        reg signed [39:0] sum3, biased4, rounded5;
        wire signed [39:0] bias_extended = {{8{bias[31]}},bias};
        wire signed [39:0] rounded4;
        wire [7:0] unused_quantized;
        for (k=0;k<6;k=k+1) begin : sign_extension
            assign extended0[k] = {{24{products0[k][15]}},products0[k]};
        end
        swage_s5_quantize u_round (
            .value(biased4),.shift(shift),.relu(1'b0),
            .rounded(rounded4),.quantized(unused_quantized)
        );
        assign projections5[o] = rounded5;
        integer i;
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                for (i=0;i<6;i=i+1) products0[i] <= 0;
                for (i=0;i<3;i=i+1) pairs1[i] <= 0;
                pairs2[0] <= 0; pairs2[1] <= 0;
                sum3 <= 0; biased4 <= 0; rounded5 <= 0;
            end else if (advance) begin
                for (i=0;i<6;i=i+1)
                    products0[i] <= $signed(s_keep[i] ? s_data[i*8 +:8] : 8'd0) *
                        $signed(weight[i*8 +:8]);
                for (i=0;i<3;i=i+1) pairs1[i] <= extended0[2*i]+extended0[2*i+1];
                pairs2[0] <= pairs1[0]+pairs1[1]; pairs2[1] <= pairs1[2];
                sum3 <= pairs2[0]+pairs2[1];
                biased4 <= sum3+bias_extended;
                rounded5 <= rounded4;
            end
        end
    end
    for (o=0;o<8;o=o+1) begin : class_scores
        if (o<NC) begin : active_class
            assign scores5[o] = projections5[o] < 0 ? 16'd0 :
                (projections5[o] > 40'sd65535 ? 16'hffff : projections5[o][15:0]);
        end else begin : padding
            assign scores5[o] = 0;
        end
    end
    for (o=0;o<4;o=o+1) begin : pair_select
        assign pair_scores[o] = scores5[2*o] >= scores5[2*o+1] ?
            scores5[2*o] : scores5[2*o+1];
        assign pair_classes[o] = scores5[2*o] >= scores5[2*o+1] ? 8'(2*o) : 8'(2*o+1);
    end
    for (o=0;o<2;o=o+1) begin : quad_select
        assign quad_scores[o] = pair_scores[2*o] >= pair_scores[2*o+1] ?
            pair_scores[2*o] : pair_scores[2*o+1];
        assign quad_classes[o] = pair_scores[2*o] >= pair_scores[2*o+1] ?
            pair_classes[2*o] : pair_classes[2*o+1];
    end
    for (o=0;o<4;o=o+1) begin : distance_clamp
        wire [12:0] clamped5 = projections5[NC+o] < 0 ? 13'd0 :
            (projections5[NC+o] > 40'sd4096 ? 13'd4096 : projections5[NC+o][12:0]);
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) distances6[o] <= 0;
            else if (advance) distances6[o] <= clamped5;
        end
    end endgenerate
    assign selected_score = quad_scores[0] >= quad_scores[1] ? quad_scores[0] : quad_scores[1];
    assign selected_class = quad_scores[0] >= quad_scores[1] ? quad_classes[0] : quad_classes[1];
    integer i;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_pipe <= 0; last_pipe <= 0; confidence6 <= 0; class6 <= 0;
            descriptor7 <= 0; eligible7 <= 0;
            for (i=0;i<8;i=i+1) begin x_pipe[i] <= 0; y_pipe[i] <= 0; level_pipe[i] <= 0; end
        end else if (advance) begin
            valid_pipe <= {valid_pipe[6:0],s_valid};
            last_pipe <= {last_pipe[6:0],s_frame_last};
            x_pipe[0] <= s_x; y_pipe[0] <= s_y; level_pipe[0] <= s_level;
            for (i=1;i<8;i=i+1) begin
                x_pipe[i] <= x_pipe[i-1]; y_pipe[i] <= y_pipe[i-1];
                level_pipe[i] <= level_pipe[i-1];
            end
            confidence6 <= selected_score; class6 <= selected_class;
            descriptor7 <= {confidence6,class6,ymax6,xmax6,ymin6,xmin6};
            eligible7 <= confidence6 >= threshold;
        end
    end
    generate if (NUM_CLASSES < 1 || NUM_CLASSES > 8) begin : invalid_parameters
        initial $fatal(1,"SWAGE_S5_HEAD PARAMETER ERROR");
    end endgenerate
endmodule
