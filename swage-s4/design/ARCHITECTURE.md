# SWAGE-S4 implementation architecture

## Schedule and ownership

The top retains `u_legacy:swage_s3_top` literally. S4 disabled is a combinational
selection of parent outputs, without a register or modified legacy algorithm.
Atomic legality validation injects a rejected format into the parent only for an
invalid S4 request, preserving its configuration-priority and error pulse.

Enhanced processing serializes frames: capture scaled RGB, optional in-place
tiled CLAHE, complete raster output, then optional normalized patch output.
No stream fork can lose a pixel when one sink stalls. Parent input is inhibited
while the captured frame is owned by processing/readout. Parent histogram remains
on the pre-enhancement stream, including its independent response reservation.

Mandatory functional hierarchy:
`u_temporal`, `u_clahe`, `u_patch_tokenizer`, `u_normalizer`, `u_frame_store`.
The tokenizer requests synchronous frame samples and hands tagged RGB vectors
to the separately instantiated normalizer. Public tokens are normalizer outputs.

## Internal interfaces frozen before implementation

All leaf interfaces use clk/rst_n. Ready/valid transfers are pre-edge.

| Module | Inputs/requests | Outputs/responses | Ownership |
|---|---|---|---|
| swage_s4_frame_store | wr_valid, wr_history, wr_addr[16:0], wr_data[23:0], wr_mask[2:0]; rd_valid/rd_ready, rd_history, rd_addr[16:0] | rsp_valid/rsp_ready, rsp_data[23:0], rsp_mask[2:0] | single outstanding read, two registered read boundaries |
| swage_s4_temporal | s_valid/s_ready, s_data/h_data[23:0], history_valid, temporal_enable, alpha/threshold[8:0] | m_valid/m_ready, m_data[23:0] | five globally stalled advance stages |
| swage_s4_clahe | start, width/height[12:0], clip_limit[8:0]; rd_ready, rsp_valid/data/mask | busy/done; rd_valid/address, rsp_ready; wr_valid/address/data/mask | sole work-store owner in tone phase |
| swage_s4_patch_tokenizer | start, width/height[12:0], patch_size[4:0]; rd_ready,rsp_valid/data; sample_ready | busy/done; rd_valid/address,rsp_ready; sample_valid,data,keep,first,last,frame_last,id,x,y,element | single sample outstanding, explicit patch/local counters |
| swage_s4_normalizer | s_valid/s_ready, s_data/keep/tags; mean/inv_std[71:0], bf16 | m_valid/m_ready, normalized lanes and delayed identical tags | eight globally stalled advance stages |

Work-store masks preserve the accepted legacy channel mask through temporal,
tone and raster output; token real/padded keep follows the separate frozen
111/000 rule. Top source-transfer counting inhibits the next parent frame as
soon as the full source raster is accepted, even if S3 drains before S4 capture.
Top capture reads prior history before temporal input, waits for the temporal
result and writes work; a following edge writes history when enabled. It counts
captured temporal outputs, not valid-only parent cycles. Last history write
completes before declaring the complete frame valid. Work and history accesses
never collide with processing ownership.

## Memory and arithmetic

Work (RGB24 plus mask3) and untoned history (RGB24) are two synchronous RAMs, each
MAX_FRAME_PIXELS words: total 51*MAX_FRAME_PIXELS bits (208896 bits by default,
3342336 bits at legal maximum). Arrays have no asynchronous reset writes and use
M20K/nomem2reg inference annotations. Metadata registers are asynchronously reset.
RAM data becomes observable only through a requested two-boundary response.
Single outstanding request intentionally trades throughput for simple arbitration.

CLAHE reuses one 256-word unsigned9 histogram and one 256-byte LUT. A tile/channel
scan performs synchronous histogram RMW; clipping scans accumulate excess,
then CDF scans compute exact registered numerator and launch the retained S3
32-by13 iterative restoring divider for each LUT entry. N<=256, sum<=256,
255*CDF<=65280. Histogram redistribution uses constant shifts/low bits. Remap
reads RGB, synchronously looks up its selected channel, and writes the complete
RGB word back with only that channel changed. Tile boundaries and addresses use
counters and registered row bases, not division/modulo. Three channels run serially.

Temporal stages capture, abs differences, motion/products, rounded selection,
output. Each channel product uses nine-bit weights and full unsigned precision.
The common stall freezes configuration/valid state together with data.
Normalizer uses signed25 difference and signed50 product, exact sign/magnitude,
registered leading-one reductions and rounding/saturation, packing and output.
Q ties-even is symmetric in sign. BF16 derives directly from the full product
and exponent offset24, keeping ties/sticky precision before mantissa rounding.
Output keep000 masks data to zero even with extreme nonzero means.

## Integration and finite progress

Drive reset low, release, and wait for cfg_ready. Drive complete legacy and S4
configuration together, hold until accepted, observe cfg_error on the next cycle.
Then drive source raster with valid held until ready and correct SOF/EOL/channel
masks. Accept raster output before relying on tokens; token sink may stall.
Do not request a new configuration until all enhanced outputs have drained.
Histogram traffic remains legal according to the parent's reservation interface.

For [0,1] normalization parameters convert to raw-pixel domain as specified in
SPEC.md. Defaults mean127.5/std128; adapter authors must quantize their selected
model's coefficients explicitly. Tokens are HWC sample vectors, not embeddings.

With ready sinks each loop has a finite geometric limit. Capture uses a bounded
history-read/temporal/write sequence per pixel plus inherited processing latency.
CLAHE per tile/channel has clear256, scanN, clip256, CDF256 with 32 divider
iterations per entry plus launch/response states, and remapN. Token phase emits
ceil(W/P)*ceil(H/P)*P*P beats plus synchronous reads of real pixels and the eight
normalizer stages. Verifier derives a generous cycle bound from these operations
and reports observed cycles. These are analytical bounds, not measured throughput.
No one-pixel-per-clock or fixed end-to-end latency is claimed.

## Reproduction, evidence and limitations

From delivered project root: `python3 tests/verify.py`. Dependencies are python3,
iverilog/vvp, verilator and yosys; exact actual versions belong in execution
receipts, not invented here. Designer may perform only static elaboration.
Independent Verifier supplies clean-tree portable smoke through the same entry
and reports source-bound results. Source selectors are rtl/*.sv, tests/*.py,
design/SPEC.md, design/ARCHITECTURE.md and preserved parent RTL for differential
comparison; no output/private/runtime directories belong in those selectors.

| Claim | Required evidence | Present design handoff status |
|---|---|---|
| Legacy preservation | independent numerical matrix plus parent cycle differential | source provenance only |
| Temporal/CLAHE correctness | exact oracle, all masks/seeds/branches and mutations | implementation, acceptance pending |
| Normalized patch transport | all sizes/formats/padding/ties/stalls and mutations | implementation, acceptance pending |
| Synthesizable inferred RAM and no latches | independent real Yosys RGB/mono checks | pending |
| >=100MHz Arria10 | native timing lint and Quartus17.1 source-bound STA | pending |
| Physical deterministic streaming | >=3 consecutive native PCIe Gen3x8 HIL passes | pending |
| Final product PDF | runtime independent review/render after engineering acceptance | pending |

Missing parent handoff/specification/intake index remain explicit; restoration
through the shared runtime is requested. No parent PPA, functional result,
synthesis, timing or physical measurement is fabricated. No product report is
authored by Designer. Applicable diagnostic-width lesson exp-6d5cc92e6f5a86894eaae275
informs safe illegal-parameter handling but is not acceptance evidence.
