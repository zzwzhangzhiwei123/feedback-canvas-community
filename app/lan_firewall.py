"""One-time Windows Firewall permission for LAN sharing (asks for admin approval once).

Why: a sharing computer must accept inbound connections. Windows silently blocks them when
the first-run firewall prompt was dismissed, only "private" was ticked on a domain/public
network, or a *block* rule was created for this exe (block rules beat allow rules).
We remove rules attached to this exe, then allow this exe plus the fixed share ports
(port rules keep working for future versions that live at a different path).
Only the sharing side needs this; receivers only make outbound connections.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

RULE = "FeedbackCanvas LAN"
TCP_PORT = 48761
UDP_PORT = 48762


def executable():
    return str(Path(sys.executable).resolve()) if getattr(sys, "frozen", False) else None


def commands(exe, tcp_port=TCP_PORT, udp_port=UDP_PORT):
    netsh = "netsh advfirewall firewall"
    cmds = [f'{netsh} delete rule name="{RULE}"', f'{netsh} delete rule name="{RULE} ports"']
    if exe:
        cmds += [f'{netsh} delete rule name=all dir=in program="{exe}"',
                 f'{netsh} add rule name="{RULE}" dir=in action=allow enable=yes profile=any program="{exe}"']
    cmds += [f'{netsh} add rule name="{RULE} ports" dir=in action=allow enable=yes profile=any protocol=TCP localport={tcp_port}',
             f'{netsh} add rule name="{RULE} ports" dir=in action=allow enable=yes profile=any protocol=UDP localport={udp_port}']
    return cmds


def state_path(data_root):
    path = Path(data_root) / "lan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_state(data_root):
    try:
        value = json.loads(state_path(data_root).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(data_root, value):
    path = state_path(data_root)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


def needs_setup(data_root, exe=None):
    if os.name != "nt":
        return False
    exe = exe or executable()
    if not exe:
        return False  # development runs from python.exe: never touch the firewall
    state = load_state(data_root)
    return exe.lower() not in {p.lower() for p in state.get("allowed", [])} and \
        exe.lower() not in {p.lower() for p in state.get("declined", [])}


def remember(data_root, exe, allowed):
    state = load_state(data_root)
    key = "allowed" if allowed else "declined"
    items = [p for p in state.get(key, []) if p.lower() != exe.lower()][-20:]
    items.append(exe)
    state[key] = items
    if allowed:
        state["declined"] = [p for p in state.get("declined", []) if p.lower() != exe.lower()]
    state["updated_at"] = time.time()
    save_state(data_root, state)


def rules_present():
    """Non-elevated read-back of our port rule (rule names are ASCII, so locale does not matter)."""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={RULE} ports"],
                             capture_output=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        text = out.stdout.decode("mbcs", errors="ignore") if os.name == "nt" else out.stdout.decode(errors="ignore")
        return out.returncode == 0 and RULE in text
    except (OSError, subprocess.SubprocessError):
        return False


def run_elevated(exe, hwnd=0, timeout=90):
    """Shows one UAC prompt; returns (ok, message). Cancelling UAC is reported, never raised."""
    if os.name != "nt":
        return False, "仅 Windows 需要。"
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    script = " & ".join(commands(exe))
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000400  # NOCLOSEPROCESS | FLAG_NO_UI (errors are reported by us)
    info.hwnd = hwnd or None
    info.lpVerb = "runas"
    info.lpFile = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    info.lpParameters = '/s /c "' + script + '"'
    info.nShow = 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.GetLastError()
        if error == 1223:
            return False, "已取消管理员确认，防火墙未修改。"
        return False, f"无法请求管理员权限（错误 {error}）。"
    try:
        ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, int(timeout * 1000))
        code = wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    finally:
        ctypes.windll.kernel32.CloseHandle(info.hProcess)
    if rules_present():
        return True, "已允许反馈画布接收局域网连接。"
    return False, f"防火墙规则未能确认（退出码 {code.value}）。公司电脑可能由管理软件统一管控防火墙。"
