# SWAGE-S2 architecture and integration

## Module and ownership map

```
swage_s2_top
  u_demosaic : swage_s2_demosaic
    u_window : swage_s2_window (8-bit raw rows)
  u_convolution : swage_s2_convolution
    u_window : swage_s2_window (packed mono or RGB rows)
  u_scaler : swage_s1_top
    u_phase
    u_scheduler
    u_line_buffer
    u_interp
```

The five `swage_s1_*` files in `rtl/` are byte-identical copies of the supplied
`swage_s1_reference/rtl/` files. They are reusable standalone without the S2
wrapper. No numerical fixup is inserted before or after their scaling arithmetic.
The missing upstream `design/GENERATIONAL_HANDOFF.md` prevents any authentic
parent STA/PPA comparison. Fresh S2 physical measurements remain required.

The wrapper owns configuration and complete-frame admission. Each spatial window
owns its source coordinates, row tags, memory addresses and scan/output position.
Arithmetic stages own their registered valid/data/marker pipelines. The unchanged
scaler owns scaling coordinates, its two-row storage and interpolation pipeline.
There is a single rising-edge clock and asynchronous active-low reset; no CDC,
derived clock, host register bus or external frame memory is introduced.

## Boundary guarantees

| Producer/consumer | Producer guarantee | Consumer/owner guarantee |
|---|---|---|
| Caller/top | Stable source word and markers while valid is stalled; complete row-major frame | Admit one frame at a time; honor configuration priority; count only handshakes |
| Top/spatial windows | Mode, border and coefficients remain constant for the complete active frame; bypassed paths receive no valid beats | Consume source words once; emit exactly one processed word per source coordinate |
| Window/arithmetic | Complete row-major tap bundle, coordinate and markers remain stable while stalled | Common enabled-edge advance freezes all arithmetic and marker stages together |
| Spatial chain/scaler | Full packed source-format words with source-sized SOF/EOL | Preserve the supplied quantized mapping and all parent format/interpolation behavior |
| Scaler/caller | Stable output data and markers while valid is stalled | Caller controls output ready; top retires a frame only after both source and output counts complete |
| Reset/all modules | Reset assertion may interrupt any phase | Validity clears, public outputs are zero and stale RAM cannot become valid without fresh ownership |

These are functional contracts, not evidence that the implementation passes.
`SPEC.md` contains the exact cycle/degenerate-boundary table and numerical rules.

## Frame-atomic routing

The wrapper stores mode, border, absolute, shift and nine packed coefficients.
It decodes mode bit 1 as demosaic enable and bit 0 as convolution enable. Ready
propagates backward through only the selected processing chain. Every forward
valid on a disabled spatial input is zero and every disabled output ready is
zero, so a bypassed stage cannot consume source traffic.

The routing is acyclic: caller -> optional demosaic -> optional convolution ->
scaler -> destination. Each registered pipeline terminates its forward data path.
There is no combinational path from an input valid to an output ready that
feeds back through forward data selection.

`frame_active` rises on the first public source transfer. Three 25-bit counters
track public source, public destination and spatial-to-scaler handshakes
independently, including simultaneous last transfers. Each can represent the
maximum complete-frame count 4096*4096.
Input is blocked after the source count reaches its limit, even if the caller
already presents the next frame. Output and remaining source progress continue
when `cfg_valid` is held during a busy frame.

While idle, any `cfg_valid` blocks first-pixel admission and `cfg_ready` is high.
The edge consumes a legal configuration or pulses the illegal-mode error without
changing configuration. Error defaults low each subsequent edge; consecutive
illegal transactions can produce consecutive error cycles. Frame completion
clears the counts and enables configuration in the following cycle. Completion
also requires all source-sized processed beats to have transferred into the
scaler. Downscaling may finish its public output before the spatial tail has
drained; this third counter prevents a premature configuration change or
next-frame admission from corrupting that tail.

The scaler may still perform its internal FINISH/RESTART bookkeeping after the
last public transfer. Its own ready naturally holds off a new scale-only frame;
new spatial stages can fill their owned rows while waiting for scaler readiness.
No top-level assumption about the number of these bookkeeping cycles is needed.

## Three-line circular window scheduler

Each `swage_s2_window` has three independent synchronous single-read row banks:
`bank0`, `bank1`, `bank2`, each `SRC_WIDTH` words deep. A physical write-bank
counter explicitly rotates 0 -> 1 -> 2 -> 0. Logical tags and valid bits, not
physical bank parity, select top, middle and bottom rows.

| State | Ownership/action | Exit |
|---|---|---|
| PREPARE | Determine clamped required top/bottom row tags. If the next source row is needed, invalidate its reclaimable write bank. Otherwise latch the three read-bank selections once all tags hit. | FILL or SCAN |
| FILL | Accept one real row in ascending x; no reads. Tag and validate the bank only when the last word transfers. Advance real source row and rotate write bank. | PREPARE |
| SCAN | Issue logical columns -1 through width, encoded as `scan_column` 0 through width+1. Read each bank once per enabled step with address clamped to 0 through width-1. | DRAIN after final request |
| DRAIN | No new reads/writes. Drain the pending synchronous read, horizontal window and its consumer transfer. | Increment center row and PREPARE, or clear row ownership for a fresh frame |

Initially center row zero loads real rows zero and one if available. Thereafter
center y is scanned only with all real rows needed for y-1,y,y+1 resident.
After y has completely drained, loading y+2 reclaims y-1, which cannot be
needed for the next center y+1. The bottom border requires no extra source row.
Each real source row is accepted once, in order. There is no frame-sized image
storage and no RAM with three independent column reads.

At scan request edge n, bank sample registers and padding/column/y metadata
update together. At n+1, the selected vertical samples shift through two
horizontal history registers per row and assemble the three-column window.
The first two requests prime the histories; requests with encoded column >=2
emit x=encoded-column-2. Tap zero (upper left) is the low slice of `w_data`;
tap eight (lower right) is the high slice.

Image-edge replication reads the nearest valid address/tag. Zero padding masks
an external column or row after synchronous sampling and before horizontal
history insertion. Its metadata follows the exact same enabled edges as RAM
results. Replicated top/center/bottom may select the same physical sample;
unwritten banks are never selected for a valid real row.

All RAM writes and registered reads live in a clock-only process, without
asynchronous memory reset. Reset clears ownership and pipeline-valid state,
not pixels. Read/write phases are disjoint, avoiding technology-dependent
read-during-write behavior. RAM attributes request block memory and prevent
unnecessary front-end register expansion.

### Degenerate schedules

For width one, the three requests -1,0,1 produce one output with x=0 and EOL.
Height one fills only row zero and selects its tag for all replicated rows.
For zero borders the top/bottom masking is independent, so only center samples
survive in the center row. Height two retains rows zero and one for both center
rows. At the maximum width, encoded column width+1 is 4097, within 13 bits;
the largest actual RAM address is 4095. The next-source-row counter can hold
4096. Explicit comparisons handle every non-power-of-two geometry.

## Stall and bubble discipline

Each arithmetic pipeline uses a common enable:
`rst_n && (!last_valid || downstream_ready)`. All its stage valid/marker/control
registers advance on that enable, while arithmetic updates are additionally
qualified by the corresponding incoming valid. Invalid output slots become
zero; a stalled valid output remains unchanged.

The window uses the same one-slot output principle, independently of the
arithmetic pipeline. A blocked window freezes scan position, synchronous RAM
reads, request metadata, horizontal histories, output bundle and coordinates.
It may accept one buffered window while arithmetic ready is low only if its
own output slot was empty. FILL accepts source beats only while ready is high.
DRAIN waits for both pending read validity and window validity to clear before
releasing a row. This permits bounded buffering without losing an outstanding
read response under a stall.

Pipeline bubbles are explicit valid zeros, not repeated pixels. Pipeline
latencies count enabled edges, so a wall-clock stall increases observed latency
without changing the fixed leaf-stage counts.

## Demosaic datapath

The raw window is eight bits wide regardless of the 24-bit top-level RGB stream.
The wrapper deliberately selects only the input low byte in demosaic modes.
Window taps 4,5,7,8 provide the forward-2x2 q00,q01,q10,q11. Logical x/y parity
selects red, blue and one of the two green pairs. The first arithmetic stage
registers red/blue and a nine-bit green sum; the second adds one, selects
bits 8:1 and packs B,G,R. SOF/EOL follow a two-bit valid-aligned pipeline.
The phase therefore restarts with each window frame rather than tracking
clamped border addresses.

## Convolution datapath

For each channel, nine 16-bit signed coefficients multiply nine explicitly
positive signed nine-bit samples. Each exact 25-bit signed product is sign
extended to a 32-bit product register. A balanced registered tree reduces
9 -> 5 -> 3 -> 2 -> 1 in four stages. No serial nine-add combinational chain
is used.

The signed sum is converted to 33-bit unsigned magnitude with a separate
negative flag. The next register adds the shift-dependent half-unit bias,
the next registers the logical right shift, and the final stage clamps using
the aligned sign and absolute control. Negative non-absolute results become
zero; all other magnitudes above 255 become 255. This is equivalent to rounding
away-from-zero ties, optional absolute magnitude, then unsigned saturation.
Zero padding occupies unused output bits for the internal 16-bit mono-width
instance, which is not selectable in legal ten-bit operation.

Shift is captured alongside the product stage and delayed through `shift0`
through `shift6` to the rounding and shift registers. Absolute control follows
eight registers to the saturation stage. Sign follows magnitude through its
last three arithmetic registers. Data and SOF/EOL arrive after nine enabled
edges including the accepting product edge, exactly as specified. Border and
kernel fields must remain stable for the frame, as enforced by the wrapper.

## Capacity, traffic and performance limits

For packed input width D, a convolution window stores 3*SRC_WIDTH*D pixel bits.
The raw demosaic window stores 3*SRC_WIDTH*8 bits. Both physical instances exist
in the hierarchy, subject to normal synthesis optimization for unreachable
formats. The parent's two-row store is unchanged. No stage allocates storage
proportional to source height.

Per spatial frame, each enabled window accepts SRC_WIDTH*SRC_HEIGHT real beats
and emits the same number of windows, with SRC_HEIGHT*(SRC_WIDTH+2) enabled
scan requests plus preparation, row-fill and drain cycles. Filling and scanning
are intentionally serialized within a window to keep memory ownership explicit.
Arithmetic can sustain one window per enabled cycle after priming, but no
one-public-pixel-per-clock throughput is promised. Cascaded stages overlap
within their finite row/pipeline capacities and propagate stalls normally.
These are structural counts, not measured bandwidth or PPA.

## Target implementation and verification handoff

Target: Intel Arria 10 GX `10AX115N2F45E1SG`, one synchronous clock with period
<=10.0 ns. Multipliers, reduction levels, magnitude, rounding, shift and clamp
have explicit register boundaries. Bank rotation and address wrap use compare/
increment logic; there are no combinational division/modulo operators. The parent
constant quotient function is elaboration-time only. Sequential state uses
nonblocking assignments and memories have synchronous registered reads.

Required sources are the nine `rtl/*.sv` modules. From the project root the
independent verification entry point is `python3 tests/verify.py`; its documented
clean reproduction subcommand is `python3 tests/verify.py --compact-reproduce`.
See `SPEC.md` for exact tool requirements, frozen matrix/coverage, retention,
parameter legality and claim-to-evidence mapping. Verification sources and
evidence are authored independently after this design handoff.

No functional simulation, synthesis, timing, physical HIL, independent delivery
approval or product PDF is implied by these implementation details. The shared
runtime must execute all admitted checks; generic synthesis cannot certify
100 MHz operation. The missing parent handoff does not waive any fresh S2 gate.
