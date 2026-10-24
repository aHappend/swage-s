# SWAGE-S1 specification

## Product and configuration

Streaming bilinear / nearest-neighbor 2D scaler. Top: `swage_s1_top`.
Parameters are elaboration-time constants for the lifetime of the instance.

| Parameter | Default | Legal values |
| --- | --- | --- |
| `SRC_WIDTH`, `SRC_HEIGHT` | 8, 8 | 1 through 4096 each |
| `DST_WIDTH`, `DST_HEIGHT` | 16, 16 | 1 through 4096 each |
| `PIXEL_BITS`, `CHANNELS` | 8, 1 | (8,1), (10,1), (8,3) only |
| `PHASE_FRAC` | 8 | 8 or 16 |
| `INTERPOLATION` | 0 | 0 bilinear, 1 nearest-neighbor |

Illegal values issue `SWAGE_S1 PARAMETER ERROR`. Safe internal elaboration
values allow the explicit diagnostic to run without illegal declarations.
These safe values do not legalize an invalid instance.

## Stream interface

Inputs: `clk`, active-low asynchronous `rst_n`, `s_axis_tdata`,
`s_axis_tvalid`, `s_axis_tuser`, `s_axis_tlast`, `m_axis_tready`.
Outputs: `s_axis_tready`, `m_axis_tdata`, `m_axis_tvalid`,
`m_axis_tuser`, `m_axis_tlast`.

Data is unsigned grayscale8 in 8 bits; grayscale10 in 16 bits with bits
15:10 zero; RGB24 in 24 bits with R=7:0, G=15:8, B=23:16.
Input grayscale10 padding must be zero; output padding is always zero.
SOF (`TUSER`) is asserted on only the first pixel; EOL (`TLAST`) on every
row's final pixel. Input must be correctly framed, raster ordered, and
noninterleaved. There is no error/status extension or TKEEP.

A transfer occurs only when ready and valid are high before the rising edge.
Sources hold data and sidebands while stalled; the DUT does likewise on output.
All public outputs, including input ready, are zero during reset. Reset aborts
the current frame; the source must restart with the first pixel of a new frame.
RAM need not be cleared because only newly completed tagged rows can be read.
The source may send consecutive frames; the DUT backpressures acquisition as
needed and consumes all unused source pixels before beginning the next frame.

## Numerical rules

Let `F=PHASE_FRAC`, `S=2^F`, `step_x=floor(SRC_WIDTH*S/DST_WIDTH)` and
`step_y=floor(SRC_HEIGHT*S/DST_HEIGHT)`. Coordinates at destination (x,y)
are `(x*step_x/S,y*step_y/S)`, with no half-pixel or align-corners offset.
Horizontal phase restarts each row, vertical phase each frame. Integer carry
is preserved through the full 4K range for both precisions.

Extract integer coordinates and fractional integers `fx,fy` before clamping.
Clamp the integer and its +1 neighbor independently to the last source pixel.
For each channel:

```
H0 = p00*(S-fx) + p01*fx
H1 = p10*(S-fx) + p11*fx
result = (H0*(S-fy) + H1*fy + 2^(2F-1)) >> (2F)
```

All intermediates are unsigned and full precision. There is no intermediate
rounding; final ties round upward. Nearest-neighbor instead selects
`floor(quantized_coordinate+0.5)`, ties upward, then clamps. It uses the same
quantized accumulated phases. Identity scaling is exact for both algorithms.
RTL contains no combinational division or remainder; steps are elaboration-only
bounded shift/subtract division.

## Timing and memory

Single synchronous clock, target 100 MHz (10.000 ns) on Intel Arria 10 GX
`10AX115N2F45E1SG`. No timing waiver is claimed. Two source-row SRAM banks
only, each with two synchronous pixel reads. Six advancing registered stages:
samples, horizontal products, horizontal sums, vertical products, rounded sum,
public output. Output stall freezes all stages. Within an acquired output row,
one coordinate is issued per advancing clock. Acquisition/draining introduces
inter-row gaps; there is no uninterrupted frame-throughput promise.

See `design/ARCHITECTURE.md` for exact pre-edge/post-edge timing, full-width
bounds, degenerate cases, direct child interfaces and bank ownership.

## Independent acceptance and reproduction

From the delivered project root, with the independent verifier present:

```
python3 tests/verify.py --check behavior
python3 tests/verify.py --check defect-sensitivity
python3 tests/verify.py --check synthesis
python3 tests/verify.py --check rtl-timing-lint
python3 tests/verify.py --check quartus-sta
python3 tests/verify.py --check board-hil
python3 tests/verify.py --check portable-reproduction
```

All seven checks remain mandatory. Behavior requires both Icarus/vvp and
Verilator, independent integer and floating references, the full admitted
format/precision/mode/geometry/pattern/stall/seed Cartesian matrix, additional
height extremes, legal/illegal elaboration, back-to-back frames, reset phases,
sideband counts, stall stability and exact stage latency. Each separate
horizontal-phase, vertical-weight and bank-association mutation must compile
and produce an oracle mismatch, not merely fail to build.

Synthesis requires real Yosys hierarchy/storage/latch/loop/undriven/divider
checks for default and 4096-wide designs. Structural timing lint uses the
installed shared-runtime interface. Physical timing requires Quartus/TimeQuest
post-fit nonnegative setup/hold slack, explained constraints and covered
parameter extremes/arithmetic variants. Hardware requires real Arria 10 GX
PCIe Gen3 x8, current-image source/hash identity, three deterministic passes,
all admitted formats/modes/precisions/geometries, and host backpressure.
Missing tools, licenses or hardware are blockers, not passing fallbacks.

Required prerequisites: Python 3, Icarus/vvp, Verilator, make, g++, Yosys,
installed shared-runtime FPGA command interface, licensed Quartus with Arria 10
and TimeQuest support, board-specific PCIe subsystem/pin inputs, authorized
programming access and a compatible physical board/host PCIe interface.
No tool version or board availability has been established by Designer.
The Verifier records actual tool versions and exact receipts in fresh
`evidence/checks/<check-id>-<unique-id>/` directories. Until such receipts exist,
all behavioral, synthesis, timing and hardware claims are **unverified**.
The independently approved product PDF is produced by the shared runtime only
after engineering acceptance and is not a prerequisite for running these checks.
