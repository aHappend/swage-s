module swage_s5_queue #(
    parameter integer WIDTH = 48,
    parameter integer DEPTH = 16
) (
    input wire clk, rst_n,
    input wire [W-1:0] s_data,
    input wire s_valid,
    output wire s_ready,
    output wire [W-1:0] m_data,
    output wire m_valid,
    input wire m_ready,
    output wire idle
);
    localparam integer W = WIDTH > 0 ? WIDTH : 1;
    localparam integer D = DEPTH >= 1 && DEPTH <= 32 ? DEPTH : 1;
    localparam integer PB = D > 1 ? $clog2(D) : 1;
    localparam integer CB = $clog2(D+1);
    reg [W-1:0] words [0:D-1];
    reg [PB-1:0] rd_ptr, wr_ptr;
    reg [CB-1:0] count;
    wire push = s_valid && s_ready;
    wire pop = m_valid && m_ready;
    assign s_ready = rst_n && (count < D || m_ready);
    assign m_valid = rst_n && count != 0;
    assign m_data = m_valid ? words[rd_ptr] : {W{1'b0}};
    assign idle = rst_n && count == 0;
    always @(posedge clk) begin
        if (rst_n && push) words[wr_ptr] <= s_data;
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rd_ptr <= 0; wr_ptr <= 0; count <= 0;
        end else begin
            if (push) begin
                if (wr_ptr == PB'(D-1)) wr_ptr <= 0;
                else wr_ptr <= wr_ptr+1'b1;
            end
            if (pop) begin
                if (rd_ptr == PB'(D-1)) rd_ptr <= 0;
                else rd_ptr <= rd_ptr+1'b1;
            end
            case ({push,pop})
                2'b10: count <= count+1'b1;
                2'b01: count <= count-1'b1;
                default: begin end
            endcase
        end
    end
    generate if (WIDTH < 1 || DEPTH < 1 || DEPTH > 32) begin : invalid_parameters
        initial $fatal(1,"SWAGE_S5_QUEUE PARAMETER ERROR");
    end endgenerate
endmodule
