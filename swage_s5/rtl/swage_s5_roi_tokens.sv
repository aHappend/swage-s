module swage_s5_roi_tokens #(
    parameter integer MAX_WIDTH = 4096,
    parameter integer MAX_TOPK = 16
) (
    input wire clk, rst_n, start_frame,
    input wire [12:0] image_width, image_height,
    input wire [4:0] patch_size,
    input wire [71:0] mean, inv_std,
    input wire bf16,
    input wire [31:0] frame_id,
    input wire wr_valid,
    input wire [16:0] wr_addr,
    input wire [23:0] wr_data,
    input wire [2:0] wr_keep,
    input wire replace_valid,
    input wire [4:0] replace_slot,
    input wire copy_valid,
    output wire copy_ready,
    input wire [4:0] copy_slot,
    input wire [12:0] copy_anchor_x, copy_grid_y, copy_base_y,
    output reg copy_done,
    input wire cmd_valid,
    output wire cmd_ready,
    input wire [4:0] cmd_slot,
    input wire [7:0] cmd_rank,
    input wire cmd_last,
    output wire [47:0] m_data,
    output wire m_valid,
    input wire m_ready,
    output wire m_first, m_last, m_frame_last,
    output wire [31:0] m_id,
    output wire [12:0] m_x, m_y,
    output wire [7:0] m_element,
    output wire [2:0] m_keep,
    output wire [7:0] m_rank,
    output wire [31:0] m_frame_id,
    output wire idle
);
    localparam integer MW = MAX_WIDTH >= 1 && MAX_WIDTH <= 4096 ? MAX_WIDTH : 1;
    localparam integer K = MAX_TOPK >= 1 && MAX_TOPK <= 16 ? MAX_TOPK : 1;
    localparam [2:0] IDLE=0, COPY_COLUMN=1, COPY_READ=2, COPY_WRITE=3,
        TOKEN_READ=4, TOKEN_SEND=5, TOKEN_DRAIN=6;
    reg [2:0] state;
    (* ramstyle = "M20K", nomem2reg *) reg [7:0] stripe_b0 [0:16*MW-1];
    (* ramstyle = "M20K", nomem2reg *) reg [7:0] stripe_b1 [0:16*MW-1];
    (* ramstyle = "M20K", nomem2reg *) reg [7:0] stripe_b2 [0:16*MW-1];
    (* ramstyle = "M20K", nomem2reg *) reg [2:0] stripe_mask [0:16*MW-1];
    (* ramstyle = "M20K", nomem2reg *) reg [7:0] retained_b0 [0:256*K-1];
    (* ramstyle = "M20K", nomem2reg *) reg [7:0] retained_b1 [0:256*K-1];
    (* ramstyle = "M20K", nomem2reg *) reg [7:0] retained_b2 [0:256*K-1];
    (* ramstyle = "M20K", nomem2reg *) reg [2:0] retained_mask [0:256*K-1];
    reg [23:0] stripe_q, retained_q;
    reg [2:0] stripe_keep_q, retained_keep_q;
    reg [K-1:0] bank_valid;
    reg [12:0] bank_x [0:K-1], bank_y [0:K-1];
    reg [31:0] bank_id [0:K-1];
    reg grid_busy;
    reg [12:0] grid_remaining, grid_width;
    reg [4:0] slot;
    reg [12:0] column_remaining, grid_x, origin_x, base_y, grid_y;
    reg [4:0] column, row;
    reg [7:0] element, rank_reg;
    reg last_reg, padded_q;
    reg [16:0] stripe_row_addr;
    reg [25:0] patch_row_id;
    wire [12:0] sample_x = origin_x+{8'd0,column};
    wire [13:0] sample_y = {1'b0,base_y}+{9'd0,row};
    wire padded = sample_x >= image_width || sample_y >= {1'b0,image_height};
    wire [16:0] stripe_rd_addr = stripe_row_addr+{4'd0,sample_x};
    wire [12:0] retained_addr = {slot,8'd0}+{5'd0,element};
    wire patch_end = column == patch_size-5'd1 && row == patch_size-5'd1;
    wire norm_ready, norm_valid;
    wire sample_valid = rst_n && state == TOKEN_SEND;
    assign copy_ready = rst_n && state == IDLE && !grid_busy && !start_frame;
    assign cmd_ready = rst_n && state == IDLE && !grid_busy && !copy_valid &&
        !start_frame && cmd_slot < K && bank_valid[cmd_slot];
    assign idle = rst_n && state == IDLE && !grid_busy;
    assign m_valid = norm_valid;
    assign m_rank = m_valid ? rank_reg : 8'd0;
    assign m_frame_id = m_valid ? frame_id : 32'd0;

    always @(posedge clk) begin
        if (rst_n && wr_valid) begin
            stripe_b0[wr_addr] <= wr_data[7:0];
            stripe_b1[wr_addr] <= wr_data[15:8];
            stripe_b2[wr_addr] <= wr_data[23:16];
            stripe_mask[wr_addr] <= wr_keep;
        end
        if (rst_n && state == COPY_READ && !padded) begin
            stripe_q <= {stripe_b2[stripe_rd_addr],stripe_b1[stripe_rd_addr],
                stripe_b0[stripe_rd_addr]};
            stripe_keep_q <= stripe_mask[stripe_rd_addr];
        end
        if (rst_n && state == COPY_WRITE) begin
            retained_b0[retained_addr] <= padded_q ? 8'd0 : stripe_q[7:0];
            retained_b1[retained_addr] <= padded_q ? 8'd0 : stripe_q[15:8];
            retained_b2[retained_addr] <= padded_q ? 8'd0 : stripe_q[23:16];
            retained_mask[retained_addr] <= padded_q ? 3'd0 : stripe_keep_q;
        end
        if (rst_n && state == TOKEN_READ) begin
            retained_q <= {retained_b2[retained_addr],retained_b1[retained_addr],
                retained_b0[retained_addr]};
            retained_keep_q <= retained_mask[retained_addr];
        end
    end
    swage_s4_normalizer u_roi_normalizer (
        .clk(clk),.rst_n(rst_n),.s_valid(sample_valid),.s_ready(norm_ready),
        .s_data(retained_q),.s_keep(retained_keep_q),
        .s_first(rank_reg == 0 && element == 0),.s_last(patch_end),
        .s_frame_last(last_reg && patch_end),.s_id(bank_id[slot]),
        .s_x(bank_x[slot]),.s_y(bank_y[slot]),.s_element(element),
        .mean(mean),.inv_std(inv_std),.bf16(bf16),
        .m_valid(norm_valid),.m_ready(m_ready),.m_data(m_data),.m_keep(m_keep),
        .m_first(m_first),.m_last(m_last),.m_frame_last(m_frame_last),
        .m_id(m_id),.m_x(m_x),.m_y(m_y),.m_element(m_element)
    );
    integer i;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE; bank_valid <= 0; grid_busy <= 0;
            grid_remaining <= 0; grid_width <= 0; slot <= 0;
            column_remaining <= 0; grid_x <= 0; origin_x <= 0;
            base_y <= 0; grid_y <= 0; column <= 0; row <= 0;
            element <= 0; rank_reg <= 0; last_reg <= 0;
            padded_q <= 0; stripe_row_addr <= 0; patch_row_id <= 0; copy_done <= 0;
            for (i=0;i<K;i=i+1) begin bank_x[i] <= 0; bank_y[i] <= 0; bank_id[i] <= 0; end
        end else begin
            copy_done <= 0;
            if (start_frame) begin
                bank_valid <= 0; grid_busy <= 1;
                grid_remaining <= image_width; grid_width <= 0; state <= IDLE;
            end else begin
                if (grid_busy) begin
                    grid_width <= grid_width+13'd1;
                    if (grid_remaining <= {8'd0,patch_size}) begin
                        grid_busy <= 0; grid_remaining <= 0;
                    end else grid_remaining <= grid_remaining-{8'd0,patch_size};
                end
                if (replace_valid && replace_slot < K) bank_valid[replace_slot] <= 0;
                case (state)
                    IDLE: begin
                        if (copy_valid && copy_ready) begin
                            slot <= copy_slot; column_remaining <= copy_anchor_x;
                            grid_x <= 0; origin_x <= 0; grid_y <= copy_grid_y;
                            base_y <= copy_base_y; state <= COPY_COLUMN;
                            column <= 0; row <= 0; element <= 0; stripe_row_addr <= 0;
                        end else if (cmd_valid && cmd_ready) begin
                            slot <= cmd_slot; rank_reg <= cmd_rank; last_reg <= cmd_last;
                            column <= 0; row <= 0; element <= 0; state <= TOKEN_READ;
                        end
                    end
                    COPY_COLUMN: begin
                        if (column_remaining < {8'd0,patch_size}) begin
                            patch_row_id <= grid_y*grid_width;
                            state <= COPY_READ;
                        end
                        else begin
                            column_remaining <= column_remaining-{8'd0,patch_size};
                            grid_x <= grid_x+13'd1;
                            origin_x <= origin_x+{8'd0,patch_size};
                        end
                    end
                    COPY_READ: begin padded_q <= padded; state <= COPY_WRITE; end
                    COPY_WRITE: begin
                        if (patch_end) begin
                            bank_x[slot] <= grid_x; bank_y[slot] <= grid_y;
                            bank_id[slot] <= {6'd0,patch_row_id}+{19'd0,grid_x};
                            bank_valid[slot] <= 1; copy_done <= 1; state <= IDLE;
                        end else begin
                            element <= element+8'd1;
                            if (column == patch_size-5'd1) begin
                                column <= 0; row <= row+5'd1;
                                stripe_row_addr <= stripe_row_addr+{4'd0,image_width};
                            end else column <= column+5'd1;
                            state <= COPY_READ;
                        end
                    end
                    TOKEN_READ: state <= TOKEN_SEND;
                    TOKEN_SEND: if (sample_valid && norm_ready) begin
                        if (patch_end) state <= TOKEN_DRAIN;
                        else begin
                            element <= element+8'd1;
                            if (column == patch_size-5'd1) begin column <= 0; row <= row+5'd1; end
                            else column <= column+5'd1;
                            state <= TOKEN_READ;
                        end
                    end
                    TOKEN_DRAIN: if (m_valid && m_ready && m_last) state <= IDLE;
                    default: state <= IDLE;
                endcase
            end
        end
    end
    generate if (MAX_WIDTH < 1 || MAX_WIDTH > 4096 || MAX_TOPK < 1 || MAX_TOPK > 16) begin : invalid_parameters
        initial $fatal(1,"SWAGE_S5_ROI_TOKENS PARAMETER ERROR");
    end endgenerate
endmodule
