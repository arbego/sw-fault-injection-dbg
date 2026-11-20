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

from openocd import Client

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

    Examples:
    - stuck-at 0: Value forced to 0
    - stuck-at 1: Value forced to 1
    - stuck-at 0xFF: Value forced to 255
    - stuck-at 0xDEADBEEF: Value forced to 0xDEADBEEF
    """

    name = "stuck-at"
    description = "Force memory location to a fixed value"

    def __init__(self, client: Client):
        super().__init__(client)
        self.config: Optional[StuckAtConfig] = None
        self._triggers = 0

    def setup(self, address: int, value: int = 0, width: int = 32,
              reset: bool = True, **kwargs) -> None:
        """
        Configure stuck-at fault.

        Args:
            address: Target memory address
            value: Value to stick at (0 for stuck-at-0, 0xFFFFFFFF for stuck-at-1)
            width: Access width in bits (8, 16, or 32)
            reset: Reset the target before configuring the fault (default True)
        """
        if self._active:
            raise RuntimeError("Remove this fault before configuring it again")
        self.client.execute("if {![array exists ::fi_stuck_hits]} {array set ::fi_stuck_hits {}}")
        active_count = int(self.client.execute("array size ::fi_stuck_hits"))
        if self.client.execute(f"info exists ::fi_stuck_hits({address})") == "1":
            raise ValueError(f"A stuck-at fault already targets 0x{address:08x}")
        if reset and active_count:
            raise ValueError("Use reset=False when adding another active stuck-at fault")
        super().setup(address, reset=reset)
        self.config = StuckAtConfig(address=address, width=width, value=value)
        was_running = self.client.execute("esp32s3.cpu0 curstate") == "running"
        self._pause_handler()

        self._triggers = 0
        watchpoint_set = False
        try:
            # Allocate before changing memory: exhaustion must not corrupt the
            # rejected target or disturb another active fault.
            status = self.client.execute(
                f"catch {{wp 0x{address:x} {width // 8} w}} ::fi_stuck_error"
            )
            if status != "0":
                detail = self.client.execute("set ::fi_stuck_error") or "OpenOCD rejected allocation"
                raise RuntimeError(f"Could not register hardware watchpoint at 0x{address:08x}: {detail}")
            watchpoint_set = True
            self.client.execute(f"set ::fi_stuck_hits({address}) 0")
            self._active = True
            if not active_count:
                self._init_counters()
            self.client.write_memory(address, [value], width)
            actual = self.client.read_memory(address, 1, width)[0]
            if actual != value:
                raise RuntimeError(f"Could not set 0x{address:08x} to {value}: read back {actual}")
            print(f"[{self.name}] Set 0x{address:08x} = 0x{actual:08x}")

            print(f"[{self.name}] Watchpoint set at 0x{address:08x}")
            self._install_handler()
        except BaseException:
            # Already paused: do not service the same pending write twice.
            if watchpoint_set:
                self.client.remove_watchpoint(address)
                self.client.execute(f"unset -nocomplain ::fi_stuck_hits({address})")
            self._active = False
            if active_count:
                self.client.execute(self.get_tcl_handler())
            if was_running:
                self.client.resume()
            raise

    def _pause_handler(self) -> None:
        """Pause without letting a pending write escape an existing fault."""
        self.client.execute("esp32s3.cpu0 configure -event halted {}")
        self.client.halt()
        self.client.execute(
            "if {[info commands ::fi_stuck_handle] ne {}} {::fi_stuck_handle 0}"
        )

    def get_tcl_handler(self) -> str:
        """
        Return the shared handler for active stuck-at watchpoints.

        S32I.N encodes the base register and offset of the pending write.
        Decode its address to count hits for the correct fault, then skip it.
        Other store instruction formats remain unsupported.
        """
        return '''
        proc ::fi_stuck_handle {resume_target} {
            set registers [get_reg {pc debugcause}]
            if {([dict get $registers debugcause] & 0x04) == 0} { return 0 }
            set pc [dict get $registers pc]
            set bytes [read_memory $pc 8 2]
            set instruction [expr {[lindex $bytes 0] | ([lindex $bytes 1] << 8)}]
            if {($instruction & 0x0f) != 9} {
                echo "stuck-at: unsupported store instruction at $pc"
                return 0
            }

            # S32I.N: bits 8..11 select the base register; bits 12..15
            # are the word offset. The instruction is two bytes long.
            set base_register [format "a%d" [expr {($instruction >> 8) & 0x0f}]]
            set base [dict get [get_reg [list $base_register]] $base_register]
            set address [expr {($base + (($instruction >> 12) & 0x0f) * 4) & 0xffffffff}]
            if {![info exists ::fi_stuck_hits($address)]} { return 0 }

            set_reg [list pc [expr {$pc + 2}]]
            incr ::fi_stuck_hits($address)
            incr ::fi_triggers
            if {$resume_target} { resume }
            return 1
        }
        esp32s3.cpu0 configure -event halted {::fi_stuck_handle 1}
        '''

    def get_stats(self) -> dict:
        """Get stuck-at specific statistics."""
        if self._active:
            self._triggers = int(self.client.execute(f"set ::fi_stuck_hits({self.config.address})"))
        stats = {"triggers": self._triggers}
        if self.config:
            stats["address"] = f"0x{self.config.address:08x}"
            stats["stuck_value"] = f"0x{self.config.value:08x}"
        return stats

    def cleanup(self) -> None:
        """Remove this address's watchpoint, keeping other stuck-at faults active."""
        if not self._active:
            return
        was_running = self.client.execute("esp32s3.cpu0 curstate") == "running"
        self._pause_handler()
        self._triggers = int(self.client.execute(f"set ::fi_stuck_hits({self.config.address})"))
        self.client.remove_watchpoint(self.config.address)
        self.client.execute(f"unset ::fi_stuck_hits({self.config.address})")
        self._active = False
        if int(self.client.execute("array size ::fi_stuck_hits")):
            self.client.execute(self.get_tcl_handler())
        if was_running:
            self.client.resume()

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
            value_str: Decimal or hex string (0x prefix for hex)
            width: Bit width (unused, kept for API compatibility)

        Returns:
            Integer value
        """
        if value_str.startswith('0x') or value_str.startswith('0X'):
            return int(value_str, 16)
        else:
            return int(value_str)
