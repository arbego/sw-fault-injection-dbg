#!/usr/bin/env python3
"""
Fault Injector using the official openocd Python library.

Implements stuck-at faults using hardware watchpoints with server-side
event handlers for zero-latency response.
"""

import time
import argparse
from dataclasses import dataclass
from typing import Optional

from openocd import Client, WatchpointType, ResetType


@dataclass
class StuckAtFault:
    """A stuck-at fault configuration."""
    address: int
    value: int
    width: int = 32


class FaultInjector:
    """Fault injection using OpenOCD watchpoints with server-side event handlers."""
    
    def __init__(self, host: str = 'localhost', port: int = 6666):
        self.client = Client(host, port, timeout=10)
        self.fault: Optional[StuckAtFault] = None
    
    def connect(self):
        """Connect to OpenOCD."""
        self.client.connect()
    
    def close(self):
        """Clean up and close connection."""
        if self.fault:
            # Remove event handler and watchpoint
            self.client.execute("esp32s3.cpu0 configure -event halted {}")
            self.client.remove_all_watchpoints()
        self.client.close()
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, *args):
        self.close()
    
    def setup_stuck_at(self, address: int, value: int, width: int = 32):
        """
        Configure a stuck-at fault with server-side event handler.
        
        The TCL event handler runs directly in OpenOCD when the watchpoint
        fires, eliminating all network latency.
        
        Args:
            address: Memory address to target
            value: Value to stick at (0 for stuck-at-0, 0xFFFFFFFF for stuck-at-1)
            width: Access width in bits (8, 16, or 32)
        """
        self.fault = StuckAtFault(address=address, value=value, width=width)
        
        # Reset and let boot complete
        print("[FI] Resetting target...")
        self.client.reset(ResetType.RUN)
        time.sleep(2)
        
        # Halt to set up
        self.client.halt()
        
        # Initialize counters in OpenOCD global namespace
        self.client.execute("set ::fi_app_triggers 0")
        self.client.execute("set ::fi_sys_triggers 0")
        self.client.execute(f"set ::fi_addr 0x{address:08x}")
        self.client.execute(f"set ::fi_value {value}")
        self.client.execute(f"set ::fi_width {width}")
        
        # Set initial stuck value
        self.client.write_memory(address, [value], width)
        
        # Verify
        val = self.client.read_memory(address, 1, width)[0]
        print(f"[FI] Set 0x{address:08x} = 0x{val:08x}")
        
        # Create TCL event handler that runs when target halts
        # This runs entirely inside OpenOCD - zero network latency!
        # Skip the store instruction (PC += 2) to prevent the write
        handler_script = f'''
        esp32s3.cpu0 configure -event halted {{
            # Read PC register and skip the store instruction
            set pc [reg pc]
            regexp {{0x([0-9a-fA-F]+)}} $pc -> pc_hex
            set pc_int [expr 0x$pc_hex]
            
            # Skip 2 bytes (narrow store instruction)
            set new_pc [expr {{$pc_int + 2}}]
            reg pc [format "0x%08x" $new_pc]
            
            incr ::fi_app_triggers
            resume
        }}
        '''
        
        print("[FI] Installing server-side event handler...")
        self.client.execute(handler_script)
        
        # Set write watchpoint
        self.client.add_watchpoint(address, width // 8, WatchpointType.WRITE)
        print(f"[FI] Watchpoint set at 0x{address:08x}")
    
    def run(self, duration: Optional[float] = None):
        """
        Run with fault injection active.
        
        The event handler runs entirely in OpenOCD, so we just wait
        and periodically check stats.
        
        Args:
            duration: How long to run in seconds (None = until Ctrl+C)
        """
        if not self.fault:
            print("[FI] No fault configured")
            return
        
        print(f"[FI] Running... (Ctrl+C to stop)")
        self.client.resume()
        
        start = time.time()
        try:
            while True:
                time.sleep(1)  # Just wait - event handler does the work
                
                if duration and (time.time() - start) >= duration:
                    break
                    
        except KeyboardInterrupt:
            print("\n[FI] Interrupted")
        
        # Stop and get stats
        self.client.halt()
        
        # Read trigger counts from OpenOCD
        app_triggers = int(self.client.execute("set ::fi_app_triggers"))
        
        print(f"\n[FI] Triggers: {app_triggers}")
    
    def get_stats(self) -> tuple:
        """Get current trigger counts."""
        app = int(self.client.execute("set ::fi_app_triggers"))
        sys = int(self.client.execute("set ::fi_sys_triggers"))
        return app, sys


def main():
    parser = argparse.ArgumentParser(
        description='Stuck-at Fault Injector',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s 0x3fc93740 0              # Stuck-at-0
  %(prog)s 0x3fc93740 1              # Stuck-at-1 (all bits = 1)
  %(prog)s led_enabled 0 --elf app.elf  # Use symbol name
  %(prog)s 0x3fc93740 0xDEAD --width 16  # 16-bit stuck value
        """
    )
    parser.add_argument('address', help='Memory address (hex) or symbol name')
    parser.add_argument('value', help='Stuck value: 0, 1, or hex value')
    parser.add_argument('--host', default='localhost', help='OpenOCD host')
    parser.add_argument('--port', type=int, default=6666, help='OpenOCD TCL port')
    parser.add_argument('--width', type=int, default=32, choices=[8, 16, 32],
                        help='Access width in bits')
    parser.add_argument('--duration', type=float, help='Run duration in seconds')
    parser.add_argument('--elf', help='ELF file for symbol lookup')
    
    args = parser.parse_args()
    
    # Resolve address
    try:
        address = int(args.address, 16)
    except ValueError:
        # Try symbol lookup
        if args.elf:
            from elf_parser import ElfParser
            elf = ElfParser(args.elf)
            sym = elf.find_symbol_by_name(args.address)
            if sym:
                address = sym.address
                print(f"[FI] Resolved '{args.address}' -> 0x{address:08x}")
            else:
                print(f"[FI] Symbol '{args.address}' not found")
                return 1
        else:
            print(f"[FI] '{args.address}' is not a hex address. Use --elf for symbol lookup.")
            return 1
    
    # Resolve value
    if args.value == '0':
        value = 0
    elif args.value == '1':
        value = (1 << args.width) - 1  # All bits set
    else:
        value = int(args.value, 16)
    
    # Run fault injection
    with FaultInjector(args.host, args.port) as fi:
        fi.setup_stuck_at(address, value, args.width)
        fi.run(args.duration)
    
    return 0


if __name__ == '__main__':
    exit(main())
