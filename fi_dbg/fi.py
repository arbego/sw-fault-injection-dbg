#!/usr/bin/env python3
"""
Fault Injector CLI.

A modular fault injection tool supporting multiple fault models.
Each model is implemented as a plugin in the fi_dbg/faults/ directory.

Usage:
    fi.py stuck-at <address> <value> [options]
    fi.py --list-models
"""

import sys
import time
import argparse
from pathlib import Path
from typing import Optional

# Allow direct execution from outside the repository root.
if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parents[1])

from openocd import Client

from fi_dbg.faults import get_fault_models, StuckAtFault, BitFlipFault


class FaultInjector:
    """
    Main fault injection controller.

    Manages OpenOCD connection and delegates to specific fault models.
    """

    def __init__(self, host: str = 'localhost', port: int = 6666):
        self.host = host
        self.port = port
        self.client: Optional[Client] = None
        self.model = None

    def connect(self) -> None:
        """Connect to OpenOCD."""
        self.client = Client(self.host, self.port, timeout=10)
        self.client.connect()

    def close(self) -> None:
        """Clean up and disconnect."""
        if self.model:
            self.model.cleanup()
        if self.client:
            self.client.close()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()

    def setup_fault(self, model_class, **kwargs) -> None:
        """
        Configure a fault model.

        Args:
            model_class: Fault model class to instantiate
            **kwargs: Model-specific parameters
        """
        self.model = model_class(self.client)
        self.model.setup(**kwargs)

    def run(self, duration: Optional[float] = None) -> dict:
        """
        Run fault injection.

        Args:
            duration: Run time in seconds (None = until Ctrl+C)

        Returns:
            Statistics dictionary
        """
        if not self.model:
            print("[FI] No fault configured")
            return {}

        print(f"[FI] Running... (Ctrl+C to stop)")
        self.client.resume()

        start = time.time()
        try:
            while True:
                time.sleep(1)
                if duration and (time.time() - start) >= duration:
                    break
        except KeyboardInterrupt:
            print("\n[FI] Interrupted")

        # Stop and collect stats
        self.client.halt()
        stats = self.model.get_stats()

        print(f"\n[FI] Triggers: {stats.get('triggers', 0)}")
        return stats


def resolve_address(address_str: str, elf_path: Optional[str] = None) -> int:
    """
    Resolve address from hex string or symbol name.

    Args:
        address_str: Hex address or symbol name
        elf_path: Optional ELF file for symbol lookup

    Returns:
        Integer address
    """
    try:
        return int(address_str, 16)
    except ValueError:
        if elf_path:
            from fi_dbg.helpers.elf.elf_parser import ElfParser
            elf = ElfParser(elf_path)
            sym = elf.find_symbol_by_name(address_str)
            if sym:
                print(f"[FI] Resolved '{address_str}' -> 0x{sym.address:08x}")
                return sym.address
            raise ValueError(f"Symbol '{address_str}' not found in {elf_path}")
        raise ValueError(f"'{address_str}' is not a hex address. Use --elf for symbol lookup.")


def main():
    # Get available fault models
    models = get_fault_models()
    model_names = list(models.keys())

    parser = argparse.ArgumentParser(
        description='Fault Injector - Modular fault injection tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Available fault models: {', '.join(model_names)}

Examples:
  %(prog)s stuck-at 0x3fc93740 0              # Stuck at 0
  %(prog)s stuck-at 0x3fc93740 1              # Stuck at 1
  %(prog)s bit-flip 0x3fc93740 --bit 0        # Flip bit 0
  %(prog)s bit-flip 0x3fc93740 --mask 0xFF    # Flip lower 8 bits
  %(prog)s bit-flip 0x3fc93740 --random       # Flip random bit
  %(prog)s --list-models                      # List available models
        """
    )

    # Global options
    parser.add_argument('--host', default='localhost', help='OpenOCD host')
    parser.add_argument('--port', type=int, default=6666, help='OpenOCD TCL port')
    parser.add_argument('--list-models', action='store_true',
                        help='List available fault models')

    # Subparsers for fault models
    subparsers = parser.add_subparsers(dest='model', help='Fault model')

    # Stuck-at model
    stuck_at_parser = subparsers.add_parser('stuck-at',
                                            help='Stuck-at fault (force fixed value)')
    stuck_at_parser.add_argument('address', help='Memory address (hex) or symbol')
    stuck_at_parser.add_argument('value', help='Stuck value (decimal or 0x hex)')
    stuck_at_parser.add_argument('--width', type=int, default=32,
                                 choices=[8, 16, 32], help='Bit width')
    stuck_at_parser.add_argument('--duration', type=float,
                                 help='Run duration in seconds')
    stuck_at_parser.add_argument('--elf', help='ELF file for symbol lookup')

    # Bit-flip model
    bitflip_parser = subparsers.add_parser('bit-flip',
                                           help='Bit-flip fault (transient, no watchpoints)')
    bitflip_parser.add_argument('address', help='Memory address (hex) or symbol')
    bitflip_group = bitflip_parser.add_mutually_exclusive_group()
    bitflip_group.add_argument('--bit', type=int, help='Bit number to flip (0-31)')
    bitflip_group.add_argument('--mask', help='Bit mask (hex, e.g. 0xFF)')
    bitflip_group.add_argument('--random', action='store_true', help='Flip random bit')
    bitflip_parser.add_argument('--width', type=int, default=32,
                                choices=[8, 16, 32], help='Bit width')
    bitflip_parser.add_argument('--count', type=int, default=1,
                                help='Number of injections')
    bitflip_parser.add_argument('--interval', type=float, default=1.0,
                                help='Seconds between injections')
    bitflip_parser.add_argument('--elf', help='ELF file for symbol lookup')

    args = parser.parse_args()

    # Handle --list-models
    if args.list_models:
        print("Available fault models:")
        for name, cls in models.items():
            print(f"  {name}: {cls.description}")
        return 0

    # Require a model
    if not args.model:
        parser.print_help()
        return 1

    try:
        # Handle stuck-at model
        if args.model == 'stuck-at':
            address = resolve_address(args.address, args.elf)
            value = StuckAtFault.parse_value(args.value, args.width)

            with FaultInjector(args.host, args.port) as fi:
                fi.setup_fault(StuckAtFault,
                              address=address,
                              value=value,
                              width=args.width)
                fi.run(args.duration)

        # Handle bit-flip model
        elif args.model == 'bit-flip':
            address = resolve_address(args.address, args.elf)

            # Determine bit mask
            if args.random:
                mask = BitFlipFault.random_bit_mask(args.width)
                print(f"[FI] Random bit mask: 0x{mask:0{args.width//4}x}")
            elif args.mask:
                mask = int(args.mask, 16)
            elif args.bit is not None:
                mask = 1 << args.bit
            else:
                mask = 1  # Default: flip bit 0

            with FaultInjector(args.host, args.port) as fi:
                fi.setup_fault(BitFlipFault,
                              address=address,
                              bit_mask=mask,
                              width=args.width,
                              count=args.count,
                              interval=args.interval)
                fi.model.run()

        return 0

    except ValueError as e:
        print(f"[FI] Error: {e}")
        return 1
    except Exception as e:
        print(f"[FI] Error: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
