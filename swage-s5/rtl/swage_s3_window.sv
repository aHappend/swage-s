module swage_s3_window #(
    parameter integer MAX_WIDTH = 8,
    parameter integer DATA_WIDTH = 8
) (
    input wire clk, rst_n,
    input wire [DATA_WIDTH-1:0] s_axis_tdata,
    input wire s_axis_tvalid, s_axis_tuser, s_axis_tlast,
    output wire s_axis_tready,
    input wire border,
    input wire [12:0] image_width, image_height,
    input wire [12:0] origin_x, origin_y,
    output wire [9*DATA_WIDTH-1:0] w_data,
    output wire w_valid, w_sof, w_eol,
    output wire [12:0] w_x, w_y,
    input wire w_ready
);
    localparam [2:0] PREPARE = 0, FILL = 1, SCAN = 2, DRAIN = 3, IDLE = 4;
    (* ramstyle = "M20K", ram_style = "block", nomem2reg *)
    reg [DATA_WIDTH-1:0] bank0 [0:MAX_WIDTH-1];
    (* ramstyle = "M20K", ram_style = "block", nomem2reg *)
    reg [DATA_WIDTH-1:0] bank1 [0:MAX_WIDTH-1];
    (* ramstyle = "M20K", ram_style = "block", nomem2reg *)
    reg [DATA_WIDTH-1:0] bank2 [0:MAX_WIDTH-1];
    reg [DATA_WIDTH-1:0] bank0_q, bank1_q, bank2_q;
    reg [2:0] state;
    reg [1:0] write_bank, top_bank, middle_bank, bottom_bank;
    reg [12:0] width_reg, height_reg;
    reg border_reg;
    reg [2:0] bank_valid;
    reg [12:0] tag0, tag1, tag2;
    reg [12:0] source_row, source_x, center_y, scan_column;
    reg request_valid, request_horizontal_zero, request_top_zero, request_bottom_zero;
    reg [12:0] request_column, request_y;
    reg [DATA_WIDTH-1:0] top_left, top_middle;
    reg [DATA_WIDTH-1:0] middle_left, middle_middle;
    reg [DATA_WIDTH-1:0] bottom_left, bottom_middle;
    reg [9*DATA_WIDTH-1:0] data_reg;
    reg valid_reg;
    reg [12:0] x_reg, y_reg;

    wire advance = rst_n && (!valid_reg || w_ready);
    wire source_transfer = s_axis_tvalid && s_axis_tready;
    wire read_enable = advance && state == SCAN;
    wire [12:0] read_address = scan_column == 0 ? 13'd0 :
        (scan_column == width_reg+13'd1 ? width_reg-13'd1 : scan_column - 13'd1);
    wire [12:0] required_top = center_y == 0 ? 13'd0 : center_y - 13'd1;
    wire [12:0] required_bottom = center_y == height_reg-13'd1 ?
        center_y : center_y + 13'd1;
    wire top_hit0 = bank_valid[0] && tag0 == required_top;
    wire top_hit1 = bank_valid[1] && tag1 == required_top;
    wire top_hit2 = bank_valid[2] && tag2 == required_top;
    wire middle_hit0 = bank_valid[0] && tag0 == center_y;
    wire middle_hit1 = bank_valid[1] && tag1 == center_y;
    wire middle_hit2 = bank_valid[2] && tag2 == center_y;
    wire bottom_hit0 = bank_valid[0] && tag0 == required_bottom;
    wire bottom_hit1 = bank_valid[1] && tag1 == required_bottom;
    wire bottom_hit2 = bank_valid[2] && tag2 == required_bottom;
    wire have_rows = (top_hit0 || top_hit1 || top_hit2) &&
                     (middle_hit0 || middle_hit1 || middle_hit2) &&
                     (bottom_hit0 || bottom_hit1 || bottom_hit2);
    wire [DATA_WIDTH-1:0] selected_top = top_bank == 0 ? bank0_q :
        (top_bank == 1 ? bank1_q : bank2_q);
    wire [DATA_WIDTH-1:0] selected_middle = middle_bank == 0 ? bank0_q :
        (middle_bank == 1 ? bank1_q : bank2_q);
    wire [DATA_WIDTH-1:0] selected_bottom = bottom_bank == 0 ? bank0_q :
        (bottom_bank == 1 ? bank1_q : bank2_q);
    wire [DATA_WIDTH-1:0] top_sample =
        (request_horizontal_zero || request_top_zero) ? {DATA_WIDTH{1'b0}} : selected_top;
    wire [DATA_WIDTH-1:0] middle_sample =
        request_horizontal_zero ? {DATA_WIDTH{1'b0}} : selected_middle;
    wire [DATA_WIDTH-1:0] bottom_sample =
        (request_horizontal_zero || request_bottom_zero) ? {DATA_WIDTH{1'b0}} : selected_bottom;

    assign s_axis_tready = rst_n && state == FILL && advance;
    assign w_data = rst_n ? data_reg : {9*DATA_WIDTH{1'b0}};
    assign w_valid = rst_n && valid_reg;
    assign w_x = rst_n ? x_reg : 13'd0;
    assign w_y = rst_n ? y_reg : 13'd0;
    assign w_sof = w_valid && x_reg == 0 && y_reg == 0;
    assign w_eol = w_valid && x_reg == width_reg-13'd1;

    always @(posedge clk) begin
        if (source_transfer && write_bank == 0)
            bank0[source_x] <= s_axis_tdata;
        if (source_transfer && write_bank == 1)
            bank1[source_x] <= s_axis_tdata;
        if (source_transfer && write_bank == 2)
            bank2[source_x] <= s_axis_tdata;
        if (read_enable) begin
            bank0_q <= bank0[read_address];
            bank1_q <= bank1[read_address];
            bank2_q <= bank2[read_address];
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            width_reg <= 0;
            height_reg <= 0;
            border_reg <= 0;
            write_bank <= 0;
            top_bank <= 0;
            middle_bank <= 0;
            bottom_bank <= 0;
            bank_valid <= 0;
            tag0 <= 0;
            tag1 <= 0;
            tag2 <= 0;
            source_row <= 0;
            source_x <= 0;
            center_y <= 0;
            scan_column <= 0;
            request_valid <= 0;
            request_horizontal_zero <= 0;
            request_top_zero <= 0;
            request_bottom_zero <= 0;
            request_column <= 0;
            request_y <= 0;
            top_left <= 0;
            top_middle <= 0;
            middle_left <= 0;
            middle_middle <= 0;
            bottom_left <= 0;
            bottom_middle <= 0;
            data_reg <= 0;
            valid_reg <= 0;
            x_reg <= 0;
            y_reg <= 0;
        end else if (advance) begin
            request_valid <= state == SCAN;
            valid_reg <= request_valid && request_column >= 2;
            if (state == SCAN) begin
                request_column <= scan_column;
                request_y <= center_y;
                request_horizontal_zero <= border_reg &&
                    (scan_column == 0 || scan_column == width_reg+13'd1);
                request_top_zero <= border_reg && center_y == 0;
                request_bottom_zero <= border_reg && center_y == height_reg-13'd1;
            end
            if (request_valid) begin
                top_left <= top_middle;
                top_middle <= top_sample;
                middle_left <= middle_middle;
                middle_middle <= middle_sample;
                bottom_left <= bottom_middle;
                bottom_middle <= bottom_sample;
                if (request_column >= 2) begin
                    data_reg <= {
                        bottom_sample, bottom_middle, bottom_left,
                        middle_sample, middle_middle, middle_left,
                        top_sample, top_middle, top_left
                    };
                    x_reg <= request_column - 13'd2;
                    y_reg <= request_y;
                end
            end
            case (state)
                IDLE: begin
                    if (s_axis_tvalid) begin
                        width_reg <= image_width;
                        height_reg <= image_height;
                        border_reg <= border;
                        state <= PREPARE;
                    end
                end
                PREPARE: begin
                    if (source_row <= required_bottom) begin
                        bank_valid[write_bank] <= 0;
                        state <= FILL;
                    end else if (have_rows) begin
                        top_bank <= top_hit0 ? 2'd0 : (top_hit1 ? 2'd1 : 2'd2);
                        middle_bank <= middle_hit0 ? 2'd0 : (middle_hit1 ? 2'd1 : 2'd2);
                        bottom_bank <= bottom_hit0 ? 2'd0 : (bottom_hit1 ? 2'd1 : 2'd2);
                        scan_column <= 0;
                        state <= SCAN;
                    end
                end
                FILL: begin
                    if (source_transfer) begin
                        if (source_x == width_reg-13'd1) begin
                            source_x <= 0;
                            source_row <= source_row + 13'd1;
                            bank_valid[write_bank] <= 1;
                            case (write_bank)
                                0: tag0 <= source_row;
                                1: tag1 <= source_row;
                                2: tag2 <= source_row;
                                default: begin end
                            endcase
                            if (write_bank == 2)
                                write_bank <= 0;
                            else
                                write_bank <= write_bank + 2'd1;
                            state <= PREPARE;
                        end else begin
                            source_x <= source_x + 13'd1;
                        end
                    end
                end
                SCAN: begin
                    if (scan_column == width_reg+13'd1) begin
                        state <= DRAIN;
                    end else begin
                        scan_column <= scan_column + 13'd1;
                    end
                end
                DRAIN: begin
                    if (!request_valid && !valid_reg) begin
                        if (center_y == height_reg-13'd1) begin
                            center_y <= 0;
                            source_row <= 0;
                            source_x <= 0;
                            write_bank <= 0;
                            bank_valid <= 0;
                            state <= IDLE;
                        end else begin
                            center_y <= center_y + 13'd1;
                            state <= PREPARE;
                        end
                    end
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
