"""Campaign for tests/app firmware; run with python -m fi_dbg.run_campaign."""


def wait_for_firmware(session):
    # A fresh acknowledgement proves this firmware is executing after flashing.
    request = session.read("fi_test_ack") % 0xFFFFFFFF + 1

    def acknowledged():
        session.write("fi_test_request", request)
        return session.read_many("fi_test_ready", "fi_test_ack") == (0x46495431, request)

    session.wait_until(acknowledged, description="test firmware startup")


def hold_and_release(session, counter, value):
    session.expect_increasing(counter)
    with session.stuck_at(counter, value):
        session.expect_value(counter, value, samples=3)
    session.expect_increasing(counter, min_delta=2)


def run(session):
    wait_for_firmware(session)
    for value in (0, 1, session.random.randint(2, 0x7FFFFFFF)):
        hold_and_release(session, "fi_stuck_at_counter", value)

    # The middle word is never overwritten by the firmware.
    word = session.address("fi_bit_flip_words", offset=4)
    before = session.read(word)
    session.bit_flip(word, bit=0)
    session.expect_value(word, before ^ 1)
