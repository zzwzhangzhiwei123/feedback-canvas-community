"""GUI-thread progress display and cross-thread save notifications."""
import time
from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QWidget,QHBoxLayout,QLabel,QProgressBar

class SaveEvents(QObject):
    progress=Signal(str,int,str)
    finished=Signal()

class SaveIndicator(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        row=QHBoxLayout(self);row.setContentsMargins(4,0,8,0);row.setSpacing(9)
        row.addWidget(QLabel('保存项目'))
        self.bar=QProgressBar();self.bar.setFixedWidth(140);self.bar.setFixedHeight(18)
        self.bar.setTextVisible(True);row.addWidget(self.bar)
        self.label=QLabel();row.addWidget(self.label,1)
        self.started=0;self.phase='';self.active=False
        self.timer=QTimer(self);self.timer.setInterval(250);self.timer.timeout.connect(self.refresh_label)
        self.hide()
    def begin(self):
        self.started=time.monotonic();self.active=True;self.phase='正在排队…'
        self.bar.setStyleSheet('');self.bar.setRange(0,0);self.bar.setFormat('%p%')
        self.show();self.timer.start();self.refresh_label()
    def update_progress(self,value,phase):
        if not self.active:return
        self.phase=phase
        if value<0:self.bar.setRange(0,0)
        else:self.bar.setRange(0,100);self.bar.setValue(value)
        self.refresh_label()
    def finish(self,success,phase):
        self.active=False;self.timer.stop();self.phase=phase
        self.bar.setRange(0,100)
        if success:self.bar.setValue(100);self.bar.setFormat('100%')
        else:self.bar.setFormat('保存失败');self.bar.setStyleSheet('QProgressBar::chunk { background: #c65a4c; }')
        self.refresh_label()
    def refresh_label(self):
        elapsed=max(0,int(time.monotonic()-self.started))
        self.label.setText(f'{self.phase}  ·  已用时 {elapsed} 秒')
        self.label.setToolTip(self.label.text())
