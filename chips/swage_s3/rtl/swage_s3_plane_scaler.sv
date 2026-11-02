module swage_s3_plane_scaler #(
    parameter integer MAX_WIDTH = 8,
    parameter integer DATA_WIDTH = 8,
    parameter integer PIXEL_BITS = 8,
    parameter integer CHANNELS = 1,
    parameter integer PHASE_FRAC = 8,
    parameter integer INTERPOLATION = 0
) (
    input wire clk, rst_n, begin_frame,
    input wire [12:0] image_width, image_height, destination_width, destination_height,
    input wire [DATA_WIDTH-1:0] s_axis_tdata,
    input wire s_axis_tvalid,
    output wire s_axis_tready,
    output wire [DATA_WIDTH-1:0] m_axis_tdata,
    output wire m_axis_tvalid, m_axis_tuser, m_axis_tlast,
    input wire m_axis_tready,
    output wire idle
);
    localparam integer F = PHASE_FRAC;
    localparam integer PW = F+13;
    localparam [2:0] IDLE=0, START_X=1, WAIT_X=2, START_Y=3, WAIT_Y=4, RUN=5;
    localparam [PW-1:0] HALF = {{(PW-1){1'b0}},1'b1} << (F-1);
    reg [2:0] state;
    reg [12:0] width_reg, height_reg, dw_reg, dh_reg;
    reg [PW-1:0] step_x, step_y, phase_x, phase_y;
    wire core_rst_n = rst_n && state == RUN;
    wire [31:0] numerator = state == START_X ?
        (32'(width_reg) << F) : (32'(height_reg) << F);
    wire [12:0] denominator = state == START_X ? dw_reg : dh_reg;
    wire div_done, div_busy;
    wire [31:0] quotient;
    wire [PW-1:0] selected_x = phase_x + (INTERPOLATION == 1 ? HALF : {PW{1'b0}});
    wire [PW-1:0] selected_y = phase_y + (INTERPOLATION == 1 ? HALF : {PW{1'b0}});
    wire [12:0] integer_x = selected_x[F +:13];
    wire [12:0] integer_y = selected_y[F +:13];
    wire [12:0] x0 = integer_x >= width_reg ? width_reg-13'd1 : integer_x;
    wire [12:0] y0 = integer_y >= height_reg ? height_reg-13'd1 : integer_y;
    wire [12:0] x1 = INTERPOLATION == 1 || integer_x >= width_reg-13'd1 ?
        x0 : integer_x+13'd1;
    wire [12:0] y1 = INTERPOLATION == 1 || integer_y >= height_reg-13'd1 ?
        y0 : integer_y+13'd1;
    wire [F-1:0] fx = INTERPOLATION == 1 ? {F{1'b0}} : phase_x[F-1:0];
    wire [F-1:0] fy = INTERPOLATION == 1 ? {F{1'b0}} : phase_y[F-1:0];
    wire frame_start, row_step, pixel_step, pipe_advance, pipe_empty;
    wire issue_valid, issue_sof, issue_eol, write_enable, write_bank;
    wire read_bank0, read_bank1;
    wire [12:0] write_address;
    wire [DATA_WIDTH-1:0] p00,p01,p10,p11;
    assign idle = rst_n && state == IDLE;
    swage_s3_divider u_divider (
        .clk(clk), .rst_n(rst_n), .start(state == START_X || state == START_Y),
        .numerator(numerator), .denominator(denominator),
        .busy(div_busy), .done(div_done), .quotient(quotient)
    );
    swage_s3_plane_scheduler u_scheduler (
        .clk(clk), .rst_n(core_rst_n),
        .source_width(width_reg), .source_height(height_reg),
        .destination_width(dw_reg), .destination_height(dh_reg),
        .y0(y0), .y1(y1), .source_valid(s_axis_tvalid), .source_ready(s_axis_tready),
        .pipe_advance(pipe_advance), .pipe_empty(pipe_empty),
        .issue_valid(issue_valid), .issue_sof(issue_sof), .issue_eol(issue_eol),
        .write_enable(write_enable), .write_bank(write_bank), .write_address(write_address),
        .read_bank0(read_bank0), .read_bank1(read_bank1), .frame_start(frame_start),
        .row_step(row_step), .pixel_step(pixel_step)
    );
    swage_s1_line_buffer #(.SRC_WIDTH(MAX_WIDTH),.DATA_WIDTH(DATA_WIDTH)) u_line_buffer (
        .clk(clk), .write_enable(write_enable), .write_bank(write_bank),
        .write_address(write_address), .write_data(s_axis_tdata),
        .read_enable(pipe_advance && issue_valid), .x0(x0), .x1(x1),
        .read_bank0(read_bank0), .read_bank1(read_bank1),
        .p00(p00), .p01(p01), .p10(p10), .p11(p11)
    );
    swage_s1_interp #(
        .PIXEL_BITS(PIXEL_BITS), .CHANNELS(CHANNELS), .DATA_WIDTH(DATA_WIDTH), .PHASE_FRAC(F)
    ) u_interp (
        .clk(clk), .rst_n(core_rst_n),
        .issue_valid(issue_valid), .issue_sof(issue_sof), .issue_eol(issue_eol),
        .fx(fx), .fy(fy), .p00(p00), .p01(p01), .p10(p10), .p11(p11),
        .pipe_advance(pipe_advance), .pipe_empty(pipe_empty),
        .m_axis_tdata(m_axis_tdata), .m_axis_tvalid(m_axis_tvalid),
        .m_axis_tuser(m_axis_tuser), .m_axis_tlast(m_axis_tlast), .m_axis_tready(m_axis_tready)
    );
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            width_reg <= 0; height_reg <= 0; dw_reg <= 0; dh_reg <= 0;
            step_x <= 0; step_y <= 0; phase_x <= 0; phase_y <= 0;
        end else begin
            case (state)
                IDLE: if (begin_frame) begin
                    width_reg <= image_width; height_reg <= image_height;
                    dw_reg <= destination_width; dh_reg <= destination_height;
                    phase_x <= 0; phase_y <= 0;
                    state <= START_X;
                end
                START_X: state <= WAIT_X;
                WAIT_X: if (div_done) begin step_x <= quotient[PW-1:0]; state <= START_Y; end
                START_Y: state <= WAIT_Y;
                WAIT_Y: if (div_done) begin step_y <= quotient[PW-1:0]; state <= RUN; end
                RUN: begin
                    if (row_step) begin
                        phase_x <= 0;
                        phase_y <= phase_y+step_y;
                    end else if (pixel_step) phase_x <= phase_x+step_x;
                    if (frame_start) state <= IDLE;
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
