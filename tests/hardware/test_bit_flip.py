"""Readback and firmware-observation tests using a real ESP32-S3 target."""

import os
import time
import unittest

from openocd import Client

from faults.bit_flip import BitFlipFault
from tests.hardware.support import prepare_target, resume_if_halted, wait_for_firmware


class BitFlipHardwareTests(unittest.TestCase):
    GUARD_BEFORE = 0x13579BDF
    GUARD_AFTER = 0x2468ACE0

    @classmethod
    def setUpClass(cls):
        host = os.environ.get("FI_OPENOCD_HOST", "localhost")
        port = int(os.environ.get("FI_OPENOCD_PORT", "6666"))
        elf = prepare_target(host, port)
        cls.addresses = {}
        for name, size in (("fi_test_ready", 4), ("fi_test_heartbeat", 4),
                           ("fi_test_request", 4), ("fi_test_ack", 4),
                           ("fi_bit_flip_words", 12), ("fi_bit_flip_observed", 4)):
            symbol = elf.find_symbol_by_name(name)
            if symbol is None or symbol.size != size:
                raise RuntimeError(f"Missing or incompatible test symbol: {name}")
            cls.addresses[name] = symbol.address

        cls.client = Client(host, port, timeout=10)
        cls.addClassCleanup(cls.client.close)
        cls.client.connect()
        cls.addClassCleanup(resume_if_halted, cls.client)
        cls.client.execute("targets esp32s3.cpu0")
        resume_if_halted(cls.client)

        wait_for_firmware(cls.client, cls.addresses)

    def tearDown(self):
        resume_if_halted(self.client)

    def check_injections(self, width, initial, mask, count=1):
        base = self.addresses["fi_bit_flip_words"]
        fault = BitFlipFault(self.client)
        try:
            self.client.halt()
            self.client.write_memory(base, [self.GUARD_BEFORE, initial, self.GUARD_AFTER], 32)
            fault.setup(base + 4, bit_mask=mask, width=width, count=count)
            results = fault.run()

            width_mask = (1 << width) - 1
            expected_word = initial ^ mask if count % 2 else initial
            value = initial & width_mask
            for result in results:
                self.assertEqual(result["old_value"], value)
                value ^= mask
                self.assertEqual(result["new_value"], value)
            self.assertEqual(len(results), count)
            self.assertEqual(fault.get_stats()["triggers"], count)

            # Read back the whole word even for 8/16-bit access. This checks
            # neighboring bytes and both guard words as well as the flipped bits.
            self.client.halt()
            words = self.client.read_memory(base, 3, 32)
            heartbeat = self.client.read_memory(self.addresses["fi_test_heartbeat"], 1, 32)[0]
            self.client.resume()
            self.assertEqual(words, [self.GUARD_BEFORE, expected_word, self.GUARD_AFTER])

            # A fresh heartbeat proves the firmware ran after the injection.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                time.sleep(0.02)
                self.client.halt()
                try:
                    observed = self.client.read_memory(self.addresses["fi_bit_flip_observed"], 1, 32)[0]
                    current = self.client.read_memory(self.addresses["fi_test_heartbeat"], 1, 32)[0]
                finally:
                    self.client.resume()
                if current != heartbeat and observed == expected_word:
                    break
            else:
                self.fail(f"Firmware did not observe 0x{expected_word:08x} after resuming")
        finally:
            fault.cleanup()
            resume_if_halted(self.client)

    def test_each_bit_at_each_access_width(self):
        for width in (8, 16, 32):
            for initial in (0, 0xFFFFFFFF, 0xA55A96C3):
                for bit in range(width):
                    with self.subTest(width=width, initial=initial, bit=bit):
                        self.check_injections(width, initial, 1 << bit)

    def test_zero_multi_bit_and_full_width_masks(self):
        for width in (8, 16, 32):
            for mask in (0, 0x81, (1 << width) - 1):
                with self.subTest(width=width, mask=mask):
                    self.check_injections(width, 0xA55A96C3, mask)

    def test_random_single_bit_at_each_width(self):
        for width in (8, 16, 32):
            with self.subTest(width=width):
                mask = BitFlipFault.random_bit_mask(width)
                self.assertGreater(mask, 0)
                self.assertLess(mask, 1 << width)
                self.assertEqual(mask & (mask - 1), 0)
                self.check_injections(width, 0xA55A96C3, mask)

    def test_repeated_flip_restores_or_toggles_the_value(self):
        for width in (8, 16, 32):
            for count in (2, 3):
                with self.subTest(width=width, count=count):
                    self.check_injections(width, 0xA55A96C3, 1 << (width - 1), count)


if __name__ == "__main__":
    unittest.main()
