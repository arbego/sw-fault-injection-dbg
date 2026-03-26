#!/usr/bin/env python3
"""
Fault Injector CLI.

A modular fault injection tool supporting multiple fault models.
Each model is implemented as a plugin in the faults/ directory.

Usage:
    fi.py stuck-at <address> <value> [options]
    fi.py --list-models
"""

import sys
import time
import argparse
from typing import Optional

from openocd import Client

from faults import get_fault_models, StuckAtFault


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
            from elf_parser import ElfParser
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
  %(prog)s stuck-at 0x3fc93740 0              # Stuck-at-0
  %(prog)s stuck-at 0x3fc93740 1              # Stuck-at-1
  %(prog)s stuck-at 0x3fc93740 0xDEAD         # Stuck at custom value
  %(prog)s stuck-at led_enabled 0 --elf app.elf  # Use symbol name
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
    stuck_at_parser.add_argument('value', help='0, 1, or hex value')
    stuck_at_parser.add_argument('--width', type=int, default=32,
                                 choices=[8, 16, 32], help='Bit width')
    stuck_at_parser.add_argument('--duration', type=float,
                                 help='Run duration in seconds')
    stuck_at_parser.add_argument('--elf', help='ELF file for symbol lookup')

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

        # Future models would be handled here
        # elif args.model == 'bit-flip':
        #     ...

        return 0

    except ValueError as e:
        print(f"[FI] Error: {e}")
        return 1
    except Exception as e:
        print(f"[FI] Error: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
