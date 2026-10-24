module swage_s1_phase #(
    parameter integer SRC_WIDTH = 8,
    parameter integer SRC_HEIGHT = 8,
    parameter integer DST_WIDTH = 16,
    parameter integer DST_HEIGHT = 16,
    parameter integer PHASE_FRAC = 8,
    parameter integer INTERPOLATION = 0
) (
    input wire clk, rst_n,
    input wire frame_start, row_step, pixel_step,
    output wire [12:0] x0, x1, y0, y1,
    output wire [PHASE_FRAC-1:0] fx, fy
);
    localparam integer PW = PHASE_FRAC + 13;

    function automatic [63:0] constant_quotient;
        input [63:0] numerator;
        input [63:0] denominator;
        reg [64:0] remainder_value;
        reg [63:0] quotient_value;
        integer bit_index;
        begin
            remainder_value = 0;
            quotient_value = 0;
            for (bit_index = 63; bit_index >= 0; bit_index = bit_index - 1) begin
                remainder_value = {remainder_value[63:0], numerator[bit_index]};
                if (remainder_value >= {1'b0, denominator}) begin
                    remainder_value = remainder_value - {1'b0, denominator};
                    quotient_value[bit_index] = 1'b1;
                end
            end
            constant_quotient = quotient_value;
        end
    endfunction

    localparam [PW-1:0] STEP_X =
        constant_quotient(64'(SRC_WIDTH) << PHASE_FRAC, 64'(DST_WIDTH));
    localparam [PW-1:0] STEP_Y =
        constant_quotient(64'(SRC_HEIGHT) << PHASE_FRAC, 64'(DST_HEIGHT));
    localparam [PW-1:0] HALF = {{(PW-1){1'b0}}, 1'b1} << (PHASE_FRAC-1);
    reg [PW-1:0] phase_x, phase_y;
    wire [PW-1:0] selected_x = phase_x + ((INTERPOLATION == 1) ? HALF : {PW{1'b0}});
    wire [PW-1:0] selected_y = phase_y + ((INTERPOLATION == 1) ? HALF : {PW{1'b0}});
    wire [12:0] integer_x = selected_x[PHASE_FRAC +: 13];
    wire [12:0] integer_y = selected_y[PHASE_FRAC +: 13];
    assign x0 = (integer_x >= SRC_WIDTH) ? 13'(SRC_WIDTH-1) : integer_x;
    assign y0 = (integer_y >= SRC_HEIGHT) ? 13'(SRC_HEIGHT-1) : integer_y;
    assign x1 = (INTERPOLATION == 1 || integer_x >= SRC_WIDTH-1) ?
                x0 : integer_x + 13'd1;
    assign y1 = (INTERPOLATION == 1 || integer_y >= SRC_HEIGHT-1) ?
                y0 : integer_y + 13'd1;
    assign fx = (INTERPOLATION == 1) ? {PHASE_FRAC{1'b0}} : phase_x[PHASE_FRAC-1:0];
    assign fy = (INTERPOLATION == 1) ? {PHASE_FRAC{1'b0}} : phase_y[PHASE_FRAC-1:0];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_x <= 0;
            phase_y <= 0;
        end else if (frame_start) begin
            phase_x <= 0;
            phase_y <= 0;
        end else begin
            if (row_step) begin
                phase_x <= 0;
                phase_y <= phase_y + STEP_Y;
            end else if (pixel_step) begin
                phase_x <= phase_x + STEP_X;
            end
        end
    end
endmodule
