module swage_s3_chroma_expand #(
    parameter integer MAX_WIDTH = 8
) (
    input wire clk, rst_n, enable,
    input wire [12:0] image_width, image_height,
    input wire [23:0] s_axis_tdata,
    input wire [2:0] s_axis_cvalid,
    input wire s_axis_tvalid, s_axis_tuser, s_axis_tlast,
    output wire s_axis_tready,
    output wire [23:0] m_axis_tdata,
    output wire [2:0] m_axis_cvalid,
    output wire m_axis_tvalid, m_axis_tuser, m_axis_tlast,
    input wire m_axis_tready
);
    localparam integer CHROMA_WIDTH = (MAX_WIDTH+1) >> 1;
    (* ramstyle = "M20K", ram_style = "block", nomem2reg *)
    reg [15:0] chroma_line [0:CHROMA_WIDTH-1];
    reg [15:0] chroma_q, last_chroma, saved_chroma;
    reg [12:0] x, y;
    reg [7:0] luma_s0;
    reg odd_row_s0;
    reg [1:0] valid_pipe, sof_pipe, eol_pipe;
    reg [23:0] output_data;
    wire advance = rst_n && enable && (!valid_pipe[1] || m_axis_tready);
    wire transfer = enable && s_axis_tvalid && s_axis_tready;
    wire [12:0] chroma_addr = x >> 1;
    assign s_axis_tready = rst_n && (enable ? advance : m_axis_tready);
    assign m_axis_tvalid = rst_n && (enable ? valid_pipe[1] : s_axis_tvalid);
    assign m_axis_tdata = !rst_n ? 24'd0 : (enable ? output_data : s_axis_tdata);
    assign m_axis_cvalid = !rst_n ? 3'd0 : (enable ? 3'b111 : s_axis_cvalid);
    assign m_axis_tuser = m_axis_tvalid && (enable ? sof_pipe[1] : s_axis_tuser);
    assign m_axis_tlast = m_axis_tvalid && (enable ? eol_pipe[1] : s_axis_tlast);
    always @(posedge clk) begin
        if (transfer && !y[0] && !x[0])
            chroma_line[chroma_addr] <= s_axis_tdata[23:8];
        if (transfer && y[0])
            chroma_q <= chroma_line[chroma_addr];
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            x <= 0; y <= 0;
            last_chroma <= 0; saved_chroma <= 0; luma_s0 <= 0;
            odd_row_s0 <= 0; valid_pipe <= 0;
            sof_pipe <= 0; eol_pipe <= 0; output_data <= 0;
        end else if (advance) begin
            valid_pipe <= {valid_pipe[0], s_axis_tvalid};
            sof_pipe <= {sof_pipe[0], s_axis_tvalid && s_axis_tuser};
            eol_pipe <= {eol_pipe[0], s_axis_tvalid && s_axis_tlast};
            if (transfer) begin
                luma_s0 <= s_axis_tdata[7:0];
                odd_row_s0 <= y[0];
                saved_chroma <= x[0] ? last_chroma : s_axis_tdata[23:8];
                if (!y[0] && !x[0]) last_chroma <= s_axis_tdata[23:8];
                if (x == image_width-13'd1) begin
                    x <= 0;
                    if (y == image_height-13'd1) y <= 0;
                    else y <= y + 13'd1;
                end else x <= x + 13'd1;
            end
            if (valid_pipe[0])
                output_data <= {odd_row_s0 ? chroma_q : saved_chroma, luma_s0};
            else output_data <= 0;
        end
    end
endmodule
