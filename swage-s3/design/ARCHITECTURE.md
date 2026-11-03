# SWAGE-S3 architecture and integration

## Ownership and memory model

`rtl/swage_s1_*.sv` and `rtl/swage_s2_*.sv` retain the supplied legacy sources.
New stages are independently bypassable stream modules. `swage_s3_top`
owns atomic configuration and frame/snapshot exclusion. It instantiates
`u_crop`, `u_demosaic`, `u_convolution`, `u_csc`, `u_scaler`, `u_histogram`.
Each spatial stage contains `swage_s3_window u_window`.
CSC contains `u_chroma_expand`; scaler contains `u_luma` and `u_chroma`.

All new stage stream ports use the public data/valid/ready/SOF/EOL/component
mask convention. Common parameters are DATA_WIDTH, MAX_WIDTH and where needed
CHANNELS, PIXEL_BITS, PHASE_FRAC, INTERPOLATION. Common controls `enable`,
`image_width`, `image_height`, `origin_x`, `origin_y` are respectively one bit
and unsigned13 metadata. Dimensions/origins are stable for an entire frame.
Each geometry-dependent engine latches its active dimensions before filling
RAM, never while an old frame is draining. Spatial controls are `border`,
`cfg_abs`, `cfg_shift[4:0]`, `cfg_coeff[143:0]`. Window ports are
`w_data[9*DATA_WIDTH-1:0]` (row-major low tap first), `w_valid/w_ready`,
`w_sof/w_eol`, and unsigned13 `w_x/w_y`.

Crop additionally takes source geometry as parameters and runtime ROI fields.
It counts the full source raster, discards outside pixels and owns one elastic
output register. Bypass directly routes the stream with no crop arithmetic.
Both window engines use three synchronous line banks of MAX_WIDTH words with
valid row tags, explicitly rotating banks0,1,2. Scans add one border column on
each side. Read requests carry registered zero-padding flags; packing uses
registered bank results on the following advancing edge. Row-tag validity,
not RAM reset, excludes stale data.

Demosaic uses low-byte windows and global crop parity. Convolution reuses the
parent nine-stage signed arithmetic organization for each byte channel.
CSC controls are mode[2:0], input_format[1:0], output_420, coeff[143:0],
in_bias/out_bias[47:0]. Its seven-stage arithmetic has signed17 biased inputs,
signed33 products and signed36 accumulation/round/output-bias registers.
Inverse420's two-stage expander stores one16-bit chroma line of
ceil(MAX_WIDTH/2) words. Its read and write schedules never collide.

Scaler controls are `is_420`, image dimensions and enable. The luma/full-color
plane engine and packed Cb/Cr plane engine each use two synchronous line banks,
tagged row scheduling, a32-bit restoring divider and the preserved six-stage
interpolator. Plane ports add unsigned13 destination width/height and pixel
data without sparse masks; SOF/EOL follow that plane's raster.
Source fanout is atomic: a chroma-site input transfers to both input queues
together. Four-source-row input queues absorb the extra chroma lookahead
without forcing the luma engine to emit every repeated upscale row first.
Four-destination-row output queues absorb downscale phase skew before merging
the destination sparse raster. Both are synchronous RAM queues, so storage
remains O(source_width+destination_width), not O(frame area).
Queue RAMs use registered prefetch; full/non-power-of-two pointers wrap by
explicit comparison. Full-color/mono paths do not activate the chroma engine.
`swage_s3_fifo` ports are clk/rst_n, `s_data[WIDTH-1:0]`, s_valid/s_ready,
`m_data[WIDTH-1:0]`, m_valid/m_ready and empty. One pending read reserves its
output slot; RAM occupancy excludes that pending word and the output register.
`swage_s3_divider` ports are clk/rst_n/start, unsigned numerator[31:0],
denominator[12:0], busy/done and unsigned quotient[31:0]. Each division
consumes32 shift/subtract edges and pulses done once. Denominators are nonzero
legal plane dimensions. Plane `begin_frame` starts setup before input acceptance;
`idle` asserts only after output arithmetic and leftover source rows drain.
The plane scheduler ports name source/destination width/height[12:0], y0/y1,
source_valid/source_ready, pipe_advance/pipe_empty, issue_valid/sof/eol,
write_enable/bank/address[12:0], read_bank0/read_bank1, frame_start,
row_step and pixel_step. Bank selectors and pulse/status ports are one bit.

Histogram ports add `frame_begin`, `frame_drained`, `frame_idle`, `frame_ready`,
`frame_complete`, `reserved`, `hist_channel[1:0]`, `rgb_output`, and the public
statistics interface. It has one elastic pass register and one256x32 synchronous
counter RAM. Clear writes one address per edge; update FSM serializes read,
increment, write. Response FSM issues eight adjacent reads and packs responses
low-bin-first. Reserved snapshots suppress both configuration and frame starts.

## Boundaries and integration sequence

1. Assert rst_n low, including a real reset transition in a simulator.
2. Deassert reset on a clock-safe boundary. Present a full valid configuration
   until cfg_valid&&cfg_ready, or use defaults. Check cfg_error after that edge.
3. Present a well-framed source and hold each pending beat under backpressure.
   The top reserves a frame before source admission; enabled histogram clear
   and scaler setup may initially lower readiness.
4. Accept output beats with arbitrary finite stalls. Read hist_done only after
   source and output completion, then handshake requests and responses.
5. A new frame may replace an unread completed histogram, but not a reserved one.

The top's source and destination counters are25 bits, sufficient for4096^2
beats. Dimensions and coordinates are13 bits, including the terminal4096 value;
ROI additions use14 bits. Phase accumulators are F+13 bits. No RAM is
asynchronously cleared. No external memory, DMA or private board path is needed
by the product interface. Target is10AX115N2F45E1SG at<=10.0ns; pipelining is
an implementation intent, not measured timing closure.

## Preset CSC integer tables

Rows are output components, columns input components. Integers are Q3.13,
derived by exact rational scaling and nearest/ties-away rounding.
Forward input bias is[0,0,0], output bias[16,128,128].
Inverse input bias is[-16,-128,-128], output bias[0,0,0].

| Mode | Row0 | Row1 | Row2 |
|---|---|---|---|
| BT.601 forward | 2104,4130,802 | -1214,-2384,3598 | 3598,-3013,-585 |
| BT.709 forward | 1496,5032,508 | -824,-2774,3598 | 3598,-3268,-330 |
| BT.601 inverse | 9539,0,13075 | 9539,-3209,-6660 | 9539,16525,0 |
| BT.709 inverse | 9539,0,14686 | 9539,-1747,-4366 | 9539,17305,0 |

## Evidence boundary

No functional, synthesis, timing or physical-board result is asserted by these
documents. Verifier-owned commands and native runtime gates in SPEC.md must
run against these actual sources. Parent STA/PPA are unknown; no comparison
or inherited passing baseline is claimed. Historical oracle-parent preparation
receipts are independent oracle diagnostics, not S3 verification.
