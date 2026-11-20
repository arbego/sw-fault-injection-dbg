"""Stuck-at scenarios expressed through the same API used by scripts."""

import unittest

from tests.hardware.support import prepare_session


FIRST = "fi_stuck_at_counter"
SECOND = "fi_stuck_at_counter_2"
THIRD = "fi_stuck_at_counter_3"


class StuckAtHardwareTests(unittest.TestCase):
    def setUp(self):
        self.session = prepare_session()
        self.addCleanup(self.session.remove_all_faults)

    def log(self, message):
        self.session.log(f"PASS: {message}", tag="stuck-at")

    def test_counter_increases_then_sticks_then_increases_again(self):
        session = self.session
        session.expect_increasing(FIRST, tag="stuck-at")

        # Leave headroom so the counter cannot wrap during recovery.
        random_value = session.random.randint(2, 0x7FFFFFFF)
        for stuck_value in (0, 1, random_value):
            with self.subTest(stuck_value=stuck_value):
                with session.stuck_at(FIRST, stuck_value) as fault:
                    previous_hits = 0
                    for sample in range(1, 4):
                        session.expect_value(FIRST, stuck_value, samples=1,
                                             interval=0.2, tag="stuck-at")
                        hits = fault.stats["triggers"]
                        self.assertGreater(hits, previous_hits,
                                           "Firmware did not make another write attempt")
                        previous_hits = hits
                        self.log(f"Stuck at {stuck_value}, check {sample}/3; watchpoint hits: {hits}.")
                session.expect_increasing(FIRST, min_delta=2, tag="stuck-at")

    def test_multiple_counters_increase_then_stick_then_increase_again(self):
        session = self.session
        first_value, second_value = session.random.sample(range(2, 0x7FFFFFFF), 2)

        # Both counters increase before injection.
        before = session.read_many(FIRST, SECOND)
        session.wait(0.2)
        after = session.read_many(FIRST, SECOND)
        for index in (0, 1):
            self.assertGreater(after[index], before[index] + 1)
        self.log(f"Both counters increased: {before} -> {after}.")

        # Freeze the first counter; the second keeps counting.
        first_fault = session.stuck_at(FIRST, first_value, name="first")
        session.wait(0.4)
        values = session.read_many(FIRST, SECOND)
        self.assertEqual(values[0], first_value)
        self.assertGreater(values[1], after[1] + 1)
        self.assertGreater(first_fault.stats["triggers"], 0)
        self.log(f"First counter stuck at {values[0]}; second increased: {after[1]} -> {values[1]}.")

        # Add the second fault without releasing the first.
        second_fault = session.stuck_at(SECOND, second_value, name="second")
        previous_hits = (first_fault.stats["triggers"], 0)
        for sample in range(1, 4):
            session.wait(0.4)
            values = session.read_many(FIRST, SECOND)
            self.assertEqual(values, (first_value, second_value))
            hits = (first_fault.stats["triggers"], second_fault.stats["triggers"])
            for index in (0, 1):
                self.assertGreater(hits[index], previous_hits[index])
            previous_hits = hits
            self.log(f"Both stuck, check {sample}/3: read back {values}; watchpoint hits: {hits}.")

        # Release only the first counter; the second must stay stuck.
        first_fault.remove()
        before = session.read_many(FIRST, SECOND)
        session.wait(0.4)
        after = session.read_many(FIRST, SECOND)
        self.assertGreater(after[0], before[0] + 1)
        self.assertEqual(after[1], second_value)
        self.assertGreater(second_fault.stats["triggers"], previous_hits[1])
        self.log(f"First released: {before[0]} -> {after[0]}; second still stuck at {after[1]}.")

        # Release the second counter; both now increase again.
        second_fault.remove()
        before = session.read_many(FIRST, SECOND)
        session.wait(0.2)
        after = session.read_many(FIRST, SECOND)
        for index in (0, 1):
            self.assertGreater(after[index], before[index] + 1)
        self.log(f"Both counters released and increasing: {before} -> {after}.")

    def test_third_stuck_at_fault_is_rejected(self):
        session = self.session
        first = session.stuck_at(FIRST, 11, name="first")
        second = session.stuck_at(SECOND, 22, name="second")
        session.wait(0.4)
        self.assertEqual(session.read_many(FIRST, SECOND), (11, 22))
        previous_hits = (first.stats["triggers"], second.stats["triggers"])
        for hits in previous_hits:
            self.assertGreater(hits, 0)
        self.log("Both hardware watchpoint slots are in use.")

        with self.assertRaisesRegex(RuntimeError, "(?i)(watchpoint|resource)") as error:
            session.stuck_at(THIRD, 33, name="third")
        self.assertNotIn("third", session.faults)
        self.assertEqual(len(session.faults), 2)
        self.log(f"Third fault rejected: {error.exception}")

        session.wait(0.4)
        values = session.read_many(FIRST, SECOND)
        self.assertEqual(values, (11, 22))
        hits = (first.stats["triggers"], second.stats["triggers"])
        for index in (0, 1):
            self.assertGreater(hits[index], previous_hits[index])
        self.log(f"Existing faults still hold {values}; watchpoint hits increased to {hits}.")


if __name__ == "__main__":
    unittest.main()
