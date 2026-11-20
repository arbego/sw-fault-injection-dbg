# Fault injection tests

Run all commands from the `fi-dbg` directory. The test application is a separate
ESP-IDF project; it does not use the LED application or require an LED component.

## Setup and default test run

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r tools/requirements.txt
.venv/bin/python -m unittest discover -s tests -t . -v
```

This suite contains only hardware tests. Connect the ESP32-S3 and run the test
command. Each test session starts a fresh local OpenOCD server using
`board/esp32s3-builtin.cfg`. If OpenOCD is already running at the configured local
endpoint, the framework shuts it down first and waits for the port to close.
It waits for OpenOCD to respond before flashing. Startup failures include the
server log, also saved as `tests/app/build/openocd.log`.

Servers started by the framework are owned by a separate supervisor that watches
a pipe held by the test runner. When the runner exits, the supervisor stops
OpenOCD, including after test failures, Ctrl+C, SIGTERM, SIGHUP, SIGKILL, or a
runner crash. OpenOCD is force-stopped if it does not terminate within five
seconds. The supervisor runs in a separate process group so a signal sent to the
test runner's group does not disable this cleanup. A configured remote server
must already be running and is left running; the framework never starts or
stops a server remotely.

Automatic startup finds Espressif OpenOCD under `IDF_TOOLS_PATH` (default
`~/.espressif`), `~/esp-tools/openocd`, or `PATH`. Override the executable with
`FI_OPENOCD_BIN` and its scripts directory with `OPENOCD_SCRIPTS` if needed.
Managed servers bind to the configured loopback address and expose only the TCL
port; GDB and telnet ports are disabled.

Before the first hardware test, the framework automatically:

1. Stops the existing local OpenOCD server, if present, and starts a supervised
   server. This also ensures cleanup when the subsequent build fails.
2. Builds `tests/app` using the host ESP-IDF installation.
3. Flashes the bootloader, partition table, and application from the generated
   `tests/app/build/flasher_args.json` through OpenOCD, with verification.
4. Resets the board and waits for the running test firmware to acknowledge a
   fresh startup request (a retained RAM signature alone is insufficient).
5. Uses `tests/app/build/fi_test_app.elf` for symbol addresses during testing.

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

## Minimal stuck-at test

```sh
.venv/bin/python -m unittest tests.hardware.test_stuck_at -v
```

This is one case with one write attempt. The firmware module
`app/main/modules/stuck_at.c` waits for a request, tries to write `1` to
`fi_stuck_at_value`, then clears the request. The test enables stuck-at-zero,
sends the request, and checks that the attempt finished while the value stayed
`0`. It uses the existing `StuckAtFault` implementation.

For another fault type, add a target module under `app/main/modules/`, register
its source in `app/main/CMakeLists.txt`, call it from the test task, and add a
corresponding Python test module under `hardware/`. Hardware modules
should call `tests.hardware.support.prepare_target(host, port)` in their class
setup to reuse the common build/flash fixture and matching ELF.
