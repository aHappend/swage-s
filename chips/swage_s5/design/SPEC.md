# SWAGE-S5 specification

## Authority and delivery status

The independently admitted SWAGE-S5 work order is the acceptance contract.
This document specifies the design; it is not a verification or timing result.
The Designer owns `rtl/*.sv`, this document and `ARCHITECTURE.md`. The independent
Verifier owns `tests/*`, numerical models, simulation, mutation and generic
synthesis. Shared-runtime timing and board gates remain mandatory. The runtime
produces the independently reviewed product PDF after engineering acceptance.

SWAGE-S5 is a programmable quantized YOLOv10/RT-DETR-inspired inference
subsystem, not a pretrained checkpoint, a training system or an accuracy claim.
One-to-one means one class and one box per spatial anchor per pyramid level.
There is no IoU, suppression, duplicate removal or NMS pass. Overlapping
high-scoring anchors remain eligible.

## Parent preservation and external interface

All 28 supplied S1--S4 modules retain their names, parameters, defaults, ports,
legal configurations and numerical/protocol behavior. The unchanged provenance
sources are `swage_s4_reference/rtl/*.sv`. The delivered parent modules are
`rtl/*.sv`; the reference directory is not a recipient reproduction dependency.
`swage_s5_top` literally instantiates `swage_s4_top u_isp`.

All S4 ports, widths and signedness are retained. All S4 parameters are retained:
`SRC_WIDTH=8`, `SRC_HEIGHT=8`, `DST_WIDTH=16`, `DST_HEIGHT=16`,
`PIXEL_BITS=8`, `CHANNELS=3`, `PHASE_FRAC=8`, `INTERPOLATION=0`,
`MAX_FRAME_PIXELS=4096`, `CLAHE_TILE=16`.

New integer parameters are `S5_MAX_WIDTH=max(SRC_WIDTH,DST_WIDTH)`,
`S5_NUM_CLASSES=4`, `S5_MAX_TOPK=16`, `S5_FIFO_DEPTH=2`. Legal ranges are
1..4096 (covering both source and destination widths), 1..8, 1..16 and 1..16,
respectively. Non-power-of-two FIFO depths are legal. Illegal static parameters
terminate with `PARAMETER ERROR`, using diagnostic-safe port/storage widths and
generate guards when necessary.

New inputs are `cfg_s5_enable[1:0]`, `cfg_s5_threshold[15:0]`,
`cfg_s5_topk[4:0]`, `weight_valid`, `weight_addr[15:0]`,
`weight_data[31:0]`, `det_axis_tready`, `roi_axis_tready`.
New outputs are `weight_ready`, `weight_error` and:

| Descriptor output | Width | Meaning |
| --- | ---: | --- |
| `det_axis_tdata` | 88 | `{confidence,class_id,y_max,x_max,y_min,x_min}` |
| `det_axis_tvalid`, `tuser`, `tlast`, `keep` | 1 each | Valid, first, final, object-present |
| `det_axis_rank`, `det_axis_count` | 8 each | Rank and final object count |
| `det_axis_frame_id` | 32 | Reset-epoch frame index |

All descriptor fields are unsigned; coordinates/confidence are 16 bits and
class is 8 bits. New ROI outputs are `roi_axis_tdata[47:0]`,
`roi_axis_tvalid`, `roi_axis_tuser`, `roi_axis_tlast`,
`roi_axis_frame_last`, `roi_axis_patch_id[31:0]`,
`roi_axis_patch_x[12:0]`, `roi_axis_patch_y[12:0]`,
`roi_axis_element[7:0]`, `roi_axis_keep[2:0]`,
`roi_axis_det_rank[7:0]`, `roi_axis_frame_id[31:0]`.
The inherited pixel, histogram and token interfaces remain distinct.

With S5 disabled, inherited monochrome8, monochrome10 and RGB8, crop, demosaic,
convolution, CSC/chroma, scaling, histogram, temporal, CLAHE and both token
formats retain parent behavior. No S5 frame-area restriction is introduced.
S4 enhanced operation retains its original on-chip frame-capacity restriction.

## Configuration and weight transactions

Bit 0 of `cfg_s5_enable` enables detection; bit 1 enables detection-triggered
ROI tokens. ROI without detection is illegal. Detection requires post-ISP RGB8,
including when S4 enhancements are disabled. Runtime topk is 1..S5_MAX_TOPK.
ROI requires patch8/14/16 and three nonzero normalization reciprocal lanes even
if inherited patch tokens are disabled.

The existing cfg transaction commits inherited and S5 values atomically.
Inherited or S5 illegality rejects the entire transaction with the inherited
error convention and changes neither configuration, history nor weights.
Configuration has priority over weights and input; no input/frame or weight is
accepted at the configuration edge. An accepted configuration becomes effective
after that edge. Configuration and weights remain blocked until all inherited
ISP activity, features, candidates, descriptors and enabled ROI outputs drain.
The next S5 frame is blocked until that same completion.

Weight writes are acknowledged only between frames. Simultaneous `cfg_valid`
holds `weight_ready=0`. An invalid acknowledged write pulses `weight_error`
without changing any weight. Reset clears weight_error, all weights/biases and
shifts, disables detection, sets topk=1 and threshold=65535.

Address is `(layer<<12)|(kind<<10)|index`. Layers 0..7 are stem, bottleneck0,
bottleneck1, fusion, F1, F2, classification and regression. Kinds 0..3 are
depthwise weights, pointwise/head weights, biases and shifts.
Depthwise index is `9*channel+tap`. Pointwise index is
`output_channel*input_channels+input_channel`. DS bias/shift indices first
address Cin depthwise lanes, then Cout pointwise lanes. Fusion/head bias/shift
indices start at zero. Fusion and heads have no depthwise entries. Cin/Cout are
3/6, 3/3, 3/3, 12/6, 6/6, 6/6, 6/classes and 6/4.
Weights require correct signed8 sign extension to 32 bits. Biases use all 32
bits. Shifts require upper bits zero and value <=31. Reserved layers, kinds or
indices are rejected.

## Numerical graph

Raw valid RGB lanes become signed8 `pixel-128`; invalid lanes contribute zero
without modifying the public pixel data or mask. Feature lanes and weights are
signed8. All reductions and bias additions are exact signed40; biases are
signed32 and are added exactly once. No intermediate reduction truncates.

`RNE(v,0)=v`. For other shifts, round the exact signed quotient by `2^shift`
to nearest, ties to even, symmetrically for negative values. RTL uses magnitude,
shifts and remainder bits, never division. Depthwise bias/RNE is saturated to
[-128,127] without activation. Pointwise feature bias/RNE is saturated to
signed8, then ReLU to [0,127]. Residual addition is signed9, then signed8
saturation and ReLU. Channels occupy increasing little-endian 8-bit slices.

Causal 3x3 tap `3*i+j` multiplies sample `(x+j-2,y+i-2)`. Top/left out-of-image
samples and invalid lanes are zero. No future-row lookahead or frame replay is
used. Frame boundaries invalidate old window contents without resetting RAM.
The stem is depthwise RGB3 followed by pointwise3->6.

C2f splits stem lanes0..2 into A and lanes3..5 into B:
`B1=ReLU(sat8(B+DSConv3(B)))`,
`B2=ReLU(sat8(B1+DSConv3(B1)))`.
Fusion pointwise12->6 consumes `[A,B,B1,B2]` in that exact order.
F0 is the fusion result. F1 is a separately weighted DSConv6 of F0 retaining
only even x/even y. F2 is a separately weighted DSConv6 of the resulting F1
raster, again retaining even coordinates. Level coordinates are local raster
coordinates; ISP strides are 1/2/4. Dimensions are ceil(W/stride),
ceil(H/stride), including singleton and odd edges.

The head projects all six feature lanes into classes and four distances, with
shared weights across levels. Confidence is RNE followed by unsigned16
saturation, with UNORM16 endpoints 0/65535; there is no sigmoid/softmax.
The winning class is largest confidence, then smallest class_id. Eligibility is
`confidence>=threshold`. Distance RNE results clamp to integer [0,4096].
For level stride s and coordinate `(ix,iy)`, anchor is `(s*ix,s*iy)`.
Boxes are half-open:
`xmin=max(0,x-left)`, `ymin=max(0,y-top)`,
`xmax=min(W,x+1+right)`, `ymax=min(H,y+1+bottom)`.
Every retained box is nonempty.

Global ranking is confidence descending, then class ascending, level ascending,
y ascending, x ascending. Emit exactly min(topk,eligible_count) in that order.
Physical candidate interleaving and stalls cannot change the order. Registered
Top-K insertion uses at most MAX_TOPK+2 acceptance steps per candidate; no
unregistered chain across all slots or score-dependent variable iterations.

A nonempty stream has keep=1, ranks0..count-1, count on every record, tuser at
rank0 and tlast at the final rank. An empty frame emits exactly one valid
completion with keep=0, count=rank=data=0, tuser=tlast=1; it is not an object.
Frame IDs start at zero and increment only after complete S5 delivery.

## ROI storage and ordering

Every final descriptor triggers the patch-size-aligned post-ISP raw RGB patch
containing its anchor, not the entire regressed box. Repeated patches are emitted
for repeated detections, in descriptor-rank order. Each patch uses raster element
order, Q8.8/BF16 inherited normalizer semantics, nearest-even rounding,
saturation and edge keep=000/zero padding. Patch x/y are grid coordinates;
patch_id is their raster grid ID. tuser marks the first element of the first
ROI, tlast each patch end, frame_last the final element of the final ROI.
Empty frames emit no ROI records.

The ISP fork acknowledges the public and S5 branches independently and retires
an ISP beat only after both have consumed it exactly once. No external DDR or
frame-memory interface is added. Raw S5 storage is bounded by one stripe of
patch_size*S5_MAX_WIDTH pixels plus (S5_MAX_TOPK+1)*patch_size^2 retained
pixels/masks and bounded bookkeeping. All other image-dependent S5 storage is
line buffers, pipeline registers or bounded FIFOs. Inherited S4 frame/history
RAM is separate and does not license a second S5 frame cache.

Input can pause at a stripe boundary while features/candidates drain and winning
patches are copied. The stripe cannot be evicted until all its anchors resolve
and all required patches are retained. Global Top-K replacement and ROI ownership
must be atomic. After the frame, tokenize only retained patches without rereading
the frame. `swage_s5_roi_tokens` literally instantiates a separate
`swage_s4_normalizer u_roi_normalizer`.

## Frozen private feature interfaces

Every feature block has `clk,rst_n`, unsigned `s_valid,s_ready,s_sof,s_eol,
s_frame_last,s_x[12:0],s_y[12:0],image_width[12:0],image_height[12:0]`,
`s_data[8*CIN-1:0],s_keep[CIN-1:0]`, and corresponding `m_*` signals
with COUT lanes. Data slices are signed8. Coordinates and markers belong to
accepted beats. `idle` is a reset-gated output identifying no pending feature
work; it is used for stripe ownership, not a debug interface.

The following additional private interfaces are frozen before RTL construction:

| Module | Additional interface / parameters |
| --- | --- |
| `swage_s5_window` | CIN, MAX_WIDTH; m_data has CIN*9 signed8 taps in channel-major order; m_keep retains center-lane validity |
| `swage_s5_dsconv` | CIN, COUT, MAX_WIDTH; dw_weight[CIN*9*8], pw_weight[COUT*CIN*8], dw_bias[CIN*32], pw_bias[COUT*32], dw_shift[CIN*5], pw_shift[COUT*5] |
| `swage_s5_pointwise` | CIN, COUT; pw_weight[COUT*CIN*8], pw_bias[COUT*32], pw_shift[COUT*5] |
| `swage_s5_bottleneck` | MAX_WIDTH; fixed CIN=COUT=3, DS weights/biases/shifts as above; internal transform and residual queue |
| `swage_s5_c2f` | MAX_WIDTH; b0/b1 dw/pw_weight/bias/shift groups for 3->3; fuse_weight[576], fuse_bias[192], fuse_shift[30] |
| `swage_s5_queue` | WIDTH, DEPTH; packed s_data/m_data, ready/valid, idle; no arithmetic or markers outside packed payload |
| `swage_s5_fork` | DATA_WIDTH; ISP s_data, s_keep[2:0], s_valid/s_ready/s_sof/s_eol; public p_* and branch b_* data/keep/valid/ready/sof/eol; branch_enable, idle |
| `swage_s5_pyramid` | MAX_WIDTH; l1/l2 DS weights/biases/shifts for 6->6; m_level[1:0] identifies local-coordinate level on the arbitrated feature stream |
| `swage_s5_head` | NUM_CLASSES; class/regression packed weights/biases/shifts, threshold[15:0], s_level[1:0]; candidate m_data[87:0], m_eligible, m_level[1:0], m_x/y[12:0], m_frame_last, ready/valid, idle |
| `swage_s5_topk` | MAX_TOPK; runtime topk[4:0]; candidate data/eligible/level/x/y/frame_last with ready/valid; finish/start-frame controls; snapshot slot read, occupied/data/anchor metadata; ranked descriptor data/count/rank/keep/first/last with ready/valid; idle |
| `swage_s5_roi_tokens` | MAX_WIDTH, MAX_TOPK; stripe pixel write and ownership controls; snapshot slot metadata and retained-patch commands; ranked descriptor/anchor association; normalizer config; ROI outputs matching top sidebands; idle |

The latter orchestration interfaces will be expanded into a bit-by-bit table
before their RTL is written, without changing the numerical or external contract.
Required literal top instances are u_isp, u_fork, u_stem, u_c2f, u_pyramid,
u_head, u_topk, u_roi_tokens. Each DSConv contains u_window. C2f contains
u_bottleneck0, u_bottleneck1 and u_fuse; pyramid contains u_level1 and u_level2.

### Pyramid, head and ranking orchestration ports

Pyramid uses the six-lane common feature interface plus `m_level[1:0]`.
Its weight groups are `l1_dw_weight,l2_dw_weight[431:0]`,
`l1_pw_weight,l2_pw_weight[287:0]`,
`l1_dw_bias,l1_pw_bias,l2_dw_bias,l2_pw_bias[191:0]`,
`l1_dw_shift,l1_pw_shift,l2_dw_shift,l2_pw_shift[29:0]`.
Its downstream x/y are local to the identified level. Its SOF/EOL/frame-last
markers identify that level's retained raster, not the physical arbitration order.
It locks arbitration while stalled and uses round-robin grants after transfers.

Head parameter is `NUM_CLASSES=4` (legal1..8, diagnostic-safe class count).
Its input is the common six-lane interface plus `s_level[1:0]`.
Weights are `class_weight[48*NUM_CLASSES-1:0]`, `reg_weight[191:0]`;
biases `class_bias[32*NUM_CLASSES-1:0]`, `reg_bias[127:0]`;
shifts `class_shift[5*NUM_CLASSES-1:0]`, `reg_shift[19:0]`;
`threshold[15:0]`. Outputs are candidate `m_data[87:0]`, `m_eligible`,
`m_level[1:0]`, native-anchor `m_x,m_y[12:0]`, `m_frame_last`,
`m_valid,m_ready,idle`. Ineligible beats still transport completion markers.

Top-K parameter is `MAX_TOPK=16` (legal1..16, diagnostic-safe slot count).
Inputs are `topk[4:0]`, `start_frame`, `finish_valid`, candidate
`s_data[87:0],s_eligible,s_level[1:0],s_x,s_y[12:0],s_frame_last,s_valid`;
outputs `finish_ready,s_ready,idle,delivery_done`. `start_frame` is issued
only after prior complete delivery; it clears occupied slots without clearing
image RAM. `finish_valid` is issued only after stripe snapshots and all features
have drained and has priority over candidate acceptance.

Snapshot input `snapshot_index[4:0]` selects a physical retained slot; outputs
are `snapshot_valid`, `snapshot_data[87:0]`, `snapshot_x,snapshot_y[12:0]`,
`snapshot_level[1:0]`. These are a combinational view of bounded registered
metadata, stable unless that slot is committed or a new frame starts. During
stripe copy, the owner has already drained candidates, so slots cannot change.

Ranked outputs are `m_data[87:0],m_valid,m_ready,m_keep,m_first,m_last`,
`m_rank,m_count[7:0]`, native anchor `m_x,m_y[12:0]`, `m_level[1:0]`,
and physical ROI slot `m_slot[4:0]`. `delivery_done` pulses after final descriptor
acceptance; it does not authorize the next frame while ROI/S4 work is pending.
All new reset-visible outputs are zero.

Ranking retains unsorted physical slots, each owning the corresponding ROI
bank. Every eligible insertion scans exactly MAX_TOPK slots, then commits once:
use the first free legal slot, or replace the worst if the new tuple is better.
This is bounded registered Top-K insertion, with MAX_TOPK+2 candidate acceptance
spacing. After finish, each ranked output scans exactly MAX_TOPK slots for the
best not-yet-emitted tuple. There is no score-dependent scan length, sorted-slot
shifting, NMS pass or ROI-bank reassociation.

## Cycle and degenerate-boundary table

| Event / configuration | Pre-edge sampling | Post-edge state and first availability |
| --- | --- | --- |
| Ordinary transfer | rst_n && valid && ready at edge E | Receiver owns beat after E; sender can advance |
| Stalled beat | valid && !ready | Data, valid and every sideband remain unchanged |
| N-stage unstalled pipeline | Accepted at E | Output valid after E+N-1; first transfer E+N |
| Reset active | No transfer, ready/valid/error/new sidebands zero | Async control/pipeline cancellation; RAM contents need not clear |
| First edge after reset | Only asserted valid && ready transfers | No stale completion; empty pipeline remains empty without input |
| cfg accepted | cfg_valid && cfg_ready; input/weights blocked | Atomic commit after E; rejection leaves all configuration unchanged |
| FIFO DEPTH=1 | Full only when its one slot occupied; simultaneous pop/push permitted | Replacement occupies the same slot, not an invalid pointer |
| FIFO DEPTH=3 or 16 | Read/write pointers wrap explicitly at DEPTH-1 | Occupancy changes only for unequal push/pop |
| First && last | Legal singleton image/record/patch-completion markers sampled together | Exactly one acceptance; never require separate first/last cycles |
| Simultaneous read/write | Read observes pre-edge RAM word; write commits after edge | Registered response is the old word (row buffers need this semantics) |
| Reset vs flush vs transfer | Reset highest priority; frame-start invalidates prior window epoch | No old state becomes an accepted new-frame result |
| Top-K replacement | Candidate and ROI ownership commit together | Old ROI cannot be emitted with the new candidate |
| Frame completion | All inherited/S5 sinks and pipelines drained | Frame ID increments; config/weight/next-frame permission resumes |

Window is two registered advances: synchronous row read, then assembly.
DSConv is 13 advances: window2, multiply1, nine-term reduction4, DW bias1,
DW RNE/saturation1, PW multiply1, reduction2, PW bias/RNE/saturation1.
Standalone fusion is five: multiply1, reduction2, bias1, quantize/activate1.
Residual merge is one registered advance. Split bypasses are handshake-aligned.
Head is eight: multiply1, reduction3, bias1, RNE1, class/distance select1,
box assembly1. Both inherited and ROI normalization retain eight advances.
Latency counts elastic advances, not stalled clocks; composite top latency
includes stripe scheduling and cannot be described as a fixed frame latency.

## Timing and independent acceptance

All delivered reachable RTL targets Arria 10 GX 10AX115N2F45E1SG at <=10 ns.
There are no combinational `/` or `%` operators, mixed blocking/nonblocking
sequential assignments or wide unregistered reductions. Non-power-of-two
coordinates/FIFOs use wrapping counters. Memories >=2 KB with byte enables use
separate unpacked byte-lane arrays/masks and no reset loops over their contents.
DSConv supports initiation interval one with continuously valid input and ready
sinks after fill. This is not a full-frame II=1 claim.

Detection with legacy processing/S4 enhancements disabled, RGB8 256x256,
three levels, eight classes, topk16/MAX_TOPK16, patch16 and ROI enabled must
complete input through final output within 3,200,000 cycles with ready sinks.
This corresponds to >=31.25 frames/s at 100 MHz. Measure the actual count.
Native timing lint, Quartus 17.1 post-fit setup/hold WNS>=0, and three consecutive
deterministic hardware-bridge HIL passes are mandatory. Connectivity-only evidence
is not functional detection equivalence. Timing waivers require Coordinator
disposition supported by actual post-synthesis STA.

`python3 tests/verify.py` is the independent portable aggregate L1 entrypoint.
It must execute compact Icarus, compiled Verilator, independent integer/Fraction
oracles, >=8 substantive disposable RTL mutations, Yosys synthesis/netlist
zero-latch inspection and clean-directory numerical/synthesis reproduction.
No preexisting receipt, final PDF or private runtime approval is an L1 prerequisite.
Clean reproduction needs only distributable RTL, SPEC, ARCHITECTURE and tests.

The mandatory S5 matrix is dimensions1x1/1x9/9x1/13x17/64x48/256x256 crossed
with seeds11/29/47: 18 compiled configuration/seed runs. Each has ready and
independently stalled identical-input/weight replays. Distribute patch8/14/16,
classes1/3/8, topk1/3/16; the performance cell uses8/16/16. Measure every specified
coverage event, including zero/multiple objects, eligible_count>topk, threshold
equal/below/above, class/ranking ties, no-NMS overlap, borders/partial patches,
signed saturation, retained descriptors from every level, independent sink stalls,
bubbles, repeated frames, reset/reconfiguration and replacement with correct ROI.
Stalls include1/2/17/257 cycles and randomized bounded patterns.

Parent numerical coverage is independent, with unchanged-reference differential
comparison supplementary only. Cover all formats; nearest/bilinear phase8/16;
crop/borders; signed/absolute/shift convolution saturation; CSC and 420/422;
histogram channels/queries; temporal alpha0/128/256, strict threshold equality and
>=3-frame history; CLAHE tile8/16 clipping/redistribution/full/partial; patch8/14/16
Q8.8/BF16 padding/reciprocal/mean extremes and stalls; standalone and S5-disabled
integration at seeds11/29/47. Include atomic invalid configurations, static
boundary-1/boundary/boundary+1 where representable, FIFO1/3/16, max geometry
elaboration and short-height maximum-width stripe/window behavior.

Mutation targets are spatial tap/row alignment, PW channel order, C2f bypass,
negative/tie rounding, reversed Top-K comparison, threshold >= changed to >,
descriptor/ROI association, BF16 truncation and inherited temporal equality.
Only real simulation with unchanged numerical expectations detecting intended
corruption counts, not syntax error, timeout or unrelated crash.

Each L1 invocation allocates fresh `evidence/l1-<UTCtimestamp>-<hash>/` and retains
exact command/cwd, versions, source hashes/snapshots, times, exit/timeout, raw logs,
coverage, cycles, mutations and complete synthesized artifacts. Preserve failures;
do not overwrite receipts. Any genuine advisory static diagnostic uses a fresh
project-local preparation directory and a retained receipt. Designer does not
author functional/synthesis harnesses. Icarus stays compact <=500,000 cycles,
expected <60 seconds; substantive medium/large, multiframe/seed/mutation tests
use compiled Verilator. No per-cycle/beat stdout or per-request fflush.
