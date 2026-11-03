module swage_s3_fifo #(
    parameter integer WIDTH = 8,
    parameter integer DEPTH = 32
) (
    input wire clk, rst_n,
    input wire [WIDTH-1:0] s_data,
    input wire s_valid,
    output wire s_ready,
    output wire [WIDTH-1:0] m_data,
    output wire m_valid,
    input wire m_ready,
    output wire empty
);
    localparam integer AW = DEPTH > 1 ? $clog2(DEPTH) : 1;
    localparam integer CW = $clog2(DEPTH+1);
    (* ramstyle = "M20K", ram_style = "block", nomem2reg *)
    reg [WIDTH-1:0] mem [0:DEPTH-1];
    reg [AW-1:0] wp, rp;
    reg [CW-1:0] count;
    reg [WIDTH-1:0] read_q, output_reg;
    reg pending, valid_reg;
    wire push = s_valid && s_ready;
    wire pop = rst_n && !pending && (!valid_reg || m_ready) && count != 0;
    assign s_ready = rst_n && count < DEPTH;
    assign m_valid = rst_n && valid_reg;
    assign m_data = rst_n ? output_reg : {WIDTH{1'b0}};
    assign empty = rst_n && count == 0 && !pending && !valid_reg;
    always @(posedge clk) begin
        if (push) mem[wp] <= s_data;
        if (pop) read_q <= mem[rp];
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wp <= 0; rp <= 0; count <= 0;
            pending <= 0; valid_reg <= 0; output_reg <= 0;
        end else begin
            if (push) begin
                if (wp == DEPTH-1) wp <= 0;
                else wp <= wp + 1'b1;
            end
            if (pop) begin
                if (rp == DEPTH-1) rp <= 0;
                else rp <= rp + 1'b1;
            end
            case ({push,pop})
                2'b10: count <= count + 1'b1;
                2'b01: count <= count - 1'b1;
                default: begin end
            endcase
            pending <= pop;
            if (valid_reg && m_ready) valid_reg <= 0;
            if (pending) begin output_reg <= read_q; valid_reg <= 1; end
        end
    end
endmodule
