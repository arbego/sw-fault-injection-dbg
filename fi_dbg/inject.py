#!/usr/bin/env python3
"""Inject an ad-hoc fault into the firmware already flashed on an ESP32-S3."""

import argparse
from contextlib import contextmanager
import math
from pathlib import Path
import signal
import sys

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parents[1])

from fi_dbg import FaultController
from fi_dbg.faults import get_fault_models
from fi_dbg.helpers.elf.elf_parser import ElfParser


class StopRequested(BaseException):
    def __init__(self, signum):
        self.signum = signum


@contextmanager
def stopping_signals():
    """Unwind through controller cleanup; repeated signals do not interrupt it."""
    requested = False

    def stop(signum, frame):
        nonlocal requested
        if not requested:
            requested = True
            raise StopRequested(signum)

    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.signal(signum, stop)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def integer(value):
    try:
        return int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use a decimal integer or a 0x-prefixed hex integer") from error


def positive_integer(value):
    number = integer(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be a positive integer")
    return number


def nonnegative_time(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("Must be a finite, nonnegative number of seconds")
    return number


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list-models', action='store_true', help='List available fault models')
    models = parser.add_subparsers(dest='model')
    stuck = models.add_parser('stuck-at', help='Hold a memory value until stopped')
    stuck.add_argument('target', help='Hex address or ELF symbol name')
    stuck.add_argument('value', type=integer, help='Value to hold, decimal or hex')
    stuck.add_argument('--duration', type=nonnegative_time, help='Hold for this many seconds; default: until Ctrl+C')
    flip = models.add_parser('bit-flip', help='Flip bits once or repeatedly')
    flip.add_argument('target', help='Hex address or ELF symbol name')
    selection = flip.add_mutually_exclusive_group()
    selection.add_argument('--bit', type=integer, help='Bit index; default: 0')
    selection.add_argument('--mask', type=integer, help='XOR mask, decimal or hex')
    selection.add_argument('--random', action='store_true', help='Choose one random bit')
    flip.add_argument('--count', type=positive_integer, default=1, help='Number of flips')
    flip.add_argument('--interval', type=nonnegative_time, default=0, help='Seconds between flips')

    # Suppressed subparser defaults preserve options supplied before the model.
    for command in (parser, stuck, flip):
        default = None if command is parser else argparse.SUPPRESS
        command.add_argument('--elf', type=Path, default=default, help='ELF matching the flashed firmware; required for symbols')
        command.add_argument('--host', default=default, help='OpenOCD host; default: FI_OPENOCD_HOST or localhost')
        command.add_argument('--port', type=positive_integer, default=default, help='Tcl port; default: FI_OPENOCD_PORT or 6666')
        command.add_argument('--seed', type=integer, default=default, help='Seed for random bit selection')
        command.add_argument('--no-reset', action='store_true',
                             default=False if command is parser else argparse.SUPPRESS,
                             help='Reuse OpenOCD and skip resetting the target; start a server if absent')
    for command in (stuck, flip):
        command.add_argument('--width', type=int, choices=(8, 16, 32), default=32, help='Memory access width; default: 32')
    return parser


def resolve_target(target, elf):
    try:
        return int(target, 16)
    except ValueError:
        if elf is None:
            raise ValueError('Symbol names require --elf; otherwise provide a hex address')
        symbol = ElfParser(str(elf.expanduser().resolve())).find_symbol_by_name(target)
        if symbol is None:
            raise ValueError(f'Symbol {target!r} not found in {elf}')
        return symbol.address


def validate(args):
    address = resolve_target(args.target, args.elf)
    if not 0 <= address <= 0xFFFFFFFF or address + args.width // 8 > 1 << 32:
        raise ValueError('Target is outside the 32-bit address space')
    if address % (args.width // 8):
        raise ValueError(f'Target must be aligned for a {args.width}-bit access')
    if args.model == 'stuck-at':
        if not 0 <= args.value < 1 << args.width:
            raise ValueError(f'Stuck value must fit in {args.width} bits')
    else:
        if args.bit is not None and not 0 <= args.bit < args.width:
            raise ValueError(f'Bit index must be between 0 and {args.width - 1}')
        if args.mask is not None and not 0 <= args.mask < 1 << args.width:
            raise ValueError(f'Mask must fit in {args.width} bits')
    return address


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.list_models:
        for name, model in get_fault_models().items():
            print(f'{name}: {model.description}')
        return 0
    if args.model is None:
        parser.error('Choose stuck-at or bit-flip')
    try:
        address = validate(args)
    except (ValueError, OSError) as error:
        parser.error(str(error))

    try:
        # No project is supplied: this entry point never builds or flashes.
        controller = FaultController(args.elf, host=args.host, port=args.port,
                                     seed=args.seed, log_prefix='inject',
                                     reuse_openocd=args.no_reset, reset=not args.no_reset)
        with stopping_signals(), controller:
            if args.model == 'bit-flip':
                result = controller.bit_flip(address, bit=args.bit, mask=args.mask,
                                             random_bit=args.random, width=args.width,
                                             count=args.count, interval=args.interval)
                controller.log(f'Completed {result.stats["triggers"]} bit-flip injection(s).')
            else:
                fault = controller.stuck_at(address, args.value, width=args.width)
                controller.log('Fault active. Press Ctrl+C to remove it.', operation='armed')
                if args.duration is None:
                    while True:
                        controller.wait(1)
                else:
                    controller.wait(args.duration)
                controller.log(f'Watchpoint hits: {fault.stats["triggers"]}')
        return 0
    except StopRequested as request:
        print(f'[inject] Stopped ({signal.Signals(request.signum).name}).', flush=True)
        return 128 + request.signum
    except KeyboardInterrupt:
        print('[inject] Interrupted.', flush=True)
        return 130
    except Exception as error:
        print(f'[inject] Error: {error}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
