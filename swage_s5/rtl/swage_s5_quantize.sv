module swage_s5_quantize (
    input wire signed [39:0] value,
    input wire [4:0] shift,
    input wire relu,
    output wire signed [39:0] rounded,
    output wire [7:0] quantized
);
    wire [39:0] magnitude = value[39] ? -value : value;
    wire [39:0] quotient = magnitude >> shift;
    wire [39:0] mask = (40'd1 << shift)-40'd1;
    wire [39:0] remainder_bits = magnitude & mask;
    wire [39:0] halfway = shift == 0 ? 40'd0 : 40'd1 << (shift-5'd1);
    wire increment = shift != 0 &&
        (remainder_bits > halfway ||
         (remainder_bits == halfway && quotient[0]));
    wire [39:0] rounded_magnitude = quotient + {39'd0,increment};
    assign rounded = value[39] ? -$signed(rounded_magnitude) :
        $signed(rounded_magnitude);
    wire [7:0] saturated = rounded > 40'sd127 ? 8'h7f :
        (rounded < -40'sd128 ? 8'h80 : rounded[7:0]);
    assign quantized = relu && saturated[7] ? 8'd0 : saturated;
endmodule
