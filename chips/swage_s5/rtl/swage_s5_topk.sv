module swage_s5_topk #(
    parameter integer MAX_TOPK = 16
) (
    input wire clk, rst_n,
    input wire [4:0] topk,
    input wire start_frame, finish_valid,
    output wire finish_ready,
    input wire [87:0] s_data,
    input wire s_eligible,
    input wire [1:0] s_level,
    input wire [12:0] s_x, s_y,
    input wire s_frame_last, s_valid,
    output wire s_ready,
    input wire [4:0] snapshot_index,
    output wire snapshot_valid,
    output wire [87:0] snapshot_data,
    output wire [12:0] snapshot_x, snapshot_y,
    output wire [1:0] snapshot_level,
    output wire [87:0] m_data,
    output wire m_valid,
    input wire m_ready,
    output wire m_keep, m_first, m_last,
    output wire [7:0] m_rank, m_count,
    output wire [12:0] m_x, m_y,
    output wire [1:0] m_level,
    output wire [4:0] m_slot,
    output reg delivery_done,
    output wire idle
);
    localparam integer K = MAX_TOPK >= 1 && MAX_TOPK <= 16 ? MAX_TOPK : 1;
    localparam [2:0] IDLE=0, INSERT_SCAN=1, INSERT_COMMIT=2, OUTPUT_SCAN=3, OUTPUT_SEND=4;
    reg [2:0] state;
    reg [K-1:0] occupied, emitted;
    reg [87:0] descriptors [0:K-1];
    reg [12:0] anchors_x [0:K-1], anchors_y [0:K-1];
    reg [1:0] levels [0:K-1];
    reg [87:0] candidate_data;
    reg [12:0] candidate_x, candidate_y;
    reg [1:0] candidate_level;
    reg [4:0] scan, worst_slot, free_slot, best_slot;
    reg worst_found, free_found, best_found;
    reg [7:0] count, rank;

    function automatic better (
        input [87:0] a_data, b_data,
        input [1:0] a_level, b_level,
        input [12:0] a_x, a_y, b_x, b_y
    );
        begin
            if (a_data[87:72] != b_data[87:72]) better = a_data[87:72] > b_data[87:72];
            else if (a_data[71:64] != b_data[71:64]) better = a_data[71:64] < b_data[71:64];
            else if (a_level != b_level) better = a_level < b_level;
            else if (a_y != b_y) better = a_y < b_y;
            else better = a_x < b_x;
        end
    endfunction

    wire slot_is_worse = better(descriptors[worst_slot],descriptors[scan],
        levels[worst_slot],levels[scan],anchors_x[worst_slot],anchors_y[worst_slot],
        anchors_x[scan],anchors_y[scan]);
    wire candidate_is_better = better(candidate_data,descriptors[worst_slot],
        candidate_level,levels[worst_slot],candidate_x,candidate_y,
        anchors_x[worst_slot],anchors_y[worst_slot]);
    wire slot_is_better = better(descriptors[scan],descriptors[best_slot],
        levels[scan],levels[best_slot],anchors_x[scan],anchors_y[scan],
        anchors_x[best_slot],anchors_y[best_slot]);
    wire [4:0] insertion_slot = free_found ? free_slot : worst_slot;
    wire insert_candidate = free_found || (worst_found && candidate_is_better);
    assign s_ready = rst_n && state == IDLE && !start_frame && !finish_valid;
    assign finish_ready = rst_n && state == IDLE && !start_frame;
    assign idle = rst_n && state == IDLE;
    assign snapshot_valid = rst_n && snapshot_index < K && occupied[snapshot_index];
    assign snapshot_data = snapshot_valid ? descriptors[snapshot_index] : 88'd0;
    assign snapshot_x = snapshot_valid ? anchors_x[snapshot_index] : 13'd0;
    assign snapshot_y = snapshot_valid ? anchors_y[snapshot_index] : 13'd0;
    assign snapshot_level = snapshot_valid ? levels[snapshot_index] : 2'd0;
    assign m_valid = rst_n && state == OUTPUT_SEND;
    assign m_keep = m_valid && count != 0;
    assign m_data = m_keep ? descriptors[best_slot] : 88'd0;
    assign m_first = m_valid && rank == 0;
    assign m_last = m_valid && (count == 0 || rank+8'd1 == count);
    assign m_rank = m_valid ? rank : 8'd0;
    assign m_count = m_valid ? count : 8'd0;
    assign m_x = m_keep ? anchors_x[best_slot] : 13'd0;
    assign m_y = m_keep ? anchors_y[best_slot] : 13'd0;
    assign m_level = m_keep ? levels[best_slot] : 2'd0;
    assign m_slot = m_keep ? best_slot : 5'd0;
    integer i;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE; occupied <= 0; emitted <= 0;
            candidate_data <= 0; candidate_x <= 0; candidate_y <= 0; candidate_level <= 0;
            scan <= 0; worst_slot <= 0; free_slot <= 0; best_slot <= 0;
            worst_found <= 0; free_found <= 0; best_found <= 0;
            count <= 0; rank <= 0; delivery_done <= 0;
            for (i=0;i<K;i=i+1) begin
                descriptors[i] <= 0; anchors_x[i] <= 0; anchors_y[i] <= 0; levels[i] <= 0;
            end
        end else begin
            delivery_done <= 0;
            if (start_frame) begin
                occupied <= 0; emitted <= 0; count <= 0; rank <= 0; state <= IDLE;
                scan <= 0; worst_slot <= 0; free_slot <= 0; best_slot <= 0;
                worst_found <= 0; free_found <= 0; best_found <= 0;
            end else begin
                case (state)
                    IDLE: begin
                        if (finish_valid && finish_ready) begin
                            rank <= 0; emitted <= 0; scan <= 0; best_found <= 0; best_slot <= 0;
                            if (count == 0) state <= OUTPUT_SEND;
                            else state <= OUTPUT_SCAN;
                        end else if (s_valid && s_ready && s_eligible) begin
                            candidate_data <= s_data; candidate_x <= s_x;
                            candidate_y <= s_y; candidate_level <= s_level;
                            scan <= 0; worst_found <= 0; free_found <= 0;
                            worst_slot <= 0; free_slot <= 0; state <= INSERT_SCAN;
                        end
                    end
                    INSERT_SCAN: begin
                        if (occupied[scan]) begin
                            if (!worst_found || slot_is_worse) begin
                                worst_slot <= scan; worst_found <= 1;
                            end
                        end else if (!free_found && scan < topk) begin
                            free_slot <= scan; free_found <= 1;
                        end
                        if (scan == 5'(K-1)) state <= INSERT_COMMIT;
                        else scan <= scan+5'd1;
                    end
                    INSERT_COMMIT: begin
                        if (insert_candidate) begin
                            descriptors[insertion_slot] <= candidate_data;
                            anchors_x[insertion_slot] <= candidate_x;
                            anchors_y[insertion_slot] <= candidate_y;
                            levels[insertion_slot] <= candidate_level;
                            occupied[insertion_slot] <= 1;
                            if (free_found) count <= count+8'd1;
                        end
                        state <= IDLE;
                    end
                    OUTPUT_SCAN: begin
                        if (occupied[scan] && !emitted[scan] && (!best_found || slot_is_better)) begin
                            best_slot <= scan; best_found <= 1;
                        end
                        if (scan == 5'(K-1)) state <= OUTPUT_SEND;
                        else scan <= scan+5'd1;
                    end
                    OUTPUT_SEND: if (m_ready) begin
                        if (count != 0) emitted[best_slot] <= 1;
                        if (m_last) begin state <= IDLE; delivery_done <= 1; end
                        else begin
                            rank <= rank+8'd1; scan <= 0; best_found <= 0;
                            best_slot <= 0; state <= OUTPUT_SCAN;
                        end
                    end
                    default: state <= IDLE;
                endcase
            end
        end
    end
    generate if (MAX_TOPK < 1 || MAX_TOPK > 16) begin : invalid_parameters
        initial $fatal(1,"SWAGE_S5_TOPK PARAMETER ERROR");
    end endgenerate
endmodule
