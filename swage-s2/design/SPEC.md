# SWAGE-S2 implementation contract

## Status and provenance

This is the admitted implementation contract, not a verification or timing
certificate. The top is `swage_s2_top`. The five files supplied in
`swage_s1_reference/rtl/` remain unchanged and are copied byte-for-byte into
`rtl/`. No repository intake source index or `design/GENERATIONAL_HANDOFF.md`
was supplied. Authentic parent post-synthesis timing and PPA are therefore
unknown; no parent-relative improvement is claimed. A subsequently supplied
authentic handoff must be read without substituting it for fresh S2 checks.

Designer owns `rtl/*.sv` and the design documents. The independent Verifier owns
`tests/*.py`, numerical references and all functional/synthesis harnesses.
Engineering acceptance additionally requires shared-runtime timing lint,
Quartus STA, physical HIL and independent review. The runtime generates and
approves the product PDF only after engineering acceptance.

## Cycle & Degenerate-Boundary Table

All transfers refer to signals immediately **before** a rising edge. Register
updates and the results below occur **after** that edge.

| Pre-edge event or boundary | Post-edge behavior |
|---|---|
| Reset low, including before the first clock | Public ready, valid, data, markers and error are zero; validity/configuration/counters are reset. Memories are not reset. |
| First edge after reset release, no transfer | No pixel is owned and no valid output is invented; default configuration remains selected. |
| Idle `cfg_valid && cfg_ready` | Consume configuration, not a simultaneous first source pixel. Legal fields replace the configuration. Illegal mode/format leaves it unchanged and raises `cfg_error` for one cycle. |
| Source valid and ready | Store exactly that beat and advance source ownership once. First transfer starts a frame. |
| Source valid without ready | No source coordinate, count or memory ownership advances. |
| Output valid and ready | Retire exactly the pre-edge output once. |
| Output valid without ready | Preserve data, SOF and EOL. Freeze the blocked pipeline and its outstanding read metadata coherently. |
| Final source and/or output transfer | Release the frame only when both full source and destination transfer counts have completed and all enabled spatial results have transferred into the scaler. Configuration is available in the following idle cycle. |
| Pending configuration during a frame | Ready stays low; source and output continue independently of `cfg_valid`. |
| Width one | Scan logical columns -1, 0, 1; emit one window. SOF and EOL can coincide. Replication selects column zero; zero padding masks the two external columns. |
| Height one | Fill one real row; all replicated vertical taps select that row. Zero padding masks top and bottom taps. |
| Height two | Fill both real rows before row zero; reuse their tags for row one without loading a third row. |
| Non-power-of-two width/height | Explicit terminal comparisons, never a binary-mask wrap assumption. Every real address remains in 0 through width-1. |
| Circular bank wrap | Physical write bank rotates 0, 1, 2, 0. Only after a complete emitted row and drained read/window registers may a no-longer-needed row be reclaimed. |
| Read and write scheduling | A window bank is filled or scanned, never both at once. No read-during-write RAM semantics are assumed. |
| Reset during fill/output/stall | Abort the whole frame and clear every valid bit. Fresh rows must be filled before any new valid spatial output. |

### Registered stage timing

An enabled edge is an edge on which that pipeline's common advance is true.
Bubbles shift through the valid registers; a blocked valid output freezes all
stages, data, markers and read metadata in that pipeline.

* Scan request accepted at edge n: synchronous bank samples and request metadata
  appear after n. The horizontal window and its coordinates appear after n+1.
  Only a completed three-column window is valid.
* Convolution window transfer at n: nine products after n; reductions 9-to-5,
  5-to-3, 3-to-2 and 2-to-1 after n+1 through n+4; unsigned signed-magnitude
  conversion after n+5; rounding addition after n+6; shift after n+7;
  saturation/optional absolute result and markers after n+8. Nine stages.
* Demosaic window transfer at n: selected R/B and nine-bit green sum after n;
  rounded RGB and markers after n+1. Two stages.
* The unchanged scaler retains six registered issue-to-output stages: issue at n
  reaches output after n+5 when continuously enabled.

There is no fixed public source-to-output latency. Row filling, padding scans,
row drains, scaling, source bubbles and destination stalls all contribute.

## Parameters and public interface

| Parameter | Default | Legal values |
|---|---|---|
| `SRC_WIDTH`, `SRC_HEIGHT` | 8, 8 | Independently 1 through 4096 |
| `DST_WIDTH`, `DST_HEIGHT` | 16, 16 | Independently 1 through 4096 |
| `PIXEL_BITS`, `CHANNELS` | 8, 1 | (8,1), (10,1), (8,3) only |
| `PHASE_FRAC` | 8 | 8 or 16 |
| `INTERPOLATION` | 0 | 0 bilinear; 1 nearest-neighbor |

`DATA_WIDTH` is 8, 16 or 24 for the three legal formats respectively. Unsupported
parameters issue an explicit `SWAGE_S2 PARAMETER ERROR` at time zero. Safe internal
elaboration dimensions do not make an invalid parameterization executable.

Ports are `clk`, asynchronous active-low `rst_n`; input stream
`s_axis_tdata[DATA_WIDTH-1:0]`, `s_axis_tvalid`, `s_axis_tuser` (SOF),
`s_axis_tlast` (EOL), output `s_axis_tready`; output stream
`m_axis_tdata[DATA_WIDTH-1:0]`, `m_axis_tvalid`, `m_axis_tuser`, `m_axis_tlast`,
input `m_axis_tready`; input `cfg_valid`, output `cfg_ready`, `cfg_error`;
inputs `cfg_mode[1:0]`, `cfg_border`, `cfg_abs`, `cfg_shift[4:0]`,
`cfg_coeff[143:0]`. All other controls are one bit.

Pixels are unsigned. RGB packing is R[7:0], G[15:8], B[23:16]. Ten-bit mono uses
input bits 9:0 and zeros output bits 15:10. Input frames are row-major, with SOF
on the first beat and EOL on the last beat of every row. Dimension counters,
not the input markers, determine framing; malformed markers are outside the
legal stimulus contract. Every output frame has exactly `DST_WIDTH*DST_HEIGHT`
beats, one SOF and one EOL per destination row.

## Configuration and composition

| `cfg_mode` | Processing order |
|---|---|
| 0 | Parent scaler |
| 1 | Convolution, parent scaler |
| 2 | Demosaic, parent scaler |
| 3 | Demosaic, convolution, parent scaler |

Ten-bit mono permits mode 0 only. Eight-bit mono permits modes 0 and 1.
Eight-bit RGB permits all modes. Demosaic modes take a single raw Bayer sample
from the low byte of each RGB-width input beat; upper bytes are ignored.
Other RGB modes consume complete RGB words. Each enabled spatial stage preserves
source dimensions, and each applies its own border extension.

Configuration is accepted only while idle. Configuration has priority over the
first source beat, including an illegal configuration transaction. Illegal
mode/format transactions consume the request, preserve all previous fields and
pulse error for exactly the following cycle. Other encoded fields are legal.
The frame is active from its first source transfer until both complete source
and output transfer counts have retired and all enabled spatial results have drained
into the scaler. No next-frame input is admitted before then. The spatial drain
condition prevents an early downscaled output from releasing configuration while
old spatial pixels are still in flight. Controls remain constant throughout that
interval.

Reset selects mode 0, replicate borders, no absolute magnitude, shift zero and
the identity kernel (center coefficient one, all others zero).

## Exact convolution arithmetic

Tap i uses signed two's-complement `cfg_coeff[16*i +:16]`, in row-major order,
upper-left first. This is correlation, without kernel reversal. For every
eight-bit channel independently:

```
A = sum(coeff[i] * tap[i], i=0..8)       # signed 32-bit exact sum
Q = A                                  # shift == 0
Q = sign(A) * ((abs(A) + 2**(shift-1)) >> shift)  # otherwise
Q = abs(Q)                             # only if cfg_abs == 1
pixel = min(255, max(0, Q))
```

Magnitude, rounding and shifting use at least 33 unsigned bits. Ties round away
from zero. There is no intermediate truncation, wrapping, early saturation or
negative arithmetic-right-shift rounding. The largest sum magnitude is
9*32768*255 = 75,202,560, within signed 32 bits. Shift accepts 0 through 31.

| Preset | Row-major coefficients | Shift | Absolute |
|---|---|---|---|
| Identity | 0,0,0; 0,1,0; 0,0,0 | 0 | 0 |
| Gaussian | 1,2,1; 2,4,2; 1,2,1 | 4 | 0 |
| Sobel horizontal | -1,0,1; -2,0,2; -1,0,1 | 0 | 1 |
| Sobel vertical | -1,-2,-1; 0,0,0; 1,2,1 | 0 | 1 |
| Unsharp/sharpen | 0,-1,0; -1,5,-1; 0,-1,0 | 0 | 0 |
| Normalized box | all nine coefficients 7282 | 16 | 0 |

Arbitrary signed kernels remain legal. The independent reference must
exhaustively verify that the box preset equals `floor((sum(taps)+4)/9)` for
every sum 0 through 2295; it must not assume equivalence.

## Border extension and forward-2x2 RGGB reconstruction

Border zero replicates the nearest valid coordinate independently on each axis.
Border one supplies zero if either coordinate lies outside the image.
For demosaic define q00=p(x,y), q01=p(x+1,y), q10=p(x,y+1),
q11=p(x+1,y+1), applying the selected extension. Phase restarts at (0,0) every
frame. Parity is the logical output location, not the clamped sample address.

| x parity, y parity | R | G | B |
|---|---|---|---|
| 0,0 | q00 | (q01+q10+1)>>1 | q11 |
| 1,0 | q01 | (q00+q11+1)>>1 | q10 |
| 0,1 | q10 | (q00+q11+1)>>1 | q01 |
| 1,1 | q11 | (q01+q10+1)>>1 | q00 |

Green addition is nine bits. This is forward-2x2 interpolation, not a
substituted 3x3 Bayer algorithm.

## Parent scaling numerics

For F=`PHASE_FRAC`, steps are floor(SRC_WIDTH*2**F/DST_WIDTH) and
floor(SRC_HEIGHT*2**F/DST_HEIGHT). Phase is output coordinate times its quantized
step. Bilinear uses the integer phase coordinates, fractions in the low F bits,
neighbors clamped at the final row/column and a single final rounding addition
of 2**(2*F-1) before shifting by 2*F. Nearest-neighbor rounds each phase with
2**(F-1), shifts by F and clamps at the last coordinate. Channels are independent.
An independent scaler reference must use these quantized steps, not ideal
rational coordinates or a translation/execution of the parent RTL.

## Storage, hierarchy and implementation constraints

The top directly instantiates `u_scaler` (`swage_s1_top`), `u_demosaic`
(`swage_s2_demosaic`) and `u_convolution` (`swage_s2_convolution`). The scaler
retains `u_phase`, `u_scheduler`, `u_line_buffer`, `u_interp`. Each spatial stage
has a `swage_s2_window` instance named `u_window`; bypassed paths never consume
input beats.

The window parameters are `SRC_WIDTH`, `SRC_HEIGHT`, `DATA_WIDTH`. Its input
uses conventional `s_axis_*` ports and `border`. Outputs are
`w_data[9*DATA_WIDTH-1:0]`, `w_valid`, `w_sof`, `w_eol`,
`w_x[12:0]`, `w_y[12:0]`; downstream supplies `w_ready`. Tap zero occupies the
least-significant slice. Exactly three circular logical row banks, each
`SRC_WIDTH` words deep, use synchronous reads. Valid row tags establish
ownership. Before emitting center y, all real rows needed for y-1,y,y+1 remain
resident. Scan logical columns -1 through SRC_WIDTH, one read per bank per
enabled step, and use horizontal registers to assemble windows. Padding does
not consume source beats. Drain reads/windows before row reclamation.
No frame-sized storage or three independent column reads from a RAM are allowed.

All delivered RTL is synthesizable and targets 100 MHz or faster on Intel
Arria 10 GX `10AX115N2F45E1SG` (period <=10.0 ns). There are no combinational
division/modulo operators or cells, latches, mixed blocking/nonblocking
sequential assignments, or combinational ready loops. Parent elaboration-time
`constant_quotient` remains unchanged. Arithmetic depth is bounded by the
registered stage schedule. Timing is a requirement, not an implementation claim:
native post-synthesis Quartus 17.1 STA must establish WNS >=0 and account for
all functional clock paths. Yosys is not timing evidence. No timing waiver is
pre-authorized. Physical PCIe Gen3x8 bridge HIL requires three consecutive
deterministic passes.

## Required independent verification and reproduction

Required installed tools: Python 3, Icarus (`iverilog`, `vvp`), Verilator, Yosys,
make and a C++ compiler. Tool versions belong in fresh execution receipts, not
assumed version claims here. No local cable, PCIe address or private host path
belongs in the portable L1 entry point.

From the delivered project root:

```
python3 tests/verify.py
python3 tests/verify.py --compact-reproduce
```

The second command is the bounded clean-copy reproduction entry required of
the Verifier implementation. It must compare a complete composed RGB frame,
a parent-compatible scale-only frame and perform a fresh Yosys no-latch check.
The full entry must automatically perform that clean-directory reproduction
without inherited caches, runtime metadata or original-directory imports.
Declared reproduction inputs are `rtl/*.sv`, `tests/*.py`, these design
documents and `swage_s1_reference/rtl/*.sv`. Generated evidence, not the future
PDF, is output. No verification source is authored or certified by Designer.

Each invocation creates fresh `evidence/l1-<timestamp_or_hash>/` outputs and
retains exact commands/cwd, source identity, versions, timestamps, timeout/exit,
raw child logs and structured comparison/coverage results, including failures.
Mutation copies and diffs must not modify delivered RTL. Supplemental checks
never replace an admitted check.

### Frozen matrix

Tuples are SRC_WIDTH,SRC_HEIGHT,DST_WIDTH,DST_HEIGHT,PIXEL_BITS,CHANNELS,
PHASE_FRAC,INTERPOLATION:

```
(1,1,1,1,8,1,8,0)       (1,5,3,7,8,1,8,1)
(5,1,7,3,8,3,8,0)       (3,5,7,9,8,3,16,0)
(7,9,3,4,8,1,16,1)      (8,6,8,6,8,3,8,0)
(9,7,4,3,10,1,16,0)     (5,3,9,7,10,1,8,1)
(4096,1,1,1,8,1,8,0)    (1,4096,1,3,8,1,16,0)
(64,48,96,72,8,3,16,1)
```

Every cell runs seeds 17,89,203 on compiled Verilator with at least eight
completed frames per cell/seed and every applicable mode. Add frames to cover
all presets, borders and absolute settings globally. Mode 0 is compared to both
the unchanged parent and the independent mathematical model.

### Frozen acceptance coverage

Compare every output pixel/marker, frame length and final drain. Required images
include zero, maximum, every corner impulse, interior impulse when possible,
horizontal/vertical ramps, checkerboards, distinct RGB channels, deterministic
random pixels and raw patterns distinguishing all Bayer phases. Cover all
signed coefficient extrema, mixed-sign cancellation, shifts 0,1,4,16,31,
positive/negative ties, both saturation limits and Sobel magnitude. Verify fixed
leaf latencies separately and compare the complete composed chain.

Measured coverage must include all applicable modes/presets, both borders and
absolute settings, all Bayer phases, every tap's independent influence, both
bank wraps, all four edges, multi-frame reuse, at least 16 frame-boundary
configuration changes, a configuration request held while busy, illegal mode
rejection and reset during fill, active output and output stall. Require at least
32 accepted source bubbles, 32 output-stall cycles, a consecutive stall of at
least 16 cycles and stalled valid SOF and EOL. Count exercised transfers and
observations, not scenario names.

Illegal parameter tests cover zero and 4097 independently in every dimension,
invalid formats, phase precision and interpolation. Legal singleton dimensions
must complete. At least seven independently executed behavioral mutations must
be rejected by actual comparisons: coefficient signedness, rounding bias,
saturation, border selection, Bayer phase/channel selection, bank rotation and
stalled data/marker advance. Compilation failure is not mutation detection.

Yosys must read the real RTL and perform hierarchy/driver checks, real synthesis,
zero-latch and zero-divider/modulo assertions for default mono, RGB and ten-bit
tops, retaining meaningful sequential/memory structure. Static operator and
sequential-assignment checks cover every delivered RTL file. Missing tools,
failed children, unknown valid outputs, malformed results, absent coverage and
data mismatches fail rather than skip.

Icarus is limited to compact startup, leaf and architectural smoke, no more
than 500000 simulated cycles and designed for under 60 seconds per run.
Compiled Verilator executes the full seeded matrix and mutations. Keep logs
compact, avoid per-cycle/per-beat prints or flushes and reclaim settled compiler
intermediates while preserving sources, receipts and failures. The shared
runtime owns execution deadlines, native timing lint, STA and physical HIL.

## Claim-to-evidence map

| Claim | Required independent evidence |
|---|---|
| Parent compatibility and spatial numerical correctness | Public-handshake pixel/marker comparisons against independent references; mode-0 parent comparisons |
| Pipeline/stall/reset/configuration correctness | Leaf timing checks plus measured end-to-end protocol/reset/configuration coverage |
| Tests discriminate real defects | Seven compiled and behaviorally rejected RTL mutations with retained diffs/failures |
| Synthesizable stateful implementation | Fresh Yosys hierarchy/driver/synthesis/no-latch/no-divider results for all three formats |
| Portable delivery | Fresh clean-directory composed RGB, parent-compatible frame and Yosys results |
| 100 MHz hardware target | Shared-runtime timing lint and real Quartus STA on the specified device |
| Physical deterministic operation | Three consecutive native Arria 10 GX PCIe bridge HIL passes |
| Engineering/product approval | Independent engineering review, followed by runtime-authored and independently approved product PDF |

None of these claims is established by this document or by static elaboration.
