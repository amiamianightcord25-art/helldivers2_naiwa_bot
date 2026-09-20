"""Install the pinned Playwright Chromium inside this project, without admin rights."""

import os
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(root / ".cache/ms-playwright"))
    raise SystemExit(subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"],
                                    cwd=root, env=environment))
