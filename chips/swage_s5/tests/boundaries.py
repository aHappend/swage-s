"""Executed parameter diagnostics, distinct from functional boundary scenes."""

from receipts import fresh


def boundary_check(root, output, run):
    cases = []
    domains = {"SW": (0, 1, 2, 4095, 4096, 4097),
               "SH": (0, 1, 2, 4095, 4096, 4097),
               "DW": (0, 1, 2, 4095, 4096, 4097),
               "DH": (0, 1, 2, 4095, 4096, 4097),
               "MW": (0, 1, 2, 4095, 4096, 4097),
               "NC": (0, 1, 2, 7, 8, 9), "K": (0, 1, 2, 15, 16, 17),
               "FD": (0, 1, 2, 3, 15, 16, 17), "CAP": (0, 1, 2, 65535, 65536, 65537),
               "TILE": (7, 8, 9, 15, 16, 17), "F": (7, 8, 9, 15, 16, 17),
               "INTERP": (-1, 0, 1, 2)}
    for parameter, values in domains.items():
        for value in values:
            if parameter in ("SW", "SH", "DW", "DH", "MW"):
                legal = 1 <= value <= 4096
            elif parameter == "NC":
                legal = 1 <= value <= 8
            elif parameter in ("K", "FD"):
                legal = 1 <= value <= 16
            elif parameter == "CAP":
                legal = 1 <= value <= 65536
            elif parameter in ("TILE", "F"):
                legal = value in (8, 16)
            else:
                legal = value in (0, 1)
            cases.append(({parameter: value}, legal))
    cases.extend((({"PB": pb, "CH": ch}, legal)
                  for pb, ch, legal in ((8, 1, True), (10, 1, True), (8, 3, True),
                                        (9, 1, False), (10, 3, False), (8, 0, False), (8, 4, False))))
    cases.extend((({"SW": 3, "MW": 2}, False),
                  ({"DW": 3, "MW": 2}, False),
                  ({"SW": 4096, "SH": 4096, "DW": 4096, "DH": 4096,
                    "NC": 8, "K": 16, "FD": 16}, True)))
    for kind, parameter, maximum in ((1, "CI", 6), (2, "CI", 6), (2, "CO", 6),
                                      (3, "CI", 12), (3, "CO", 8), (6, "NC", 8),
                                      (7, "K", 16), (8, "DEPTH", 32), (10, "K", 16),
                                      (1, "MW", 4096), (2, "MW", 4096), (4, "MW", 4096),
                                      (5, "MW", 4096), (7, "MW", 4096)):
        for value in sorted({0, 1, 2, maximum-1, maximum, maximum+1}):
            parameters = {"TYPE": kind, parameter: value}
            if kind == 3:
                parameters.setdefault("CI", 3)
                parameters.setdefault("CO", 6)
            cases.append((parameters, 1 <= value <= maximum))
    for kind in (8, 9):
        for value in (0, 1, 2):
            cases.append(({"TYPE": kind, "WIDTH": value}, value >= 1))
    rtl = [str(p.relative_to(root)) for p in sorted(root.glob("rtl/*.sv"))]
    results = []
    for parameters, legal in cases:
        case = fresh(output, "parameters")
        binary = str((case / "simulation.vvp").relative_to(root))
        run(["iverilog", "-g2012", "-Wall", "-s", "boundary_tb",
             *[f"-Pboundary_tb.{key}={value}" for key, value in parameters.items()],
             "-o", binary, "tests/boundary_tb.sv", *rtl], "boundary-compile")
        text = run(["vvp", binary], "boundary-simulation", timeout=600, expected_exit=None)
        command = run.__self__.record["commands"][-1]
        if legal:
            assert command["exit_code"] == 0 and "ELABORATED legal tuple" in text
            assert "PARAMETER ERROR" not in text
        else:
            assert command["exit_code"] != 0 and "PARAMETER ERROR" in text, (
                f"Missing explicit rejection {parameters}: {text[-1000:]}")
        results.append({"parameters": parameters, "legal": legal,
                        "exit_code": command["exit_code"], "stdout": command["stdout"],
                        "scope": "static elaboration and diagnostic only"})
    return results
