# Fault injection tests

Run all commands from the `fi-dbg` directory. The test application is a separate
ESP-IDF project; it does not use the LED application or require an LED component.

## Setup and default test run

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r tools/requirements.txt
.venv/bin/python -m unittest discover -s tests -t . -v
```

This suite contains only hardware tests. Connect the ESP32-S3 and start the host OpenOCD
server in another terminal before running the test command:

```sh
openocd -f board/esp32s3-builtin.cfg
```

Before the first hardware test, the framework automatically:

1. Builds `tests/app` using the host ESP-IDF installation.
2. Flashes the bootloader, partition table, and application from the generated
   `tests/app/build/flasher_args.json` through OpenOCD, with verification.
3. Resets the board and waits for the running test firmware to acknowledge a
   fresh startup request (a retained RAM signature alone is insufficient).
4. Uses `tests/app/build/fi_test_app.elf` for symbol addresses during testing.

This replaces the firmware currently on the connected target. Building and
flashing happen once per test process, shared by the hardware fault modules.
Build, connection, programming, and readiness failures fail the suite.
While OpenOCD flashes and verifies, the terminal shows a spinner and elapsed time.
Redirected output gets an update every five seconds. A completion line reports
the total time; no percentage is estimated from elapsed time.
An OS lock prevents overlapping hardware test runs on this host against the same
OpenOCD endpoint. A second run fails before building or flashing. The lock is
released when the test process exits; unrelated debugger clients are not covered.

ESP-IDF is located using `IDF_PATH`, `idf.py` on `PATH`, or the previous test
build's framework path. Common host installation locations, including the
ESPHome framework cache on this host, are also checked. Set `IDF_PATH` explicitly
to select an installation. ESP-IDF's `export.sh` runs in a child shell so its
Python environment is independent of the test runner's `.venv`.

The default OpenOCD connection is `localhost:6666` (the TCL port). Override it as
needed:

```sh
FI_OPENOCD_HOST=localhost FI_OPENOCD_PORT=6666 \
.venv/bin/python -m unittest discover -s tests -t . -v
```

OpenOCD must be able to read the test build directory at the same absolute path
as the test runner. For a remote OpenOCD server, share the directory at that path.
The ELF is always taken from the automatically built application; `FI_TEST_ELF`
is no longer used.

## Build the target application manually

Activate the existing host ESP-IDF environment, then build:

```sh
export IDF_PATH="$HOME/Library/Caches/esphome/idf/frameworks/5.5.5"
export IDF_PYTHON_ENV_PATH="$HOME/.espressif/python_env/idf5.5_py3.14_env"
. "$IDF_PATH/export.sh"
IDF_COMPONENT_MANAGER=0 idf.py -C tests/app -DIDF_TARGET=esp32s3 build
```

Adjust the two environment paths if ESP-IDF is installed elsewhere. The component
manager is unnecessary because this application only uses bundled IDF components.
The build produces `tests/app/build/fi_test_app.elf` and `fi_test_app.bin`.

Manual flashing is unnecessary for tests; the hardware fixture handles it over
JTAG before running assertions.

The application runs its test task on core 0. `main/modules/bit_flip.c` owns a
three-word buffer: guard word, injection target, guard word. The firmware never
overwrites that buffer. Instead, it publishes the target value to
`fi_bit_flip_observed` every 10 ms. A heartbeat and a protocol signature let the
host check that the test firmware is running.

## Run the hardware bit-flip module

To build, flash, and run only the hardware bit-flip module:

```sh
.venv/bin/python -m unittest tests.hardware.test_bit_flip -v
```

The hardware suite calls the production `BitFlipFault` module and verifies:

- Every bit in both directions for 8-, 16-, and 32-bit accesses.
- Zero, multiple-bit, full-width, and random single-bit masks.
- Repeated flips: two restore the initial value; three flip it again.
- Memory readback, unchanged neighboring bytes and guard words, and injection logs.
- A fresh firmware heartbeat and the value observed by the running application.

Run with no other debugger or injector controlling the target. Tests resume
execution during cleanup.

For another fault type, add a target module under `app/main/modules/`, register
its source in `app/main/CMakeLists.txt`, call it from the test task, and add a
corresponding Python test module under `hardware/`. Hardware modules
should call `tests.hardware.support.prepare_target(host, port)` in their class
setup to reuse the common build/flash fixture and matching ELF.
