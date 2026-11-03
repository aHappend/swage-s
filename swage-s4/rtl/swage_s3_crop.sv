module swage_s3_crop #(
    parameter integer SRC_WIDTH = 8,
    parameter integer SRC_HEIGHT = 8,
    parameter integer DATA_WIDTH = 8
) (
    input wire clk, rst_n, enable,
    input wire [12:0] roi_x, roi_y, roi_width, roi_height,
    input wire [DATA_WIDTH-1:0] s_axis_tdata,
    input wire [2:0] s_axis_cvalid,
    input wire s_axis_tvalid, s_axis_tuser, s_axis_tlast,
    output wire s_axis_tready,
    output wire [DATA_WIDTH-1:0] m_axis_tdata,
    output wire [2:0] m_axis_cvalid,
    output wire m_axis_tvalid, m_axis_tuser, m_axis_tlast,
    input wire m_axis_tready
);
    reg [12:0] x, y;
    reg [DATA_WIDTH-1:0] data_reg;
    reg [2:0] mask_reg;
    reg valid_reg, sof_reg, eol_reg;
    wire [13:0] right_edge = {1'b0, roi_x} + {1'b0, roi_width};
    wire [13:0] bottom_edge = {1'b0, roi_y} + {1'b0, roi_height};
    wire retain = x >= roi_x && {1'b0,x} < right_edge &&
                  y >= roi_y && {1'b0,y} < bottom_edge;
    wire advance = !valid_reg || m_axis_tready;
    wire transfer = s_axis_tvalid && s_axis_tready;
    assign s_axis_tready = rst_n && (enable ? (!retain || advance) : m_axis_tready);
    assign m_axis_tvalid = rst_n && (enable ? valid_reg : s_axis_tvalid);
    assign m_axis_tdata = !rst_n ? {DATA_WIDTH{1'b0}} :
        (enable ? data_reg : s_axis_tdata);
    assign m_axis_cvalid = !rst_n ? 3'd0 : (enable ? mask_reg : s_axis_cvalid);
    assign m_axis_tuser = m_axis_tvalid && (enable ? sof_reg : s_axis_tuser);
    assign m_axis_tlast = m_axis_tvalid && (enable ? eol_reg : s_axis_tlast);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            x <= 0; y <= 0;
            data_reg <= 0; mask_reg <= 0;
            valid_reg <= 0; sof_reg <= 0; eol_reg <= 0;
        end else begin
            if (enable && advance) begin
                valid_reg <= 0;
                sof_reg <= 0;
                eol_reg <= 0;
            end
            if (transfer) begin
                if (enable && retain) begin
                    data_reg <= s_axis_tdata;
                    mask_reg <= s_axis_cvalid;
                    valid_reg <= 1;
                    sof_reg <= x == roi_x && y == roi_y;
                    eol_reg <= {1'b0,x}+14'd1 == right_edge;
                end
                if (x == SRC_WIDTH-1) begin
                    x <= 0;
                    if (y == SRC_HEIGHT-1) y <= 0;
                    else y <= y + 13'd1;
                end else x <= x + 13'd1;
            end
        end
    end
endmodule
