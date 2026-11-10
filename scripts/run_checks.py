"""Run supported desktop suites in isolated processes."""
from pathlib import Path
import compileall
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SUITES = ("test_v065.py", "test_v064.py", "test_lan_core.py", "test_lan_ui.py", "test_v6.py")

def main():
    if not compileall.compile_dir(ROOT / "app", quiet=1):
        return 1
    env = os.environ.copy()
    env.update(QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    temp_root = ROOT / ".test-run"
    temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="checks-", dir=temp_root) as folder:
        env.update(TEMP=folder, TMP=folder, FEEDBACK_DATA_DIR=folder)
        for suite in SUITES:
            print(f"\n=== {suite} ===", flush=True)
            try:
                result = subprocess.run([sys.executable, str(ROOT / "app" / suite)],
                                        cwd=ROOT, env=env, timeout=180)
            except subprocess.TimeoutExpired:
                print(f"FAIL: {suite} exceeded 180 seconds", flush=True)
                return 1
            if result.returncode:
                print(f"FAIL: {suite} exited {result.returncode}", flush=True)
                return 1
    print("PASS: all supported regression suites", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
