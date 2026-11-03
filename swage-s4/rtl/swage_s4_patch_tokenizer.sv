module swage_s4_patch_tokenizer (
    input wire clk, rst_n, start,
    input wire [12:0] width, height,
    input wire [4:0] patch_size,
    output wire busy,
    output reg done,
    output wire rd_valid,
    input wire rd_ready,
    output wire [16:0] rd_addr,
    input wire rsp_valid,
    output wire rsp_ready,
    input wire [23:0] rsp_data,
    output wire sample_valid,
    input wire sample_ready,
    output wire [23:0] sample_data,
    output wire [2:0] sample_keep,
    output wire sample_first, sample_last, sample_frame_last,
    output wire [31:0] sample_id,
    output wire [12:0] sample_x, sample_y,
    output wire [7:0] sample_element
);
    localparam [2:0] IDLE=0, PREPARE=1, ADDRESS=2, REQUEST=3, RESPONSE=4, SEND=5;
    reg [2:0] state;
    reg [12:0] width_reg, height_reg, base_x, base_y, grid_x, grid_y;
    reg [4:0] size_reg, local_x, local_y;
    reg [31:0] id_reg;
    reg [7:0] element_reg;
    reg [16:0] row_base, row_stride, local_row, address_reg;
    reg [23:0] data_reg;
    reg real_reg;
    wire [13:0] absolute_x = {1'b0,base_x}+{9'd0,local_x};
    wire [13:0] absolute_y = {1'b0,base_y}+{9'd0,local_y};
    wire [13:0] next_base_x = {1'b0,base_x}+{9'd0,size_reg};
    wire [13:0] next_base_y = {1'b0,base_y}+{9'd0,size_reg};
    wire final_element = local_x == size_reg-5'd1 && local_y == size_reg-5'd1;
    wire final_patch = next_base_x >= {1'b0,width_reg} &&
        next_base_y >= {1'b0,height_reg};
    assign busy = rst_n && state != IDLE;
    assign rd_valid = rst_n && state == REQUEST;
    assign rd_addr = address_reg;
    assign rsp_ready = rst_n && state == RESPONSE;
    assign sample_valid = rst_n && state == SEND;
    assign sample_data = sample_valid && real_reg ? data_reg : 24'd0;
    assign sample_keep = sample_valid && real_reg ? 3'b111 : 3'b000;
    assign sample_first = sample_valid && id_reg == 0 && element_reg == 0;
    assign sample_last = sample_valid && final_element;
    assign sample_frame_last = sample_valid && final_element && final_patch;
    assign sample_id = sample_valid ? id_reg : 32'd0;
    assign sample_x = sample_valid ? grid_x : 13'd0;
    assign sample_y = sample_valid ? grid_y : 13'd0;
    assign sample_element = sample_valid ? element_reg : 8'd0;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE; done <= 0; width_reg <= 0; height_reg <= 0;
            size_reg <= 0; base_x <= 0; base_y <= 0; grid_x <= 0; grid_y <= 0;
            local_x <= 0; local_y <= 0; id_reg <= 0; element_reg <= 0;
            row_base <= 0; row_stride <= 0; local_row <= 0; address_reg <= 0;
            data_reg <= 0; real_reg <= 0;
        end else begin
            done <= 0;
            case (state)
                IDLE: if (start) begin
                    width_reg <= width; height_reg <= height; size_reg <= patch_size;
                    base_x <= 0; base_y <= 0; grid_x <= 0; grid_y <= 0;
                    local_x <= 0; local_y <= 0; id_reg <= 0; element_reg <= 0;
                    row_base <= 0; row_stride <= width * patch_size;
                    state <= PREPARE;
                end
                PREPARE: begin
                    real_reg <= absolute_x < {1'b0,width_reg} &&
                        absolute_y < {1'b0,height_reg};
                    if (absolute_x < {1'b0,width_reg} && absolute_y < {1'b0,height_reg}) begin
                        local_row <= local_y * width_reg;
                        state <= ADDRESS;
                    end else begin
                        data_reg <= 0;
                        state <= SEND;
                    end
                end
                ADDRESS: begin
                    address_reg <= row_base + local_row + {4'd0,base_x} + {12'd0,local_x};
                    state <= REQUEST;
                end
                REQUEST: if (rd_ready) state <= RESPONSE;
                RESPONSE: if (rsp_valid) begin data_reg <= rsp_data; state <= SEND; end
                SEND: if (sample_ready) begin
                    if (final_element) begin
                        if (final_patch) begin state <= IDLE; done <= 1; end
                        else begin
                            id_reg <= id_reg+32'd1; element_reg <= 0;
                            local_x <= 0; local_y <= 0;
                            if (next_base_x >= {1'b0,width_reg}) begin
                                base_x <= 0; grid_x <= 0;
                                base_y <= next_base_y[12:0]; grid_y <= grid_y+13'd1;
                                row_base <= row_base+row_stride;
                            end else begin
                                base_x <= next_base_x[12:0]; grid_x <= grid_x+13'd1;
                            end
                            state <= PREPARE;
                        end
                    end else begin
                        element_reg <= element_reg+8'd1;
                        if (local_x == size_reg-5'd1) begin
                            local_x <= 0; local_y <= local_y+5'd1;
                        end else local_x <= local_x+5'd1;
                        state <= PREPARE;
                    end
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
