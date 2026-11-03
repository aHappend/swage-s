"""Read-only programming-device and PCIe discovery; never programs a device."""

import json
import shutil

from evidence import execution_environment


def discover(e):
    commands = (
        ("shared-runtime-version", ["vulcan", "--version"]),
        ("shared-runtime-help", ["vulcan", "hardware", "fpga", "--help"]),
        ("shared-runtime-board-help",
         ["vulcan", "hardware", "fpga", "board-hil", "--help"]),
        ("programmer-version", ["quartus_pgm", "--version"]),
        ("programmer-help", ["quartus_pgm", "--help"]),
        ("programming-devices", ["quartus_pgm", "-l"]),
        ("pci-version", ["lspci", "--version"]),
        ("pci-identities", ["lspci", "-Dnn"]),
        ("pci-link-capabilities", ["lspci", "-Dvvnn"]),
    )
    observations = []
    for name, argv in commands:
        item = {"name": name, "requested_argv": argv}
        observations.append(item)
        if not shutil.which(argv[0], path=execution_environment().get("PATH")):
            item["status"] = "unavailable-command"
        else:
            try:
                e.run(argv, name=name, expected=tuple(range(256)))
                command = e.record["commands"][-1]
                item.update(
                    status="executed",
                    exit_status=command["exit_status"],
                    stdout=command["stdout"], stderr=command["stderr"],
                )
            except (OSError, TimeoutError) as error:
                item.update(status="discovery-error", error=str(error))
        e.record["discovery"] = observations
        e.save()
    e.record["discovery_scope"] = (
        "Local authorized tool interfaces only; no programming, MMIO, remote "
        "execution, or board configuration changes. A successful discovery "
        "process is not proof of a matching board or hardware acceptance. "
        "Inspect raw tool errors and link/device identities independently."
    )
    (e.path / "discovery.json").write_text(
        json.dumps(observations, indent=2) + "\n", encoding="ascii"
    )
    e.save()
