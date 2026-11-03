module swage_s5_pyramid #(
    parameter integer MAX_WIDTH = 4096
) (
    input wire clk, rst_n,
    input wire [47:0] s_data,
    input wire [5:0] s_keep,
    input wire s_valid,
    output wire s_ready,
    input wire s_sof, s_eol, s_frame_last,
    input wire [12:0] s_x, s_y, image_width, image_height,
    input wire [431:0] l1_dw_weight, l2_dw_weight,
    input wire [287:0] l1_pw_weight, l2_pw_weight,
    input wire [191:0] l1_dw_bias, l1_pw_bias, l2_dw_bias, l2_pw_bias,
    input wire [29:0] l1_dw_shift, l1_pw_shift, l2_dw_shift, l2_pw_shift,
    output reg [47:0] m_data,
    output reg [5:0] m_keep,
    output wire m_valid,
    input wire m_ready,
    output reg m_sof, m_eol, m_frame_last,
    output reg [12:0] m_x, m_y,
    output wire [1:0] m_level,
    output wire idle
);
    wire [12:0] width1 = (image_width >> 1)+{12'd0,image_width[0]};
    wire [12:0] height1 = (image_height >> 1)+{12'd0,image_height[0]};
    wire [12:0] width2 = (width1 >> 1)+{12'd0,width1[0]};
    wire [12:0] height2 = (height1 >> 1)+{12'd0,height1[0]};
    wire l1_ready, l1_valid, l1_out_ready, l1_idle;
    wire l2_ready, l2_valid, l2_out_ready, l2_idle;
    wire [47:0] l1_data, l2_data;
    wire [5:0] l1_keep, l2_keep;
    wire l1_sof, l1_eol, l1_last, l2_sof, l2_eol, l2_last;
    wire [12:0] l1_x, l1_y, l2_x, l2_y;
    wire retain1 = !l1_x[0] && !l1_y[0];
    wire retain2 = !l2_x[0] && !l2_y[0];
    wire [12:0] f1_x = l1_x >> 1;
    wire [12:0] f1_y = l1_y >> 1;
    wire [12:0] f2_x = l2_x >> 1;
    wire [12:0] f2_y = l2_y >> 1;
    reg f0_head_done, f0_conv_done, f1_head_done, f1_conv_done;
    wire [2:0] available = {
        l2_valid && retain2,
        l1_valid && retain1 && !f1_head_done,
        s_valid && !f0_head_done
    };
    reg [1:0] turn, locked_selection;
    reg locked;
    reg [1:0] selection;
    wire grant0 = m_valid && selection == 0 && m_ready;
    wire grant1 = m_valid && selection == 1 && m_ready;
    wire grant2 = m_valid && selection == 2 && m_ready;
    assign s_ready = rst_n && (f0_head_done || grant0) && (f0_conv_done || l1_ready);
    assign l1_out_ready = rst_n && (!retain1 ||
        ((f1_head_done || grant1) && (f1_conv_done || l2_ready)));
    assign l2_out_ready = rst_n && (!retain2 || grant2);
    assign m_valid = rst_n && available[selection];
    assign m_level = m_valid ? selection : 2'd0;
    assign idle = rst_n && !s_valid && l1_idle && l2_idle && !locked &&
        !f0_head_done && !f0_conv_done && !f1_head_done && !f1_conv_done;

    always @* begin
        selection = locked_selection;
        if (!locked) begin
            case (turn)
                0: begin
                    if (available[0]) selection = 0;
                    else if (available[1]) selection = 1;
                    else if (available[2]) selection = 2;
                    else selection = 0;
                end
                1: begin
                    if (available[1]) selection = 1;
                    else if (available[2]) selection = 2;
                    else if (available[0]) selection = 0;
                    else selection = 1;
                end
                default: begin
                    if (available[2]) selection = 2;
                    else if (available[0]) selection = 0;
                    else if (available[1]) selection = 1;
                    else selection = 2;
                end
            endcase
        end
    end
    always @* begin
        m_data = 0; m_keep = 0; m_sof = 0; m_eol = 0; m_frame_last = 0;
        m_x = 0; m_y = 0;
        if (rst_n && m_valid) begin
            case (selection)
                0: begin
                    m_data = s_data; m_keep = s_keep; m_sof = s_sof;
                    m_eol = s_eol; m_frame_last = s_frame_last; m_x = s_x; m_y = s_y;
                end
                1: begin
                    m_data = l1_data; m_keep = l1_keep;
                    m_sof = f1_x == 0 && f1_y == 0;
                    m_eol = f1_x == width1-13'd1;
                    m_frame_last = f1_x == width1-13'd1 && f1_y == height1-13'd1;
                    m_x = f1_x; m_y = f1_y;
                end
                default: begin
                    m_data = l2_data; m_keep = l2_keep;
                    m_sof = f2_x == 0 && f2_y == 0;
                    m_eol = f2_x == width2-13'd1;
                    m_frame_last = f2_x == width2-13'd1 && f2_y == height2-13'd1;
                    m_x = f2_x; m_y = f2_y;
                end
            endcase
        end
    end
    swage_s5_dsconv #(.CIN(6),.COUT(6),.MAX_WIDTH(MAX_WIDTH)) u_level1 (
        .clk(clk),.rst_n(rst_n),.s_data(s_data),.s_keep(s_keep),
        .s_valid(s_valid && !f0_conv_done),.s_ready(l1_ready),
        .s_sof(s_sof),.s_eol(s_eol),.s_frame_last(s_frame_last),.s_x(s_x),.s_y(s_y),
        .image_width(image_width),.image_height(image_height),
        .dw_weight(l1_dw_weight),.pw_weight(l1_pw_weight),
        .dw_bias(l1_dw_bias),.pw_bias(l1_pw_bias),.dw_shift(l1_dw_shift),.pw_shift(l1_pw_shift),
        .m_data(l1_data),.m_keep(l1_keep),.m_valid(l1_valid),.m_ready(l1_out_ready),
        .m_sof(l1_sof),.m_eol(l1_eol),.m_frame_last(l1_last),
        .m_x(l1_x),.m_y(l1_y),.idle(l1_idle)
    );
    swage_s5_dsconv #(.CIN(6),.COUT(6),.MAX_WIDTH(MAX_WIDTH)) u_level2 (
        .clk(clk),.rst_n(rst_n),.s_data(l1_data),.s_keep(l1_keep),
        .s_valid(l1_valid && retain1 && !f1_conv_done),.s_ready(l2_ready),
        .s_sof(f1_x == 0 && f1_y == 0),.s_eol(f1_x == width1-13'd1),
        .s_frame_last(f1_x == width1-13'd1 && f1_y == height1-13'd1),.s_x(f1_x),.s_y(f1_y),
        .image_width(width1),.image_height(height1),
        .dw_weight(l2_dw_weight),.pw_weight(l2_pw_weight),
        .dw_bias(l2_dw_bias),.pw_bias(l2_pw_bias),.dw_shift(l2_dw_shift),.pw_shift(l2_pw_shift),
        .m_data(l2_data),.m_keep(l2_keep),.m_valid(l2_valid),.m_ready(l2_out_ready),
        .m_sof(l2_sof),.m_eol(l2_eol),.m_frame_last(l2_last),
        .m_x(l2_x),.m_y(l2_y),.idle(l2_idle)
    );
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            f0_head_done <= 0; f0_conv_done <= 0;
            f1_head_done <= 0; f1_conv_done <= 0;
            turn <= 0; locked_selection <= 0; locked <= 0;
        end else begin
            if (s_valid && s_ready) begin f0_head_done <= 0; f0_conv_done <= 0; end
            else begin
                if (grant0) f0_head_done <= 1;
                if (s_valid && !f0_conv_done && l1_ready) f0_conv_done <= 1;
            end
            if (l1_valid && l1_out_ready) begin f1_head_done <= 0; f1_conv_done <= 0; end
            else begin
                if (grant1) f1_head_done <= 1;
                if (l1_valid && retain1 && !f1_conv_done && l2_ready) f1_conv_done <= 1;
            end
            if (m_valid && m_ready) begin
                locked <= 0;
                if (selection == 2) turn <= 0;
                else turn <= selection+2'd1;
            end else if (m_valid && !m_ready) begin
                locked <= 1; locked_selection <= selection;
            end
        end
    end
endmodule
