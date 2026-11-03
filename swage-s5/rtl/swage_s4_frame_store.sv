module swage_s4_frame_store #(
    parameter integer MAX_FRAME_PIXELS = 4096
) (
    input wire clk, rst_n,
    input wire wr_valid, wr_history,
    input wire [16:0] wr_addr,
    input wire [23:0] wr_data,
    input wire [2:0] wr_mask,
    input wire rd_valid,
    output wire rd_ready,
    input wire rd_history,
    input wire [16:0] rd_addr,
    output wire rsp_valid,
    input wire rsp_ready,
    output wire [23:0] rsp_data,
    output wire [2:0] rsp_mask
);
    localparam integer CAP = MAX_FRAME_PIXELS >= 1 &&
        MAX_FRAME_PIXELS <= 65536 ? MAX_FRAME_PIXELS : 1;
    (* ramstyle = "M20K", nomem2reg *) reg [26:0] work_mem [0:CAP-1];
    (* ramstyle = "M20K", nomem2reg *) reg [23:0] history_mem [0:CAP-1];
    reg [26:0] work_q;
    reg [23:0] history_q, response;
    reg [2:0] response_mask;
    reg pending, valid_reg, history_reg;
    assign rd_ready = rst_n && !pending && !valid_reg;
    assign rsp_valid = rst_n && valid_reg;
    assign rsp_data = rst_n && valid_reg ? response : 24'd0;
    assign rsp_mask = rst_n && valid_reg ? response_mask : 3'd0;
    always @(posedge clk) begin
        if (rst_n && wr_valid) begin
            if (wr_history) history_mem[wr_addr] <= wr_data;
            else work_mem[wr_addr] <= {wr_mask,wr_data};
        end
        if (rd_valid && rd_ready) begin
            work_q <= work_mem[rd_addr];
            history_q <= history_mem[rd_addr];
        end
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pending <= 0; valid_reg <= 0; history_reg <= 0; response <= 0;
            response_mask <= 0;
        end else begin
            if (rsp_valid && rsp_ready) valid_reg <= 0;
            if (pending) begin
                response <= history_reg ? history_q : work_q[23:0];
                response_mask <= history_reg ? 3'd0 : work_q[26:24];
                valid_reg <= 1;
                pending <= 0;
            end
            if (rd_valid && rd_ready) begin
                pending <= 1;
                history_reg <= rd_history;
            end
        end
    end
    generate if (MAX_FRAME_PIXELS < 1 || MAX_FRAME_PIXELS > 65536) begin : bad_cap
        initial $fatal(1,"SWAGE_S4_FRAME_STORE PARAMETER ERROR");
    end endgenerate
endmodule
