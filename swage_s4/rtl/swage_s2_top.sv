module swage_s2_top #(
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
    input wire m_axis_tready,
    input wire cfg_valid,
    output wire cfg_ready, cfg_error,
    input wire [1:0] cfg_mode,
    input wire cfg_border, cfg_abs,
    input wire [4:0] cfg_shift,
    input wire [143:0] cfg_coeff
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
    localparam integer INTERP = (INTERPOLATION == 1) ? 1 : 0;
    localparam [24:0] SOURCE_BEATS = 25'(SW*SH);
    localparam [24:0] DESTINATION_BEATS = 25'(DW*DH);

    generate if (SRC_WIDTH < 1 || SRC_WIDTH > 4096 ||
                 SRC_HEIGHT < 1 || SRC_HEIGHT > 4096 ||
                 DST_WIDTH < 1 || DST_WIDTH > 4096 ||
                 DST_HEIGHT < 1 || DST_HEIGHT > 4096 ||
                 !((PIXEL_BITS == 8 && CHANNELS == 1) ||
                   (PIXEL_BITS == 10 && CHANNELS == 1) ||
                   (PIXEL_BITS == 8 && CHANNELS == 3)) ||
                 !(PHASE_FRAC == 8 || PHASE_FRAC == 16) ||
                 !(INTERPOLATION == 0 || INTERPOLATION == 1)) begin : invalid_parameters
        initial $fatal(1, "SWAGE_S2 PARAMETER ERROR");
    end endgenerate

    reg frame_active, error_reg;
    reg [24:0] source_count, destination_count, scaler_input_count;
    reg [1:0] mode_reg;
    reg border_reg, abs_reg;
    reg [4:0] shift_reg;
    reg [143:0] coeff_reg;
    wire input_enabled = rst_n && (frame_active ?
        source_count < SOURCE_BEATS : !cfg_valid);
    wire source_transfer = s_axis_tvalid && s_axis_tready;
    wire destination_transfer = m_axis_tvalid && m_axis_tready;
    wire source_complete = source_count == SOURCE_BEATS ||
        (source_transfer && source_count == SOURCE_BEATS-25'd1);
    wire destination_complete = destination_count == DESTINATION_BEATS ||
        (destination_transfer && destination_count == DESTINATION_BEATS-25'd1);
    wire illegal_configuration = (PB == 10 && cfg_mode != 0) ||
        (CH != 3 && cfg_mode[1]);
    wire demosaic_enabled = mode_reg[1];
    wire convolution_enabled = mode_reg[0];
    wire [23:0] demosaic_data;
    wire demosaic_valid, demosaic_sof, demosaic_eol, demosaic_input_ready;
    wire demosaic_output_ready;
    wire [DATA_WIDTH-1:0] convolution_input_data, convolution_data;
    wire convolution_input_valid, convolution_input_sof, convolution_input_eol;
    wire convolution_input_ready, convolution_valid, convolution_sof, convolution_eol;
    wire [DATA_WIDTH-1:0] scaler_input_data;
    wire scaler_input_valid, scaler_input_sof, scaler_input_eol, scaler_input_ready;
    wire scaler_input_transfer = scaler_input_valid && scaler_input_ready;
    wire spatial_complete = scaler_input_count == SOURCE_BEATS ||
        (scaler_input_transfer && scaler_input_count == SOURCE_BEATS-25'd1);
    wire [DATA_WIDTH-1:0] scaler_data;
    wire scaler_valid, scaler_sof, scaler_eol;
    wire [DATA_WIDTH-1:0] demosaic_word = DATA_WIDTH'(demosaic_data);

    assign cfg_ready = rst_n && !frame_active;
    assign cfg_error = rst_n && error_reg;
    assign s_axis_tready = input_enabled &&
        (demosaic_enabled ? demosaic_input_ready :
         (convolution_enabled ? convolution_input_ready : scaler_input_ready));
    assign m_axis_tdata = rst_n ? scaler_data : {DATA_WIDTH{1'b0}};
    assign m_axis_tvalid = rst_n && scaler_valid;
    assign m_axis_tuser = m_axis_tvalid && scaler_sof;
    assign m_axis_tlast = m_axis_tvalid && scaler_eol;

    assign convolution_input_data = demosaic_enabled ? demosaic_word : s_axis_tdata;
    assign convolution_input_valid = convolution_enabled &&
        (demosaic_enabled ? demosaic_valid : (input_enabled && s_axis_tvalid));
    assign convolution_input_sof = demosaic_enabled ? demosaic_sof : s_axis_tuser;
    assign convolution_input_eol = demosaic_enabled ? demosaic_eol : s_axis_tlast;
    assign demosaic_output_ready = demosaic_enabled &&
        (convolution_enabled ? convolution_input_ready : scaler_input_ready);
    assign scaler_input_data = convolution_enabled ? convolution_data :
        (demosaic_enabled ? demosaic_word : s_axis_tdata);
    assign scaler_input_valid = convolution_enabled ? convolution_valid :
        (demosaic_enabled ? demosaic_valid : (input_enabled && s_axis_tvalid));
    assign scaler_input_sof = convolution_enabled ? convolution_sof :
        (demosaic_enabled ? demosaic_sof : s_axis_tuser);
    assign scaler_input_eol = convolution_enabled ? convolution_eol :
        (demosaic_enabled ? demosaic_eol : s_axis_tlast);

    swage_s2_demosaic #(
        .SRC_WIDTH(SW), .SRC_HEIGHT(SH)
    ) u_demosaic (
        .clk(clk), .rst_n(rst_n), .s_axis_tdata(s_axis_tdata[7:0]),
        .s_axis_tvalid(input_enabled && demosaic_enabled && s_axis_tvalid),
        .s_axis_tuser(s_axis_tuser), .s_axis_tlast(s_axis_tlast),
        .s_axis_tready(demosaic_input_ready), .border(border_reg),
        .m_axis_tdata(demosaic_data), .m_axis_tvalid(demosaic_valid),
        .m_axis_tuser(demosaic_sof), .m_axis_tlast(demosaic_eol),
        .m_axis_tready(demosaic_output_ready)
    );
    swage_s2_convolution #(
        .SRC_WIDTH(SW), .SRC_HEIGHT(SH), .DATA_WIDTH(DATA_WIDTH), .CHANNELS(CH)
    ) u_convolution (
        .clk(clk), .rst_n(rst_n), .s_axis_tdata(convolution_input_data),
        .s_axis_tvalid(convolution_input_valid),
        .s_axis_tuser(convolution_input_sof), .s_axis_tlast(convolution_input_eol),
        .s_axis_tready(convolution_input_ready), .border(border_reg),
        .cfg_abs(abs_reg), .cfg_shift(shift_reg), .cfg_coeff(coeff_reg),
        .m_axis_tdata(convolution_data), .m_axis_tvalid(convolution_valid),
        .m_axis_tuser(convolution_sof), .m_axis_tlast(convolution_eol),
        .m_axis_tready(convolution_enabled && scaler_input_ready)
    );
    swage_s1_top #(
        .SRC_WIDTH(SW), .SRC_HEIGHT(SH), .DST_WIDTH(DW), .DST_HEIGHT(DH),
        .PIXEL_BITS(PB), .CHANNELS(CH), .PHASE_FRAC(F), .INTERPOLATION(INTERP)
    ) u_scaler (
        .clk(clk), .rst_n(rst_n), .s_axis_tdata(scaler_input_data),
        .s_axis_tvalid(scaler_input_valid),
        .s_axis_tuser(scaler_input_sof), .s_axis_tlast(scaler_input_eol),
        .s_axis_tready(scaler_input_ready),
        .m_axis_tdata(scaler_data), .m_axis_tvalid(scaler_valid),
        .m_axis_tuser(scaler_sof), .m_axis_tlast(scaler_eol),
        .m_axis_tready(rst_n && m_axis_tready)
    );

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            frame_active <= 0;
            error_reg <= 0;
            source_count <= 0;
            destination_count <= 0;
            scaler_input_count <= 0;
            mode_reg <= 0;
            border_reg <= 0;
            abs_reg <= 0;
            shift_reg <= 0;
            coeff_reg <= 144'd1 << 64;
        end else begin
            error_reg <= 0;
            if (cfg_valid && cfg_ready) begin
                if (illegal_configuration) begin
                    error_reg <= 1;
                end else begin
                    mode_reg <= cfg_mode;
                    border_reg <= cfg_border;
                    abs_reg <= cfg_abs;
                    shift_reg <= cfg_shift;
                    coeff_reg <= cfg_coeff;
                end
            end
            if (!frame_active) begin
                if (source_transfer) begin
                    frame_active <= 1;
                    source_count <= 25'd1;
                    destination_count <= 0;
                    scaler_input_count <= scaler_input_transfer ? 25'd1 : 25'd0;
                end
            end else begin
                if (source_transfer)
                    source_count <= source_count + 25'd1;
                if (destination_transfer)
                    destination_count <= destination_count + 25'd1;
                if (scaler_input_transfer)
                    scaler_input_count <= scaler_input_count + 25'd1;
                if (source_complete && destination_complete && spatial_complete) begin
                    frame_active <= 0;
                    source_count <= 0;
                    destination_count <= 0;
                    scaler_input_count <= 0;
                end
            end
        end
    end
endmodule
