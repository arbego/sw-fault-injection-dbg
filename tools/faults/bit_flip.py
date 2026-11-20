#!/usr/bin/env python3
"""
Bit-flip fault model.

Implements transient bit-flip faults by:
1. Halting the target
2. Reading the current value at the address
3. Flipping specified bit(s) via XOR
4. Writing the corrupted value back
5. Resuming execution

This is a "transient" fault - it happens once at injection time,
not continuously like stuck-at faults. No watchpoints are used.
"""

import time
import random
from dataclasses import dataclass
from typing import Optional, List

from openocd import Client

from .base import FaultModel, FaultConfig


@dataclass
class BitFlipConfig(FaultConfig):
    """Bit-flip fault configuration."""
    bit_mask: int = 1  # Which bits to flip (XOR mask)
    count: int = 1     # Number of injections
    interval: float = 0.0  # Seconds between injections (0 = single shot)


class BitFlipFault(FaultModel):
    """
    Bit-flip fault model.

    Flips specified bit(s) at a memory location. This simulates
    transient faults like SEU (Single Event Upset) caused by
    cosmic rays or voltage glitches.

    Unlike stuck-at, this is a one-shot (or repeated) injection
    that doesn't use watchpoints.

    Examples:
    - bit-flip 0x3fc93740 --bit 0        # Flip bit 0
    - bit-flip 0x3fc93740 --bit 7        # Flip bit 7
    - bit-flip 0x3fc93740 --mask 0xFF    # Flip lower 8 bits
    - bit-flip 0x3fc93740 --random       # Flip random bit
    """

    name = "bit-flip"
    description = "Flip bit(s) at memory location (transient fault)"

    def __init__(self, client: Client):
        super().__init__(client)
        self.config: Optional[BitFlipConfig] = None
        self.injections: List[dict] = []  # Log of injections

    def setup(self, address: int, bit_mask: int = 1, width: int = 32,
              count: int = 1, interval: float = 0.0,
              reset: bool = False, **kwargs) -> None:
        """
        Configure bit-flip fault.

        Args:
            address: Target memory address
            bit_mask: Bits to flip (XOR mask)
            width: Access width in bits (8, 16, or 32)
            count: Number of injections to perform
            interval: Seconds between injections (0 = immediate)
            reset: Reset the target before configuring the fault (default False)
        """
        super().setup(address, reset=reset)
        self.config = BitFlipConfig(
            address=address,
            width=width,
            bit_mask=bit_mask,
            count=count,
            interval=interval
        )

        print(f"[{self.name}] Target: 0x{address:08x}")
        print(f"[{self.name}] Bit mask: 0x{bit_mask:0{width//4}x}")
        print(f"[{self.name}] Injections: {count}")

        self._active = True

    def inject(self) -> dict:
        """
        Perform a single bit-flip injection.

        Returns:
            Dictionary with injection details
        """
        if not self.config:
            raise RuntimeError("Fault not configured")

        addr = self.config.address
        width = self.config.width
        mask = self.config.bit_mask

        # Halt target
        self.client.halt()

        # Read current value
        values = self.client.read_memory(addr, 1, width)
        old_value = values[0]

        # Flip bits
        new_value = old_value ^ mask

        # Write corrupted value
        self.client.write_memory(addr, [new_value], width)

        # Resume target
        self.client.resume()

        # Log injection
        injection = {
            "address": addr,
            "old_value": old_value,
            "new_value": new_value,
            "mask": mask,
            "timestamp": time.time()
        }
        self.injections.append(injection)

        print(f"[{self.name}] Flipped 0x{addr:08x}: 0x{old_value:0{width//4}x} -> 0x{new_value:0{width//4}x}")

        return injection

    def run(self, count: Optional[int] = None, interval: Optional[float] = None) -> List[dict]:
        """
        Run bit-flip injection(s).

        Args:
            count: Number of injections (default from config)
            interval: Seconds between injections (default from config)

        Returns:
            List of injection records
        """
        if not self.config:
            raise RuntimeError("Fault not configured")

        n = count if count is not None else self.config.count
        delay = interval if interval is not None else self.config.interval

        results = []
        for i in range(n):
            if i > 0 and delay > 0:
                time.sleep(delay)

            result = self.inject()
            results.append(result)

        return results

    def get_tcl_handler(self) -> str:
        """
        Not used for bit-flip (no watchpoints).

        Returns empty handler for API compatibility.
        """
        return ""

    def get_stats(self) -> dict:
        """Get bit-flip statistics."""
        return {
            "triggers": len(self.injections),
            "injections": self.injections
        }

    def cleanup(self) -> None:
        """Clean up (minimal for bit-flip)."""
        self._active = False

    @staticmethod
    def bit_to_mask(bit: int) -> int:
        """Convert bit number to mask."""
        return 1 << bit

    @staticmethod
    def random_bit_mask(width: int = 32) -> int:
        """Generate mask with single random bit set."""
        return 1 << random.randint(0, width - 1)

    @staticmethod
    def parse_mask(mask_str: str, width: int) -> int:
        """
        Parse mask from string.

        Args:
            mask_str: Bit number, hex mask, or "random"
            width: Bit width

        Returns:
            Integer mask
        """
        if mask_str == "random":
            return BitFlipFault.random_bit_mask(width)
        elif mask_str.startswith("0x") or mask_str.startswith("0X"):
            return int(mask_str, 16)
        else:
            # Assume it's a bit number
            bit = int(mask_str)
            return 1 << bit
