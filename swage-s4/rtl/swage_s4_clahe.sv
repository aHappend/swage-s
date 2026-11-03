module swage_s4_clahe #(
    parameter integer CLAHE_TILE = 16
) (
    input wire clk, rst_n, start,
    input wire [12:0] width, height,
    input wire [8:0] clip_limit,
    output wire busy,
    output reg done,
    output wire rd_valid,
    input wire rd_ready,
    output wire [16:0] rd_addr,
    input wire rsp_valid,
    output wire rsp_ready,
    input wire [23:0] rsp_data,
    input wire [2:0] rsp_mask,
    output wire wr_valid,
    output wire [16:0] wr_addr,
    output wire [23:0] wr_data,
    output wire [2:0] wr_mask
);
    localparam integer TILE = CLAHE_TILE == 8 ? 8 : 16;
    localparam [4:0] IDLE=0, SETUP=1, CLEAR=2, SCAN_REQUEST=3,
        SCAN_RESPONSE=4, HIST_READ=5, HIST_WRITE=6, CLIP_READ=7, CLIP_ACC=8,
        DISTRIBUTE=9, CDF_READ=10, CDF_SUM=11, NUMERATOR=12, DIV_START=13,
        DIV_WAIT=14, LUT_WRITE=15, REMAP_SETUP=16, REMAP_REQUEST=17,
        REMAP_RESPONSE=18, LUT_READ=19, REMAP_WRITE=20, NEXT_TILE=21;
    reg [4:0] state;
    reg [12:0] width_reg, height_reg, tile_x, tile_y;
    reg [4:0] tile_width, tile_height, local_x, local_y;
    reg [1:0] channel;
    reg [8:0] limit_reg, samples, excess, distribution_q, cdf;
    reg [7:0] index_reg, distribution_r;
    reg [16:0] tile_base, tile_row_base, tile_row_stride, scan_addr, scan_row_base;
    reg [23:0] pixel_reg;
    reg [2:0] mask_reg;
    reg [31:0] numerator_reg;
    (* ramstyle = "M20K", nomem2reg *) reg [8:0] histogram [0:255];
    (* ramstyle = "M20K", nomem2reg *) reg [7:0] lut [0:255];
    reg [8:0] histogram_q;
    reg [7:0] lut_q;
    wire [7:0] pixel_value = pixel_reg[channel*8 +:8];
    wire [8:0] clipped = histogram_q > limit_reg ? limit_reg : histogram_q;
    wire [8:0] redistribution = distribution_q +
        (index_reg < distribution_r ? 9'd1 : 9'd0);
    wire [8:0] next_cdf = cdf+histogram_q+redistribution;
    wire last_sample = local_x == tile_width-5'd1 && local_y == tile_height-5'd1;
    wire [13:0] next_tile_x = {1'b0,tile_x}+14'(TILE);
    wire [13:0] next_tile_y = {1'b0,tile_y}+14'(TILE);
    wire [13:0] remaining_x = {1'b0,width_reg}-next_tile_x;
    wire [13:0] remaining_y = {1'b0,height_reg}-next_tile_y;
    wire divider_busy, divider_done;
    wire [31:0] quotient;
    reg [23:0] remapped_data;
    wire histogram_write = state == CLEAR || state == HIST_WRITE || state == CLIP_ACC;
    wire [7:0] histogram_write_addr = state == HIST_WRITE ? pixel_value : index_reg;
    wire [8:0] histogram_write_data = state == CLEAR ? 9'd0 :
        (state == HIST_WRITE ? histogram_q+9'd1 : clipped);
    wire histogram_read = state == HIST_READ || state == CLIP_READ || state == CDF_READ;
    wire [7:0] histogram_read_addr = state == HIST_READ ? pixel_value : index_reg;
    assign busy = rst_n && state != IDLE;
    assign rd_valid = rst_n && (state == SCAN_REQUEST || state == REMAP_REQUEST);
    assign rd_addr = scan_addr;
    assign rsp_ready = rst_n && (state == SCAN_RESPONSE || state == REMAP_RESPONSE);
    assign wr_valid = rst_n && state == REMAP_WRITE;
    assign wr_addr = scan_addr;
    assign wr_data = remapped_data;
    assign wr_mask = mask_reg;
    always @* begin
        remapped_data = pixel_reg;
        remapped_data[channel*8 +:8] = lut_q;
    end
    swage_s3_divider u_lut_divider (
        .clk(clk),.rst_n(rst_n),.start(rst_n && state == DIV_START),
        .numerator(numerator_reg),.denominator({4'd0,samples}),
        .busy(divider_busy),.done(divider_done),.quotient(quotient)
    );
    always @(posedge clk) begin
        if (rst_n) begin
            if (histogram_write) histogram[histogram_write_addr] <= histogram_write_data;
            if (histogram_read) histogram_q <= histogram[histogram_read_addr];
            if (state == LUT_WRITE) lut[index_reg] <= quotient[7:0];
            if (state == LUT_READ) lut_q <= lut[pixel_value];
        end
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE; done <= 0; width_reg <= 0; height_reg <= 0;
            tile_x <= 0; tile_y <= 0; tile_width <= 0; tile_height <= 0;
            local_x <= 0; local_y <= 0; channel <= 0; limit_reg <= 0;
            samples <= 0; excess <= 0; distribution_q <= 0; distribution_r <= 0;
            cdf <= 0; index_reg <= 0; tile_base <= 0; tile_row_base <= 0;
            tile_row_stride <= 0; scan_addr <= 0; scan_row_base <= 0;
            pixel_reg <= 0; mask_reg <= 0; numerator_reg <= 0;
        end else begin
            done <= 0;
            case (state)
                IDLE: if (start) begin
                    width_reg <= width; height_reg <= height; limit_reg <= clip_limit;
                    tile_x <= 0; tile_y <= 0; tile_base <= 0; tile_row_base <= 0;
                    tile_row_stride <= width << (TILE == 8 ? 3 : 4);
                    tile_width <= width < TILE ? width[4:0] : 5'(TILE);
                    tile_height <= height < TILE ? height[4:0] : 5'(TILE);
                    channel <= 0; state <= SETUP;
                end
                SETUP: begin
                    samples <= tile_width*tile_height; index_reg <= 0;
                    excess <= 0; cdf <= 0;
                    local_x <= 0; local_y <= 0; scan_addr <= tile_base;
                    scan_row_base <= tile_base; state <= CLEAR;
                end
                CLEAR: if (index_reg == 255) begin
                    index_reg <= 0; state <= SCAN_REQUEST;
                end else index_reg <= index_reg+8'd1;
                SCAN_REQUEST: if (rd_ready) state <= SCAN_RESPONSE;
                SCAN_RESPONSE: if (rsp_valid) begin pixel_reg <= rsp_data; state <= HIST_READ; end
                HIST_READ: state <= HIST_WRITE;
                HIST_WRITE: begin
                    if (last_sample) begin index_reg <= 0; state <= CLIP_READ; end
                    else begin
                        if (local_x == tile_width-5'd1) begin
                            local_x <= 0; local_y <= local_y+5'd1;
                            scan_row_base <= scan_row_base+{4'd0,width_reg};
                            scan_addr <= scan_row_base+{4'd0,width_reg};
                        end else begin local_x <= local_x+5'd1; scan_addr <= scan_addr+17'd1; end
                        state <= SCAN_REQUEST;
                    end
                end
                CLIP_READ: state <= CLIP_ACC;
                CLIP_ACC: begin
                    excess <= excess+histogram_q-clipped;
                    if (index_reg == 255) state <= DISTRIBUTE;
                    else begin index_reg <= index_reg+8'd1; state <= CLIP_READ; end
                end
                DISTRIBUTE: begin
                    distribution_q <= {8'd0,excess[8]};
                    distribution_r <= excess[7:0];
                    index_reg <= 0; cdf <= 0; state <= CDF_READ;
                end
                CDF_READ: state <= CDF_SUM;
                CDF_SUM: begin cdf <= next_cdf; state <= NUMERATOR; end
                NUMERATOR: begin
                    numerator_reg <= ({23'd0,cdf} << 8)-{23'd0,cdf};
                    state <= DIV_START;
                end
                DIV_START: state <= DIV_WAIT;
                DIV_WAIT: if (divider_done) state <= LUT_WRITE;
                LUT_WRITE: if (index_reg == 255) state <= REMAP_SETUP;
                    else begin index_reg <= index_reg+8'd1; state <= CDF_READ; end
                REMAP_SETUP: begin
                    local_x <= 0; local_y <= 0; scan_addr <= tile_base;
                    scan_row_base <= tile_base; state <= REMAP_REQUEST;
                end
                REMAP_REQUEST: if (rd_ready) state <= REMAP_RESPONSE;
                REMAP_RESPONSE: if (rsp_valid) begin
                    pixel_reg <= rsp_data; mask_reg <= rsp_mask; state <= LUT_READ;
                end
                LUT_READ: state <= REMAP_WRITE;
                REMAP_WRITE: begin
                    if (last_sample) begin
                        if (channel == 2) state <= NEXT_TILE;
                        else begin channel <= channel+2'd1; state <= SETUP; end
                    end else begin
                        if (local_x == tile_width-5'd1) begin
                            local_x <= 0; local_y <= local_y+5'd1;
                            scan_row_base <= scan_row_base+{4'd0,width_reg};
                            scan_addr <= scan_row_base+{4'd0,width_reg};
                        end else begin local_x <= local_x+5'd1; scan_addr <= scan_addr+17'd1; end
                        state <= REMAP_REQUEST;
                    end
                end
                NEXT_TILE: begin
                    channel <= 0;
                    if (next_tile_x < {1'b0,width_reg}) begin
                        tile_x <= next_tile_x[12:0]; tile_base <= tile_base+17'(TILE);
                        tile_width <= remaining_x < TILE ? remaining_x[4:0] : 5'(TILE);
                        state <= SETUP;
                    end else if (next_tile_y < {1'b0,height_reg}) begin
                        tile_x <= 0; tile_y <= next_tile_y[12:0];
                        tile_row_base <= tile_row_base+tile_row_stride;
                        tile_base <= tile_row_base+tile_row_stride;
                        tile_width <= width_reg < TILE ? width_reg[4:0] : 5'(TILE);
                        tile_height <= remaining_y < TILE ? remaining_y[4:0] : 5'(TILE);
                        state <= SETUP;
                    end else begin state <= IDLE; done <= 1; end
                end
                default: state <= IDLE;
            endcase
        end
    end
    generate if (CLAHE_TILE != 8 && CLAHE_TILE != 16) begin : bad_tile
        initial $fatal(1,"SWAGE_S4_CLAHE PARAMETER ERROR");
    end endgenerate
endmodule
