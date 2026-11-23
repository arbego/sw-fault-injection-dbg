#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./bootstrap.sh [--skip-system-deps]

Install OS prerequisites (Homebrew on macOS, APT on Debian/Ubuntu), ESP-IDF
v5.5.5 and its ESP32-S3 tools, then create/reuse .venv and install Python
requirements. No firmware is built or flashed.

By default, the SDK is installed at <checkout>/.esp/esp-idf and its toolchain,
downloads, and SDK Python environment at <checkout>/.esp/tools.
IDF_PATH and IDF_TOOLS_PATH can override these locations. Existing SDK
directories are reused as-is.
Use --skip-system-deps on an already prepared host or another Linux distribution.
System installation can prompt for sudo or native installer confirmation.
EOF
}

FI_BOOTSTRAP_SYSTEM_DEPS=1
FI_BOOTSTRAP_PATH_PREFIX=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --skip-system-deps) FI_BOOTSTRAP_SYSTEM_DEPS=0 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done

as_root() {
    if [[ $EUID -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        printf 'System package installation needs root or sudo.\n' >&2
        return 1
    fi
}

prepend_path() {
    export PATH="$1:$PATH"
    FI_BOOTSTRAP_PATH_PREFIX="$1${FI_BOOTSTRAP_PATH_PREFIX:+:$FI_BOOTSTRAP_PATH_PREFIX}"
}

find_brew() {
    if command -v brew >/dev/null 2>&1; then
        command -v brew
    elif [[ -x /opt/homebrew/bin/brew ]]; then
        printf '%s\n' /opt/homebrew/bin/brew
    elif [[ -x /usr/local/bin/brew ]]; then
        printf '%s\n' /usr/local/bin/brew
    else
        return 1
    fi
}

install_system_dependencies() {
    case "$(uname -s)" in
        Darwin)
            local brew_command installer brew_prefix
            if ! brew_command="$(find_brew)"; then
                printf '[bootstrap] Installing Homebrew using its official installer.\n'
                installer="$(mktemp "${TMPDIR:-/tmp}/fi-bootstrap-brew.XXXXXX")"
                if ! curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$installer" ||
                   ! /bin/bash "$installer"; then
                    rm -f "$installer"
                    return 1
                fi
                rm -f "$installer"
                brew_command="$(find_brew)"
            fi
            if ! xcode-select -p >/dev/null 2>&1; then
                xcode-select --install
                printf 'Finish the Apple Command Line Tools installer, then rerun bootstrap.sh.\n' >&2
                return 1
            fi
            brew_prefix="$("$brew_command" --prefix)"
            prepend_path "$brew_prefix/bin:$brew_prefix/sbin"
            printf '[bootstrap] Installing macOS prerequisites through Homebrew.\n'
            "$brew_command" install git cmake ninja ccache dfu-util libusb
            if ! command -v python3 >/dev/null 2>&1 ||
               ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
                "$brew_command" install python@3.12
                prepend_path "$("$brew_command" --prefix python@3.12)/libexec/bin"
            fi
            ;;
        Linux)
            if ! command -v apt-get >/dev/null 2>&1; then
                printf 'Automatic Linux prerequisites currently support APT. Prepare dependencies and use --skip-system-deps on other distributions.\n' >&2
                return 1
            fi
            printf '[bootstrap] Installing Debian/Ubuntu prerequisites through APT.\n'
            as_root apt-get update
            as_root apt-get install -y --no-install-recommends --no-upgrade \
                git wget curl flex bison gperf build-essential python3 python3-pip \
                python3-venv cmake ninja-build ccache libffi-dev libssl-dev \
                dfu-util libusb-1.0-0 libusb-1.0-0-dev udev
            ;;
        *)
            printf 'Automatic prerequisites support macOS and Debian/Ubuntu. Use --skip-system-deps on a prepared host.\n' >&2
            return 1
            ;;
    esac
}

if [[ $FI_BOOTSTRAP_SYSTEM_DEPS -eq 1 ]]; then
    install_system_dependencies
fi

for prerequisite in git python3; do
    if ! command -v "$prerequisite" >/dev/null 2>&1; then
        printf 'Missing prerequisite: %s. See the README setup instructions.\n' "$prerequisite" >&2
        exit 1
    fi
done

FI_BOOTSTRAP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
FI_BOOTSTRAP_PYTHON="$(command -v python3)"
FI_BOOTSTRAP_VERSION="v5.5.5"
FI_BOOTSTRAP_ESP="$FI_BOOTSTRAP_ROOT/.esp"
FI_BOOTSTRAP_IDF="${IDF_PATH:-$FI_BOOTSTRAP_ESP/esp-idf}"
FI_BOOTSTRAP_TOOLS="${IDF_TOOLS_PATH:-$FI_BOOTSTRAP_ESP/tools}"

check_python() {
    "$1" -c 'import sys; sys.exit("Python 3.10 or newer is required.") if sys.version_info < (3, 10) else None'
}

check_python "$FI_BOOTSTRAP_PYTHON"
FI_BOOTSTRAP_IDF="$("$FI_BOOTSTRAP_PYTHON" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$FI_BOOTSTRAP_IDF")"
FI_BOOTSTRAP_TOOLS="$("$FI_BOOTSTRAP_PYTHON" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$FI_BOOTSTRAP_TOOLS")"

if [[ -e "$FI_BOOTSTRAP_IDF" ]]; then
    for sdk_file in install.sh export.sh tools/idf.py; do
        if [[ ! -f "$FI_BOOTSTRAP_IDF/$sdk_file" ]]; then
            printf 'Existing path is not a complete ESP-IDF installation: %s\n' "$FI_BOOTSTRAP_IDF" >&2
            exit 1
        fi
    done
    printf '[bootstrap] Reusing ESP-IDF at %s\n' "$FI_BOOTSTRAP_IDF"
else
    printf '[bootstrap] Installing ESP-IDF %s at %s\n' "$FI_BOOTSTRAP_VERSION" "$FI_BOOTSTRAP_IDF"
    mkdir -p "$(dirname "$FI_BOOTSTRAP_IDF")"
    git clone --recursive --branch "$FI_BOOTSTRAP_VERSION" \
        https://github.com/espressif/esp-idf.git "$FI_BOOTSTRAP_IDF"
fi

(
    export IDF_PATH="$FI_BOOTSTRAP_IDF"
    export IDF_TOOLS_PATH="$FI_BOOTSTRAP_TOOLS"
    # Do not reuse an SDK Python environment inherited from another installation.
    unset IDF_PYTHON_ENV_PATH
    cd "$FI_BOOTSTRAP_IDF"
    ./install.sh esp32s3
    "$FI_BOOTSTRAP_PYTHON" tools/idf_tools.py install openocd-esp32
    . ./export.sh
    idf.py --version
    if [[ $FI_BOOTSTRAP_SYSTEM_DEPS -eq 1 && "$(uname -s)" == Linux ]]; then
        FI_BOOTSTRAP_USB_RULES="${OPENOCD_SCRIPTS}/../contrib/60-openocd.rules"
        if [[ ! -e /etc/udev/rules.d/60-openocd.rules ]]; then
            printf '[bootstrap] Installing OpenOCD USB access rules.\n'
            as_root install -D -m 0644 "$FI_BOOTSTRAP_USB_RULES" /etc/udev/rules.d/60-openocd.rules
            as_root udevadm control --reload-rules
            as_root udevadm trigger --subsystem-match=usb
        fi
    fi
)

FI_BOOTSTRAP_VENV="$FI_BOOTSTRAP_ROOT/.venv"
if [[ ! -e "$FI_BOOTSTRAP_VENV" ]]; then
    "$FI_BOOTSTRAP_PYTHON" -m venv "$FI_BOOTSTRAP_VENV"
elif [[ ! -f "$FI_BOOTSTRAP_VENV/pyvenv.cfg" || ! -x "$FI_BOOTSTRAP_VENV/bin/python" ]]; then
    printf 'Existing .venv is not a usable Python virtual environment: %s\n' "$FI_BOOTSTRAP_VENV" >&2
    exit 1
fi
check_python "$FI_BOOTSTRAP_VENV/bin/python"
"$FI_BOOTSTRAP_VENV/bin/python" -m pip install -r "$FI_BOOTSTRAP_ROOT/requirements.txt"

FI_BOOTSTRAP_ACTIVATE="$FI_BOOTSTRAP_VENV/activate-idf.sh"
{
    printf '# Generated by bootstrap.sh; source this file in your terminal.\n'
    if [[ -n "$FI_BOOTSTRAP_PATH_PREFIX" ]]; then
        printf 'export PATH=%q:"$PATH"\n' "$FI_BOOTSTRAP_PATH_PREFIX"
    fi
    printf 'export IDF_PATH=%q\n' "$FI_BOOTSTRAP_IDF"
    printf 'export IDF_TOOLS_PATH=%q\n' "$FI_BOOTSTRAP_TOOLS"
    printf 'unset IDF_PYTHON_ENV_PATH\n'
    printf '. %q\n' "$FI_BOOTSTRAP_IDF/export.sh"
} > "$FI_BOOTSTRAP_ACTIVATE"

printf '\n[bootstrap] Setup complete. Activate ESP-IDF in your terminal with:\n'
printf '  . %q\n' "$FI_BOOTSTRAP_ACTIVATE"
printf 'Then run commands from:\n  cd %q\n' "$FI_BOOTSTRAP_ROOT"
