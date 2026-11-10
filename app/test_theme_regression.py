"""Verify a UI theme change does not change document/export colors or actions."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent

def probe(module_dir, data_dir):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["FEEDBACK_DATA_DIR"] = str(data_dir)
    sys.path.insert(0, str(module_dir))
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage
    from main import MainWindow, STYLE_DEFAULT
    from extras import ExportDialog
    from storage import load_project, render_records
    from items import new_record
    app = QApplication(["theme-regression"])
    app.setStyle("Fusion")
    window = MainWindow()
    actions = {key: [shortcut.toString() for shortcut in value.shortcuts()] for key, value in window.actions.items()}
    dialog = ExportDialog(window)
    records, _ = load_project(str(ROOT / "outputs" / "示例反馈.fbboard"))
    def raster_hash(image):
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        return hashlib.sha256(bytes(image.constBits())).hexdigest()
    before_records = json.dumps(records, ensure_ascii=False, sort_keys=True)
    hashes = []
    for bg in ("#ffffff", "transparent", "#f1f3f5"):
        hashes.append(raster_hash(render_records(records, background=bg)))
    samples = [new_record(k, id=k, x=idx*40, y=idx*15, text="文字描边测试", text_outline=True, font_size=21, fill="#aaccee")
               for idx,k in enumerate(("text","rect","ellipse","pen","polyline","bezier","arrow","line","region","number","redact"))]
    hashes.append(raster_hash(render_records(samples, background="transparent")))
    result = {"style_defaults": STYLE_DEFAULT, "actions": actions,
              "export_defaults": [dialog.scale, dialog.background, dialog.padding],
              "export_choices": [dialog._background.itemData(i) for i in range(dialog._background.count())],
              "image_hashes": hashes, "records_unchanged": before_records == json.dumps(records, ensure_ascii=False, sort_keys=True),
              "minimum_window": [window.minimumWidth(),window.minimumHeight()],
              "toolbox_width": window.toolbox.width()}
    window._closing = True
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", nargs=2)
    args = parser.parse_args()
    if args.probe:
        print(json.dumps(probe(Path(args.probe[0]), Path(args.probe[1])), ensure_ascii=False, sort_keys=True))
    else:
        results = []
        for path, suffix in ((ROOT / ".revision-v011-baseline", "before"), (ROOT / "app", "after")):
            proc = subprocess.run([sys.executable, __file__, "--probe", str(path), str(ROOT / (".test-theme-" + suffix))],
                                  capture_output=True, text=True, encoding="utf-8", timeout=30,
                                  env={**os.environ, "PYTHONIOENCODING":"utf-8"})
            if proc.returncode:
                raise RuntimeError(proc.stderr)
            results.append(json.loads(proc.stdout))
        before, after = results
        comparisons = {key: before[key] == after[key] for key in before}
        report = {"passed": all(comparisons.values()), "checks": comparisons,
                  "sample_export_sha256": after["image_hashes"], "document_records_unchanged": after["records_unchanged"]}
        print(json.dumps(report,ensure_ascii=False,indent=2))
        (ROOT / "outputs" / "配色独立性验证-v0.1.1.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        raise SystemExit(0 if report["passed"] else 1)
