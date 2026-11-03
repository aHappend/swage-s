module swage_s1_top #(
    parameter integer SRC_WIDTH = 8,
    parameter integer SRC_HEIGHT = 8,
    parameter integer DST_WIDTH = 16,
    parameter integer DST_HEIGHT = 16,
    parameter integer PIXEL_BITS = 8,
    parameter integer CHANNELS = 1,
    parameter integer PHASE_FRAC = 8,
    parameter integer INTERPOLATION = 0
) (
    input wire clk, rst_n,
    input wire [DATA_WIDTH-1:0] s_axis_tdata,
    input wire s_axis_tvalid, s_axis_tuser, s_axis_tlast,
    output wire s_axis_tready,
    output wire [DATA_WIDTH-1:0] m_axis_tdata,
    output wire m_axis_tvalid, m_axis_tuser, m_axis_tlast,
    input wire m_axis_tready
);
    localparam integer DATA_WIDTH = (PIXEL_BITS == 10 && CHANNELS == 1) ? 16 :
                                   ((PIXEL_BITS == 8 && CHANNELS == 3) ? 24 : 8);
    localparam integer SW = (SRC_WIDTH >= 1 && SRC_WIDTH <= 4096) ? SRC_WIDTH : 1;
    localparam integer SH = (SRC_HEIGHT >= 1 && SRC_HEIGHT <= 4096) ? SRC_HEIGHT : 1;
    localparam integer DW = (DST_WIDTH >= 1 && DST_WIDTH <= 4096) ? DST_WIDTH : 1;
    localparam integer DH = (DST_HEIGHT >= 1 && DST_HEIGHT <= 4096) ? DST_HEIGHT : 1;
    localparam integer PB = (PIXEL_BITS == 10 && CHANNELS == 1) ? 10 : 8;
    localparam integer CH = (PIXEL_BITS == 8 && CHANNELS == 3) ? 3 : 1;
    localparam integer F = (PHASE_FRAC == 16) ? 16 : 8;
    localparam integer MODE = (INTERPOLATION == 1) ? 1 : 0;

    generate if (SRC_WIDTH < 1 || SRC_WIDTH > 4096 ||
                 SRC_HEIGHT < 1 || SRC_HEIGHT > 4096 ||
                 DST_WIDTH < 1 || DST_WIDTH > 4096 ||
                 DST_HEIGHT < 1 || DST_HEIGHT > 4096 ||
                 !((PIXEL_BITS == 8 && CHANNELS == 1) ||
                   (PIXEL_BITS == 10 && CHANNELS == 1) ||
                   (PIXEL_BITS == 8 && CHANNELS == 3)) ||
                 !(PHASE_FRAC == 8 || PHASE_FRAC == 16) ||
                 !(INTERPOLATION == 0 || INTERPOLATION == 1)) begin : invalid_parameters
        initial $fatal(1, "SWAGE_S1 PARAMETER ERROR");
    end endgenerate

    wire [12:0] x0, x1, y0, y1, write_address;
    wire [F-1:0] fx, fy;
    wire frame_start, row_step, pixel_step;
    wire pipe_advance, pipe_empty, issue_valid, issue_sof, issue_eol;
    wire write_enable, write_bank, read_bank0, read_bank1;
    wire [DATA_WIDTH-1:0] p00, p01, p10, p11;

    swage_s1_phase #(
        .SRC_WIDTH(SW), .SRC_HEIGHT(SH), .DST_WIDTH(DW), .DST_HEIGHT(DH),
        .PHASE_FRAC(F), .INTERPOLATION(MODE)
    ) u_phase (
        .clk(clk), .rst_n(rst_n),
        .frame_start(frame_start), .row_step(row_step), .pixel_step(pixel_step),
        .x0(x0), .x1(x1), .y0(y0), .y1(y1), .fx(fx), .fy(fy)
    );
    swage_s1_scheduler #(
        .SRC_WIDTH(SW), .SRC_HEIGHT(SH), .DST_WIDTH(DW), .DST_HEIGHT(DH)
    ) u_scheduler (
        .clk(clk), .rst_n(rst_n), .y0(y0), .y1(y1),
        .source_valid(s_axis_tvalid), .source_ready(s_axis_tready),
        .pipe_advance(pipe_advance), .pipe_empty(pipe_empty),
        .issue_valid(issue_valid), .issue_sof(issue_sof), .issue_eol(issue_eol),
        .write_enable(write_enable), .write_bank(write_bank),
        .write_address(write_address), .read_bank0(read_bank0), .read_bank1(read_bank1),
        .frame_start(frame_start), .row_step(row_step), .pixel_step(pixel_step)
    );
    swage_s1_line_buffer #(
        .SRC_WIDTH(SW), .DATA_WIDTH(DATA_WIDTH)
    ) u_line_buffer (
        .clk(clk), .write_enable(write_enable), .write_bank(write_bank),
        .write_address(write_address), .write_data(s_axis_tdata),
        .read_enable(pipe_advance && issue_valid), .x0(x0), .x1(x1),
        .read_bank0(read_bank0), .read_bank1(read_bank1),
        .p00(p00), .p01(p01), .p10(p10), .p11(p11)
    );
    swage_s1_interp #(
        .PIXEL_BITS(PB), .CHANNELS(CH), .DATA_WIDTH(DATA_WIDTH), .PHASE_FRAC(F)
    ) u_interp (
        .clk(clk), .rst_n(rst_n),
        .issue_valid(issue_valid), .issue_sof(issue_sof), .issue_eol(issue_eol),
        .fx(fx), .fy(fy), .p00(p00), .p01(p01), .p10(p10), .p11(p11),
        .pipe_advance(pipe_advance), .pipe_empty(pipe_empty),
        .m_axis_tdata(m_axis_tdata), .m_axis_tvalid(m_axis_tvalid),
        .m_axis_tuser(m_axis_tuser), .m_axis_tlast(m_axis_tlast),
        .m_axis_tready(m_axis_tready)
    );
endmodule
