module swage_s4_normalizer (
    input wire clk, rst_n,
    input wire s_valid,
    output wire s_ready,
    input wire [23:0] s_data,
    input wire [2:0] s_keep,
    input wire s_first, s_last, s_frame_last,
    input wire [31:0] s_id,
    input wire [12:0] s_x, s_y,
    input wire [7:0] s_element,
    input wire [71:0] mean, inv_std,
    input wire bf16,
    output wire m_valid,
    input wire m_ready,
    output wire [47:0] m_data,
    output wire [2:0] m_keep,
    output wire m_first, m_last, m_frame_last,
    output wire [31:0] m_id,
    output wire [12:0] m_x, m_y,
    output wire [7:0] m_element
);
    reg [7:0] valid_pipe, format_pipe, first_pipe, last_pipe, final_pipe;
    reg [2:0] keep_pipe [0:7];
    reg [31:0] id_pipe [0:7];
    reg [12:0] x_pipe [0:7], y_pipe [0:7];
    reg [7:0] element_pipe [0:7];
    reg [47:0] packed6, result7;
    wire [47:0] lanes5;
    wire advance = rst_n && (!valid_pipe[7] || m_ready);
    assign s_ready = advance;
    assign m_valid = rst_n && valid_pipe[7];
    assign m_data = m_valid && keep_pipe[7] != 0 ? result7 : 48'd0;
    assign m_keep = m_valid ? keep_pipe[7] : 3'd0;
    assign m_first = m_valid && first_pipe[7];
    assign m_last = m_valid && last_pipe[7];
    assign m_frame_last = m_valid && final_pipe[7];
    assign m_id = m_valid ? id_pipe[7] : 32'd0;
    assign m_x = m_valid ? x_pipe[7] : 13'd0;
    assign m_y = m_valid ? y_pipe[7] : 13'd0;
    assign m_element = m_valid ? element_pipe[7] : 8'd0;

    function automatic [5:0] leading_position(input [49:0] value);
        reg [49:0] shifted;
        reg [5:0] position;
        begin
            shifted = value;
            position = 0;
            if (|shifted[49:32]) begin shifted = shifted >> 32; position = position+32; end
            if (|shifted[49:16]) begin shifted = shifted >> 16; position = position+16; end
            if (|shifted[49:8]) begin shifted = shifted >> 8; position = position+8; end
            if (|shifted[49:4]) begin shifted = shifted >> 4; position = position+4; end
            if (|shifted[49:2]) begin shifted = shifted >> 2; position = position+2; end
            if (shifted[1]) position = position+1;
            leading_position = position;
        end
    endfunction

    genvar c;
    generate for (c=0;c<3;c=c+1) begin : channels
        reg [7:0] pixel0;
        reg signed [23:0] mean0;
        reg [23:0] reciprocal0, reciprocal1;
        reg signed [24:0] difference1;
        reg signed [49:0] product2;
        reg sign3, zero3;
        reg [49:0] magnitude3;
        reg [5:0] leading3;
        reg [33:0] q_floor3;
        reg q_increment3;
        reg [34:0] q_candidate4;
        reg sign4, zero4;
        reg [7:0] exponent4, bf_base4;
        reg bf_increment4;
        reg [15:0] lane5;
        wire [49:0] product_magnitude = product2[49] ? -product2 : product2;
        wire [5:0] product_leading = leading_position(product_magnitude);
        wire [5:0] bf_shift = leading3 >= 7 ? leading3-6'd7 : 6'd0;
        wire [49:0] bf_floor = leading3 >= 7 ?
            magnitude3 >> bf_shift : magnitude3 << (6'd7-leading3);
        wire [49:0] bf_mask = (50'd1 << bf_shift)-50'd1;
        wire [49:0] bf_remainder = magnitude3 & bf_mask;
        wire [49:0] bf_half = bf_shift != 0 ? 50'd1 << (bf_shift-6'd1) : 50'd0;
        wire bf_increment = bf_shift != 0 &&
            (bf_remainder > bf_half || (bf_remainder == bf_half && bf_floor[0]));
        wire [8:0] bf_rounded = {1'b0,bf_base4}+{8'd0,bf_increment4};
        wire [7:0] bf_exponent = exponent4 + {7'd0,bf_rounded[8]};
        wire [6:0] bf_fraction = bf_rounded[8] ? 7'd0 : bf_rounded[6:0];
        wire [15:0] q_low_signed = sign4 ? -q_candidate4[15:0] : q_candidate4[15:0];
        assign lanes5[c*16 +:16] = lane5;
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                pixel0 <= 0; mean0 <= 0; reciprocal0 <= 0; reciprocal1 <= 0;
                difference1 <= 0; product2 <= 0;
                sign3 <= 0; zero3 <= 0; magnitude3 <= 0; leading3 <= 0;
                q_floor3 <= 0; q_increment3 <= 0; q_candidate4 <= 0;
                sign4 <= 0; zero4 <= 0; exponent4 <= 0;
                bf_base4 <= 0; bf_increment4 <= 0; lane5 <= 0;
            end else if (advance) begin
                pixel0 <= s_data[c*8 +:8];
                mean0 <= mean[c*24 +:24];
                reciprocal0 <= inv_std[c*24 +:24];
                difference1 <= $signed({9'd0,pixel0,8'd0}) -
                    $signed({mean0[23],mean0});
                reciprocal1 <= reciprocal0;
                product2 <= difference1 * $signed({1'b0,reciprocal1});
                sign3 <= product2[49];
                zero3 <= product_magnitude == 0;
                magnitude3 <= product_magnitude;
                leading3 <= product_leading;
                q_floor3 <= product_magnitude[49:16];
                q_increment3 <= product_magnitude[15:0] > 16'h8000 ||
                    (product_magnitude[15:0] == 16'h8000 && product_magnitude[16]);
                q_candidate4 <= {1'b0,q_floor3}+{34'd0,q_increment3};
                sign4 <= sign3; zero4 <= zero3;
                exponent4 <= 8'd103+{2'd0,leading3};
                bf_base4 <= bf_floor[7:0];
                bf_increment4 <= bf_increment;
                if (format_pipe[4]) begin
                    lane5 <= zero4 ? 16'd0 : {sign4,bf_exponent,bf_fraction};
                end else if (!sign4 && q_candidate4 > 35'd32767) lane5 <= 16'h7fff;
                else if (sign4 && q_candidate4 > 35'd32768) lane5 <= 16'h8000;
                else lane5 <= q_low_signed;
            end
        end
    end endgenerate
    integer i;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_pipe <= 0; format_pipe <= 0; first_pipe <= 0;
            last_pipe <= 0; final_pipe <= 0; packed6 <= 0; result7 <= 0;
            for (i=0;i<8;i=i+1) begin
                keep_pipe[i] <= 0; id_pipe[i] <= 0;
                x_pipe[i] <= 0; y_pipe[i] <= 0; element_pipe[i] <= 0;
            end
        end else if (advance) begin
            valid_pipe <= {valid_pipe[6:0],s_valid};
            format_pipe <= {format_pipe[6:0],bf16};
            first_pipe <= {first_pipe[6:0],s_first};
            last_pipe <= {last_pipe[6:0],s_last};
            final_pipe <= {final_pipe[6:0],s_frame_last};
            keep_pipe[0] <= s_keep; id_pipe[0] <= s_id;
            x_pipe[0] <= s_x; y_pipe[0] <= s_y; element_pipe[0] <= s_element;
            for (i=1;i<8;i=i+1) begin
                keep_pipe[i] <= keep_pipe[i-1]; id_pipe[i] <= id_pipe[i-1];
                x_pipe[i] <= x_pipe[i-1]; y_pipe[i] <= y_pipe[i-1];
                element_pipe[i] <= element_pipe[i-1];
            end
            packed6 <= lanes5;
            result7 <= packed6;
        end
    end
endmodule
