"""PureRef-style loading bar at the bottom of the canvas. The canvas stays usable meanwhile.

begin() returns a token; report() may be called from worker threads; end() on completion.
The bar only appears if a task is still running after a short delay, so fast work never flickers.
"""
from __future__ import annotations

import itertools
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QWidget

_ids = itertools.count(1)


class LoadBar(QFrame):
    _report = Signal(int, object, object, object)  # token, done, total, text (thread-safe hop)

    def __init__(self, viewport: QWidget):
        super().__init__(viewport)
        self.setObjectName("loadBar")
        self.setStyleSheet(
            "QFrame#loadBar{background:rgba(32,32,32,235);border:1px solid #4a4a4a;border-radius:8px;}"
            "QLabel{color:#dcdcdc;background:transparent;font-size:12px;}"
            "QProgressBar{background:#3a3a3a;border:none;border-radius:3px;max-height:6px;min-height:6px;}"
            "QProgressBar::chunk{background:#d6d6d6;border-radius:3px;}")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(12)
        self.label = QLabel()
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setMinimumWidth(180)
        self.percent = QLabel()
        self.percent.setMinimumWidth(34)
        self.percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.label)
        row.addWidget(self.bar, 1)
        row.addWidget(self.percent)
        self.tasks = {}      # token -> [text, done, total, started]
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
        self._report.connect(self._apply, Qt.ConnectionType.QueuedConnection)
        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.timeout.connect(self._refresh)
        viewport.installEventFilter(self)

    # --- public API -------------------------------------------------------------------------
    def begin(self, text, total=0, delay=250):
        token = next(_ids)
        self.tasks[token] = [text, 0, total, time.monotonic()]
        self._show_timer.start(delay)
        return token

    def report(self, token, done=None, total=None, text=None):
        """Safe from any thread."""
        self._report.emit(token, done, total, text)

    def reporter(self, token, text=None):
        """Callable(done, total) for worker code."""
        return lambda done, total: self.report(token, done, total, text)

    def end(self, token):
        self.tasks.pop(token, None)
        self._refresh()

    # --- internals --------------------------------------------------------------------------
    def _apply(self, token, done, total, text):
        task = self.tasks.get(token)
        if task is None:
            return
        if text:
            task[0] = text
        if done is not None:
            task[1] = done
        if total is not None:
            task[2] = total
        if self.isVisible():
            self._refresh()

    def _refresh(self):
        if not self.tasks:
            self.hide()
            return
        now = time.monotonic()
        visible = [t for t in self.tasks.values() if now - t[3] >= 0.2]
        if not visible:
            self._show_timer.start(120)
            return
        text, done, total, _ = visible[-1]
        extra = len(self.tasks) - 1
        self.label.setText(text + (f"（另有 {extra} 项）" if extra > 0 else ""))
        if total and total > 0:
            value = max(0, min(1000, int(done * 1000 / total)))
            self.bar.setRange(0, 1000)
            self.bar.setValue(value)
            self.percent.setText(f"{value // 10}%")
        else:
            self.bar.setRange(0, 0)  # indeterminate, animated
            self.percent.setText("")
        self._place()
        self.show()
        self.raise_()

    def _place(self):
        parent = self.parentWidget()
        if parent is None:
            return
        width = max(300, min(520, parent.width() - 40))
        self.setFixedWidth(width)
        self.adjustSize()
        self.move((parent.width() - width) // 2, parent.height() - self.height() - 18)

    def eventFilter(self, watched, event):
        from PySide6.QtCore import QEvent
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False
