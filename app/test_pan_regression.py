"""Continuous pixel-anchored navigation regression (synthetic input, no desktop capture)."""
import argparse
import importlib
import json
import math
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument("--module-dir", default=str(Path(__file__).resolve().parent))
parser.add_argument("--report", default=str(ROOT / "outputs" / "平移回归-v0.1.1.json"))
parser.add_argument("--baseline", action="store_true")
args = parser.parse_args()
sys.path.insert(0, args.module_dir)
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QTransform
from PySide6.QtWidgets import QApplication, QGraphicsView
from board import BoardView
from items import new_record, ensure_fonts

app = QApplication.instance() or QApplication(["pan-regression"])
ensure_fonts()
view = BoardView()
view.setWindowTitle("Navigation regression")
view.show()
app.processEvents()
B = Qt.MouseButton
M = Qt.KeyboardModifier

def send(kind, position, button=B.NoButton, buttons=B.NoButton, modifiers=M.NoModifier):
    global_pos = QPointF(view.viewport().mapToGlobal(position.toPoint())) + (position - QPointF(position.toPoint()))
    event = QMouseEvent(kind, position, global_pos, button, buttons, modifiers)
    QApplication.sendEvent(view.viewport(), event)

results = []
for size in [(980, 720), (981, 721)]:
    for zoom in [.01, .1, .37, .75, 1.0, 2.0, 10.0]:
        for name, button, modifiers, maximized in [
            ("middle", B.MiddleButton, M.NoModifier, False),
            ("alt_left", B.LeftButton, M.AltModifier, False),
            ("right_maximized", B.RightButton, M.NoModifier, True),
        ]:
            view.showNormal()
            view.resize(*size)
            if maximized:
                view.showMaximized()
            app.processEvents()
            view.load_records([new_record("rect", x=-30, y=-20, w=60, h=40, locked=True)])
            view.setTransform(QTransform.fromScale(zoom, zoom))
            view.centerOn(145.4, -99.8)
            records_before = view.records()
            signals = []
            def changed():
                signals.append(True)
            view.changed.connect(changed)
            start = QPointF(view.viewport().width() * .43, view.viewport().height() * .46)
            origin_inverse, valid = view.viewportTransform().inverted()
            assert valid
            anchor = origin_inverse.map(start)
            send(QEvent.Type.MouseButtonPress, start, button, button, modifiers)
            offsets = [QPointF(i * .51, math.sin(i / 19) * 35) for i in range(1, 161)]
            offsets += list(reversed(offsets)) + [QPointF()]
            offsets += [QPointF()] * 40
            worst = 0.0
            for offset in offsets:
                cursor = start + offset
                send(QEvent.Type.MouseMove, cursor, buttons=button, modifiers=modifiers)
                # Right-click has a deliberate drag threshold; it is not yet pan before activation.
                if name == "right_maximized" and not view.right_moved:
                    continue
                rendered = view.viewportTransform().map(anchor)
                worst = max(worst, math.hypot((rendered-cursor).x(), (rendered-cursor).y()))
            final = view.viewportTransform().map(anchor)-start
            returned = math.hypot(final.x(), final.y())
            send(QEvent.Type.MouseButtonRelease, start, button, B.NoButton, modifiers)
            stopped = view.viewportTransform()
            send(QEvent.Type.MouseMove, start + QPointF(17, 9))
            stationary_after_release = stopped == view.viewportTransform()
            view.changed.disconnect(changed)
            unchanged = records_before == view.records() and not signals
            result = {"gesture": name, "viewport": size, "zoom": zoom, "events": len(offsets),
                      "maximum_cursor_error_px": round(worst, 6), "roundtrip_error_px": round(returned, 6),
                      "document_unchanged": unchanged, "stopped_on_release": stationary_after_release,
                      "passed": worst <= 1.5 and returned <= 1.0 and unchanged and stationary_after_release}
            results.append(result)
            print(name, size, zoom, "max error", result["maximum_cursor_error_px"], "return", result["roundtrip_error_px"], "PASS" if result["passed"] else "FAIL")
view.hide()
report = {"platform": app.platformName(), "device_pixel_ratio": view.devicePixelRatioF(), "cases": len(results), "events_per_case": len(offsets),
          "passed": all(r["passed"] for r in results), "maximum_cursor_error_px": max(r["maximum_cursor_error_px"] for r in results),
          "maximum_roundtrip_error_px": max(r["roundtrip_error_px"] for r in results), "results": results}
Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("SUMMARY", json.dumps({k: v for k, v in report.items() if k != "results"}))
raise SystemExit(0 if report["passed"] or args.baseline else 1)
