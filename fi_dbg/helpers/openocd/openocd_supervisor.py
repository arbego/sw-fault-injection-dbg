"""Own OpenOCD and stop it when the session owner's lifetime pipe closes.

Executed as a standalone process so cleanup also works after runner SIGKILL,
os._exit(), or a crash that bypasses Python exit handlers.
"""

import os
import select
import signal
import subprocess
import sys


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def exit_on_signal(signum, frame):
    raise SystemExit(128 + signum)


def main():
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, exit_on_signal)

    process = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL)
    try:
        while process.poll() is None:
            readable, _, _ = select.select([sys.stdin.fileno()], [], [], 0.2)
            if readable and not os.read(sys.stdin.fileno(), 4096):
                return 0
        return process.returncode
    finally:
        # Do not let a second termination signal interrupt OpenOCD cleanup.
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, signal.SIG_IGN)
        stop_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
