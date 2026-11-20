"""Exercise bit-flip experiments through the shared Python API."""

import unittest

from tests.hardware.support import prepare_session


class BitFlipHardwareTests(unittest.TestCase):
    GUARD_BEFORE = 0x13579BDF
    GUARD_AFTER = 0x2468ACE0

    @classmethod
    def setUpClass(cls):
        cls.session = prepare_session()
        for name, size in (("fi_test_heartbeat", 4), ("fi_bit_flip_words", 12),
                           ("fi_bit_flip_observed", 4)):
            if cls.session.symbol(name).size != size:
                raise RuntimeError(f"Incompatible test firmware symbol: {name}")

    def tearDown(self):
        self.session.remove_all_faults()

    def check_injections(self, width, initial, mask, count=1):
        session = self.session
        session.write_memory("fi_bit_flip_words", [self.GUARD_BEFORE, initial, self.GUARD_AFTER])
        fault = session.bit_flip("fi_bit_flip_words", offset=4, mask=mask,
                                 width=width, count=count)
        width_mask = (1 << width) - 1
        expected_word = initial ^ mask if count % 2 else initial
        value = initial & width_mask
        for result in fault.injections:
            self.assertEqual(result["old_value"], value)
            value ^= mask
            self.assertEqual(result["new_value"], value)
        self.assertEqual(len(fault.injections), count)
        self.assertEqual(fault.stats["triggers"], count)
        self.assertFalse(fault.active)

        # Check the whole word and its neighbors, including narrow accesses.
        self.assertEqual(session.read_memory("fi_bit_flip_words", count=3),
                         [self.GUARD_BEFORE, expected_word, self.GUARD_AFTER])
        heartbeat = session.read("fi_test_heartbeat")

        def observed_by_running_firmware():
            observed, current = session.read_many("fi_bit_flip_observed", "fi_test_heartbeat")
            return current != heartbeat and observed == expected_word

        session.wait_until(observed_by_running_firmware, timeout=2, interval=0.02,
                           description=f"firmware observation of 0x{expected_word:08x}")

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
                mask = 1 << self.session.random.randrange(width)
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
