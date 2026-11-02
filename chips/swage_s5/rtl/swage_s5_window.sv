module swage_s5_window #(
    parameter integer CIN = 3,
    parameter integer MAX_WIDTH = 4096
) (
    input wire clk, rst_n,
    input wire [8*CI-1:0] s_data,
    input wire [CI-1:0] s_keep,
    input wire s_valid,
    output wire s_ready,
    input wire s_sof, s_eol, s_frame_last,
    input wire [12:0] s_x, s_y, image_width, image_height,
    output wire [72*CI-1:0] m_data,
    output wire [CI-1:0] m_keep,
    output wire m_valid,
    input wire m_ready,
    output wire m_sof, m_eol, m_frame_last,
    output wire [12:0] m_x, m_y,
    output wire idle
);
    localparam integer CI = CIN >= 1 && CIN <= 6 ? CIN : 1;
    localparam integer MW = MAX_WIDTH >= 1 && MAX_WIDTH <= 4096 ? MAX_WIDTH : 1;
    reg valid0, valid1, sof0, sof1, eol0, eol1, last0, last1;
    reg [12:0] x0, y0, x1, y1;
    reg [CI-1:0] keep0, keep1;
    wire advance = rst_n && (!valid1 || m_ready);
    assign s_ready = advance;
    assign m_valid = rst_n && valid1;
    assign m_keep = m_valid ? keep1 : {CI{1'b0}};
    assign m_sof = m_valid && sof1;
    assign m_eol = m_valid && eol1;
    assign m_frame_last = m_valid && last1;
    assign m_x = m_valid ? x1 : 13'd0;
    assign m_y = m_valid ? y1 : 13'd0;
    assign idle = rst_n && !valid0 && !valid1;

    genvar c;
    generate for (c=0;c<CI;c=c+1) begin : channels
        (* ramstyle = "M20K", nomem2reg *) reg [7:0] row_even [0:MW-1];
        (* ramstyle = "M20K", nomem2reg *) reg [7:0] row_odd [0:MW-1];
        reg [7:0] even_q, odd_q, center0;
        reg [7:0] top1, top2, mid1, mid2, bot1, bot2;
        reg [71:0] assembled1;
        wire [7:0] input_lane = s_keep[c] ? s_data[c*8 +:8] : 8'd0;
        wire [7:0] top = y0 < 2 ? 8'd0 : (y0[0] ? odd_q : even_q);
        wire [7:0] middle = y0 == 0 ? 8'd0 : (y0[0] ? even_q : odd_q);
        wire [7:0] top_left1 = x0 == 0 ? 8'd0 : top1;
        wire [7:0] top_left2 = x0 < 2 ? 8'd0 : top2;
        wire [7:0] mid_left1 = x0 == 0 ? 8'd0 : mid1;
        wire [7:0] mid_left2 = x0 < 2 ? 8'd0 : mid2;
        wire [7:0] bot_left1 = x0 == 0 ? 8'd0 : bot1;
        wire [7:0] bot_left2 = x0 < 2 ? 8'd0 : bot2;
        assign m_data[c*72 +:72] = m_valid ? assembled1 : 72'd0;

        // Parity banks preserve both old rows, including read-before-write at width one.
        always @(posedge clk) begin
            if (rst_n && advance && s_valid) begin
                even_q <= row_even[s_x];
                odd_q <= row_odd[s_x];
                if (s_y[0]) row_odd[s_x] <= input_lane;
                else row_even[s_x] <= input_lane;
            end
        end
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                center0 <= 0; top1 <= 0; top2 <= 0;
                mid1 <= 0; mid2 <= 0; bot1 <= 0; bot2 <= 0; assembled1 <= 0;
            end else if (advance) begin
                if (s_valid) center0 <= input_lane;
                if (valid0) begin
                    assembled1 <= {center0,bot_left1,bot_left2,
                        middle,mid_left1,mid_left2,top,top_left1,top_left2};
                    top1 <= top; top2 <= top_left1;
                    mid1 <= middle; mid2 <= mid_left1;
                    bot1 <= center0; bot2 <= bot_left1;
                end
            end
        end
    end endgenerate
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid0 <= 0; valid1 <= 0; sof0 <= 0; sof1 <= 0;
            eol0 <= 0; eol1 <= 0; last0 <= 0; last1 <= 0;
            x0 <= 0; y0 <= 0; x1 <= 0; y1 <= 0; keep0 <= 0; keep1 <= 0;
        end else if (advance) begin
            valid0 <= s_valid; valid1 <= valid0;
            sof0 <= s_sof; sof1 <= sof0; eol0 <= s_eol; eol1 <= eol0;
            last0 <= s_frame_last; last1 <= last0;
            x0 <= s_x; y0 <= s_y; x1 <= x0; y1 <= y0;
            keep0 <= s_keep; keep1 <= keep0;
        end
    end
    generate if (CIN < 1 || CIN > 6 || MAX_WIDTH < 1 || MAX_WIDTH > 4096) begin : invalid_parameters
        initial $fatal(1,"SWAGE_S5_WINDOW PARAMETER ERROR");
    end endgenerate
endmodule
