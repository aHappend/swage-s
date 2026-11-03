module swage_s3_scaler #(
    parameter integer MAX_WIDTH = 8,
    parameter integer DST_WIDTH = 16,
    parameter integer DST_HEIGHT = 16,
    parameter integer DATA_WIDTH = 8,
    parameter integer PIXEL_BITS = 8,
    parameter integer CHANNELS = 1,
    parameter integer PHASE_FRAC = 8,
    parameter integer INTERPOLATION = 0
) (
    input wire clk, rst_n, enable, is_420,
    input wire [12:0] image_width, image_height, origin_x, origin_y,
    input wire [DATA_WIDTH-1:0] s_axis_tdata,
    input wire [2:0] s_axis_cvalid,
    input wire s_axis_tvalid, s_axis_tuser, s_axis_tlast,
    output wire s_axis_tready,
    output wire [DATA_WIDTH-1:0] m_axis_tdata,
    output wire [2:0] m_axis_cvalid,
    output wire m_axis_tvalid, m_axis_tuser, m_axis_tlast,
    input wire m_axis_tready,
    output wire idle
);
    localparam integer CW = (MAX_WIDTH+1)>>1;
    localparam integer CDW = (DST_WIDTH+1)>>1;
    localparam integer CDH = (DST_HEIGHT+1)>>1;
    reg [12:0] sx,sy,dx,dy;
    wire source_site = !sx[0] && !sy[0];
    wire destination_site = !dx[0] && !dy[0];
    wire y_in_ready, c_in_ready, y_valid, c_valid, y_ready, c_ready;
    wire y_source_ready,c_source_ready,y_source_valid,c_source_valid;
    wire y_source_empty,c_source_empty;
    wire [DATA_WIDTH-1:0] y_source_data;
    wire [15:0] c_source_data;
    wire [DATA_WIDTH-1:0] y_data;
    wire [15:0] c_data;
    wire y_sof,y_eol,c_sof,c_eol,y_idle,c_idle;
    wire y_queue_ready,c_queue_ready,yq_valid,cq_valid,yq_ready,cq_ready;
    wire [DATA_WIDTH+1:0] yq_data;
    wire [15:0] cq_data;
    wire yq_empty,cq_empty;
    wire chroma_required = is_420 && source_site;
    wire [23:0] source_color = 24'(s_axis_tdata);
    wire [DATA_WIDTH-1:0] luma_input = is_420 ?
        DATA_WIDTH'({16'd0,source_color[7:0]}) : s_axis_tdata;
    wire pair_available = yq_valid && (!is_420 || !destination_site || cq_valid);
    wire output_transfer = enable && m_axis_tvalid && m_axis_tready;
    wire source_transfer = enable && s_axis_tvalid && s_axis_tready;
    wire [23:0] merged_data = {destination_site ? cq_data : 16'd0,yq_data[7:0]};
    assign s_axis_tready = rst_n && (enable ?
        (y_source_ready && (!chroma_required || c_source_ready)) : m_axis_tready);
    assign m_axis_tvalid = rst_n && (enable ? pair_available : s_axis_tvalid);
    assign m_axis_tdata = !rst_n ? {DATA_WIDTH{1'b0}} :
        (enable ? (is_420 ? DATA_WIDTH'(merged_data) : yq_data[DATA_WIDTH-1:0]) : s_axis_tdata);
    assign m_axis_cvalid = !rst_n ? 3'd0 : (enable ?
        (CHANNELS == 1 ? 3'b001 :
         (is_420 && !destination_site ? 3'b001 : 3'b111)) : s_axis_cvalid);
    assign m_axis_tuser = m_axis_tvalid && (enable ? dx==0 && dy==0 : s_axis_tuser);
    assign m_axis_tlast = m_axis_tvalid && (enable ? dx==DST_WIDTH-1 : s_axis_tlast);
    assign yq_ready = enable && m_axis_tready && (!is_420 || !destination_site || cq_valid);
    assign cq_ready = enable && is_420 && destination_site && m_axis_tready && yq_valid;
    assign y_ready = y_queue_ready;
    assign c_ready = c_queue_ready;
    assign idle = rst_n && (!enable || (y_idle && yq_empty && y_source_empty &&
        (!is_420 || (c_idle && cq_empty && c_source_empty))));

    swage_s3_fifo #(.WIDTH(DATA_WIDTH),.DEPTH(4*MAX_WIDTH)) u_luma_input_queue (
        .clk(clk),.rst_n(rst_n),.s_data(luma_input),
        .s_valid(enable && s_axis_tvalid && (!chroma_required || c_source_ready)),
        .s_ready(y_source_ready),.m_data(y_source_data),.m_valid(y_source_valid),
        .m_ready(y_in_ready),.empty(y_source_empty)
    );
    swage_s3_fifo #(.WIDTH(16),.DEPTH(4*CW)) u_chroma_input_queue (
        .clk(clk),.rst_n(rst_n),.s_data(source_color[23:8]),
        .s_valid(enable && is_420 && source_site && s_axis_tvalid && y_source_ready),
        .s_ready(c_source_ready),.m_data(c_source_data),.m_valid(c_source_valid),
        .m_ready(c_in_ready),.empty(c_source_empty)
    );

    swage_s3_plane_scaler #(
        .MAX_WIDTH(MAX_WIDTH),.DATA_WIDTH(DATA_WIDTH),.PIXEL_BITS(PIXEL_BITS),
        .CHANNELS(CHANNELS),.PHASE_FRAC(PHASE_FRAC),.INTERPOLATION(INTERPOLATION)
    ) u_luma (
        .clk(clk),.rst_n(rst_n),.begin_frame(enable && y_source_valid),
        .image_width(image_width),.image_height(image_height),
        .destination_width(13'(DST_WIDTH)),.destination_height(13'(DST_HEIGHT)),
        .s_axis_tdata(y_source_data),
        .s_axis_tvalid(enable && y_source_valid),
        .s_axis_tready(y_in_ready),.m_axis_tdata(y_data),.m_axis_tvalid(y_valid),
        .m_axis_tuser(y_sof),.m_axis_tlast(y_eol),.m_axis_tready(y_ready),.idle(y_idle)
    );
    swage_s3_plane_scaler #(
        .MAX_WIDTH(CW),.DATA_WIDTH(16),.PIXEL_BITS(8),.CHANNELS(2),
        .PHASE_FRAC(PHASE_FRAC),.INTERPOLATION(INTERPOLATION)
    ) u_chroma (
        .clk(clk),.rst_n(rst_n),
        .begin_frame(enable && is_420 && c_source_valid),
        .image_width((image_width+13'd1)>>1),.image_height((image_height+13'd1)>>1),
        .destination_width(13'(CDW)),.destination_height(13'(CDH)),
        .s_axis_tdata(c_source_data),
        .s_axis_tvalid(enable && is_420 && c_source_valid),
        .s_axis_tready(c_in_ready),.m_axis_tdata(c_data),.m_axis_tvalid(c_valid),
        .m_axis_tuser(c_sof),.m_axis_tlast(c_eol),.m_axis_tready(c_ready),.idle(c_idle)
    );
    swage_s3_fifo #(.WIDTH(DATA_WIDTH+2),.DEPTH(4*DST_WIDTH)) u_luma_queue (
        .clk(clk),.rst_n(rst_n),.s_data({y_sof,y_eol,y_data}),.s_valid(y_valid),
        .s_ready(y_queue_ready),.m_data(yq_data),.m_valid(yq_valid),
        .m_ready(yq_ready),.empty(yq_empty)
    );
    swage_s3_fifo #(.WIDTH(16),.DEPTH(4*CDW)) u_chroma_queue (
        .clk(clk),.rst_n(rst_n),.s_data(c_data),.s_valid(c_valid),
        .s_ready(c_queue_ready),.m_data(cq_data),.m_valid(cq_valid),
        .m_ready(cq_ready),.empty(cq_empty)
    );
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin sx<=0;sy<=0;dx<=0;dy<=0;end
        else begin
            if (source_transfer) begin
                if (sx == image_width-13'd1) begin
                    sx<=0;
                    if (sy == image_height-13'd1) sy<=0;
                    else sy<=sy+13'd1;
                end else sx<=sx+13'd1;
            end
            if (output_transfer) begin
                if (dx == DST_WIDTH-1) begin
                    dx<=0;
                    if (dy == DST_HEIGHT-1) dy<=0;
                    else dy<=dy+13'd1;
                end else dx<=dx+13'd1;
            end
        end
    end
endmodule
