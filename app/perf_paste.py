"""Paste + frame benchmark on the real Windows backend WITHOUT using the system clipboard.
The screenshot QImage is handed to the same pipeline paste() uses after reading the clipboard."""
import os,sys,time,json,statistics,math
from pathlib import Path
here=Path(__file__).resolve().parent;sys.path.insert(0,str(here))
os.environ['QT_QPA_PLATFORM']=os.environ.get('PERF_QPA','offscreen');os.environ['FEEDBACK_DATA_DIR']=str(here.parent/'.perf-data'/str(time.time_ns()))
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
app=QApplication([]);app.setStyle('Fusion')
from perf_probe import screenshot,Stall
from main import MainWindow
from workspace import WorkspaceWindow
import storage

def frames(v,action,n=60):
    ts=[]
    for i in range(n):
        action(i);t=time.perf_counter();v.viewport().repaint();ts.append((time.perf_counter()-t)*1000)
    return [round(statistics.mean(ts),2),round(max(ts),2)]

def main(label):
    w=WorkspaceWindow(MainWindow);w.showMaximized();app.processEvents();QTest.qWait(300)
    doc=w.current_document();v=doc.view;res={'label':label,'viewport':[v.viewport().width(),v.viewport().height()],'dpr':v.devicePixelRatioF()}
    shots=[screenshot(seed=n) for n in range(4)]
    pastes=[]
    for img in shots:
        count=len(v.all_items());stall=Stall();stall.start();t=time.perf_counter()
        # what paste() does after capture_clipboard() returned ("bitmap", image)
        doc._paste_payload(("bitmap",img)) if hasattr(doc,'_paste_payload') else doc._queue_import(lambda img=img:storage.decode_clipboard(("bitmap",img)),v.insertion_point())
        call=(time.perf_counter()-t)*1000
        end=time.monotonic()+20
        while len(v.all_items())<=count and time.monotonic()<end:app.processEvents();QTest.qWait(1)
        v.viewport().repaint();visible=(time.perf_counter()-t)*1000
        for _ in range(10):app.processEvents();QTest.qWait(2)
        pastes.append({'call_ms':round(call,1),'until_painted_ms':round(visible,1),'worst_freeze_ms':stall.stop()})
    res['paste_4k']=pastes
    v.focus_items();app.processEvents()
    def pan(i):
        if i==0:v._begin_pan(QPointF(500,400))
        v._pan_to(QPointF(500+math.sin(i/5)*300,400+math.cos(i/7)*200))
    res['pan_right_after']=frames(v,pan)
    QTest.qWait(3000);app.processEvents()
    res['pan_after_3s_idle']=frames(v,pan)
    res['zoom']=frames(v,lambda i:v._zoom_at(1.08 if (i//10)%2==0 else 1/1.08,QPointF(900,500).toPoint()))
    v.select_items([]);app.processEvents()
    res['pan_unselected']=frames(v,pan)
    for d in w.documents:d._confirm_unsaved=lambda:True
    w.close();app.processEvents()
    return res
if __name__=='__main__':
    r=main(sys.argv[1] if len(sys.argv)>1 else 'run');print(json.dumps(r,ensure_ascii=False))
    with open(here.parent.parent/'perf-results.jsonl','a',encoding='utf-8') as f:f.write(json.dumps(r,ensure_ascii=False)+'\n')
