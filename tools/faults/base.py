#!/usr/bin/env python3
"""
Base class for fault models.

All fault models inherit from FaultModel and implement their specific
fault injection logic via TCL event handlers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from openocd import Client, WatchpointType, ResetType


@dataclass
class FaultConfig:
    """Base fault configuration."""
    address: int
    width: int = 32  # bits


class FaultModel(ABC):
    """
    Abstract base class for fault injection models.

    Each fault model implements:
    - name: Human-readable name
    - description: What the fault does
    - setup(): Configure the fault (watchpoints, handlers, etc.)
    - get_stats(): Return trigger statistics
    """

    name: str = "base"
    description: str = "Base fault model"

    def __init__(self, client: Client):
        """
        Initialize fault model with OpenOCD client.

        Args:
            client: Connected OpenOCD client instance
        """
        self.client = client
        self.config: Optional[FaultConfig] = None
        self._active = False

    @abstractmethod
    def setup(self, address: int, **kwargs) -> None:
        """
        Configure and arm the fault.

        Args:
            address: Target memory address
            **kwargs: Model-specific parameters
        """
        pass

    @abstractmethod
    def get_tcl_handler(self) -> str:
        """
        Return the TCL event handler script.

        This script runs inside OpenOCD when the watchpoint fires.
        It should be self-contained and handle the fault injection
        with zero network latency.

        Returns:
            TCL script string
        """
        pass

    def get_stats(self) -> dict:
        """
        Get fault injection statistics.

        Returns:
            Dictionary with stats (triggers, etc.)
        """
        try:
            triggers = int(self.client.execute("set ::fi_triggers"))
        except:
            triggers = 0
        return {"triggers": triggers}

    def cleanup(self) -> None:
        """Clean up fault injection state."""
        # Remove event handler
        self.client.execute("esp32s3.cpu0 configure -event halted {}")
        # Remove watchpoints
        self.client.remove_all_watchpoints()
        self._active = False

    def _reset_target(self, wait: float = 2.0) -> None:
        """Reset target and wait for boot."""
        print(f"[{self.name}] Resetting target...")
        self.client.reset(ResetType.RUN)
        import time
        time.sleep(wait)

    def _init_counters(self) -> None:
        """Initialize TCL counter variables."""
        self.client.execute("set ::fi_triggers 0")

    def _install_handler(self) -> None:
        """Install the TCL event handler."""
        handler = self.get_tcl_handler()
        print(f"[{self.name}] Installing event handler...")
        self.client.execute(handler)

    def _set_watchpoint(self, address: int, size: int,
                        wp_type: WatchpointType = WatchpointType.WRITE) -> None:
        """Set a hardware watchpoint."""
        self.client.add_watchpoint(address, size, wp_type)
        print(f"[{self.name}] Watchpoint set at 0x{address:08x}")


def get_fault_models() -> dict:
    """
    Discover available fault models.

    Returns:
        Dictionary mapping model names to classes
    """
    from . import stuck_at

    models = {}

    # Register stuck-at model
    models[stuck_at.StuckAtFault.name] = stuck_at.StuckAtFault

    # Future models can be added here or discovered dynamically
    # from . import bit_flip
    # models[bit_flip.BitFlipFault.name] = bit_flip.BitFlipFault

    return models
