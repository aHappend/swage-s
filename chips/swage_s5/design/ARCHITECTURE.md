# SWAGE-S5 architecture

## Implementation boundary

The design is under construction. This document distinguishes implemented
source from the remaining integration; it claims no simulation, synthesis,
timing or hardware acceptance. The admitted contract is `design/SPEC.md`.

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
They have not yet been statically elaborated or functionally verified.
The top/ROI integration shown below remains to be implemented:

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

The remaining ROI integration must retain one raw RGB/mask stripe of at most
`patch_size*S5_MAX_WIDTH` pixels and at most `(MAX_TOPK+1)*patch_size^2`
retained patches. A stripe cannot be reused until all causal feature work and
candidate updates have drained and needed snapshots have completed. Candidate
replacement must replace ROI ownership atomically. Output tokenization reads
retained patches only and repeats patches in descriptor-rank order when needed.

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
Integration, private orchestration port completion and inherited numerical
documentation remain to be completed before a ready design handoff.

## Next design increment

Implement `swage_s5_roi_tokens.sv` and `swage_s5_top.sv`, first freezing the
remaining ROI bit-level command interface. Wire atomic inherited/S5 configuration,
weight decoding/reset/rejection, stripe write/drain/snapshot ownership, ranked
descriptor-to-ROI association and full inherited/S5 frame-drain blocking.
Complete the inherited arithmetic/configuration translation from the reference
sources. Then perform at most a fast static syntax/elaboration check with
retained project-local receipt and hand off to the independent Verifier.
No functional simulation, synthesis, preparation harness or hardware experiment
has been executed by this design increment.
