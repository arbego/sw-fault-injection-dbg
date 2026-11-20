"""Hardware tests for the fault injector and its dedicated ESP32-S3 target app."""

import sys
from pathlib import Path

# The existing command-line tools use imports relative to tools/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
