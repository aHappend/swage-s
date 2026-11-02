module swage_s3_histogram #(
    parameter integer DATA_WIDTH = 8,
    parameter integer PIXEL_BITS = 8,
    parameter integer CHANNELS = 1
) (
    input wire clk, rst_n, enable,
    input wire frame_begin, frame_drained, frame_idle,
    output wire frame_ready, frame_complete, reserved,
    input wire [1:0] hist_channel,
    input wire rgb_output,
    input wire [DATA_WIDTH-1:0] s_axis_tdata,
    input wire [2:0] s_axis_cvalid,
    input wire s_axis_tvalid, s_axis_tuser, s_axis_tlast,
    output wire s_axis_tready,
    output wire [DATA_WIDTH-1:0] m_axis_tdata,
    output wire [2:0] m_axis_cvalid,
    output wire m_axis_tvalid, m_axis_tuser, m_axis_tlast,
    input wire m_axis_tready,
    input wire hist_req_valid,
    input wire [4:0] hist_req_addr,
    output wire hist_req_ready,
    output wire hist_rsp_valid,
    output wire [255:0] hist_rsp_data,
    input wire hist_rsp_ready,
    output wire hist_done
);
    localparam [3:0] IDLE=0,CLEAR=1,COUNT=2,UPDATE1=3,UPDATE2=4,
                     SNAPSHOT=5,READ=6,RESPONSE=7;
    (* ramstyle = "M20K", ram_style = "block", nomem2reg *)
    reg [31:0] counters [0:255];
    reg [31:0] counter_q, increment_reg;
    reg [7:0] clear_addr, update_addr, response_base;
    reg [3:0] read_index, state;
    reg [255:0] response_reg;
    reg done_reg, response_valid;
    reg [DATA_WIDTH-1:0] data_reg;
    reg [2:0] mask_reg;
    reg valid_reg, sof_reg, eol_reg;
    wire [23:0] color = 24'(data_reg);
    wire [15:0] intensity = 16'(color[7:0])*16'd77 +
        16'(color[15:8])*16'd150 + 16'(color[23:16])*16'd29 + 16'd128;
    wire [7:0] mono_bin = PIXEL_BITS == 10 ? 8'(data_reg >> 2) : data_reg[7:0];
    wire [7:0] selected_bin = CHANNELS == 1 ? mono_bin :
        (hist_channel == 3 ? intensity[15:8] :
         (hist_channel == 0 ? color[7:0] :
          (hist_channel == 1 ? color[15:8] : color[23:16])));
    wire selected_valid = CHANNELS == 1 || hist_channel == 3 ||
        (hist_channel == 0 ? mask_reg[0] :
         (hist_channel == 1 ? mask_reg[1] : mask_reg[2]));
    wire output_allowed = !enable || state == COUNT;
    wire output_transfer = m_axis_tvalid && m_axis_tready;
    wire count_transfer = enable && output_transfer && selected_valid;
    wire input_transfer = s_axis_tvalid && s_axis_tready;
    wire request_transfer = hist_req_valid && hist_req_ready;
    wire response_read = state == READ && read_index < 8;
    wire [7:0] response_address = response_base + {4'd0,read_index};
    wire [7:0] read_address = count_transfer ? selected_bin : response_address;
    assign m_axis_tvalid = rst_n && valid_reg && output_allowed;
    assign m_axis_tdata = rst_n ? data_reg : {DATA_WIDTH{1'b0}};
    assign m_axis_cvalid = rst_n ? mask_reg : 3'd0;
    assign m_axis_tuser = m_axis_tvalid && sof_reg;
    assign m_axis_tlast = m_axis_tvalid && eol_reg;
    assign s_axis_tready = rst_n && state == COUNT && (!valid_reg || output_transfer);
    assign frame_ready = rst_n && state == COUNT;
    assign frame_complete = rst_n && (enable ? done_reg : frame_drained);
    assign reserved = rst_n && (state == READ || state == RESPONSE);
    assign hist_done = rst_n && enable && done_reg;
    assign hist_req_ready = rst_n && enable && done_reg && frame_idle &&
        state == SNAPSHOT && !frame_begin;
    assign hist_rsp_valid = rst_n && response_valid;
    assign hist_rsp_data = rst_n ? response_reg : 256'd0;
    always @(posedge clk) begin
        if (state == CLEAR && rst_n) counters[clear_addr] <= 0;
        if (state == UPDATE2 && rst_n) counters[update_addr] <= increment_reg;
        if (rst_n && (count_transfer || response_read)) counter_q <= counters[read_address];
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state<=IDLE; clear_addr<=0; update_addr<=0; response_base<=0;
            read_index<=0; response_reg<=0; response_valid<=0; done_reg<=0;
            increment_reg<=0; data_reg<=0; mask_reg<=0;
            valid_reg<=0; sof_reg<=0; eol_reg<=0;
        end else begin
            if (output_transfer) valid_reg <= 0;
            if (input_transfer) begin
                data_reg<=s_axis_tdata; mask_reg<=s_axis_cvalid;
                valid_reg<=1; sof_reg<=s_axis_tuser; eol_reg<=s_axis_tlast;
            end
            if (frame_begin) begin
                valid_reg<=0;
                if (enable) begin state<=CLEAR; clear_addr<=0; done_reg<=0; end
                else state<=COUNT;
            end else case (state)
                CLEAR: begin
                    if (clear_addr == 255) state<=COUNT;
                    else clear_addr<=clear_addr+8'd1;
                end
                COUNT: begin
                    if (count_transfer) begin
                        update_addr<=selected_bin;
                        state<=UPDATE1;
                    end else if (frame_drained && !valid_reg) begin
                        if (enable) done_reg<=1;
                        state<=SNAPSHOT;
                    end
                end
                UPDATE1: begin increment_reg<=counter_q+32'd1;state<=UPDATE2;end
                UPDATE2: state<=COUNT;
                SNAPSHOT: if (request_transfer) begin
                    response_base<={hist_req_addr,3'b000};
                    read_index<=0; response_reg<=0; state<=READ;
                end
                READ: begin
                    if (read_index != 0)
                        response_reg[(read_index-4'd1)*32 +:32] <= counter_q;
                    if (read_index == 8) begin
                        response_valid<=1;
                        state<=RESPONSE;
                    end else read_index<=read_index+4'd1;
                end
                RESPONSE: if (response_valid && hist_rsp_ready) begin
                    response_valid<=0; state<=SNAPSHOT;
                end
                default: begin end
            endcase
        end
    end
endmodule
