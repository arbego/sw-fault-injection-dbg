#!/usr/bin/env python3
"""
Stuck-at fault model.

Implements stuck-at-0 and stuck-at-1 faults by:
1. Setting a hardware watchpoint on the target address
2. When a write is attempted, skip the store instruction (PC += 2)
3. This prevents the write from completing, keeping the stuck value

Works because Xtensa watchpoints fire BEFORE the store commits.
"""

from dataclasses import dataclass
from typing import Optional

from openocd import Client, WatchpointType

from .base import FaultModel, FaultConfig


@dataclass
class StuckAtConfig(FaultConfig):
    """Stuck-at fault configuration."""
    value: int = 0  # Value to stick at


class StuckAtFault(FaultModel):
    """
    Stuck-at fault model.

    Forces a memory location to remain at a fixed value by intercepting
    all writes and skipping the store instruction.

    Supports:
    - stuck-at-0: Value forced to 0
    - stuck-at-1: Value forced to all 1s
    - stuck-at-X: Value forced to any arbitrary value
    """

    name = "stuck-at"
    description = "Force memory location to a fixed value"

    def __init__(self, client: Client):
        super().__init__(client)
        self.config: Optional[StuckAtConfig] = None

    def setup(self, address: int, value: int = 0, width: int = 32, **kwargs) -> None:
        """
        Configure stuck-at fault.

        Args:
            address: Target memory address
            value: Value to stick at (0 for stuck-at-0, 0xFFFFFFFF for stuck-at-1)
            width: Access width in bits (8, 16, or 32)
        """
        self.config = StuckAtConfig(address=address, width=width, value=value)

        # Reset and wait for boot
        self._reset_target()

        # Halt to configure
        self.client.halt()

        # Initialize counters
        self._init_counters()

        # Store config in TCL namespace for handler access
        self.client.execute(f"set ::fi_addr 0x{address:08x}")
        self.client.execute(f"set ::fi_value {value}")
        self.client.execute(f"set ::fi_width {width}")

        # Set initial stuck value
        self.client.write_memory(address, [value], width)

        # Verify
        actual = self.client.read_memory(address, 1, width)[0]
        print(f"[{self.name}] Set 0x{address:08x} = 0x{actual:08x}")

        # Install event handler
        self._install_handler()

        # Set watchpoint
        self._set_watchpoint(address, width // 8, WatchpointType.WRITE)

        self._active = True

    def get_tcl_handler(self) -> str:
        """
        Return TCL handler that skips store instructions.

        The handler:
        1. Reads the current PC
        2. Adds 2 to skip the narrow store instruction (S32I.N)
        3. Resumes execution

        This prevents the store from completing, keeping the stuck value.
        """
        return '''
        esp32s3.cpu0 configure -event halted {
            # Read PC register
            set pc [reg pc]
            regexp {0x([0-9a-fA-F]+)} $pc -> pc_hex
            set pc_int [expr 0x$pc_hex]

            # Skip 2 bytes (narrow store instruction S32I.N)
            # Note: wide stores (S32I) are 3 bytes - may need detection
            set new_pc [expr {$pc_int + 2}]
            reg pc [format "0x%08x" $new_pc]

            # Increment trigger counter
            incr ::fi_triggers

            # Resume execution
            resume
        }
        '''

    def get_stats(self) -> dict:
        """Get stuck-at specific statistics."""
        stats = super().get_stats()
        if self.config:
            stats["address"] = f"0x{self.config.address:08x}"
            stats["stuck_value"] = f"0x{self.config.value:08x}"
        return stats

    @staticmethod
    def add_arguments(parser) -> None:
        """Add stuck-at specific CLI arguments."""
        parser.add_argument('value',
                           help='Stuck value: 0 (stuck-at-0), 1 (stuck-at-1), or hex')

    @staticmethod
    def parse_value(value_str: str, width: int) -> int:
        """
        Parse the value argument.

        Args:
            value_str: "0", "1", or hex string
            width: Bit width

        Returns:
            Integer value
        """
        if value_str == '0':
            return 0
        elif value_str == '1':
            return (1 << width) - 1  # All bits set
        else:
            return int(value_str, 16)
