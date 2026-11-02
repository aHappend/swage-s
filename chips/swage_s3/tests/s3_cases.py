"""Requirement-directed scenarios, independent of hardware implementation."""

from dataclasses import replace

from isp_oracle import Config


def color_cases(parameters, seeds, make_case):
    cases = []
    for seed in seeds:
        for mode in range(4):
            for pattern in ("primaries", "random", "0", "255"):
                cfg = Config(enable=56, csc_mode=mode, input_format=int(mode >= 2))
                cases.append(make_case(parameters, cfg, seed, pattern,
                                       name=f"preset444-{mode}-{pattern}-{seed}"))
        for mode in range(4):
            cfg = Config(enable=56, csc_mode=mode, input_format=2 if mode >= 2 else 0,
                         output_420=mode < 2)
            for scale in (False, True):
                c = replace(cfg, enable=cfg.enable if scale else cfg.enable & ~16)
                cases.append(make_case(parameters, c, seed, "ramp",
                                       name=f"preset420-{mode}-scale{int(scale)}-{seed}"))
        matrices = [
            ((8192, 0, 0, 0, 8192, 0, 0, 0, 8192), (0, 0, 0), (0, 0, 0), "primaries"),
            ((4096, 0, 0, -4096, 0, 0, 32767, 0, 0), (0, 0, 0), (0, 128, 0), "1"),
            ((-32768, 32767, -32768, 32767, -32768, 32767, -32768, -32768, 32767),
             (-32768, 32767, -32768), (32767, -32768, 32767), "random"),
            ((-8192, 0, 8192, 4096, 8192, -4096, 32767, -32768, 8192),
             (-16, -128, -128), (16, 128, 128), "checkerboard"),
        ]
        for direction in (0, 1, 2):
            for i, (matrix, ib, ob, pattern) in enumerate(matrices):
                cfg = Config(enable=56, input_format=direction, csc_mode=4,
                             csc_coeff=matrix, in_bias=ib, out_bias=ob)
                cases.append(make_case(parameters, cfg, seed, pattern,
                                       name=f"custom-{direction}-{i}-{seed}"))
        kernels = [
            ((0, 0, 0, 0, 1, 0, 0, 0, 0), 0),
            ((1, 1, 1, 1, 1, 1, 1, 1, 1), 3),
            ((-1, -2, -1, 0, 0, 0, 1, 2, 1), 1),
            ((0, 0, 0, 0, -1, 0, 0, 0, 0), 0),
            ((0, 0, 0, 0, 1, 0, 0, 0, 0), 1),
            ((0, 0, 0, 0, -1, 0, 0, 0, 0), 1),
            ((32767,) * 9, 0),
            ((-32768,) * 9, 31),
            ((-32768, 32767, -32768, 32767, -32768, 32767, -32768, 32767, -32768), 7),
        ]
        for i, (coeff, shift) in enumerate(kernels):
            for absolute in (False, True):
                for border in (False, True):
                    cfg = Config(enable=52, coeff=coeff, shift=shift, absolute=absolute,
                                 border=border)
                    cases.append(make_case(parameters, cfg, seed,
                                           ("impulse", "ramp", "255", "checkerboard")[i % 4],
                                           name=f"filter-{i}-abs{int(absolute)}-border{int(border)}-{seed}"))
        for channel in range(4):
            cfg = Config(enable=48, hist_channel=channel)
            cases.append(make_case(parameters, cfg, seed, "random",
                                   name=f"histogram-rgb-channel-{channel}-{seed}"))
        for fmt in (1, 2):
            for channel in range(3):
                cfg = Config(enable=48, input_format=fmt, hist_channel=channel)
                cases.append(make_case(parameters, cfg, seed, "ramp",
                                       name=f"histogram-format-{fmt}-channel-{channel}-{seed}"))
        # A constant sequence detects read/modify/write hazards at identical addresses.
        cases.append(make_case(parameters, Config(enable=32), seed, "77",
                               name=f"histogram-repeated-bin-{seed}"))
        # Fields belonging to bypassed stages are deliberately nonapplicable.
        cases.append(make_case(parameters, Config(enable=0, roi=(8191, 8191, 0, 0),
                               csc_mode=7, coeff=(-32768,)*9, shift=31, hist_channel=3),
                               seed, "random", traffic=0,
                               name=f"ignored-bypass-controls-{seed}"))
        cases.append(make_case(parameters, Config(enable=0, input_format=1,
                                                  hist_channel=3, csc_mode=7),
                               seed, "primaries", name=f"ignored-ycbcr-histogram-field-{seed}"))
    return cases


def sparse_cases(parameters, seeds, make_case):
    sw, sh = parameters[:2]
    configs = [
        Config(enable=48, input_format=2, hist_channel=1),
        Config(enable=48, input_format=2, hist_channel=2),
        Config(enable=56, output_420=True),
        Config(enable=56, output_420=True, csc_mode=1),
        Config(enable=56, input_format=2, csc_mode=2),
        Config(enable=56, input_format=2, csc_mode=3),
        Config(enable=49, input_format=2, roi=(0, 0, sw, sh), hist_channel=1),
        Config(enable=57, input_format=2, roi=(0, 0, sw, sh), csc_mode=3),
    ]
    if sw >= 3 and sh >= 3:
        configs.append(Config(enable=49, input_format=2,
                              roi=(2, 2, sw-2, sh-2), hist_channel=2))
    return [make_case(parameters, config, seed, "ramp",
                      name=f"sparse-{i}-seed-{seed}")
            for seed in seeds for i, config in enumerate(configs)]
