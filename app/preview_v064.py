"""Offscreen previews of the v0.6.4 LAN dialog and host window (synthetic project)."""
import os,sys,time
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
root=Path(__file__).resolve().parent.parent/'preview';root.mkdir(exist_ok=True)
os.environ['FEEDBACK_DATA_DIR']=str(root/('data-'+str(time.time_ns())))
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtTest import QTest
app=QApplication([]);app.setStyle('Fusion')
from items import ensure_fonts,new_record;ensure_fonts()
from main import MainWindow
from workspace import WorkspaceWindow
import storage
QMessageBox.question=lambda *a,**k:QMessageBox.StandardButton.No
QMessageBox.warning=lambda *a:print('WARN',a[1:])
p=root/f'演示-{time.time_ns()}.fbboard';storage.save_project(str(p),[new_record('text',text='demo')],{})
w=WorkspaceWindow(MainWindow);w.resize(1440,900);w.show();w.open_document(str(p.resolve()))
def wait(pred,n=1000):
    for _ in range(n):
        app.processEvents();QTest.qWait(10)
        if pred():return True
wait(lambda:not w.current_document()._loading_project)
w.lan.show_dialog();app.processEvents();w.lan.dialog.grab().save(str(root/'lan-idle.png'))
w.lan.quick_share();wait(lambda:w.lan.host_binding is not None and not w.lan.host_binding.busy)
w.lan.update_dialog();app.processEvents();w.lan.dialog.grab().save(str(root/'lan-sharing.png'))
w.lan.dialog.more.setChecked(True);app.processEvents();w.lan.dialog.grab().save(str(root/'lan-advanced.png'))
w.lan.dialog.hide();app.processEvents();w.grab().save(str(root/'host-window.png'))
for d in w.documents:d._confirm_unsaved=lambda:True
wait(lambda:False,30);w.close()
