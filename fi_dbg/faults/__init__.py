"""
Fault injection models.

Each fault model is a self-contained implementation that defines:
- How to set up the fault (watchpoints, initial values)
- TCL event handler for zero-latency fault injection
- Statistics collection

Available models:
- stuck_at: Force memory to fixed value (stuck-at-0, stuck-at-1)
- bit_flip: Flip specific bits (transient fault, no watchpoints)

Future models:
- transient: Temporary value corruption
- delay: Add latency to memory access
"""

from .base import FaultModel, FaultConfig, get_fault_models
from .stuck_at import StuckAtFault, StuckAtConfig
from .bit_flip import BitFlipFault, BitFlipConfig

__all__ = [
    'FaultModel',
    'FaultConfig',
    'get_fault_models',
    'StuckAtFault',
    'StuckAtConfig',
    'BitFlipFault',
    'BitFlipConfig',
]
