"""Authorized Linux BAR0 transport for the actual SWAGE-S1 Arria 10 image.

Board setup is project-relative board/verification.json (or SWAGE_BOARD_CONFIG).
Required keys: device, bdf, vendor_id, device_id, cable, jtag_index,
board-system, board-settings, board-sdc, scaler-clock, dependencies. scaler-clock
names the actual application/scaler clock in TimeQuest, not the PCIe reference.
The three board inputs
obey fpga/README.md. Dependencies enumerate the complete board/IP file inventory.
No defaults manufacture a board identity, programming result or pixel response.
"""

import hashlib
import json
import mmap
from pathlib import Path
import struct
import time

from evidence import require
from reference import compare_models, image, integer_reference


def inspect_link(e, board, label):
    device = Path("/sys/bus/pci/devices") / board["bdf"]
    require(device.is_dir(), f"Physical PCIe endpoint unavailable: {board['bdf']}")
    result = {}
    for key in ("vendor", "device", "current_link_speed", "current_link_width"):
        path = device / key
        require(path.is_file(), f"PCIe identity/link facility unavailable: {path}")
        result[key] = path.read_text().strip()
    require(int(result["vendor"], 0) == int(board["vendor_id"], 0)
            and int(result["device"], 0) == int(board["device_id"], 0),
            "Actual PCIe endpoint identity differs from authorized board configuration")
    require(result["current_link_speed"].split()[0] == "8.0"
            and int(result["current_link_width"]) == 8,
            f"Required negotiated PCIe Gen3 x8 link absent: {result}")
    e.record.setdefault("physical_link_observations", []).append({"label": label, **result})
    e.save()
    return device, result


class Bar:
    def __init__(self, device):
        self.fd = (device / "resource0").open("r+b", buffering=0)
        try:
            self.mm = mmap.mmap(self.fd.fileno(), 4096,
                                flags=mmap.MAP_SHARED, prot=mmap.PROT_READ | mmap.PROT_WRITE)
        except Exception:
            self.fd.close()
            raise

    def read(self, offset):
        return struct.unpack_from("<I", self.mm, offset)[0]

    def write(self, offset, value):
        struct.pack_into("<I", self.mm, offset, value)
        self.read(0x0c)  # Flush posted PCIe writes before subsequent observation.

    def close(self):
        self.mm.close()
        self.fd.close()


def run_passes(e, board, c, build_id, directory):
    device, link = inspect_link(e, board, f"programmed-{c.label}")
    bar = Bar(device)
    results = []
    try:
        abi = bar.read(0x24)
        require(abi == 0x53573101, "Hardware ABI identifier mismatch")
        observed_id = sum(bar.read(0x40 + 4*i) << (32*i) for i in range(8))
        require(observed_id == int(build_id, 16) != 0,
                "Programmed hardware identity not bound to current build/source hashes")
        source_shape, destination_shape, pixel_format = (
            bar.read(0x18), bar.read(0x1c), bar.read(0x20))
        require(source_shape == (c.sh << 16 | c.sw)
                and destination_shape == (c.dh << 16 | c.dw)
                and pixel_format == (c.mode << 24 | c.frac << 16 | c.channels << 8 | c.bits),
                "Hardware parameter tuple differs from fitted image")
        (directory / "hardware-identity.json").write_text(json.dumps({
            "abi": abi, "build_id": f"{observed_id:064x}",
            "source_shape": source_shape, "destination_shape": destination_shape,
            "format": pixel_format,
        }, indent=2)+"\n")
        # Three consecutive identical workloads: determinism is measured from
        # returned bytes, not from a success token or expected-data digest.
        for pass_index in range(3):
            trace = directory / f"hardware-pass-{pass_index}.jsonl"
            digest = hashlib.sha256()
            total_pixels = total_frames = stalls = 0
            with trace.open("x", encoding="ascii") as stream:
                for pattern in ("random", "horizontal", "vertical", "checker", "full", "impulse"):
                    inputs, expected = [], []
                    for frame in range(3):
                        pixels = image(c, pattern, 2026, frame)
                        golden = integer_reference(c, pixels)
                        compare_models(c, pixels, golden)
                        inputs.extend(
                            p | (int(i == 0) << 24) | (int(i % c.sw == c.sw-1) << 25)
                            for i, p in enumerate(pixels)
                        )
                        expected.extend(p | (sof << 24) | (eol << 25) for p, sof, eol in golden)
                    bar.write(8, 1)
                    require(bar.read(0x10) == 0 and bar.read(0x14) == 0,
                            "Hardware reset did not clear transport counters")
                    bar.write(8, 2)
                    canceled = 0
                    start = time.monotonic()
                    while not (bar.read(0x0c) & 2):
                        require(time.monotonic() - start < 3600,
                                "Physical reset-with-stalled-output precondition not reached")
                        if bar.read(0x0c) & 1 and canceled < len(inputs):
                            bar.write(0, inputs[canceled])
                            canceled += 1
                    require(canceled > 0 and bar.read(0x10) == canceled
                            and bar.read(0x14) == 0,
                            "Partial-frame reset did not reserve a real outstanding output")
                    reset_observation = {
                        "reset_aborted_inputs": canceled, "held_status": bar.read(0x0c),
                        "aborted_input_count": bar.read(0x10),
                        "aborted_output_count": bar.read(0x14),
                    }
                    bar.write(8, 1)
                    require(bar.read(0x10) == 0 and bar.read(0x14) == 0
                            and bar.read(0x0c) & 3 == 0,
                            "Partial-frame reset leaked old transport state")
                    reset_observation["post_reset"] = {
                        "inputs": bar.read(0x10), "outputs": bar.read(0x14),
                        "pending": bar.read(0x0c) & 3,
                    }
                    stream.write(json.dumps(reset_observation)+"\n")
                    bar.write(8, 0)
                    ni = no = 0
                    held = False
                    start = time.monotonic()
                    while ni < len(inputs) or no < len(expected):
                        require(time.monotonic() - start < 3600,
                                "Physical frame transport incomplete after 3600 seconds")
                        status = bar.read(0x0c)
                        require(status & ~0xf == 0 and status & 4 == 0,
                                "Hardware status/reset corrupted")
                        if status & 1 and ni < len(inputs):
                            bar.write(0, inputs[ni])
                            stream.write(json.dumps({"input": ni, "word": inputs[ni]})+"\n")
                            ni += 1
                        if status & 2 and not held:
                            bar.write(8, 2)
                            before = bar.read(0x14)
                            begin = time.monotonic()
                            time.sleep(0.001)
                            after_status = bar.read(0x0c)
                            require(after_status & 0xa == 0xa and bar.read(0x14) == before,
                                    "Output reservation not stable under host-driven hold")
                            duration = time.monotonic() - begin
                            require(duration >= 32e-8, "Directed hardware stall shorter than 32 clocks")
                            stream.write(json.dumps({"hold_seconds": duration,
                                                     "output_count": before,
                                                     "after_status": after_status,
                                                     "after_output_count": bar.read(0x14)})+"\n")
                            bar.write(8, 0)
                            held = True
                            stalls += 1
                        if status & 2:
                            actual = bar.read(4)
                            require(no < len(expected) and actual == expected[no],
                                    f"Physical output mismatch {c.label}/{pattern}/{no}: "
                                    f"got={actual:#x} expected="
                                    f"{expected[no] if no < len(expected) else 'excess'}")
                            stream.write(json.dumps({"output": no, "actual": actual,
                                                     "expected": expected[no]})+"\n")
                            digest.update(struct.pack("<I", actual))
                            no += 1
                    require(held, "Host-driven backpressure event did not occur")
                    actual_inputs, actual_outputs = bar.read(0x10), bar.read(0x14)
                    require(actual_inputs == ni and actual_outputs == no,
                            "Hardware source/result conservation counters differ")
                    pending_output = bar.read(0x0c) & 2
                    require(not pending_output, "Excess hardware output")
                    stream.write(json.dumps({"conservation": {
                        "inputs": actual_inputs, "outputs": actual_outputs,
                        "pending_output": pending_output,
                    }})+"\n")
                    total_pixels += no
                    total_frames += 3
            result = {
                "pass": pass_index, "compared_pixels": total_pixels, "frames": total_frames,
                "host_stalls": stalls, "returned_sha256": digest.hexdigest(), "link": link,
                "raw_transport": e.relative(trace), "build_id": build_id,
            }
            require(total_pixels > 0 and total_frames == 18 and stalls == 6,
                    "Incomplete hardware workload coverage")
            if results:
                require(result["returned_sha256"] == results[0]["returned_sha256"],
                        "Consecutive physical runs are nondeterministic")
            results.append(result)
            inspect_link(e, board, f"pass-{pass_index}-{c.label}")
    finally:
        bar.close()
    require(len(results) == 3, "Three consecutive real hardware passes not completed")
    return results
