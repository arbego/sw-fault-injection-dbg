"""Public API for ESP32-S3 fault experiments."""

__all__ = ["FaultHandle", "FaultController"]


def __getattr__(name):
    # Keep standalone subcommands from preloading the module they will execute.
    if name in __all__:
        from .controller import FaultHandle, FaultController

        globals().update(FaultHandle=FaultHandle, FaultController=FaultController)
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
