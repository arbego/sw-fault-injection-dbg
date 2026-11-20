"""Host ESP-IDF builds, JTAG flashing, and exclusive access for experiments."""

from contextlib import contextmanager
import fcntl
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

LOCK_DIR = Path(tempfile.gettempdir())


class OpenOCDClient(Client):
    """Fail on disconnects and timeouts instead of hanging or returning None."""

    def _recv(self):
        data = bytearray()
        while self.COMMAND_TOKEN not in data:
            chunk = self._socket.recv(self._buffer_size)
            if not chunk:
                raise ConnectionError("OpenOCD disconnected")
            data.extend(chunk)
        return data.split(self.COMMAND_TOKEN, 1)[0].decode("utf-8").strip()

    def execute(self, command):
        result = super().execute(command)
        if result is None:
            raise TimeoutError(f"OpenOCD timed out executing: {command.splitlines()[0]}")
        return result


def claim_target_lock(host, port):
    """Hold an OS lock for this endpoint; process exit releases it automatically."""
    endpoint = "localhost" if host in ("localhost", "127.0.0.1", "::1") else host
    key = hashlib.sha256(f"{endpoint}:{port}".encode()).hexdigest()[:16]
    handle = (LOCK_DIR / f"fi-dbg-hardware-{key}.lock").open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        handle.close()
        raise RuntimeError(f"Another fault session is using {host}:{port}; wait for it to finish.") from error
    return handle


def find_idf(build_dir):
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
            description = json.loads((Path(build_dir) / "project_description.json").read_text())
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


def build_app(project):
    project = Path(project).resolve()
    idf_path = find_idf(project / "build")
    env = os.environ.copy()
    env["IDF_PATH"] = str(idf_path)
    # Bundled test components need no downloads. Other projects can opt in.
    env.setdefault("IDF_COMPONENT_MANAGER", "0")
    print(f"[hardware] Building {project.name} with host ESP-IDF: {idf_path}", flush=True)
    try:
        subprocess.run(
            ["/bin/bash", "-c",
             'set -e\n. "$IDF_PATH/export.sh"\n'
             'exec idf.py -C "$1" -DIDF_TARGET=esp32s3 build',
             "fi-build", str(project)],
            cwd=project, env=env, check=True, timeout=600,
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise RuntimeError("Building firmware failed; no flashing attempted.") from error


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
    message = f"[hardware] Flashing and verifying firmware via {host}:{port}"
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


def flash_app(host, port, build_dir):
    # OpenOCD uses its own filesystem to read these locally built images.
    client = OpenOCDClient(host, port, timeout=120)
    client.connect()
    try:
        targets = client.execute("target names")
        if not targets or "esp32s3.cpu0" not in targets.split():
            raise RuntimeError("OpenOCD does not expose the expected esp32s3.cpu0 target.")
        client.execute("targets esp32s3.cpu0")
        with flashing_progress(host, port):
            result = client.execute(
                f"program_esp_bins {tcl_quote(Path(build_dir).resolve())} flasher_args.json verify reset"
            )
            # Espressif's helper returns 0 on success and -1 on programming failure.
            if result.strip() != "0":
                raise RuntimeError(f"Flashing firmware failed or timed out: {result!r}")
    finally:
        client.close()


def resume_if_halted(client):
    """OpenOCD reports an error if asked to resume an already running CPU."""
    if client.execute("esp32s3.cpu0 curstate") == "halted":
        client.resume()
