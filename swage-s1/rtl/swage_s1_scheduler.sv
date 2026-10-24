module swage_s1_scheduler #(
    parameter integer SRC_WIDTH = 8,
    parameter integer SRC_HEIGHT = 8,
    parameter integer DST_WIDTH = 16,
    parameter integer DST_HEIGHT = 16
) (
    input wire clk, rst_n,
    input wire [12:0] y0, y1,
    input wire source_valid,
    output wire source_ready,
    input wire pipe_advance, pipe_empty,
    output wire issue_valid, issue_sof, issue_eol,
    output wire write_enable, write_bank,
    output wire [12:0] write_address,
    output reg read_bank0, read_bank1,
    output wire frame_start, row_step, pixel_step
);
    localparam [2:0] PREPARE = 0, FILL = 1, ISSUE = 2,
                     DRAIN = 3, FINISH = 4, RESTART = 5;
    reg [2:0] state;
    reg bank0_valid, bank1_valid;
    reg [12:0] bank0_tag, bank1_tag;
    reg [12:0] source_row, source_x, destination_y, destination_x;
    reg fill_store, fill_bank;
    wire hit00 = bank0_valid && bank0_tag == y0;
    wire hit01 = bank1_valid && bank1_tag == y0;
    wire hit10 = bank0_valid && bank0_tag == y1;
    wire hit11 = bank1_valid && bank1_tag == y1;
    wire have_rows = (hit00 || hit01) && (hit10 || hit11);
    wire [12:0] missing_row = (hit00 || hit01) ? y1 : y0;
    wire reclaim_bank0 = !bank0_valid || (bank0_tag != y0 && bank0_tag != y1);
    wire source_transfer = source_valid && source_ready;

    assign source_ready = rst_n && ((state == FILL) ||
                          (state == FINISH && source_row < SRC_HEIGHT));
    assign write_enable = rst_n && state == FILL && fill_store && source_transfer;
    assign write_bank = fill_bank;
    assign write_address = source_x;
    assign issue_valid = rst_n && state == ISSUE;
    assign issue_sof = issue_valid && destination_x == 0 && destination_y == 0;
    assign issue_eol = issue_valid && destination_x == DST_WIDTH-1;
    assign pixel_step = issue_valid && pipe_advance;
    assign row_step = rst_n && state == DRAIN && pipe_empty &&
                      destination_y != DST_HEIGHT-1;
    assign frame_start = rst_n && state == RESTART;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= PREPARE;
            bank0_valid <= 0;
            bank1_valid <= 0;
            bank0_tag <= 0;
            bank1_tag <= 0;
            source_row <= 0;
            source_x <= 0;
            destination_y <= 0;
            destination_x <= 0;
            fill_store <= 0;
            fill_bank <= 0;
            read_bank0 <= 0;
            read_bank1 <= 0;
        end else begin
            case (state)
                PREPARE: begin
                    if (have_rows) begin
                        read_bank0 <= !hit00;
                        read_bank1 <= !hit10;
                        state <= ISSUE;
                    end else begin
                        fill_store <= source_row == missing_row;
                        fill_bank <= !reclaim_bank0;
                        if (source_row == missing_row) begin
                            if (reclaim_bank0)
                                bank0_valid <= 0;
                            else
                                bank1_valid <= 0;
                        end
                        state <= FILL;
                    end
                end
                FILL: begin
                    if (source_transfer) begin
                        if (source_x == SRC_WIDTH-1) begin
                            source_x <= 0;
                            source_row <= source_row + 13'd1;
                            if (fill_store) begin
                                if (!fill_bank) begin
                                    bank0_valid <= 1;
                                    bank0_tag <= source_row;
                                end else begin
                                    bank1_valid <= 1;
                                    bank1_tag <= source_row;
                                end
                            end
                            state <= PREPARE;
                        end else begin
                            source_x <= source_x + 13'd1;
                        end
                    end
                end
                ISSUE: begin
                    if (pixel_step) begin
                        if (destination_x == DST_WIDTH-1) begin
                            destination_x <= 0;
                            state <= DRAIN;
                        end else begin
                            destination_x <= destination_x + 13'd1;
                        end
                    end
                end
                DRAIN: begin
                    if (pipe_empty) begin
                        if (destination_y == DST_HEIGHT-1) begin
                            state <= FINISH;
                        end else begin
                            destination_y <= destination_y + 13'd1;
                            state <= PREPARE;
                        end
                    end
                end
                FINISH: begin
                    if (source_row == SRC_HEIGHT) begin
                        state <= RESTART;
                    end else if (source_transfer) begin
                        if (source_x == SRC_WIDTH-1) begin
                            source_x <= 0;
                            source_row <= source_row + 13'd1;
                        end else begin
                            source_x <= source_x + 13'd1;
                        end
                    end
                end
                RESTART: begin
                    bank0_valid <= 0;
                    bank1_valid <= 0;
                    source_row <= 0;
                    source_x <= 0;
                    destination_y <= 0;
                    destination_x <= 0;
                    fill_store <= 0;
                    state <= PREPARE;
                end
                default: begin
                    state <= RESTART;
                end
            endcase
        end
    end
endmodule
