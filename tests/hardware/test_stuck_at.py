"""Check that a counter increases, stays stuck, and increases again."""

import os
import random
import time
import unittest

from openocd import Client

from faults.stuck_at import StuckAtFault
from tests.hardware.support import prepare_target, resume_if_halted, wait_for_firmware


class StuckAtHardwareTests(unittest.TestCase):
    def setUp(self):
        host = os.environ.get("FI_OPENOCD_HOST", "localhost")
        port = int(os.environ.get("FI_OPENOCD_PORT", "6666"))
        self.elf = prepare_target(host, port)
        self.client = Client(host, port, timeout=10)
        self.client.connect()
        self.addCleanup(self.client.close)
        self.addCleanup(resume_if_halted, self.client)
        self.client.execute("targets esp32s3.cpu0")
        wait_for_firmware(self.client, {
            name: self.elf.find_symbol_by_name(name).address
            for name in ("fi_test_ready", "fi_test_request", "fi_test_ack")
        })
        self.faults = []

    def new_fault(self):
        fault = StuckAtFault(self.client)
        self.faults.append(fault)
        self.addCleanup(fault.cleanup)
        return fault

    def read_counters(self, *addresses):
        active_fault = next((fault for fault in self.faults if fault._active), None)
        if active_fault:
            # Leave all watchpoints installed and service any pending write.
            active_fault._pause_handler()
        else:
            self.client.halt()
        try:
            return tuple(self.client.read_memory(address, 1, 32)[0] for address in addresses)
        finally:
            if active_fault:
                self.client.execute(active_fault.get_tcl_handler())
            self.client.resume()

    def test_counter_increases_then_sticks_then_increases_again(self):
        client = self.client
        counter_address = self.elf.find_symbol_by_name("fi_stuck_at_counter").address
        fault = self.new_fault()

        def read_counter():
            return self.read_counters(counter_address)[0]

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
                        value = read_counter()
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

    def test_multiple_counters_increase_then_stick_then_increase_again(self):
        client = self.client
        first_address = self.elf.find_symbol_by_name("fi_stuck_at_counter").address
        second_address = self.elf.find_symbol_by_name("fi_stuck_at_counter_2").address
        first_fault = self.new_fault()
        second_fault = self.new_fault()
        first_value, second_value = random.sample(range(2, 0x7FFFFFFF), 2)

        def read_both():
            return self.read_counters(first_address, second_address)

        # 1. Both counters increase before injection.
        before = read_both()
        time.sleep(0.2)
        after = read_both()
        for index in (0, 1):
            self.assertGreater(after[index], before[index] + 1)
        print(f"[test: stuck-at] PASS: Both counters increased: {before} -> {after}.", flush=True)

        # 2. Freeze the first counter; the second keeps counting.
        first_fault.setup(first_address, value=first_value, reset=False)
        client.resume()
        # Debug halts slow the shared task while a watchpoint is active.
        time.sleep(0.4)
        values = read_both()
        self.assertEqual(values[0], first_value)
        self.assertGreater(values[1], after[1] + 1)
        self.assertGreater(first_fault.get_stats()["triggers"], 0)
        print(f"[test: stuck-at] PASS: First counter stuck at {values[0]}; "
              f"second counter still increasing: {after[1]} -> {values[1]}.", flush=True)

        # 3. Add the second fault without releasing the first.
        second_fault.setup(second_address, value=second_value, reset=False)
        client.resume()
        previous_hits = (first_fault.get_stats()["triggers"], 0)
        for sample in range(1, 4):
            time.sleep(0.4)
            values = read_both()
            self.assertEqual(values, (first_value, second_value))
            hits = (first_fault.get_stats()["triggers"], second_fault.get_stats()["triggers"])
            for index in (0, 1):
                self.assertGreater(hits[index], previous_hits[index])
            previous_hits = hits
            print(f"[test: stuck-at] PASS: Both stuck, check {sample}/3: "
                  f"read back {values}; watchpoint hits: {hits}.", flush=True)

        # 4. Release only the first counter; the second must stay stuck.
        first_fault.cleanup()
        before = read_both()
        time.sleep(0.4)
        after = read_both()
        self.assertGreater(after[0], before[0] + 1)
        self.assertEqual(after[1], second_value)
        self.assertGreater(second_fault.get_stats()["triggers"], previous_hits[1])
        print(f"[test: stuck-at] PASS: First counter released: {before[0]} -> {after[0]}; "
              f"second counter still stuck at {after[1]}.", flush=True)

        # 5. Release the second counter; both now increase again.
        second_fault.cleanup()
        before = read_both()
        time.sleep(0.2)
        after = read_both()
        for index in (0, 1):
            self.assertGreater(after[index], before[index] + 1)
        print(f"[test: stuck-at] PASS: Both counters released and increasing: "
              f"{before} -> {after}.", flush=True)


if __name__ == "__main__":
    unittest.main()
