#!/usr/bin/env python3
"""Run a Python procedure through the same fault API as the hardware tests."""

import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import runpy
import sys

# Also support invoking this script directly from outside the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fi_dbg import FaultSession


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path, help="Python file defining run(session)")
    parser.add_argument("--elf", required=True, type=Path, help="ELF matching the target firmware")
    parser.add_argument("--project", type=Path, help="ESP-IDF project to build and flash first")
    parser.add_argument("--host", help="OpenOCD host (default: FI_OPENOCD_HOST or localhost)")
    parser.add_argument("--port", type=int, help="TCL port (default: FI_OPENOCD_PORT or 6666)")
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=os.environ.get("FI_SEED"),
                        help="Random seed, decimal or hex (default: FI_SEED or a new seed)")
    parser.add_argument("--log", type=Path, help="Save seed, outcome, and operation history as JSON")
    args = parser.parse_args()
    script = args.script.expanduser().resolve()
    if not script.is_file():
        parser.error(f"Experiment script does not exist: {script}")

    session = FaultSession(args.elf, project=args.project, host=args.host,
                           port=args.port, seed=args.seed)
    # Open the report before touching hardware, so an invalid path fails early.
    report_context = args.log.open("w") if args.log else nullcontext(None)
    with report_context as report:
        outcome = "failed"
        failure = None
        previous_path = sys.path[:]
        try:
            sys.path.insert(0, str(script.parent))
            namespace = runpy.run_path(str(script), run_name="fi_experiment")
            procedure = namespace.get("run")
            if not callable(procedure):
                raise ValueError(f"{script} must define run(session)")
            with session:
                procedure(session)
            outcome = "passed"
        except BaseException as error:
            failure = {"type": type(error).__name__, "message": str(error)}
            raise
        finally:
            sys.path[:] = previous_path
            if report is not None:
                try:
                    json.dump({"script": str(script), "seed": session.seed,
                               "outcome": outcome, "error": failure,
                               "history": session.history}, report, indent=2)
                    report.write("\n")
                except Exception as error:
                    if failure is None:
                        raise
                    print(f"Could not write experiment log: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
