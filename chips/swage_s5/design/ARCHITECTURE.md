# SWAGE-S5 architecture

## Implementation boundary

The integrated design implements the admitted contract in `design/SPEC.md`.
This document describes source, not simulation, synthesis, timing or hardware
acceptance. All numerical/protocol and physical results require the independent
Verifier and shared-runtime gates.

The S1--S4 source inventory is preserved from `swage_s4_reference/rtl/` into
`rtl/`. Its historical post-fit figures in `GENERATIONAL_HANDOFF.md`
(setup0.971 ns, hold0.053 ns, 9642 ALMs, 14940 registers, 36 M20Ks) are parent
provenance, not current S5 measurements. Historical HIL was connectivity-only.
No private parent path is needed by recipient commands.

## Streaming compute

The new window stores two alternating parity row banks per channel. At input
acceptance, both old row words are read synchronously and the current row word
overwrites its parity bank. At the next advance, assembly uses the old words and
two horizontal delays per row. Read-before-write is necessary for width1.
Coordinates suppress stale row/horizontal contents at top/left boundaries.
Invalid lanes are zeroed before writing the row banks. RAM is never reset.

Every DSConv has a literal `u_window`, then registered signed40 balanced
reductions, depthwise bias/round/saturation, pointwise products/reductions and
pointwise bias/round/saturation/ReLU. The 13-stage schedule is in SPEC. Its
arithmetic tail uses a shared elastic advance; the window has its own two-slot
elastic advance and can buffer while the tail is full. Stalls preserve all data
and coordinate/marker sidebands. No arithmetic depends on wall-clock latency.

C2f implements the real split/transform/concatenate graph. Each bottleneck
aligns the original B with its DSConv result using a bounded ordered queue, then
registers the residual saturation/ReLU. The first split stores original A/B
until B2 arrives; the second stores B1 until B2 arrives. Joining consumes every
branch exactly once before the five-stage `u_fuse` pointwise block.
Little-endian concatenation is `{B2,B1,B,A}`.

The implemented compute leaves are `swage_s5_window`, `swage_s5_dsconv`,
`swage_s5_pointwise`, `swage_s5_bottleneck`, `swage_s5_c2f`,
`swage_s5_pyramid`, `swage_s5_head` and `swage_s5_topk`. Helper leaves are
`swage_s5_quantize`, `swage_s5_queue` and `swage_s5_fork`.
The top and ROI owner connect these blocks as follows:

```text
u_isp -> u_fork -> u_stem -> u_c2f -> u_pyramid -> u_head -> u_topk
            |                            |                    |
       public pixels              levels 0/1/2         final ranking
            |                                                 |
       raw stripe ------------------------------------> u_roi_tokens
                                                         u_roi_normalizer
```

The fork has independent acknowledgement flags, not an eager upstream retirement:
ISP ready asserts only when both enabled branches have acknowledged the current
beat. Disabled S5 is the ordinary inherited pixel ready/valid path.

Pyramid forks F0 to the head arbiter and its F1 convolution, then retained F1 to
the arbiter and its F2 convolution. Independent acknowledgement flags prevent
repeating a beat when one branch consumes earlier. Decimation uses even local
coordinates; level dimensions and marker comparisons use rounded-up half sizes.
The round-robin arbiter locks its selected producer until a stalled beat transfers.

The eight-stage head has parallel signed40 class and regression projections.
Three registered reduction stages follow products, then bias, exact RNE,
class tournament/distance clamps, and registered clipped-box assembly.
Classification tournament ties choose the earlier class. Padded inactive
classes cannot defeat a legal class because zero-score ties prefer class0.

Top-K keeps unsorted physical slots, with a permanent slot-to-ROI-bank mapping.
Insertion scans one slot per cycle for exactly MAX_TOPK cycles and commits
once. A free runtime-legal slot takes precedence; otherwise only a candidate
better than the worst replaces it. Descriptor output scans the bounded slots
for the best un-emitted tuple before each transfer. Both scans have fixed lengths
independent of scores. This avoids a slot-wide combinational insertion chain
and avoids moving ROI data when rank order changes.

## Storage bounds and ownership

Per spatial convolution row storage is `2*CIN*MAX_WIDTH` bytes; horizontal
state is `6*CIN` bytes. Stem/bottlenecks/pyramid have CIN3/3/3/6/6, hence at
most `42*S5_MAX_WIDTH` row bytes in total before reduced-width opportunities.
Row banks are separate unpacked byte arrays with synchronous accesses.
C2f bypass queues are fixed-depth bounded feature storage, not image-height RAM.

The ROI integration retains one raw RGB/mask stripe of at most
`patch_size*S5_MAX_WIDTH` pixels and at most `(MAX_TOPK+1)*patch_size^2`
retained patches. A stripe cannot be reused until all causal feature work and
candidate updates have drained and needed snapshots have completed. Candidate
replacement invalidates ROI ownership at the same commit edge. Output tokenization reads
retained patches only and repeats patches in descriptor-rank order when needed.

Raw stripe and retained-patch storage use separate unpacked byte lanes and
mask arrays, with synchronous reset-gated accesses. No RAM-content reset loops
or external readback interface are present. Physical RAM is sized for patch16;
valid ownership uses only patch_size*image_width stripe entries and
K*patch_size^2 retained entries. Each retained bank uses a fixed256-location
address range; unused tail entries for patch8/14 are not owned or read.
The contract permits K+1 patches; this implementation needs only K.

At maximum width4096 the five convolution windows hold42*4096=172032 raw
feature bytes, plus bounded horizontal/pipeline state. Maximum stripe storage is
65536 pixels (196608 RGB bytes and196608 mask bits). Maximum K16 retained
storage is4096 pixels (12288 RGB bytes and12288 mask bits). These are separate
from the unchanged S4 work/history store, and neither S5 allocation depends on
image height. C2f bypass queues total336 payload bytes; the candidate FIFO is
118*S5_FIFO_DEPTH bits plus bounded pointers/control. Weight/bias/shift registers
are bounded by the fixed channel graph, not frame dimensions. Array padding
for common weight banks is unused where the layer's declared count is smaller.
These are architectural capacities, not measured Quartus resource counts.

## Atomic control and stripe schedule

`u_isp` is the unchanged S4 instance, not a substitute ISP. The new top repeats
the parent configuration legality predicates to commit its own registers on the
same accepted edge. S5 illegality is forwarded as reserved input format3 to
`u_isp`, causing its existing rejection path to leave all inherited state
unchanged. The top likewise leaves S5 state unchanged. The complete rejection
and arithmetic translation is in SPEC; the unchanged parent is supplementary
provenance, not the independent numerical oracle.

Configuration acceptance suppresses input and weights on that edge. Weight
acceptance suppresses a new input on its edge; invalid acknowledged writes
pulse weight_error without modifying any bank. Config valid has priority and
holds weight_ready low. Pending configuration or weight requests do not suppress
ongoing source transfers when the request cannot yet be acknowledged: otherwise
an outstanding request could deadlock frame drain. Weights cannot change while
the inherited ISP or any S5 frame work is active.

The first source transfer starts S5, clears Top-K occupancy and ROI validity,
and starts the patch-grid-width subtract/count sequencer. A source-beat counter
closes input after the complete SRC raster; it prevents the next parent frame
from starting while S5 delivery is outstanding. Output coordinates advance
only when the fork's S5 branch accepts, independently of public pixel retirement.
At each patch-row boundary, S5 pauses branch capture and drains the complete
stem/C2f/pyramid/head/candidate-FIFO/Top-K insertion graph. Causal windows retain
their two prior rows across this pause.

| Top state | Work | Exit |
| --- | --- | --- |
| RUN | Capture stripe; advance streaming inference | Final row of stripe accepted |
| DRAIN | No new branch input; complete pending inference/insertion | All compute and candidate storage idle |
| SNAPSHOT | Scan exactly K occupied physical slots | Current-stripe slot launches copy, or scan ends |
| COPY_WAIT | Sequential column alignment and synchronous raw patch copy | copy_done; resume scan |
| FINISH | All final stripe snapshots complete | Top-K finish handshake |
| DELIVERY | Ranked descriptors and associated normalized ROI patches | Descriptor completion, ROI idle, parent cfg_ready, fork idle |

Snapshot slots anchored in earlier stripes retain their existing bank data.
Slots anchored in this stripe are copied after all candidate decisions settle;
the stripe remains untouched until every such patch is complete. Insertion
replacement asserts replace_valid/replace_slot and clears the corresponding
bank_valid on the same edge as descriptor metadata changes. Thus ownership is
pending, never falsely valid with old pixels. Final delivery cannot begin until
every winning slot's pending ownership has been committed by snapshot copying.
No stale losing bank is emitted, and sorting never changes physical association.

Column alignment subtracts patch_size until the anchor remainder is smaller;
the quotient is grid_x and the accumulated origin is patch-aligned x.
The stripe already owns an aligned patch row; its sequential grid_y provides
patch y. The registered grid-row product plus grid_x gives raster patch ID.
Copy uses explicit column/row/element wrap and two clocks per pixel: read then
write. Outside-image positions skip stripe reads and store zero/keep000.
Inside-image positions store the original RGB bytes and masks.

A ranked descriptor transfers only when its public sink and, if required, the
ROI command interface are ready. That same edge starts the slot's tokenization.
The token owner synchronously reads retained pixels, sends them through the
separate eight-stage u_roi_normalizer, then waits for the normalized patch end
to transfer. Rank/frame sidebands remain held for the entire patch. The next
descriptor/ROI command waits for this completion, so rank-ordered repeated
patches need no extra descriptor queue or frame replay. Empty completion does
not issue an ROI command. Frame ID advances only at full DELIVERY drain.

## Pipeline, boundary and performance accounting

The pre-edge/post-edge and degenerate cases are frozen in SPEC. Window latency
is2 advances; each DSConv13; each residual merge1; fusion5; head8;
normalizer8. The C2f critical transformed path is14+14+5=33 advances,
with bypass queues matched by accepted-beat order. The stem-to-F0-head path
is13+33+8=54 advances; F1/F2 add13/26 convolution advances respectively.
Arbitration, decimation, queue acceptance, Top-K insertion and stripe snapshots
add scheduling advances; these numbers are not a fixed top output latency.

| Boundary | Implementation rule |
| --- | --- |
| Width/height1 | Window uses old row word on synchronous read/write; coincident first/last accepted once |
| Odd pyramid edges | Ceil half sizes; even local coordinates retained with regenerated level markers |
| FIFO depth1/3/16 | Explicit terminal wrap, simultaneous enqueue/dequeue preserves occupancy |
| Patch8/14/16 | Counter wrap, no modulo; edge padding still emits a complete patch |
| Independent stalls | Fork acknowledgement flags and elastic enables prevent duplicate/drop |
| Mid-frame reset | Async cancellation of control/pipelines, invalidated RAM ownership, frame ID0 |
| Invalid cfg/write | Atomic rejection or acknowledged error, no partial state update |

There are86016 spatial anchors at256x256 across strides1/2/4.
With every anchor eligible and K16, the bounded insertion schedule is18 clocks
per candidate acceptance interval. Each stripe snapshots at most K patches,
each with two clocks per raw pixel plus alignment/command overhead; final ROI
delivery reads at most16*256 pixels and drains normalization. These operation
counts motivate the admitted3200000-cycle workload bound, but do not establish
the actual coupled schedule's cycle count. The Verifier must measure input to
final output with the exact eight-class/topk16/patch16/ROI-ready configuration.
No frame II=1, STA margin, detector accuracy or FPGA PPA is claimed from RTL.

## Reproduction and pending evidence

From the delivered root, the independent Verifier's aggregate command is:

```sh
python3 tests/verify.py
```

That entrypoint is not authored by the Designer. Tool dependencies are Python3,
Icarus/vvp, Verilator, C++ compiler/make and Yosys; current measured tool versions
and all execution results must come from independent retained receipts.
Native runtime timing lint, Quartus17.1 Arria10 setup/hold STA and hardware HIL
are not duplicated in portable L1.

Current S5 cycle count, PPA, timing and functional evidence are pending.
Composite latency is the pipeline advance schedules plus candidate insertion,
stripe drain/copy and retained ROI delivery; it is not a constant frame latency.
Recipient startup is active-low reset, release on the common clock, configure
with cfg_valid/cfg_ready (check cfg_error), program signed-extended weights
between frames (check weight_error), then stream the complete declared source
raster with SOF/EOL. Keep all desired output sinks ready or honor their stalls.
cfg_s5_topk must be1..S5_MAX_TOPK even in a disabled configuration. Detection
requires RGB8; ROI additionally requires patch8/14/16 and nonzero reciprocal
lanes. When ROI is disabled the S5 stripe scheduler uses16 rows; the inherited
patch setting is unaffected. All interface widths/defaults are in SPEC.

The source/evidence mapping is intentionally prospective until independent
execution: leaf/window/C2f/pyramid oracles establish numerical connections;
matrix descriptor/ROI comparisons establish ranking, replacement and association;
parent mathematical tests plus supplementary reference comparisons establish
preservation; mutation receipts establish checker sensitivity; synthesized
netlists establish state/connections/no-latches; native lint/STA/HIL establish
their own distinct target-level claims. Preparation syntax evidence, if listed
below, establishes elaboration only. The independent product PDF is authored
after engineering acceptance, never used as a prerequisite for these checks.
