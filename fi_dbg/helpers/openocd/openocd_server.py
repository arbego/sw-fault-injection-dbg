"""Start a fresh local OpenOCD server for the lifetime of a fault session."""

import ipaddress
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time


def is_local(host):
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def server_version(host, port, timeout=1):
    """Check the TCL protocol without changing the target or hanging on EOF."""
    try:
        connection = socket.create_connection((host, port), timeout=timeout)
    except ConnectionRefusedError:
        return None
    with connection:
        connection.sendall(b"version\x1a")
        response = bytearray()
        while b"\x1a" not in response:
            chunk = connection.recv(4096)
            if not chunk:
                raise ConnectionError("OpenOCD closed the connection before responding")
            response.extend(chunk)
        version = response.split(b"\x1a", 1)[0].decode("utf-8", errors="replace").strip()
    if "Open On-Chip Debugger" not in version:
        raise RuntimeError(f"Endpoint {host}:{port} did not identify itself as OpenOCD: {version!r}")
    return version


def find_openocd():
    configured = os.environ.get("FI_OPENOCD_BIN")
    if configured:
        candidates = [Path(shutil.which(configured) or configured).expanduser()]
    else:
        tools_path = Path(os.environ.get("IDF_TOOLS_PATH", str(Path.home() / ".espressif"))).expanduser()
        candidates = sorted(
            (tools_path / "tools/openocd-esp32").glob("*/openocd-esp32/bin/openocd"),
            reverse=True,
        )
        candidates.append(Path.home() / "esp-tools/openocd/bin/openocd")
        executable = shutil.which("openocd")
        if executable:
            candidates.append(Path(executable))
    for candidate in candidates:
        candidate = candidate.resolve()
        scripts = Path(os.environ.get(
            "OPENOCD_SCRIPTS", str(candidate.parent.parent / "share/openocd/scripts")
        )).expanduser()
        if os.access(candidate, os.X_OK) and (scripts / "board/esp32s3-builtin.cfg").is_file():
            return candidate, scripts
    raise RuntimeError(
        "Espressif OpenOCD not found. Set FI_OPENOCD_BIN to its executable and, "
        "if needed, OPENOCD_SCRIPTS to its scripts directory."
    )


def shutdown_openocd(host, port):
    """Ask the identified local server to exit, then wait for its port to close."""
    try:
        connection = socket.create_connection((host, port), timeout=1)
    except ConnectionRefusedError:
        return
    with connection:
        connection.sendall(b"shutdown\x1a")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            connection = socket.create_connection((host, port), timeout=0.5)
        except ConnectionRefusedError:
            return
        connection.close()
        time.sleep(0.1)
    raise RuntimeError(f"Existing OpenOCD at {host}:{port} did not stop within 5 seconds")


class ManagedOpenOCD:
    def __init__(self, process, log):
        self.process = process
        self.log = log

    def stop(self):
        try:
            if self.process.stdin is not None and not self.process.stdin.closed:
                self.process.stdin.close()
            try:
                self.process.wait(timeout=7)
            except subprocess.TimeoutExpired:
                # The supervisor and OpenOCD share a separate process group.
                # This also handles a supervisor that has become unresponsive.
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=5)
        finally:
            self.log.close()


def start_openocd(host, port, build_dir, *, reuse=False):
    """Start a supervised server, optionally borrowing an existing one.

    The caller owns stop() on returned servers. Borrowed and remote servers
    return None and remain externally managed.
    """
    try:
        version = server_version(host, port)
    except (OSError, RuntimeError) as error:
        raise RuntimeError(f"Cannot use OpenOCD at {host}:{port}: {error}") from error
    if not is_local(host):
        if version:
            print(f"[hardware] Using remote OpenOCD at {host}:{port}", flush=True)
            return None
        raise RuntimeError(f"Remote OpenOCD is unavailable at {host}:{port}; start it on that host.")

    if reuse and version:
        print(f"[hardware] Reusing OpenOCD at {host}:{port}", flush=True)
        return None
    if reuse:
        print(f"[hardware] No OpenOCD found at {host}:{port}; initializing a local server.", flush=True)

    executable, scripts = find_openocd()
    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    if version:
        print(f"[hardware] Stopping existing OpenOCD at {host}:{port}", flush=True)
        shutdown_openocd(host, port)
    log_path = build_dir / "openocd.log"
    log = log_path.open("w")
    bind_address = "127.0.0.1" if host.lower() == "localhost" else host
    command = [str(executable), "-s", str(scripts), "-f", "board/esp32s3-builtin.cfg",
               "-c", f"bindto {bind_address}", "-c", f"tcl_port {port}",
               "-c", "gdb_port disabled", "-c", "telnet_port disabled"]
    print(f"[hardware] Starting OpenOCD at {host}:{port} (log: {log_path})", flush=True)
    try:
        supervisor = Path(__file__).with_name("openocd_supervisor.py")
        process = subprocess.Popen([sys.executable, str(supervisor), *command],
                                   stdin=subprocess.PIPE, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as error:
        log.close()
        raise RuntimeError(f"Could not start OpenOCD: {error}") from error
    server = ManagedOpenOCD(process, log)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"OpenOCD exited with status {process.returncode}")
            try:
                ready = server_version(host, port)
            except OSError:
                ready = None
            if ready and process.poll() is None:
                print(f"[hardware] OpenOCD ready at {host}:{port}", flush=True)
                return server
            time.sleep(0.1)
        raise RuntimeError("OpenOCD did not become ready within 15 seconds")
    except BaseException as error:
        server.stop()
        if isinstance(error, Exception):
            details = "\n".join(log_path.read_text(errors="replace").splitlines()[-30:])
            raise RuntimeError(f"{error}. OpenOCD log: {log_path}\n{details}") from error
        raise
