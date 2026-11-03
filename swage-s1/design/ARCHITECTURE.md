# SWAGE-S1 architecture

## Module interfaces (frozen before RTL implementation)

All control and data use the rising edge of `clk`. Control registers use
active-low asynchronous `rst_n`. The direct children of `swage_s1_top` are:

| Instance / module | Inputs | Outputs / responsibility |
| --- | --- | --- |
| `u_phase` / `swage_s1_phase` | `frame_start`, `row_step`, `pixel_step` | 13-bit clamped `x0,x1,y0,y1`, F-bit `fx,fy`; accumulated origin-aligned coordinates |
| `u_scheduler` / `swage_s1_scheduler` | coordinates, source valid, pipeline advance/empty | source ready, bank write enable/address/data ownership, bank selects, issue valid/SOF/EOL, phase step controls |
| `u_line_buffer` / `swage_s1_line_buffer` | bank write, two read addresses, read enable, bank selects | synchronous stage-1 `p00,p01,p10,p11`; two logical source-row banks |
| `u_interp` / `swage_s1_interp` | issue tags/fractions, synchronous samples, output ready | five arithmetic/output stages after SRAM stage 1; pipeline advance/empty, public stream |

`DATA_WIDTH` is 8, 16 or 24; channel widths are 8 or 10. Internal coordinates,
source row counts and raster counters are 13 bits, including the terminal 4096
source-row count. No coordinate output is a function of returned SRAM data.
The top and host bridge derive `DATA_WIDTH` as a module-body `localparam`,
not an overridable parameter. This avoids Quartus 17.1's unsupported
parameter-port-list `localparam` syntax while preserving packed port widths.
Unsupported format tuples retain a safe 8-bit width so the top's explicit
parameter diagnostic remains reachable.

## Cycle and degenerate-boundary table

All transfers use **pre-edge** valid and ready. All updates below are
**post-edge**. Reset overrides every transfer.

| Event | Result |
| --- | --- |
| Reset asserted | Public data, valid, sidebands and input ready zero; all ownership/valid/phase state cleared; RAM contents unchanged |
| First edge after reset release | Scheduler prepares row acquisition; no old RAM row is valid |
| E0, issue and advance | Synchronous SRAM samples, selected bank tags, valid/SOF/EOL and fractions enter stage 1 |
| E1 | Four horizontal products per channel enter stage 2 |
| E2 | Two horizontal sums per channel enter stage 3 |
| E3 | Two vertical products per channel enter stage 4 |
| E4 | Carry-save constant rounding and one carry-propagating sum enter stage 5 |
| E5 | Stage-5 rounded pixels and tags enter public stage 6 |
| E6 | Earliest transfer of the E0 issue if ready is high |
| Public valid and not ready | All six stages and the issue coordinate hold |
| Public transfer and incoming stage 5 | Old output accepted and new output installed at the same edge |
| End of issued row | No more issues until all six stages drain; only then change bank ownership and advance vertical phase |
| Width 1 | Address remains zero; SOF and EOL may coincide; no binary-wrap assumption |
| Height 1 / bottom clamp | Both required rows select the same valid bank |
| Non-power-of-two width | Explicit comparison to `SRC_WIDTH-1` and `DST_WIDTH-1`; no address masking |
| Concurrent bank read/write | Not scheduled: acquisition and output issue are distinct states; SRAM port A gives writes priority defensively |
| Reset during fill/compute/stall | Partial frame abandoned; next frame begins with invalid banks and zero phases |
| Output finishes before source | Consume remaining source rows without storing; do not accept the next frame early |

External frame latency also includes acquisition and scheduling. Six stages is
an issue-to-output advancing-edge contract, not a source-transfer latency.

## Ownership and scheduling

The scheduler maintains two actual row tags and validity bits, a source raster
cursor, a destination raster cursor, and six states:
`PREPARE`, `FILL`, `ISSUE`, `DRAIN`, `FINISH`, `RESTART`.

In PREPARE, resolve both required rows against bank tags. If missing, consume
the next source row. Store it only when it is the first missing required row;
otherwise discard it. A stored row uses an invalid bank or a bank whose tag is
neither required row. Invalidate that bank before acquisition and mark it valid
only after the final source pixel transfers. ISSUE holds bank association fixed
and issues one pixel per enabled clock. DRAIN waits for every pipeline valid bit
to clear before PREPARE can reclaim storage. Repeated destination rows reuse
valid tags without consuming unnecessary source rows. FINISH drains all remaining
input; RESTART starts a new ownership epoch.

The two stores each contain exactly `SRC_WIDTH * DATA_WIDTH` bits. Port A
writes during fill or synchronously reads x0 during issue; port B synchronously
reads x1 during issue. Reads never overlap fill. No SRAM reset is inferred.
There is no replicated row, frame store, or third row bank. Samples and arithmetic
registers are bounded pipeline storage. RAM inference attributes request M20K
embedded storage; actual inference and fit require independent Quartus evidence.

## Numerical widths and timing structure

`S=2^F`. A 64-iteration elaboration-only restoring divider computes constant
steps without division/remainder operators. Phase storage is `F+13` bits:
the largest terminal phase is `DST_DIM * floor(SRC_DIM*S/DST_DIM) <= 4096*S`,
which needs F+13 bits. Thus F=8 uses 21 bits (not a wrapping 16-bit Q8.8
word), and F=16 uses 29 bits. The selected precision names fractional precision,
not a restriction of the integer coordinate to eight or sixteen bits.

For B-bit channels, horizontal products and sums have B+F+1 bits; vertical
products and the rounded sum have B+2F+3 bits. These include conservative guards.
The exact horizontal sum is at most `(2^B-1)*S`; the unrounded vertical sum
is at most `(2^B-1)*S*S`. The rounding constant is `2^(2F-1)`, so the
shifted result never exceeds `2^B-1`. There is no saturation or intermediate
rounding.

Stages 2 and 4 contain only parallel multipliers; stage 3 contains one adder
per row/channel. Stage 5 compresses the two products plus constant rounding
using bitwise carry-save logic, then one full-width carry-propagating adder.
This avoids a serial pair of wide adders. Stage 6 only packs channel results.
Nearest-neighbor chooses rounded, clamped coordinates in the phase module,
zeros weights, and traverses exactly the same six stages.

## FPGA integration

`fpga/swage_s1_pcie_top.sv` wraps the unchanged scaler with an Avalon-MM
register/stream bridge and an externally generated Arria 10 PCIe subsystem.
The subsystem provides a 100 MHz application clock, synchronized active-low
application reset and a 32-bit word-addressed Avalon-MM master. PCIe BAR access
transports packed pixels plus SOF/EOL; output reads acknowledge transfers.
The bridge's explicit output hold control provides host-driven backpressure.
No software scaling or hardware loopback is used.

`fpga/create_project.tcl` creates fresh Quartus projects for either the scaler
STA top or the PCIe top. `fpga/pcie_subsystem_hw.tcl` specifies the generated
subsystem's required interface and validates an installed board-specific Qsys
system instead of guessing pin assignments, reset release, or licensed IP
interfaces. See `fpga/README.md` for the precise external integration contract.
This boundary is an outstanding physical integration prerequisite, not a
claim that a PCIe image has been built or tested.

## Evidence ownership

Independent `tests/verify.py` owns behavior, oracle comparisons, mutations,
synthesis, lint, STA, hardware and clean reproduction. None was executed by
Designer. Source existence, RAM attributes and target constraints are not
evidence of functional correctness, timing closure, or a functioning board.
