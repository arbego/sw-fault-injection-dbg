"""One API session per test process, plus the test application's ready handshake."""

from functools import lru_cache
import os
from pathlib import Path

from fi_dbg import FaultController


APP_DIR = Path(__file__).resolve().parents[1] / "app"


@lru_cache(maxsize=1)
def prepare_session():
    seed = os.environ.get("FI_SEED")
    session = FaultController(
        elf=APP_DIR / "build/fi_test_app.elf", project=APP_DIR,
        seed=int(seed, 0) if seed is not None else None, log_prefix="test",
    )
    try:
        session.open()
        for name in ("fi_test_ready", "fi_test_request", "fi_test_ack"):
            if session.symbol(name).size != 4:
                raise RuntimeError(f"Incompatible test firmware symbol: {name}")
        request = session.read("fi_test_ack") % 0xFFFFFFFF + 1

        def acknowledged():
            session.write("fi_test_request", request)
            return session.read_many("fi_test_ready", "fi_test_ack") == (0x46495431, request)

        session.wait_until(acknowledged, description="test firmware startup acknowledgement")
        return session
    except BaseException:
        session.close()
        raise
