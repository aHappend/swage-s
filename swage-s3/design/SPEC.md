# SWAGE-S3 contract

Status: implementation contract, not verification or timing acceptance.
The supplied `swage_s2_reference/rtl/` is immutable parent provenance.
An authoritative generational handoff and parent STA/PPA are unavailable.

## Public interface and legal parameters

Top: `swage_s3_top`. Parameters retain the S2 names and defaults:
`SRC_WIDTH=8, SRC_HEIGHT=8, DST_WIDTH=16, DST_HEIGHT=16,
PIXEL_BITS=8, CHANNELS=1, PHASE_FRAC=8, INTERPOLATION=0`.
Dimensions are 1..4096; supported (bits,channels) pairs are (8,1),
(10,1), (8,3); phase precision is 8 or 16; interpolation is 0 bilinear
or 1 nearest. Invalid tuples issue `SWAGE_S3 PARAMETER ERROR`.
Data containers are respectively 8,16,24 bits; mono10 uses bits 9:0.

All streams have `s_axis_tdata`, `s_axis_tvalid`, `s_axis_tready`,
`s_axis_tuser` (SOF), `s_axis_tlast` (EOL), `s_axis_cvalid[2:0]`,
and corresponding `m_axis_*` ports. Component masks are not byte strobes.
Low-first bytes mean R/G/B or Y/Cb/Cr. Mono/raw masks are 001;
full-color masks are 111. Sparse420 has mask111 only at even x,even y,
otherwise mask001 and zero chroma. SOF is only (0,0); every row ends
with EOL, including singleton rows. Producers hold all fields under stall.
Malformed framing recovery is outside the caller contract.

Control ports: `cfg_valid`, `cfg_ready`, `cfg_error`,
`cfg_enable[5:0]` (Crop,Demosaic,Filter,CSC,Scale,Histogram low-first),
`cfg_border`, `cfg_abs`, `cfg_shift[4:0]`, `cfg_coeff[143:0]`,
`cfg_roi_x/y/width/height[12:0]`, `cfg_input_format[1:0]`,
`cfg_csc_mode[2:0]`, `cfg_output_420`, `cfg_csc_coeff[143:0]`,
`cfg_csc_in_bias/out_bias[47:0]`, `cfg_hist_channel[1:0]`.
All scalar ports are one bit; packed containers are unsigned.
Coefficient slices are signed16 row-major, tap0 in bits15:0;
bias slices are signed16 low-component-first.

Statistics: `hist_req_valid`, `hist_req_ready`, `hist_req_addr[4:0]`,
`hist_rsp_valid`, `hist_rsp_ready`, `hist_rsp_data[255:0]`, `hist_done`.
There are 256 unsigned32 counters. Address a returns bins8a..8a+7,
lowest bin in bits31:0. A response reserves the completed snapshot
until retirement and holds its data while stalled.

Active-low reset asserts asynchronously. Public outputs, including ready,
data, masks and status, are zero during reset. Reset aborts partial work,
flushes valid state and invalidates statistics; unreset RAM is hidden.
Configuration handshakes only while idle without a statistics reservation.
At idle cfg_valid suppresses source acceptance, even for rejection.
Accepted invalid configuration pulses cfg_error once and changes no fields.
Busy attempts do not handshake or report an error.
Defaults enable scale only, clamp borders, identity filter, full ROI,
input_format0, CSC601 forward, no420, histogram channel0.

## Stage and numerical rules

Order is Crop -> Demosaic -> 3x3 Filter -> CSC -> 2D Scale -> Histogram.
Each disabled stage routes around its arithmetic.
Crop consumes the complete source and retains the selected rectangle,
regenerating markers. Enabled ROI must be nonempty and fit by widened
coordinate addition. Spatial dimensions become the crop dimensions.
Global RGGB parity includes the crop origin.

Input formats: 0 mono/RGB/raw according to parameters and demosaic enable,
1 YCbCr444, 2 YCbCr420; 3 always rejects. YCbCr requires color8 and no
demosaic/filter. An enabled420 crop must have even origin (odd size is legal).
Demosaic requires color8 and format0. Filter requires 8-bit components and
format0. CSC requires color8. Modes0/1 are forward601/709, modes2/3 are
inverse601/709, mode4 is custom; other enabled modes reject. Direction for
custom follows input format. Forward requires format0 and inverse requires
format1/2. Output420 only affects enabled forward conversion.
Bypassed-stage numerical fields are ignored. Histogram channels0..2
select available components; channel3 requires RGB output and computes
`(77R+150G+29B+128)>>8`. Mono only permits channel0; mono10 bins bits9:2.
Sparse chroma contributes only on valid-component beats.

Demosaic preserves the parent forward-looking 2x2 samples
q00=(x,y),q01=(x+1,y),q10=(x,y+1),q11=(x+1,y+1).
For y-even, R is x-even?q00:q01 and B is x-even?q11:q10;
for y-odd, R is x-even?q10:q11 and B is x-even?q01:q00.
If x/y parity differ, greens are q00,q11, otherwise q01,q10.
G=(green_a+green_b+1)>>1. Borders clamp or zero-pad.

Filter sums nine signed16 times unsigned8 products exactly. For sum S,
M=abs(S), Q=(M+(shift?2^(shift-1):0))>>shift. Negative S becomes zero
unless cfg_abs; remaining results saturate to255. Shift spans0..31.

CSC uses signed Q3.13 coefficients. For component i,
`A=sum(Cij*(sample_j+in_bias_j))`, then saturates
`round_ties_away_from_zero(A/8192)+out_bias_i` to unsigned8.
Products and sums are exact; biases are integer sample units.
Preset integer tables and bias vectors are in ARCHITECTURE.md.
Forward420 is co-sited point subsampling, not averaging.
Inverse420 replicates each chroma sample over its2x2 luma cell.

Scale independently treats Y and the two ceil(W/2)-by-ceil(H/2) chroma
planes for420. Destination chroma dimensions are ceil(DW/2),ceil(DH/2).
For each plane, step=floor((source_dimension<<F)/destination_dimension),
phase=destination_coordinate*step. Bilinear uses floor coordinates,
clamped neighbors, F-bit fractions and a single final rounding:
`(p00*(2^F-fx)*(2^F-fy)+p01*fx*(2^F-fy)+
p10*(2^F-fx)*fy+p11*fx*fy+2^(2F-1))>>(2F)`.
Nearest is min(S-1,(phase+2^(F-1))>>F).
No half-pixel subtraction. Mono10 retains ten bits.
Runtime division is sequential, not a combinational operator.

Histogram increments only on external output handshakes and serializes
read/increment/write to avoid repeated-bin hazards. Enabled frames clear
all256 counters before admission. Completion waits for the entire source,
all outputs and the final increment. Snapshot requests cannot race replacement.
No next frame is admitted until all source/output work and updates retire.

## Cycle and degenerate-boundary table

| Event | Pre-edge observation | Post-edge state |
|---|---|---|
| Transfer E0 | valid && ready | counters advance once; NBA data is next observation |
| Reset asserted | no transfer | all public fields zero immediately; RAM validity cleared |
| First released edge | cleared state | only explicitly admitted work starts |
| Valid config E0 | idle; source suppressed | all configuration fields update atomically |
| Invalid config E0 | idle; source suppressed | cfg_error=1 until E1; old configuration retained |
| Output stall | valid && !ready | data, mask, SOF/EOL and arithmetic registers hold |
| Singleton row/frame | SOF and EOL may coincide | wrap by explicit compare, not binary truncation |
| Non-power-of-two width | last coordinate is width-1 | coordinate and FIFO pointers explicitly wrap |
| Crop retain E0 | accepted source | one elastic register available after E0 |
| Window request E0 | synchronous bank read | samples registered E0; packing E1 |
| Demosaic window E0 | accepted window | sample/green sum E0, RGB E1 |
| Filter window E0 | accepted window | products,pairs,intermediates,halves,total,magnitude,round,shift,saturate E0..E8 |
| CSC input E0 | reconstructed/full sample | bias,products,pairs,total,round,bias,saturate E0..E6 |
| Inverse420 input E0 | accepted sparse pixel | RAM/read metadata E0, expanded pixel E1, then CSC |
| Scale issue E0 | RAM read and weights | horizontal products E1, sums E2, vertical products E3, round E4, output E5 |
| Histogram transfer E0 | external handshake | bin read E0, increment E1, commit E2 |
| Stats request E0 | snapshot reserved | reads E1..E8, packed response E9; earliest retirement E10 |
| RAM operation | explicit FSM owner | no dependency on unspecified same-address read/write |
| Reset vs update | reset has priority | pending data/response discarded, counters hidden |

Pipeline cycles above are advancing edges; stalls freeze them. Row filling,
border scans, phase setup and crop discards add geometry-dependent latency.
Counter clear uses256 write edges. Plane phase setup uses two32-edge
restoring divisions, with finite setup/latch FSM edges.

## Delivery and independent acceptance

Required tools are Python3, Icarus/vvp, compiled Verilator, Yosys; native
runtime gates additionally require Quartus17.1 and the Arria10 hardware bridge.
No tool versions or results are claimed before execution.
Independent Verifier owns tests/*.py and the following self-contained commands:

```
python3 tests/verify.py --suite behavior --seeds 17,43,89
python3 tests/verify.py --suite sensitivity
python3 tests/verify.py --suite synthesis
python3 tests/verify.py --suite portable
```

All required exact comparisons, seven mutation classes, comparator corruption,
legacy differential checks, all64 legal bypass patterns, three seeds, specified
geometry/format/rounding/reset/stall coverage, clean-directory reproduction and
zero-latch synthesis remain mandatory. Maximum-size elaboration is not full-size
behavioral execution. L1 does not certify target timing: native timing lint,
fresh source-bound <=10ns Quartus STA with WNS>=0 and no unconstrained functional
paths, then three deterministic physical-board passes are still required.
Product PDF generation and independent approval follow engineering acceptance.

The mandatory matrix uses seeds17,43,89, all64 bypass combinations in the
color/raw8x6->11x9 profile with an odd-origin3x3 ROI, and actual numerical
comparisons for1x1->1x1,1x7->3x5,7x1->5x3,2x2->1x1,3x5->7x3,
8x6->8x6,17x9->5x13,64x33->37x19. Across these cases cover both F values,
both interpolation modes, all three legal bit/channel pairs, both borders,
full/edge-touching/odd-origin/singleton crops and scale enabled/bypassed.
For every seed cover four444 presets, both forward420 presets, both inverse420
presets, custom forward/inverse matrices, identity and signed/extreme coefficients,
clipping and positive/negative half ties. Stimuli include black,white,primaries,
nominal video endpoints,out-of-range video,impulses,ramps,checkerboards,
constants and random images. Include odd420 sizes, singleton chroma, aligned
420 crops, chroma plane up/downscaling and every inverse replicated pixel.
Filter cases include identity,blur,signed edge,abs on/off,shifts0/31,
coefficient extrema and clipping.

Exercise source bubbles, always/alternating/random output readiness and
>=32-cycle stalls on first/EOL/final pixels and statistics responses.
Reset during fill, stalled arithmetic and pending response; cover accepted,
rejected and busy configuration, rejection retention, source/config priority,
three consecutive frames per seed, snapshot replacement and reservation.
Exhaust all256 singleton mono8 values, every histogram bin, repeated bins,
independent channels, mono10 bin boundaries and disabled statistics.
Require zero value/mask/marker/configuration/statistics mismatches, zero active
X/Z and zero unmet mandatory scenario counters, with machine-readable counts.
Compare legacy behavior independently and also S3 against real parent modules
where shared transactions are legal; retain identified parent failures separately.

Compact Icarus runs are bounded to500000 cycles; multi-seed and mutation sweeps
use compiled Verilator. At least one compact444 and420 chain runs on both.
Disposable mutations must compile, execute and fail the intended assertion:
ROI off-by-one, Bayer parity/channel, filter signed/rounding, CSC coefficient/
rounding,420 phase/mask, scaler phase, histogram repeated-bin loss.
Additionally corrupt one expected pixel and one expected histogram value.
Compiler failure or unrelated timeout is not defect detection.
Synthesis covers mono8,mono10,color, singleton and non-power-of-two geometry,
fully connected configuration inputs, real hierarchy/process/memory/synth
operations, zero latch/div/mod cells, no unresolved modules or undriven active
outputs. Boundary elaboration covers4096, invalid0/4097, illegal format pairs,
phase/interpolation and every illegal configuration category.
The portable suite copies only rtl/*.sv, tests/*.py, parent rtl/*.sv and
design/*.md, then regenerates compact444/420,legacy and zero-latch checks
without original builds, hidden imports or runtime metadata.
