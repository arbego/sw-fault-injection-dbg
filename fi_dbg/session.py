"""Python API shared by fault experiments and hardware tests."""

import atexit
from contextlib import contextmanager
from pathlib import Path
import json
import os
import random
import secrets
import time
from types import MappingProxyType

from tools.elf_parser import ElfParser
from tools.faults import BitFlipFault, StuckAtFault
from .hardware import OpenOCDClient, build_app, claim_target_lock, flash_app, resume_if_halted
from .openocd_server import start_openocd


class FaultHandle:
    """A named fault that can be removed explicitly or used as a context manager."""

    def __init__(self, session, model, name, address):
        self._session = session
        self._model = model
        self.name = name
        self.address = address

    @property
    def active(self):
        return self._model._active

    @property
    def stats(self):
        return self._model.get_stats()

    @property
    def injections(self):
        return self.stats.get("injections", [])

    def remove(self):
        self._session.remove(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.remove()


class FaultSession:
    """Own the target, fault handles, observations, random seed, and OpenOCD.

    Supplying project builds and flashes that ESP-IDF application. With only elf,
    the session operates on the application already flashed to the target.
    """

    def __init__(self, elf, *, project=None, host=None, port=None, seed=None,
                 log_prefix="session"):
        self.elf_path = Path(elf).expanduser().resolve()
        self.project = Path(project).expanduser().resolve() if project else None
        self.host = host or os.environ.get("FI_OPENOCD_HOST", "localhost")
        self.port = int(port if port is not None else os.environ.get("FI_OPENOCD_PORT", "6666"))
        if not 1 <= self.port <= 65535:
            raise ValueError("OpenOCD port must be between 1 and 65535")
        self.seed = secrets.randbits(64) if seed is None else seed
        self.random = random.Random(self.seed)
        self.log_prefix = log_prefix
        self.history = []
        self._client = None
        self._server = None
        self._lock = None
        self._elf = None
        self._faults = {}
        self._next_id = 0
        self._pause_depth = 0
        self._started = time.monotonic()
        self._closed = False

    @property
    def faults(self):
        """Currently active faults, indexed by their names."""
        return MappingProxyType(self._faults)

    def _record(self, operation, **fields):
        self.history.append({"elapsed": time.monotonic() - self._started,
                             "operation": operation, **fields})

    def log(self, message, *, tag=None, operation="message", **fields):
        self._record(operation, message=message, **fields)
        prefix = f"{self.log_prefix}: {tag}" if tag else self.log_prefix
        print(f"[{prefix}] {message}", flush=True)

    def open(self):
        if self._client is not None or self._closed:
            raise RuntimeError("Create a new FaultSession for each session")
        if self.project and not (self.project / "CMakeLists.txt").is_file():
            raise ValueError(f"Not an ESP-IDF project: {self.project}")
        if not self.project:
            self._elf = ElfParser(str(self.elf_path))
        self._lock = claim_target_lock(self.host, self.port)
        atexit.register(self.close)
        try:
            build_dir = self.project / "build" if self.project else self.elf_path.parent
            self._server = start_openocd(self.host, self.port, build_dir)
            if self.project:
                build_app(self.project)
                description = json.loads((build_dir / "project_description.json").read_text())
                built_elf = (build_dir / description["app_elf"]).resolve()
                if self.elf_path != built_elf:
                    raise ValueError(f"ELF must match the application being flashed: {built_elf}")
                self._elf = ElfParser(str(self.elf_path))
                flash_app(self.host, self.port, build_dir)
            client = OpenOCDClient(self.host, self.port, timeout=10)
            client.connect()
            self._client = client
            targets = client.execute("target names")
            if "esp32s3.cpu0" not in targets.split():
                raise RuntimeError("OpenOCD does not expose esp32s3.cpu0")
            client.execute("targets esp32s3.cpu0")
            resume_if_halted(client)
            self.log(f"Random seed: {self.seed}", operation="open", seed=self.seed,
                     elf=str(self.elf_path), host=self.host, port=self.port)
            return self
        except BaseException:
            try:
                self.close()
            except Exception as error:
                self.log(f"Cleanup also failed: {error}", operation="cleanup_error")
            raise

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.close()
        except Exception as error:
            if exc is None:
                raise
            self.log(f"Cleanup also failed: {error}", operation="cleanup_error")

    def close(self):
        if self._closed:
            return
        errors = []
        try:
            self.remove_all_faults()
        except Exception as error:
            errors.append(error)
        if self._client is not None:
            try:
                resume_if_halted(self._client)
            except Exception as error:
                errors.append(error)
            try:
                self._client.close()
            except Exception as error:
                errors.append(error)
            self._client = None
        try:
            if self._server is not None:
                self._server.stop()
        except Exception as error:
            errors.append(error)
        finally:
            if self._lock is not None:
                self._lock.close()
                self._lock = None
            self._closed = True
            atexit.unregister(self.close)
        self._record("close", errors=[str(error) for error in errors])
        if errors:
            raise RuntimeError("Session cleanup failed: " + "; ".join(map(str, errors))) from errors[0]

    def _require_open(self):
        if self._client is None:
            raise RuntimeError("Open FaultSession with a with statement or open() first")

    def symbol(self, name):
        self._require_open()
        symbol = self._elf.find_symbol_by_name(name)
        if symbol is None:
            raise ValueError(f"Symbol {name!r} not found in {self.elf_path}")
        return symbol

    def address(self, target, *, offset=0):
        self._require_open()
        if isinstance(target, int):
            address = target
        elif target.startswith(("0x", "0X")):
            address = int(target, 16)
        else:
            address = self.symbol(target).address
        address += offset
        if not 0 <= address <= 0xFFFFFFFF:
            raise ValueError("Target address is outside the 32-bit address space")
        return address

    @staticmethod
    def _validate_access(width, count=1):
        if width not in (8, 16, 32) or not isinstance(count, int) or count < 1:
            raise ValueError("Use width 8, 16, or 32 and a positive integer count")

    @contextmanager
    def paused(self):
        """Take a consistent snapshot without releasing any pending stuck-at write."""
        self._require_open()
        if self._pause_depth == 0:
            active = next((handle for handle in self._faults.values()
                           if isinstance(handle._model, StuckAtFault) and handle.active), None)
            if active:
                active._model._pause_handler()
            else:
                self._client.halt()
            if self._client.execute("esp32s3.cpu0 curstate") != "halted":
                raise RuntimeError("Could not pause the target for inspection")
        self._pause_depth += 1
        try:
            yield
        finally:
            self._pause_depth -= 1
            if self._pause_depth == 0:
                active = next((handle for handle in self._faults.values()
                               if isinstance(handle._model, StuckAtFault) and handle.active), None)
                if active:
                    self._client.execute(active._model.get_tcl_handler())
                resume_if_halted(self._client)

    def read_memory(self, target, count=1, *, width=32, offset=0):
        self._validate_access(width, count)
        address = self.address(target, offset=offset)
        with self.paused():
            values = self._client.read_memory(address, count, width)
        self._record("read", address=address, width=width, values=values)
        return values

    def read(self, target, *, width=32, offset=0):
        return self.read_memory(target, width=width, offset=offset)[0]

    def read_many(self, *targets, width=32):
        self._validate_access(width)
        addresses = [self.address(target) for target in targets]
        with self.paused():
            values = tuple(self._client.read_memory(address, 1, width)[0] for address in addresses)
        self._record("read_many", addresses=addresses, width=width, values=values)
        return values

    def _check_unfaulted(self, address, size):
        for handle in self._faults.values():
            if isinstance(handle._model, StuckAtFault) and handle.active:
                end = handle.address + handle._model.config.width // 8
                if address < end and handle.address < address + size:
                    raise ValueError(f"Remove {handle.name!r} before modifying its target")

    def write_memory(self, target, values, *, width=32, offset=0):
        values = list(values)
        self._validate_access(width, len(values))
        if any(not isinstance(value, int) or not 0 <= value < 1 << width for value in values):
            raise ValueError(f"Values must fit in {width} bits")
        address = self.address(target, offset=offset)
        self._check_unfaulted(address, len(values) * width // 8)
        with self.paused():
            self._client.write_memory(address, values, width)
        self._record("write", address=address, width=width, values=values)

    def write(self, target, value, *, width=32, offset=0):
        self.write_memory(target, [value], width=width, offset=offset)

    def _fault_name(self, model_name, name):
        self._require_open()
        if name is None:
            self._next_id += 1
            name = f"{model_name}-{self._next_id}"
        if not isinstance(name, str) or not name or name in self._faults:
            raise ValueError(f"Fault name must be nonempty and unique: {name!r}")
        return name

    def _check_reset(self, reset):
        if reset and self._faults:
            raise ValueError("Remove active faults before resetting the target")

    def stuck_at(self, target, value, *, width=32, reset=False, name=None):
        self._validate_access(width)
        if not isinstance(value, int) or not 0 <= value < 1 << width:
            raise ValueError(f"Stuck value must fit in {width} bits")
        address = self.address(target)
        name = self._fault_name("stuck-at", name)
        self._check_reset(reset)
        model = StuckAtFault(self._client)
        try:
            model.setup(address, value=value, width=width, reset=reset)
            handle = FaultHandle(self, model, name, address)
            self._faults[name] = handle
        except Exception as error:
            self.log(f"Could not add {name}: {error}", tag="stuck-at",
                     operation="fault_error", name=name)
            raise
        finally:
            if self._pause_depth == 0:
                resume_if_halted(self._client)
        self.log(f"Added {name}: 0x{address:08x} held at {value}", tag="stuck-at", operation="stuck_at",
                 name=name, address=address, value=value, width=width)
        return handle

    def bit_flip(self, target, *, bit=None, mask=None, random_bit=False, width=32,
                 count=1, interval=0.0, reset=False, offset=0, name=None):
        self._validate_access(width, count)
        if sum((bit is not None, mask is not None, bool(random_bit))) > 1:
            raise ValueError("Choose one of bit, mask, or random_bit")
        if bit is not None and (not isinstance(bit, int) or not 0 <= bit < width):
            raise ValueError(f"Bit must be between 0 and {width - 1}")
        if interval < 0:
            raise ValueError("Injection interval cannot be negative")
        if random_bit:
            mask = 1 << self.random.randrange(width)
        elif bit is not None:
            mask = 1 << bit
        elif mask is None:
            mask = 1
        if not isinstance(mask, int) or not 0 <= mask < 1 << width:
            raise ValueError(f"Mask must fit in {width} bits")
        address = self.address(target, offset=offset)
        self._check_unfaulted(address, width // 8)
        name = self._fault_name("bit-flip", name)
        self._check_reset(reset)
        model = BitFlipFault(self._client, pause=self.paused)
        model.setup(address, bit_mask=mask, width=width, count=count, interval=interval, reset=reset)
        handle = FaultHandle(self, model, name, address)
        self._faults[name] = handle
        try:
            model.run()
            self._record("bit_flip", name=name, address=address, mask=mask,
                         width=width, injections=handle.injections)
            return handle
        finally:
            handle.remove()

    def remove(self, fault):
        if isinstance(fault, str):
            fault = self._faults[fault]
        if not isinstance(fault, FaultHandle) or fault._session is not self:
            raise ValueError("The fault belongs to another session")
        if not fault.active:
            if self._faults.get(fault.name) is fault:
                self._faults.pop(fault.name)
            return
        try:
            fault._model.cleanup()
        finally:
            if self._pause_depth == 0:
                resume_if_halted(self._client)
        self._faults.pop(fault.name, None)
        self.log(f"Removed {fault.name}", tag=fault._model.name,
                 operation="remove", name=fault.name, stats=fault.stats)

    def remove_all_faults(self):
        errors = []
        for fault in reversed(tuple(self._faults.values())):
            try:
                fault.remove()
            except Exception as error:
                errors.append(f"{fault.name}: {error}")
        if errors:
            raise RuntimeError("; ".join(errors))

    def wait(self, seconds):
        self._require_open()
        if seconds < 0:
            raise ValueError("Wait duration cannot be negative")
        self._record("wait", seconds=seconds)
        time.sleep(seconds)

    def wait_until(self, predicate, *, timeout=5, interval=0.05, description="condition"):
        self._require_open()
        if timeout <= 0 or interval <= 0:
            raise ValueError("Timeout and polling interval must be positive")
        deadline = time.monotonic() + timeout
        while True:
            result = predicate()
            if result:
                self._record("wait_until", description=description)
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Timed out waiting for {description}")
            time.sleep(min(interval, remaining))

    def expect_value(self, target, value, *, samples=3, interval=0.2, width=32, tag=None):
        if not isinstance(samples, int) or samples < 1 or interval < 0:
            raise ValueError("Use a positive sample count and nonnegative interval")
        observed = []
        for sample in range(1, samples + 1):
            self.wait(interval)
            actual = self.read(target, width=width)
            observed.append(actual)
            if actual != value:
                message = f"{target}: expected {value}, read back {actual} (sample {sample}/{samples})"
                self.log(f"FAIL: {message}", tag=tag, operation="expect_value", expected=value, actual=actual)
                raise AssertionError(message)
            self.log(f"PASS: {target}, check {sample}/{samples}: read back {actual}.",
                     tag=tag, operation="expect_value", expected=value, actual=actual)
        return tuple(observed)

    def expect_increasing(self, target, *, over=0.2, min_delta=1, width=32, tag=None):
        if over < 0 or min_delta < 1:
            raise ValueError("Use a nonnegative observation time and positive minimum delta")
        before = self.read(target, width=width)
        self.wait(over)
        after = self.read(target, width=width)
        if after - before < min_delta:
            message = f"{target}: expected an increase of at least {min_delta}, observed {before} -> {after}"
            self.log(f"FAIL: {message}", tag=tag, operation="expect_increasing", before=before, after=after)
            raise AssertionError(message)
        self.log(f"PASS: {target} increased: {before} -> {after}.", tag=tag,
                 operation="expect_increasing", before=before, after=after)
        return before, after
