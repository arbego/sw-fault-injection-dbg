"""One write attempt against a 32-bit value held at zero."""

import os
import time
import unittest

from openocd import Client

from faults.stuck_at import StuckAtFault
from tests.hardware.support import prepare_target, resume_if_halted


class StuckAtHardwareTests(unittest.TestCase):
    def test_value_stays_zero_when_firmware_writes_one(self):
        host = os.environ.get("FI_OPENOCD_HOST", "localhost")
        port = int(os.environ.get("FI_OPENOCD_PORT", "6666"))
        elf = prepare_target(host, port)
        value_address = elf.find_symbol_by_name("fi_stuck_at_value").address
        request_address = elf.find_symbol_by_name("fi_stuck_at_request").address

        client = Client(host, port, timeout=10)
        client.connect()
        self.addCleanup(client.close)
        self.addCleanup(resume_if_halted, client)
        client.execute("targets esp32s3.cpu0")

        # Hold the target variable at zero.
        fault = StuckAtFault(client)
        self.addCleanup(fault.cleanup)
        fault.setup(value_address, value=0, width=32)

        # Ask the firmware to write one, then let it run.
        client.write_memory(request_address, [1], 32)
        client.resume()
        time.sleep(0.2)

        # Remove the handler before halting to inspect the result.
        fault.cleanup()
        client.halt()
        self.assertEqual(client.read_memory(request_address, 1, 32), [0],
                         "Firmware did not finish the write attempt")
        print("[test: stuck-at] PASS: Firmware completed the attempt to write 1 (request cleared).",
              flush=True)

        value = client.read_memory(value_address, 1, 32)[0]
        print(f"[test: stuck-at] Read back 0x{value:08x} from 0x{value_address:08x}.", flush=True)
        self.assertEqual(value, 0,
                         "The stuck-at-zero fault did not prevent the write")
        print("[test: stuck-at] PASS: Value remained stuck at 0 after the attempted write of 1.",
              flush=True)


if __name__ == "__main__":
    unittest.main()
