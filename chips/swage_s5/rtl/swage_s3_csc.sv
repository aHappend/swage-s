module swage_s3_csc #(
    parameter integer MAX_WIDTH = 8,
    parameter integer DATA_WIDTH = 24
) (
    input wire clk, rst_n, enable,
    input wire [12:0] image_width, image_height, origin_x, origin_y,
    input wire [1:0] input_format,
    input wire [2:0] mode,
    input wire output_420,
    input wire [143:0] coeff,
    input wire [47:0] in_bias, out_bias,
    input wire [DATA_WIDTH-1:0] s_axis_tdata,
    input wire [2:0] s_axis_cvalid,
    input wire s_axis_tvalid, s_axis_tuser, s_axis_tlast,
    output wire s_axis_tready,
    output wire [DATA_WIDTH-1:0] m_axis_tdata,
    output wire [2:0] m_axis_cvalid,
    output wire m_axis_tvalid, m_axis_tuser, m_axis_tlast,
    input wire m_axis_tready
);
    wire inverse = input_format != 0;
    wire subsample = output_420 && !inverse;
    wire [23:0] expanded_data;
    wire [2:0] expanded_mask;
    wire expanded_valid, expanded_sof, expanded_eol, expand_ready;
    wire advance;
    reg [143:0] matrix;
    reg [47:0] bias_in, bias_out;
    reg [6:0] valid_pipe, sof_pipe, eol_pipe;
    reg [20:0] mask_pipe;
    reg [12:0] x, y;
    wire [23:0] packed_result;
    wire [23:0] masked_result = mask_pipe[20:18] == 3'b001 ?
        {16'd0, packed_result[7:0]} : packed_result;
    assign advance = rst_n && enable && (!valid_pipe[6] || m_axis_tready);
    assign s_axis_tready = rst_n && (enable ? expand_ready : m_axis_tready);
    assign m_axis_tvalid = rst_n && (enable ? valid_pipe[6] : s_axis_tvalid);
    assign m_axis_tdata = !rst_n ? {DATA_WIDTH{1'b0}} :
        (enable ? DATA_WIDTH'(masked_result) : s_axis_tdata);
    assign m_axis_cvalid = !rst_n ? 3'd0 :
        (enable ? mask_pipe[20:18] : s_axis_cvalid);
    assign m_axis_tuser = m_axis_tvalid && (enable ? sof_pipe[6] : s_axis_tuser);
    assign m_axis_tlast = m_axis_tvalid && (enable ? eol_pipe[6] : s_axis_tlast);

    swage_s3_chroma_expand #(.MAX_WIDTH(MAX_WIDTH)) u_chroma_expand (
        .clk(clk), .rst_n(rst_n), .enable(enable && input_format == 2),
        .image_width(image_width), .image_height(image_height),
        .s_axis_tdata(24'(s_axis_tdata)), .s_axis_cvalid(s_axis_cvalid),
        .s_axis_tvalid(enable && s_axis_tvalid),
        .s_axis_tuser(s_axis_tuser), .s_axis_tlast(s_axis_tlast),
        .s_axis_tready(expand_ready), .m_axis_tdata(expanded_data),
        .m_axis_cvalid(expanded_mask), .m_axis_tvalid(expanded_valid),
        .m_axis_tuser(expanded_sof), .m_axis_tlast(expanded_eol),
        .m_axis_tready(advance)
    );

    always @* begin
        matrix = coeff;
        bias_in = in_bias;
        bias_out = out_bias;
        case (mode)
            0: begin
                matrix = {-16'sd585,-16'sd3013,16'sd3598,
                          16'sd3598,-16'sd2384,-16'sd1214,
                          16'sd802,16'sd4130,16'sd2104};
                bias_in = 0;
                bias_out = {16'd128,16'd128,16'd16};
            end
            1: begin
                matrix = {-16'sd330,-16'sd3268,16'sd3598,
                          16'sd3598,-16'sd2774,-16'sd824,
                          16'sd508,16'sd5032,16'sd1496};
                bias_in = 0;
                bias_out = {16'd128,16'd128,16'd16};
            end
            2: begin
                matrix = {16'sd0,16'sd16525,16'sd9539,
                          -16'sd6660,-16'sd3209,16'sd9539,
                          16'sd13075,16'sd0,16'sd9539};
                bias_in = {-16'sd128,-16'sd128,-16'sd16};
                bias_out = 0;
            end
            3: begin
                matrix = {16'sd0,16'sd17305,16'sd9539,
                          -16'sd4366,-16'sd1747,16'sd9539,
                          16'sd14686,16'sd0,16'sd9539};
                bias_in = {-16'sd128,-16'sd128,-16'sd16};
                bias_out = 0;
            end
            default: begin end
        endcase
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_pipe <= 0; sof_pipe <= 0; eol_pipe <= 0;
            x <= 0; y <= 0;
            mask_pipe <= 0;
        end else if (advance) begin
            valid_pipe <= {valid_pipe[5:0],expanded_valid};
            sof_pipe <= {sof_pipe[5:0],expanded_valid && expanded_sof};
            eol_pipe <= {eol_pipe[5:0],expanded_valid && expanded_eol};
            mask_pipe <= {mask_pipe[17:0],
                (subsample && (x[0] || y[0])) ? 3'b001 : 3'b111};
            if (expanded_valid) begin
                if (x == image_width-13'd1) begin
                    x <= 0;
                    if (y == image_height-13'd1) y <= 0;
                    else y <= y + 13'd1;
                end else x <= x + 13'd1;
            end
        end
    end
    genvar c, j;
    generate for (c=0;c<3;c=c+1) begin : components
        wire signed [32:0] products [0:2];
        reg signed [35:0] pair_s2, third_s2, total_s3, rounded_s4, biased_s5;
        reg [7:0] result_s6;
        wire [35:0] magnitude = total_s3[35] ? -total_s3 : total_s3;
        wire [35:0] rounded_magnitude = (magnitude + 36'd4096) >> 13;
        wire signed [35:0] rounded_value = total_s3[35] ?
            -$signed(rounded_magnitude) : $signed(rounded_magnitude);
        wire signed [15:0] output_offset = bias_out[c*16 +:16];
        assign packed_result[c*8 +:8] = result_s6;
        for (j=0;j<3;j=j+1) begin : terms
            wire signed [15:0] input_offset = bias_in[j*16 +:16];
            wire signed [15:0] coefficient = matrix[(c*3+j)*16 +:16];
            wire signed [16:0] sample_unsigned = {9'd0,expanded_data[j*8 +:8]};
            reg signed [16:0] input_s0;
            reg signed [32:0] product_s1;
            assign products[j] = product_s1;
            always @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin input_s0 <= 0; product_s1 <= 0; end
                else if (advance) begin
                    if (expanded_valid) input_s0 <= sample_unsigned + input_offset;
                    if (valid_pipe[0]) product_s1 <= input_s0 * coefficient;
                end
            end
        end
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                pair_s2 <= 0; third_s2 <= 0; total_s3 <= 0;
                rounded_s4 <= 0; biased_s5 <= 0; result_s6 <= 0;
            end else if (advance) begin
                if (valid_pipe[1]) begin
                    pair_s2 <= {{3{products[0][32]}},products[0]} +
                               {{3{products[1][32]}},products[1]};
                    third_s2 <= {{3{products[2][32]}},products[2]};
                end
                if (valid_pipe[2]) total_s3 <= pair_s2 + third_s2;
                if (valid_pipe[3]) rounded_s4 <= rounded_value;
                if (valid_pipe[4]) biased_s5 <= rounded_s4 + output_offset;
                if (valid_pipe[5]) begin
                    if (biased_s5 < 0) result_s6 <= 0;
                    else if (biased_s5 > 255) result_s6 <= 255;
                    else result_s6 <= biased_s5[7:0];
                end else result_s6 <= 0;
            end
        end
    end endgenerate
endmodule
