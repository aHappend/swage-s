# SWAGE-S4 frozen design contract

## Scope, provenance and ownership

Top `swage_s4_top` preserves callable S1/S2/S3 tops and their source behavior.
`swage_s3_reference/rtl/*.sv` is read-only provenance, not an independent oracle.
The parent specification, repository-intake source index/linked intake record and
`design/GENERATIONAL_HANDOFF.md` were unavailable at intake. No historical
Quartus timing, PPA or acceptance is credited. Restoration is requested through
the shared runtime; absence does not waive current checks.

Designer owns `rtl/*.sv`, this specification and `design/ARCHITECTURE.md`.
Independent Verifier owns `tests/*.py`, functional harness generation, mathematical
oracles, mutations and synthesis. The shared runtime owns timing, hardware gates
and the independently reviewed product PDF after engineering acceptance.

## Parameters and ports

Retain every S3 port name, width, direction and meaning, including configuration,
RGB/mono AXIS and histogram request/response. Parameters are SRC_WIDTH=8,
SRC_HEIGHT=8, DST_WIDTH=16, DST_HEIGHT=16, PIXEL_BITS=8, CHANNELS=3,
PHASE_FRAC=8, INTERPOLATION=0, MAX_FRAME_PIXELS=4096, CLAHE_TILE=16.
The preserved S3 default CHANNELS remains 1.

| Legacy port(s) | Direction | Width |
|---|---|---|
| clk, rst_n | input | 1 each |
| s_axis_tdata | input | DATA_WIDTH |
| s_axis_cvalid | input | 3 |
| s_axis_tvalid, s_axis_tuser, s_axis_tlast | input | 1 each |
| s_axis_tready | output | 1 |
| m_axis_tdata | output | DATA_WIDTH |
| m_axis_cvalid | output | 3 |
| m_axis_tvalid, m_axis_tuser, m_axis_tlast | output | 1 each |
| m_axis_tready | input | 1 |
| cfg_valid | input | 1 |
| cfg_ready, cfg_error | output | 1 each |
| cfg_enable | input | 6 |
| cfg_border, cfg_abs | input | 1 each |
| cfg_shift | input | 5 |
| cfg_coeff | input | 144 |
| cfg_roi_x, cfg_roi_y, cfg_roi_width, cfg_roi_height | input | 13 each |
| cfg_input_format | input | 2 |
| cfg_csc_mode | input | 3 |
| cfg_output_420 | input | 1 |
| cfg_csc_coeff | input | 144 |
| cfg_csc_in_bias, cfg_csc_out_bias | input | 48 each |
| cfg_hist_channel | input | 2 |
| hist_req_valid | input | 1 |
| hist_req_addr | input | 5 |
| hist_req_ready, hist_rsp_valid | output | 1 each |
| hist_rsp_data | output | 256 |
| hist_rsp_ready | input | 1 |
| hist_done | output | 1 |

cfg_enable bits0..5 enable crop, demosaic, convolution, CSC, scaling, histogram.
Formats0/1/2 denote RGB (or mono for mono tuples), YUV444, YUV420.
cfg_hist_channel0/1/2 selects the corresponding component, 3 selects RGB intensity.
Legacy AXIS low-byte-first RGB and masks retain their exact parent semantics.

Dimensions are 1..4096; legal (PIXEL_BITS,CHANNELS) are (8,1), (10,1),
(8,3); DATA_WIDTH is respectively 8,16,24, with mono10 bits 15:10 zero.
PHASE_FRAC is 8 or16; INTERPOLATION is 0 (bilinear) or1 (nearest).
MAX_FRAME_PIXELS is 1..65536; CLAHE_TILE is 8 or16. Illegal elaboration
parameters issue a named `PARAMETER ERROR` with safe diagnostic-only widths.
Capacity restricts enabled enhancements, not legacy bypass dimensions.

New inputs: cfg_s4_enable[2:0] (temporal, CLAHE, tokens in bits 0,1,2),
cfg_temporal_alpha[8:0], cfg_motion_threshold[8:0], cfg_clip_limit[8:0],
cfg_patch_size[4:0], cfg_norm_bf16, cfg_norm_mean[71:0],
cfg_norm_inv_std[71:0], token_axis_tready.
Mean/reciprocal fields use channel c in bits 24*c +:24, R,G,B order.
New outputs: token_axis_tdata[47:0], token_axis_tvalid, token_axis_tuser,
token_axis_tlast, token_axis_frame_last, token_axis_patch_id[31:0],
token_axis_patch_x[12:0], token_axis_patch_y[12:0],
token_axis_element[7:0], token_axis_keep[2:0]. Each sixteen-bit lane is
R then G then B in increasing bit positions.

This is normalized HWC patch-sample transport for a tensor-core adapter, not
learned patch projection, embeddings, attention, model weights, or an asserted
proprietary Gemma-4/Qwen-VL protocol.

## Configuration, reset and admission

One clock, asynchronous active-low rst_n. Reset forces ALL public data, sidebands,
valid, ready, errors and done to zero independently of inputs. No memory clearing
is required; validity prevents observing stale memory. Reset disables S4,
sets alpha=128, threshold=16, clip=4, P=16, Q8.8, each mean=32640,
each reciprocal=512. Parent defaults, including enable=010000, are unchanged.
Parent reset border/abs/shift are zero, convolution coeff=144'd1<<64,
ROI origin=(0,0), ROI dimensions=source dimensions, input format/CSC mode/
output420/CSC coefficients/input-output biases/hist channel all zero; source/
destination counters and frame-active/error state clear.

Configuration is atomic at cfg_valid && cfg_ready. Accepted configurations,
even identical repeats, invalidate history. Rejections pulse common cfg_error
for one cycle and update neither generation's configuration nor history.
cfg_valid has priority over starting a frame. In S4 bypass all inherited
signals match the parent cycle-for-cycle without an extra public-path register.
Enhanced cfg_ready stays low through capture, CLAHE, raster and token drainage.
Additional inputs are ignored if all S4 features are disabled.

Effective W,H are destination dimensions when scaling, else ROI dimensions when
crop enabled, else source dimensions. Any enhancement requires RGB8 parameters,
RGB final format and W*H <= MAX_FRAME_PIXELS. Temporal alpha/threshold are 0..256;
CLAHE clip is 1..CLAHE_TILE squared; token P is 8,14,16 and every reciprocal
nonzero. Disabled-feature fields are ignored. All parent legality rules remain:
format 3 illegal; mono input must be format0; cropped ROI nonzero/in bounds and
YUV420 ROI origin even; demosaic requires RGB8 format0; convolution requires
8-bit format0; CSC requires RGB channels and mode<=4; modes0/1 require RGB input,
modes2/3 require YUV input; histogram mono requires channel0, intensity channel3
requires RGB output. Incompatible YUV/mono enhancement and capacity excess reject.
CSC output is RGB for inverse conversion, otherwise YUV444 or requested YUV420;
without CSC output format equals input format.

## Literal legacy numerics

Order: crop, Bayer demosaic, signed 3x3 spatial convolution, CSC, 2D scaling,
256-bin histogram. Crop consumes full source raster, regenerates SOF/EOL and
retains absolute Bayer parity. Border0 replicates; border1 zero pads.
Demosaic uses the parent's forward 2x2 block anchored at the current pixel,
direct red/blue selection and green=(green_a+green_b+1)>>1.
Convolution coefficients are signed16 row-major low-field-first. Signed sums
use magnitude rounding +2^(shift-1) for shift>0, optional abs, byte saturation;
negative non-abs results become zero.

CSC coefficient scale is 8192; rows below are output channels, columns input
channels. Signed magnitude rounding is nearest, ties away from zero, before
output bias and byte saturation.

| Mode | Matrix rows | Input bias | Output bias |
|---|---|---|---|
| 0 | (2104,4130,802); (-1214,-2384,3598); (3598,-3013,-585) | (0,0,0) | (16,128,128) |
| 1 | (1496,5032,508); (-824,-2774,3598); (3598,-3268,-330) | (0,0,0) | (16,128,128) |
| 2 | (9539,0,13075); (9539,-3209,-6660); (9539,16525,0) | (-16,-128,-128) | (0,0,0) |
| 3 | (9539,0,14686); (9539,-1747,-4366); (9539,17305,0) | (-16,-128,-128) | (0,0,0) |
| 4 | programmable signed16 row-major low-field-first | programmable signed16 | programmable signed16 |

Preserve parent YUV420 placement, chroma replication, masks and odd dimensions.
Scale phase increments=floor((source_dimension<<F)/destination_dimension).
Bilinear rounds the full 2D weighted sum once using +2^(2F-1).
Nearest selects floor(phase+2^(F-1)); coordinates clamp to last sample.
YUV420 ceil-half chroma planes scale separately.
Histogram counts accepted pre-S4 output samples: mono10 bin=sample>>2,
RGB intensity=(77R+150G+29B+128)>>8, otherwise valid selected channel only.
Each five-bit response address selects eight little-field-first unsigned32 bins.
Preserve request priority, snapshot reservation, stable responses and done lifetime.

## Temporal and tone numerics

H is the same-position previous UNTONED temporal result.
M=max(abs(X_R-H_R),abs(X_G-H_G),abs(X_B-H_B)).
Invalid history or M>=threshold selects X; otherwise
Y=((256-alpha)*X+alpha*H+128)>>8 independently per channel, with full
unsigned intermediate precision. Alpha weights history. Threshold0 always selects
X; threshold256 always blends valid history. Alpha0 selects X, alpha256 selects
H in the static branch. Capture stores Y as next history, valid only after a
complete temporal-enabled capture. Reset mid-capture discards it; temporal
disabled never consults or claims valid history.

CLAHE tiles are anchored at (0,0), square CLAHE_TILE, clipped at real boundaries.
For each channel/tile independently, with N real pixels:
h[i]=count, c[i]=min(h[i],clip), E=sum(h-c), q=floor(E/256),
r=E-256*q, b[i]=c[i]+q+(i<r). One redistribution only, no reclipping;
sum b=N. LUT[i]=floor(255*sum(b[0..i])/N), no CDF-min subtraction.
Replace real samples with LUT[v]. No tile interpolation/hue-preservation claim.
Tone bypass is bit-exact. Histogram/LUT accesses are synchronous; quotient
generation uses single-bit-per-cycle registered division, not combinational / or %.

## Patch order and normalization

Patch-grid columns=ceil(W/P), rows=ceil(H/P), patch row-major, then local
pixel row-major. patch_id=patch_y*columns+patch_x; element=local_y*P+local_x.
Counters implement wrapping. Emit P*P RGB-vector beats for EVERY patch including
partial patches. Padding keep=000 and data=0; real samples keep=111.
Padding never enters CLAHE, history or histogram. tuser marks only frame first,
tlast marks each patch final element, frame_last marks final patch final element.
Every valid beat and all tags remain stable until accepted.

Mean is signed24 Q*.8 in raw pixel units; reciprocal unsigned24 Q*.16.
T=((pixel<<8)-mean)*reciprocal is exact signed, value=T/2^24.
Q8.8=signed16 saturation of round-nearest-even(T/2^16).
BF16=round-nearest-even of exact T/2^24, NOT converted rounded Q8.8.
Exact zero is +0; admitted fields produce finite BF16. Sign, leading-one,
exponent and guard/round/sticky handling are explicit.
For conventional [0,1] mean mu and standard deviation sigma, configure
mean=round_nearest_even(mu*255*256),
reciprocal=round_nearest_even(65536/(sigma*255)); software must use sufficient
precision for those coefficient conversions, and reject results outside the
signed24/unsigned24 configuration ranges. Mean quantization error is at most
1/512 raw-pixel unit; reciprocal quantization error is at most 1/131072 inverse
raw-pixel unit. The reciprocal must remain nonzero.
No estimated per-frame statistics or hidden model constants.

## Cycle and degenerate-boundary tables

Transfers use PRE-edge valid && ready; results are observed POST-edge after NBA.
Stalled pipelines advance only when their last stage is empty or ready.

| Event | Pre-edge | Post-edge |
|---|---|---|
| Reset asserted | all public outputs suppressed immediately | configuration defaults, invalid history, empty pipelines |
| First released clock | no fabricated valid beat | idle unless explicit input/configuration present |
| Accepted configuration | cfg_valid && cfg_ready, legal | both configurations replaced; history invalid |
| Rejected configuration | cfg_valid && cfg_ready, illegal | cfg_error=1; all configuration/history unchanged |
| Next clock after rejection | prior error may be observed | cfg_error clears unless another rejection |
| Configuration + new frame | cfg_valid=1 | configuration priority; no frame start |
| Hist request + idle frame | request valid && done | parent reservation priority; no frame start |
| Final raster stalled | m_valid=1, m_ready=0 | data/mask/SOF/EOL held; no token phase |
| Final token stalled | token_valid=1, token_ready=0 | every lane/tag/flag held; no idle/config admission |
| Final token accepted | valid && ready | phase may return idle only after all pipelines drain |
| Reset mid-frame | partial capture/readout exists | discard phases, invalidate history, suppress every output |

| Temporal advance edge | Registered boundary |
|---|---|
| E | X,H,configuration and tags captured |
| E+1 | three absolute differences |
| E+2 | motion decision and channel weighted products |
| E+3 | rounded channel sums/current selection |
| E+4 | Y and valid exposed (five stages including capture) |

| Normalizer advance edge | Registered boundary |
|---|---|
| E | samples, coefficients, format and tags |
| E+1 | subtract means |
| E+2 | exact multiply |
| E+3 | sign/magnitude, Q rounding information or leading-one position |
| E+4 | rounded Q candidate or BF16 shift/mantissa information |
| E+5 | Q saturation or BF16 mantissa rounding |
| E+6 | pack three lanes |
| E+7 | expose valid/data/tags (first consumable at E+8) |

RAM request accepted at E reads RAM into q at E; E+1 stages response from q.
Requester uses only response handshake, never old q. Single outstanding request
precludes read/write collision: capture reads history before writing that address;
CLAHE reads work before its remap write. Reset/annul outranks all operations.
The preserved FIFO DEPTH1 and DEPTH3 use explicit wrap: public empty means no
available output or pending prefetch; memory-full push stalls even when a
prefetch occurs on that edge. Simultaneous accepted push and internal memory
prefetch preserve stored occupancy and independently advance their pointers.
Output staging/prefetch occupancy is distinct from stored-memory occupancy.

For W=H=1 and P=8/14/16: one patch; first element is real (keep111,
tuser1), remaining P*P-1 are zero padding (keep000). Final element has
tlast=frame_last=1, tuser=0. A singleton frame does not mean a singleton token
beat because P>=8. The sole raster pixel has SOF=EOL=1.
Enhanced ordering is IDLE -> CAPTURE -> optional CLAHE -> PIXEL_READOUT ->
optional TOKEN_READOUT -> IDLE, after final accepted beats and pipeline drainage.
Full frame latency is geometry/algorithm/stall dependent; no fixed throughput claim.

## Mandatory independent acceptance

`python3 tests/verify.py` is the portable complete L1 entry, owned by Verifier.
Real Icarus/vvp compact checks, compiled Verilator matrices/mutations, independent
integer/rational comparisons, strict active-X/Z rejection and real Yosys coarse
synthesis are mandatory. Compare every accepted pixel/mask/marker, token lane/tag,
all requested bins and exact stream lengths. Parent differential checks supplement
the independent oracle. Observed transfers/assertions, not scenario names, count
as machine-readable coverage.

Minimum legacy matrix: all twelve tuple/F/interpolation combinations; 1x1,1x7,
7x1,3x5,7x11; up/down/identity scaling; all legal RGB cfg_enable combinations;
CSC0..4 and RGB/YUV444/YUV420 directions with odd420; both borders; four ROI
parities; signed/extreme coefficients, abs both, shifts0/1/15/31; mono10 endpoints;
hist channels/all32 addresses; invalid ROI/formats/modes/channels and busy config.
Representative bypass cycle equality in EVERY parameter tuple is mandatory.

Enhancements: all seven masks, seeds17/89/203, >=3 consecutive frames EACH;
stationary/low-motion/moving; alpha0/1/128/255/256, threshold0/1/16/255/256,
below/equal/above boundaries; reseed/reset mid-frame/accepted-rejected config.
CLAHE tiles8/16, full/partial, constant/ramp/impulse/checkerboard, N=1,
nonzero clipping excess and remainder, clip1/default/N.
For every P and both formats: 1x1,(P-1)x(P+1),PxP,(P+1)x(P-1),
(2P+1)x(P+1), padding/order/multiple patch rows/columns/coincident final flags.
Normalizer: positive/negative/zero, signed mean extremes, reciprocal min/max,
both Q saturation directions, exact/neighbor rounding ties, BF16 mantissa carry.
Capacity1/257, exact64x64 default fit, reject65x64. Elaborate legal maxima;
reject illegal dimensions/tuples/F/interpolation/capacity/tile and configuration
patch/alpha257/threshold257/zero reciprocal/zero clip. Cover isolated/sustained
gaps/stalls, final raster/token stalls, histogram priority/reservation and idle
simultaneous requests. Prove bounded completion with continuously ready sink
using cycle bounds from finite dimensions and division iterations; report cycles,
not host speed as functional acceptance.

At least seven disposable compiling/executing mutations must be rejected by an
unchanged oracle with named first mismatch: threshold equality, temporal rounding,
CLAHE clipping/redistribution, CDF/LUT, patch ordering/padding/tags, signed Q rounding/
saturation, BF16 ties-even. Compile failure/timeout/PASS grep is not sensitivity.
Synthesize actual default RGB and mono8/mono10 bypass: hierarchy/check, process
lowering, optimization, inferred memories, no black boxes/latches, nonempty
sequential implementation; retain substantive enhancement hierarchy/netlist stats.
Compact FIFO DEPTH1/3 boundaries are mandatory. Fresh clean reproduction from
declared sources must execute independently compared enhanced+legacy smoke,
without recursive reproduction or private metadata.

Each L1 invocation creates fresh evidence/l1-<timestamp_or_hash>/ with exact
argv/cwd, source hashes, tool versions, start/end, child exits/timeouts, raw logs
and comparison artifacts. Retain failures and repair links, never overwrite or
reuse stale shards. Icarus only <=500k cycles and designed <60s; sweeps/mutations
use compiled Verilator, no per-beat tracing/flush. No installations, local cable/
BDF configuration, duplicate timing/HIL gates or final-report prerequisites in L1.

All RTL targets <=10.0ns on Arria10 GX 10AX115N2F45E1SG: zero combinational
division/modulo, zero mixed sequential assignments, bounded registered logic,
synchronous BRAM, no generated clocks/functional false paths/black-box shortcuts.
L1 is not timing proof. Native timing lint, Quartus17.1 STA WNS>=0 at <=10ns
with no unconstrained functional clock paths, and >=3 consecutive deterministic
PCIe Gen3x8 physical-board passes remain mandatory. No timing waiver is admitted.
The independently approved runtime product PDF is final delivery after acceptance.
