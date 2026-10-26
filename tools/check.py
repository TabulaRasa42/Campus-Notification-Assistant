"""Run the same offline checks locally and in CI, from any working directory."""

import os
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "CNA_OFFLINE_CHECKS": "1"}
    print(f"Checking {root}", flush=True)
    for command in (("ruff", "check", "."), ("mypy",), ("pytest", "-q")):
        print("Running " + " ".join(command), flush=True)
        result = subprocess.run([sys.executable, "-m", *command], cwd=root, env=env, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
