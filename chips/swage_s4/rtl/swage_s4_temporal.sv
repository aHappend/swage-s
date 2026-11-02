module swage_s4_temporal (
    input wire clk, rst_n,
    input wire s_valid,
    output wire s_ready,
    input wire [23:0] s_data, h_data,
    input wire history_valid, temporal_enable,
    input wire [8:0] alpha, threshold,
    output wire m_valid,
    input wire m_ready,
    output wire [23:0] m_data
);
    reg [4:0] valid_pipe;
    reg [23:0] x0, h0, x1, x2, selected3, result4;
    reg [8:0] alpha0, alpha1, threshold0, threshold1;
    reg use0, use1, blend2;
    wire advance = rst_n && (!valid_pipe[4] || m_ready);
    wire [8:0] diff [0:2];
    reg [8:0] diff1 [0:2];
    reg [16:0] current2 [0:2], history2 [0:2];
    wire [8:0] max_rg = diff1[0] > diff1[1] ? diff1[0] : diff1[1];
    wire [8:0] motion = max_rg > diff1[2] ? max_rg : diff1[2];
    assign s_ready = advance;
    assign m_valid = rst_n && valid_pipe[4];
    assign m_data = m_valid ? result4 : 24'd0;
    genvar c;
    generate for (c=0;c<3;c=c+1) begin : lanes
        assign diff[c] = x0[c*8 +:8] >= h0[c*8 +:8] ?
            {1'b0,x0[c*8 +:8]} - {1'b0,h0[c*8 +:8]} :
            {1'b0,h0[c*8 +:8]} - {1'b0,x0[c*8 +:8]};
        wire [17:0] rounded_sum =
            {1'b0,current2[c]} + {1'b0,history2[c]} + 18'd128;
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                diff1[c] <= 0; current2[c] <= 0; history2[c] <= 0;
                selected3[c*8 +:8] <= 0;
            end else if (advance) begin
                diff1[c] <= diff[c];
                current2[c] <= (9'd256-alpha1) * x1[c*8 +:8];
                history2[c] <= alpha1 * h0_delayed[c*8 +:8];
                selected3[c*8 +:8] <= blend2 ? rounded_sum[15:8] : x2[c*8 +:8];
            end
        end
    end endgenerate
    reg [23:0] h0_delayed;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_pipe <= 0; x0 <= 0; h0 <= 0; h0_delayed <= 0;
            x1 <= 0; x2 <= 0; result4 <= 0;
            alpha0 <= 0; alpha1 <= 0; threshold0 <= 0; threshold1 <= 0;
            use0 <= 0; use1 <= 0; blend2 <= 0;
        end else if (advance) begin
            valid_pipe <= {valid_pipe[3:0],s_valid};
            x0 <= s_data; h0 <= h_data;
            alpha0 <= alpha; threshold0 <= threshold;
            use0 <= history_valid && temporal_enable;
            x1 <= x0; h0_delayed <= h0;
            alpha1 <= alpha0; threshold1 <= threshold0; use1 <= use0;
            x2 <= x1;
            blend2 <= use1 && motion < threshold1;
            result4 <= selected3;
        end
    end
endmodule
