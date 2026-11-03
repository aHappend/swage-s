module swage_s3_divider (
    input wire clk, rst_n, start,
    input wire [31:0] numerator,
    input wire [12:0] denominator,
    output reg busy, done,
    output reg [31:0] quotient
);
    reg [31:0] shift_quotient;
    reg [12:0] remainder_reg, denominator_reg;
    reg [5:0] bit_count;
    wire [13:0] shifted_remainder = {remainder_reg,shift_quotient[31]};
    wire [13:0] difference = shifted_remainder - {1'b0,denominator_reg};
    wire subtract_ok = !difference[13];
    wire [31:0] next_quotient = {shift_quotient[30:0],subtract_ok};
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            busy <= 0; done <= 0; quotient <= 0;
            shift_quotient <= 0; remainder_reg <= 0;
            denominator_reg <= 0; bit_count <= 0;
        end else begin
            done <= 0;
            if (start && !busy) begin
                busy <= 1;
                shift_quotient <= numerator;
                denominator_reg <= denominator;
                remainder_reg <= 0;
                bit_count <= 0;
            end else if (busy) begin
                shift_quotient <= next_quotient;
                remainder_reg <= subtract_ok ? difference[12:0] : shifted_remainder[12:0];
                if (bit_count == 31) begin
                    quotient <= next_quotient;
                    busy <= 0;
                    done <= 1;
                end else bit_count <= bit_count + 6'd1;
            end
        end
    end
endmodule
