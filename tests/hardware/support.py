"""Build and flash the dedicated test firmware once per test process."""

import atexit
from contextlib import contextmanager
import fcntl
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from openocd import Client

from elf_parser import ElfParser
from tests.hardware.openocd_server import ensure_openocd


PROJECT_DIR = Path(__file__).resolve().parents[2]
APP_DIR = PROJECT_DIR / "tests/app"
BUILD_DIR = APP_DIR / "build"
ELF_PATH = BUILD_DIR / "fi_test_app.elf"
LOCK_DIR = Path(tempfile.gettempdir())


def claim_target_lock(host, port):
    """Hold an OS lock for this endpoint; process exit releases it automatically."""
    endpoint = "localhost" if host in ("localhost", "127.0.0.1", "::1") else host
    key = hashlib.sha256(f"{endpoint}:{port}".encode()).hexdigest()[:16]
    handle = (LOCK_DIR / f"fi-dbg-hardware-{key}.lock").open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        handle.close()
        raise RuntimeError(f"Another hardware test run is using {host}:{port}; wait for it to finish.") from error
    return handle


@lru_cache(maxsize=None)
def lock_target(host, port):
    handle = claim_target_lock(host, port)
    atexit.register(handle.close)
    return handle


def find_idf():
    """Find the host framework, preferring an explicitly configured install."""
    configured = os.environ.get("IDF_PATH")
    if configured:
        candidates = [Path(configured).expanduser()]
    else:
        candidates = []
        executable = shutil.which("idf.py")
        if executable:
            candidates.append(Path(executable).resolve().parents[1])
        try:
            description = json.loads((BUILD_DIR / "project_description.json").read_text())
            candidates.append(Path(description["idf_path"]))
        except (OSError, ValueError, KeyError):
            pass
        candidates.extend((Path.home() / "esp/esp-idf", Path.home() / "Workspace/esp-idf"))
        candidates.extend(sorted(
            (Path.home() / "Library/Caches/esphome/idf/frameworks").glob("*"),
            reverse=True,
        ))
    for candidate in candidates:
        if (candidate / "export.sh").is_file() and (candidate / "tools/idf.py").is_file():
            return candidate.resolve()
    raise RuntimeError("Host ESP-IDF not found. Set IDF_PATH to its installation directory.")


def build_test_app():
    idf_path = find_idf()
    env = os.environ.copy()
    env["IDF_PATH"] = str(idf_path)
    # This project uses only bundled components. No dependency downloads needed.
    env["IDF_COMPONENT_MANAGER"] = "0"
    print(f"[hardware] Building test firmware with host ESP-IDF: {idf_path}", flush=True)
    try:
        subprocess.run(
            ["/bin/bash", "-c",
             'set -e\n. "$IDF_PATH/export.sh"\n'
             'exec idf.py -C "$1" -DIDF_TARGET=esp32s3 build',
             "fi-test-build", str(APP_DIR)],
            cwd=PROJECT_DIR, env=env, check=True, timeout=600,
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise RuntimeError("Building the hardware test firmware failed; no flashing attempted.") from error


def tcl_quote(value):
    """Quote a path as one Tcl argument without allowing command substitution."""
    value = str(value)
    for char in ('\\', '"', '$', '[', ']'):
        value = value.replace(char, '\\' + char)
    return '"' + value.replace('\n', '\\n').replace('\r', '\\r') + '"'


@contextmanager
def flashing_progress(host, port):
    """Report activity while OpenOCD's blocking flash command is running."""
    output = sys.stdout
    terminal = output.isatty()
    finished = threading.Event()
    started = time.monotonic()
    message = f"[hardware] Flashing and verifying test firmware via {host}:{port}"
    print(message, file=output, flush=True)

    def report():
        tick = 0
        while not finished.wait(0.2 if terminal else 5):
            elapsed = time.monotonic() - started
            if terminal:
                spinner = "|/-\\"[tick % 4]
                print(f"\r{message} {spinner} {elapsed:.1f}s elapsed", end="", file=output, flush=True)
            else:
                print(f"[hardware] Still flashing and verifying... {elapsed:.1f}s elapsed", file=output, flush=True)
            tick += 1

    reporter = threading.Thread(target=report, name="flash-progress", daemon=True)
    reporter.start()
    try:
        yield
    finally:
        finished.set()
        reporter.join()
        if terminal:
            # Erase the activity line before unittest prints the next result.
            print("\r" + " " * (len(message) + 40) + "\r", end="", file=output, flush=True)
    print(f"[hardware] Flashing and verification complete ({time.monotonic() - started:.1f}s)",
          file=output, flush=True)


def flash_test_app(host, port):
    # OpenOCD uses its own filesystem to read these locally built images.
    client = Client(host, port, timeout=120)
    client.connect()
    try:
        targets = client.execute("target names")
        if not targets or "esp32s3.cpu0" not in targets.split():
            raise RuntimeError("OpenOCD does not expose the expected esp32s3.cpu0 target.")
        client.execute("targets esp32s3.cpu0")
        with flashing_progress(host, port):
            result = client.execute(
                f"program_esp_bins {tcl_quote(BUILD_DIR)} flasher_args.json verify reset"
            )
            # Espressif's helper returns 0 on success and -1 on programming failure.
            # The Python client also returns None on timeout, not an exception.
            if result is None or result.strip() != "0":
                raise RuntimeError(f"Flashing test firmware failed or timed out: {result!r}")
    finally:
        client.close()


def resume_if_halted(client):
    """OpenOCD reports an error if asked to resume an already running CPU."""
    if client.execute("esp32s3.cpu0 curstate") == "halted":
        client.resume()


def wait_for_firmware(client, addresses, timeout=5):
    """Require a fresh acknowledgement, not a ready marker retained across reset."""
    client.halt()
    try:
        old_ack = client.read_memory(addresses["fi_test_ack"], 1, 32)[0]
    finally:
        client.resume()
    # Nonzero and different from the previous acknowledgement, including wrap.
    request = old_ack % 0xFFFFFFFF + 1
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client.halt()
        try:
            ready = client.read_memory(addresses["fi_test_ready"], 1, 32)[0]
            ack = client.read_memory(addresses["fi_test_ack"], 1, 32)[0]
            if ready == 0x46495431 and ack == request:
                return
            client.write_memory(addresses["fi_test_request"], [request], 32)
        finally:
            client.resume()
        time.sleep(0.05)
    raise RuntimeError("Test firmware did not acknowledge startup after automatic flashing")


@lru_cache(maxsize=1)
def prepare_target(host, port):
    """Only cache successful preparation; all fault modules share this fixture."""
    lock_target(host, port)
    ensure_openocd(host, port, BUILD_DIR)
    build_test_app()
    elf = ElfParser(str(ELF_PATH))
    flash_test_app(host, port)
    return elf
