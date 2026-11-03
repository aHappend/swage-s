"""Frozen coverage targets and deterministic public input generation."""

import random
from dataclasses import dataclass, field


@dataclass
class Case:
    name: str
    params: tuple = (3, 5, 7, 11, 8, 3, 8, 0, 4096, 16)
    cfg: dict = field(default_factory=dict)
    frames: int = 1
    seed: int = 17
    pattern: str = "random"
    configure: bool = True
    reject: bool = False
    stalls: bool = True
    busy: bool = False
    abort: bool = False
    history_reconfiguration: bool = False
    labels: tuple = ()

    def __post_init__(self):
        sw, sh = self.params[:2]
        defaults = dict(enable=0, border=0, abs=0, shift=0,
                        coeff=[0, 0, 0, 0, 1, 0, 0, 0, 0],
                        rx=0, ry=0, rw=sw, rh=sh, format=0, mode=0, out420=0,
                        csc=[8192, 0, 0, 0, 8192, 0, 0, 0, 8192],
                        ib=[0, 0, 0], ob=[0, 0, 0], hist=0,
                        s4=0, alpha=128, threshold=16, clip=4, patch=16,
                        bf16=0, means=[32640]*3, reciprocals=[512]*3)
        defaults.update(self.cfg)
        self.cfg = defaults


def image(case, frame):
    sw, sh, _, _, bits, channels = case.params[:6]
    rng = random.Random(case.seed)
    rows, masks = [], []
    for y in range(sh):
        row, mm = [], []
        for x in range(sw):
            if case.pattern == "constant":
                p = (37, 81, 129)
            elif case.pattern == "ramp":
                p = ((x+sw*y)*17 % 256, x*23 % 256, y*29 % 256)
            elif case.pattern == "impulse":
                p = (255, 127, 63) if (x, y) == (sw//2, sh//2) else (0, 0, 0)
            elif case.pattern == "checkerboard":
                p = (255, 0, 255) if (x+y) & 1 else (0, 255, 0)
            elif case.pattern == "endpoints":
                p = ((0, 1, 255, 256, 1022, 1023)[(x+sw*y) % 6],)
            else:
                p = tuple(rng.randrange(256) for _ in range(3))
            if channels == 1:
                p = (p[0] & ((1 << bits)-1),)
            if frame:
                delta = 0 if frame % 3 == 1 else (1 if frame % 3 == 2 else 97)
                p = tuple(min((1 << bits)-1, v + delta) for v in p)
            mask = 1 if channels == 1 or (case.cfg["format"] == 2 and (x & 1 or y & 1)) else 7
            if channels == 3 and mask == 1:
                p = (p[0], 0, 0)
            row.append(p)
            mm.append(mask)
        rows.append(row)
        masks.append(mm)
    return rows, masks


def compact_cases():
    return [
        Case("compact-legacy", (3, 5, 7, 11, 8, 3, 8, 0, 257, 8),
             dict(enable=48, hist=3), labels=("compact_legacy",)),
        Case("compact-enhanced", (1, 1, 1, 1, 8, 3, 8, 0, 1, 8),
             dict(s4=7, patch=8, clip=1), frames=3, pattern="constant",
             labels=("compact_enhanced",)),
    ]


def matrix():
    cases = []
    for bits, channels in ((8, 1), (10, 1), (8, 3)):
        for frac in (8, 16):
            for nearest in (0, 1):
                for sw, sh in ((1, 1), (1, 7), (7, 1), (3, 5), (7, 11)):
                    for dw, dh, direction in ((sw, sh, "identity"),
                                               (sw*2+1, sh*2+1, "up"),
                                               (max(1, sw//2), max(1, sh//2), "down")):
                        params = (sw, sh, dw, dh, bits, channels, frac, nearest, 4096, 16)
                        cases.append(Case(f"legacy-{bits}-{channels}-{frac}-{nearest}-{sw}x{sh}-{direction}",
                                          params, dict(enable=48), pattern="endpoints" if bits == 10 else "random",
                                          labels=(f"tuple_{bits}_{channels}_{frac}_{nearest}", f"scale_{direction}",
                                                  f"geometry_{sw}x{sh}")))
    # Every legal RGB enable mask is exercised, including CSC forward transforms.
    for mask in range(64):
        cases.append(Case(f"rgb-enable-{mask}", cfg=dict(enable=mask),
                          labels=(f"rgb_enable_{mask}",), busy=True))
    for parity in range(4):
        for border in (0, 1):
            cases.append(Case(f"roi-parity-{parity}-border-{border}",
                              (7, 11, 3, 5, 8, 3, 8, 0, 4096, 16),
                              dict(enable=7, rx=parity & 1, ry=parity >> 1,
                                   rw=3, rh=5, border=border),
                              labels=(f"roi_parity_{parity}", f"border_{border}")))
    for absolute in (0, 1):
        for shift in (0, 1, 15, 31):
            for coeff in ([32767]*9, [-32768]*9,
                          [-1, 2, -3, 4, -5, 6, -7, 8, -9]):
                cases.append(Case(f"conv-{absolute}-{shift}-{coeff[0]}",
                                  cfg=dict(enable=4, abs=absolute, shift=shift, coeff=coeff),
                                  labels=(f"conv_abs_{absolute}", f"conv_shift_{shift}",
                                          f"conv_coeff_{coeff[0]}")))
    for mode in range(5):
        for fmt in ((0,) if mode < 2 else ((1, 2) if mode < 4 else (0, 1, 2))):
            for out420 in (0, 1):
                for scaling in (0, 16):
                    for hist in (0, 1, 2):
                        cases.append(Case(f"csc-{mode}-{fmt}-{out420}-{scaling}-{hist}",
                                          cfg=dict(enable=8|32|scaling, mode=mode, format=fmt,
                                                   out420=out420, hist=hist),
                                          labels=(f"csc_mode_{mode}", f"csc_format_{fmt}",
                                                  f"hist_channel_{hist}")))
    cases.append(Case("hist-intensity", cfg=dict(enable=32, hist=3), labels=("hist_channel_3",)))
    cases.append(Case("ignored-disabled-s4-fields",
                      cfg=dict(enable=48, s4=0, alpha=511, threshold=511, clip=0,
                               patch=0, bf16=1, means=[-8388608]*3, reciprocals=[0]*3),
                      labels=("disabled_s4_fields_ignored",)))
    for enable in (0, 16, 32, 48):
        cases.append(Case(f"420-bypass-{enable}", cfg=dict(enable=enable, format=2),
                          labels=("odd420",)))
    # Four frames include stationary, low motion, and deliberate motion after seeding.
    for mask in range(1, 8):
        for seed in (17, 89, 203):
            cases.append(Case(f"s4-mask-{mask}-seed-{seed}",
                              (9, 11, 7, 5, 8, 3, 8, 0, 257, 8),
                              dict(s4=mask, enable=48, hist=3, patch=8),
                              frames=4, seed=seed,
                              labels=(f"s4_mask_{mask}_seed_{seed}",), busy=True))
        cases.append(Case(f"s4-ready-{mask}", (3, 5, 3, 5, 8, 3, 8, 0, 257, 8),
                          dict(s4=mask, patch=8), frames=4, stalls=False,
                          labels=(f"continuous_ready_mask_{mask}",)))
    for alpha in (0, 1, 128, 255, 256):
        for threshold in (0, 1, 16, 255, 256):
            cases.append(Case(f"temporal-{alpha}-{threshold}",
                              (3, 5, 3, 5, 8, 3, 8, 0, 257, 16),
                              dict(s4=1, alpha=alpha, threshold=threshold),
                              frames=4, labels=(f"alpha_{alpha}", f"threshold_{threshold}")))
    for tile in (8, 16):
        for geometry in ((1, 1), (tile, tile), (tile+1, tile-1), (tile+1, tile+1)):
            for pattern in ("constant", "ramp", "impulse", "checkerboard"):
                for limit in (1, 4, tile*tile):
                    w, h = geometry
                    cases.append(Case(f"clahe-{tile}-{w}x{h}-{pattern}-{limit}",
                                      (w, h, w, h, 8, 3, 8, 0, 4096, tile),
                                      dict(s4=2, clip=limit), pattern=pattern,
                                      labels=(f"tile_{tile}", f"clahe_{pattern}", f"clip_{limit}")))
            w, h = geometry
            real_n = min(w, tile)*min(h, tile)
            cases.append(Case(f"clahe-real-N-{tile}-{w}x{h}",
                              (w, h, w, h, 8, 3, 8, 0, 4096, tile),
                              dict(s4=2, clip=real_n), pattern="constant",
                              labels=(f"clahe_limit_N_tile_{tile}",)))
    for patch in (8, 14, 16):
        for bf in (0, 1):
            for w, h in ((1, 1), (patch-1, patch+1), (patch, patch),
                         (patch+1, patch-1), (2*patch+1, patch+1)):
                cases.append(Case(f"tokens-{patch}-{bf}-{w}x{h}",
                                  (w, h, w, h, 8, 3, 8, 0, 4096, 16),
                                  dict(s4=4, patch=patch, bf16=bf), pattern="ramp",
                                  labels=(f"patch_{patch}_format_{bf}_geometry_{w}x{h}",)))
    for capacity, w, h in ((1, 1, 1), (257, 257, 1), (4096, 64, 64)):
        cases.append(Case(f"capacity-{capacity}", (w, h, w, h, 8, 3, 8, 0, capacity, 16),
                          dict(s4=1), frames=3, stalls=False, labels=(f"capacity_{capacity}",)))
    # Cropped enhanced input must still consume the complete source raster.
    cases.append(Case("enhanced-crop-tail", (7, 11, 3, 5, 8, 3, 8, 0, 257, 8),
                      dict(enable=1, rw=3, rh=5, s4=5, patch=8),
                      frames=3, labels=("enhanced_crop_tail",)))
    # Rejected transactions retain the previous configuration/history.
    bad = [
        ("roi-zero", dict(enable=1, rw=0)), ("roi-bounds", dict(enable=1, rx=3)),
        ("format", dict(format=3)), ("mode", dict(enable=8, mode=5)),
        ("inverse-rgb", dict(enable=8, mode=2)),
        ("forward-yuv", dict(enable=8, mode=0, format=1)),
        ("demosaic-yuv", dict(enable=2, format=1)),
        ("conv-yuv", dict(enable=4, format=1)),
        ("hist-yuv-intensity", dict(enable=32, format=1, hist=3)),
        ("420-odd-roi", dict(enable=1, format=2, rx=1, rw=1)),
        ("patch", dict(s4=4, patch=9)), ("alpha", dict(s4=1, alpha=257)),
        ("threshold", dict(s4=1, threshold=257)), ("clip-zero", dict(s4=2, clip=0)),
        ("clip-large", dict(s4=2, clip=257)),
        ("reciprocal", dict(s4=4, reciprocals=[512, 0, 512])),
        ("enhance-yuv", dict(s4=1, format=1)),
    ]
    for name, cfg in bad:
        cases.append(Case(f"reject-{name}", cfg=cfg, reject=True, labels=(f"reject_{name}",)))
    cases.append(Case("reject-capacity", (65, 64, 65, 64, 8, 3, 8, 0, 4096, 16),
                      dict(s4=1), reject=True, labels=("reject_capacity",)))
    for bits in (8, 10):
        cases.append(Case(f"reject-mono-{bits}", (3, 5, 3, 5, bits, 1, 8, 0, 257, 16),
                          dict(s4=1), reject=True, labels=(f"reject_mono_{bits}",)))
        cases.append(Case(f"reject-mono-hist-{bits}", (3, 5, 3, 5, bits, 1, 8, 0, 257, 16),
                          dict(enable=32, hist=1), reject=True, labels=(f"reject_mono_hist_{bits}",)))
        for name, cfg in (("format", dict(format=1)), ("csc", dict(enable=8)),
                          ("demosaic", dict(enable=2))):
            cases.append(Case(f"reject-mono-{name}-{bits}", (3, 5, 3, 5, bits, 1, 8, 0, 257, 16),
                              cfg, reject=True, labels=(f"reject_mono_{name}_{bits}",)))
    cases.append(Case("reject-mono10-convolution", (3, 5, 3, 5, 10, 1, 8, 0, 257, 16),
                      dict(enable=4), reject=True, labels=("reject_mono10_convolution",)))
    cases.append(Case("reset-mid-capture", (7, 11, 7, 11, 8, 3, 8, 0, 257, 8),
                      dict(s4=7, patch=8), abort=True, frames=3,
                      labels=("reset_mid_frame",)))
    cases.append(Case("history-reconfiguration", (3, 5, 3, 5, 8, 3, 8, 0, 257, 8),
                      dict(s4=1, alpha=256, threshold=256), frames=5,
                      history_reconfiguration=True,
                      labels=("rejected_config_preserves_history", "accepted_repeat_reseeds_history")))
    return cases
