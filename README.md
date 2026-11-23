## Setup

Connect the board's native USB Serial/JTAG port. The LED demonstration expects
an addressable RGB LED on GPIO48, as configured in `app/fi_dbg.c`.

Run the [bootstrap script](bootstrap.sh):

```bash
./bootstrap.sh
. .venv/activate-idf.sh
```

The script installs OS prerequisites through Homebrew on macOS or APT on
Debian/Ubuntu. It installs Homebrew if missing and requests Apple's Command Line
Tools installer if necessary; finish that installer and rerun the script if
prompted. System installation can ask for your sudo password or native installer
confirmation.

It then installs ESP-IDF v5.5.5 under `.esp/esp-idf` in this checkout if absent.
The ESP32-S3 toolchain, OpenOCD, download cache, and SDK Python environment live
under `.esp/tools`. The entire `.esp/` directory is ignored by Git. The script
also creates or reuses `.venv` with the project dependencies.
On Linux it installs OpenOCD's USB access rules as well. Python 3.10 or newer is
required; macOS installs Python 3.12 if a suitable interpreter is missing.
It does not build or flash firmware. Existing SDK directories are reused as-is.
The activation command must be sourced in your terminal after the script exits;
the script prints the correct path. The generated activation file also adds any
Homebrew/Python paths required by this installation.

On an already prepared host, or another Linux distribution, use
`./bootstrap.sh --skip-system-deps`. See
[Espressif's prerequisites](https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32s3/get-started/linux-macos-setup.html)
for manual setup on other distributions and
[Homebrew's installation documentation](https://docs.brew.sh/Installation) for its
platform requirements.

To reuse an existing ESP-IDF installation and tool directory instead:

```bash
export IDF_PATH="/path/to/esp-idf"
export IDF_TOOLS_PATH="/path/to/esp-tools"
./bootstrap.sh
. .venv/activate-idf.sh
```

Both overrides are optional; without them, the installation stays inside the
checkout. An inherited `IDF_PYTHON_ENV_PATH` is cleared so ESP-IDF selects its
Python environment from the configured tools directory.

ESP-IDF needs framework sources and native tools in addition to Python packages;
pip alone does not install the complete SDK. Builds use the host framework; a
container is unnecessary. Re-running the bootstrap reuses the SDK and virtual
environment without checking out another version over your existing SDK.

## 1. Run the hardware tests

```sh
.venv/bin/python -m unittest discover -s tests -t . -v
```

The suite automatically starts OpenOCD, builds and flashes `tests/app`, runs the
tests, and stops its server. It checks bit-flips across access widths and masks,
stuck-at enforcement and recovery, and simultaneous faults. Expect seven passing
tests.

## 2. Inject faults manually

First build and flash the LED application. This step is required after the tests,
which leave their dedicated test firmware on the board:

```sh
IDF_COMPONENT_MANAGER=1 .venv/bin/python - <<'PY'
from fi_dbg import FaultController

with FaultController(project=".", elf="build/fi_dbg.elf"):
    pass
PY
```

This uses JTAG for flashing and enables downloading the LED strip dependency
needed on a fresh checkout. The LED should now blink at approximately 5 Hz.

**Bit-flip: slow blinking from 5 Hz to 1 Hz.**

```sh
.venv/bin/python -m fi_dbg.inject bit-flip led_blink_frequency_hz --bit 2 --elf build/fi_dbg.elf
```

Flipping bit 2 changes the frequency from `5` (`0101`) to `1` (`0001`). The
command returns after injection; the LED continues blinking slowly.

**Stuck-at: hold the LED on for five seconds.**

```sh
.venv/bin/python -m fi_dbg.inject stuck-at led_enabled 1 --duration 5 --elf build/fi_dbg.elf
```

The LED stays on despite the firmware trying to toggle it. After five seconds,
the fault is removed and normal blinking resumes. Use value `0` to hold it off,
or omit `--duration` and press Ctrl+C to remove the fault manually.

See [test details](tests/README.md) and the [Python API and campaigns](fi_dbg/README.md)
for more options.
