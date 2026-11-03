module swage_s5_fork #(
    parameter integer DATA_WIDTH = 24
) (
    input wire clk, rst_n,
    input wire branch_enable,
    input wire [W-1:0] s_data,
    input wire [2:0] s_keep,
    input wire s_valid, s_sof, s_eol,
    output wire s_ready,
    output wire [W-1:0] p_data,
    output wire [2:0] p_keep,
    output wire p_valid, p_sof, p_eol,
    input wire p_ready,
    output wire [W-1:0] b_data,
    output wire [2:0] b_keep,
    output wire b_valid, b_sof, b_eol,
    input wire b_ready,
    output wire idle
);
    localparam integer W = DATA_WIDTH > 0 ? DATA_WIDTH : 1;
    reg public_done, branch_done;
    assign p_valid = rst_n && s_valid && !public_done;
    assign b_valid = rst_n && branch_enable && s_valid && !branch_done;
    assign p_data = rst_n ? s_data : {W{1'b0}};
    assign p_keep = rst_n ? s_keep : 3'd0;
    assign b_data = rst_n ? s_data : {W{1'b0}};
    assign b_keep = rst_n ? s_keep : 3'd0;
    assign p_sof = p_valid && s_sof;
    assign p_eol = p_valid && s_eol;
    assign b_sof = b_valid && s_sof;
    assign b_eol = b_valid && s_eol;
    assign s_ready = rst_n && (public_done || p_ready) &&
        (!branch_enable || branch_done || b_ready);
    assign idle = rst_n && !s_valid && !public_done && !branch_done;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin public_done <= 0; branch_done <= 0; end
        else if (s_valid && s_ready) begin
            public_done <= 0; branch_done <= 0;
        end else begin
            if (p_valid && p_ready) public_done <= 1;
            if (b_valid && b_ready) branch_done <= 1;
        end
    end
    generate if (DATA_WIDTH < 1) begin : invalid_parameters
        initial $fatal(1,"SWAGE_S5_FORK PARAMETER ERROR");
    end endgenerate
endmodule
