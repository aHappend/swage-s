module swage_s1_line_buffer #(
    parameter integer SRC_WIDTH = 8,
    parameter integer DATA_WIDTH = 8
) (
    input wire clk,
    input wire write_enable, write_bank,
    input wire [12:0] write_address,
    input wire [DATA_WIDTH-1:0] write_data,
    input wire read_enable,
    input wire [12:0] x0, x1,
    input wire read_bank0, read_bank1,
    output wire [DATA_WIDTH-1:0] p00, p01, p10, p11
);
    (* ramstyle = "M20K", ram_style = "block" , nomem2reg *) reg [DATA_WIDTH-1:0] bank0 [0:SRC_WIDTH-1];
    (* ramstyle = "M20K", ram_style = "block" , nomem2reg *) reg [DATA_WIDTH-1:0] bank1 [0:SRC_WIDTH-1];
    reg [DATA_WIDTH-1:0] bank0_a, bank0_b, bank1_a, bank1_b;
    reg selected_bank0, selected_bank1;
    wire [12:0] port_a_address = write_enable ? write_address : x0;

    always @(posedge clk) begin
        if (write_enable && !write_bank)
            bank0[port_a_address] <= write_data;
        else if (read_enable)
            bank0_a <= bank0[port_a_address];
    end
    always @(posedge clk) begin
        if (read_enable)
            bank0_b <= bank0[x1];
    end
    always @(posedge clk) begin
        if (write_enable && write_bank)
            bank1[port_a_address] <= write_data;
        else if (read_enable)
            bank1_a <= bank1[port_a_address];
    end
    always @(posedge clk) begin
        if (read_enable)
            bank1_b <= bank1[x1];
    end
    always @(posedge clk) begin
        if (read_enable) begin
            selected_bank0 <= read_bank0;
            selected_bank1 <= read_bank1;
        end
    end
    assign p00 = selected_bank0 ? bank1_a : bank0_a;
    assign p01 = selected_bank0 ? bank1_b : bank0_b;
    assign p10 = selected_bank1 ? bank1_a : bank0_a;
    assign p11 = selected_bank1 ? bank1_b : bank0_b;
endmodule
