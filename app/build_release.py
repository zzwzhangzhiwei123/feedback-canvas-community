"""Build with the interpreter of the currently activated isolated environment."""
from pathlib import Path
from datetime import datetime
import subprocess
import os
import sys
from contracts import APP_VERSION

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

if __name__ == "__main__":
    output = ROOT / "outputs"
    output.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # New build directories preserve prior output and avoid deleting cached files.
    name = "反馈画布-v" + APP_VERSION
    if (output / (name + ".exe")).exists():
        name += "-" + stamp
    import PyInstaller
    bootstrap="import sys;sys.path.insert(0,"+repr(str(Path(PyInstaller.__file__).parent.parent))+");from PyInstaller.__main__ import run;run()"
    command = [
        sys.executable, "-c", bootstrap, "--noconfirm", "--onefile", "--windowed",
        "--name", name, "--icon", str(HERE / "feedback.ico"),
        "--distpath", str(output), "--workpath", str(ROOT / ".build" / stamp),
        "--specpath", str(HERE), "--add-data", str(HERE / "assets") + ";assets",
        str(HERE / "main.py"),
    ]
    # Some managed runtimes ship an SSL trust-policy data file beside ssl.py.
    # Preserve that policy in the frozen application instead of changing SSL behavior.
    import ssl
    ssl_policy=Path(ssl.__file__).with_name('codex-rejected-cas.pem')
    if ssl_policy.is_file():command[-1:-1]=['--add-data',str(ssl_policy)+';.']
    # Collect DLLs only from this Python/Qt installation and Windows.
    # Host app media tools can put incompatible CRT/OpenSSL DLLs on PATH.
    import PySide6, shiboken6
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([
        str(Path(sys.executable).parent), sys.base_prefix,
        str(Path(sys.base_prefix) / "DLLs"),
        str(Path(PySide6.__file__).parent), str(Path(shiboken6.__file__).parent),
        str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"),
    ])
    env.pop("PYTHONPATH", None)
    env.pop("QT_PLUGIN_PATH", None)
    subprocess.run(command, check=True, env=env)
