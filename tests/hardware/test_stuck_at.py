"""Check that a counter increases, stays stuck, and increases again."""

import os
import random
import time
import unittest

from openocd import Client

from faults.stuck_at import StuckAtFault
from tests.hardware.support import prepare_target, resume_if_halted, wait_for_firmware


class StuckAtHardwareTests(unittest.TestCase):
    def test_counter_increases_then_sticks_then_increases_again(self):
        host = os.environ.get("FI_OPENOCD_HOST", "localhost")
        port = int(os.environ.get("FI_OPENOCD_PORT", "6666"))
        elf = prepare_target(host, port)
        counter_address = elf.find_symbol_by_name("fi_stuck_at_counter").address

        client = Client(host, port, timeout=10)
        client.connect()
        self.addCleanup(client.close)
        self.addCleanup(resume_if_halted, client)
        client.execute("targets esp32s3.cpu0")
        wait_for_firmware(client, {
            name: elf.find_symbol_by_name(name).address
            for name in ("fi_test_ready", "fi_test_request", "fi_test_ack")
        })
        fault = StuckAtFault(client)
        self.addCleanup(fault.cleanup)

        def read_counter(with_fault=False):
            # Pause auto-resume so our inspection halt stays halted.
            # The watchpoint itself remains installed while sampling.
            if with_fault:
                client.execute("esp32s3.cpu0 configure -event halted {}")
            client.halt()
            try:
                return client.read_memory(counter_address, 1, 32)[0]
            finally:
                if with_fault:
                    client.execute(fault.get_tcl_handler())
                if with_fault and client.read_registers(["debugcause"])["debugcause"] & 0x04:
                    # A watchpoint may have halted at the pending increment.
                    # Let the fault handler skip it before resuming.
                    client.execute("esp32s3.cpu0 invoke-event halted")
                else:
                    client.resume()

        # 1. Without a fault, the counter must increase.
        before = read_counter()
        time.sleep(0.2)
        after = read_counter()
        self.assertGreater(after, before, "Counter was not increasing before injection")
        print(f"[test: stuck-at] PASS: Counter increased before injection: {before} -> {after}.",
              flush=True)

        # Leave headroom so the 32-bit counter cannot wrap during recovery.
        random_value = random.randint(2, 0x7FFFFFFF)
        for stuck_value in (0, 1, random_value):
            with self.subTest(stuck_value=stuck_value):
                # 2. Hold the counter at this value and check it three times.
                try:
                    fault.setup(counter_address, value=stuck_value, width=32, reset=False)
                    client.resume()
                    previous_triggers = 0
                    for sample in range(1, 4):
                        time.sleep(0.2)
                        value = read_counter(with_fault=True)
                        triggers = fault.get_stats()["triggers"]
                        self.assertEqual(value, stuck_value,
                                         f"Counter did not stay stuck at {stuck_value}")
                        self.assertGreater(triggers, previous_triggers,
                                           "Firmware did not make another write attempt")
                        previous_triggers = triggers
                        print(f"[test: stuck-at] PASS: Stuck at {stuck_value}, check {sample}/3: "
                              f"read back {value}; watchpoint hits: {triggers}.", flush=True)
                finally:
                    fault.cleanup()

                # 3. Remove the fault and verify the counter increases again.
                before = read_counter()
                time.sleep(0.2)
                after = read_counter()
                # One pending increment can complete even with a stale watchpoint.
                self.assertGreater(after, before + 1,
                                   "Counter did not resume continuous counting after removing the fault")
                print(f"[test: stuck-at] PASS: Counter increased after removing stuck-at-{stuck_value}: "
                      f"{before} -> {after}.", flush=True)


if __name__ == "__main__":
    unittest.main()
