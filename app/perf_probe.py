"""Performance probe: GUI-thread blocking on paste, and frame cost while panning/zooming.
Synthetic images only; never touches the real system clipboard (offscreen platform)."""
import os,sys,time,json,statistics
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parent.parent
os.environ['FEEDBACK_DATA_DIR']=str(ROOT/'.perf-data'/str(time.time_ns()))
from PySide6.QtCore import Qt,QTimer,QElapsedTimer,QObject,QEvent,QPointF,QBuffer,QIODevice,QByteArray,QMimeData
from PySide6.QtGui import QImage,QPainter,QColor,QLinearGradient,QMouseEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
app=QApplication.instance() or QApplication(['perf']);app.setStyle('Fusion')
from main import MainWindow
from workspace import WorkspaceWindow

def screenshot(w=3840,h=2160,seed=0):
    im=QImage(w,h,QImage.Format.Format_RGB32);p=QPainter(im)
    g=QLinearGradient(0,0,w,h);g.setColorAt(0,QColor(30+seed*20,40,60));g.setColorAt(1,QColor(200,180-seed*10,120));p.fillRect(im.rect(),g)
    for i in range(400):p.fillRect((i*97)%w,(i*53)%h,80,30,QColor((i*37)%255,(i*91)%255,(i*13)%255))
    p.end();return im

class Stall(QObject):
    """Measures the longest gap between event-loop turns (what the user feels as a freeze)."""
    def __init__(self):
        super().__init__();self.timer=QTimer();self.timer.setInterval(0);self.timer.timeout.connect(self.tick)
        self.clock=QElapsedTimer();self.last=0;self.gaps=[]
    def start(self):self.gaps=[];self.clock.start();self.last=0;self.timer.start()
    def tick(self):
        now=self.clock.elapsed();self.gaps.append(now-self.last);self.last=now
    def stop(self):self.timer.stop();return max(self.gaps or [0])

def put_png(image):
    buf=QBuffer();buf.open(QIODevice.OpenModeFlag.WriteOnly);image.save(buf,'PNG',1)
    mime=QMimeData();mime.setData('image/png',QByteArray(buf.data()));QApplication.clipboard().setMimeData(mime)

def wait(pred,timeout=30):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        app.processEvents()
        if pred():return True
        QTest.qWait(1)
    return False

def frame_ms(view,steps=40,action=None):
    """Average and worst cost of a full viewport repaint (render to an offscreen image like a real frame)."""
    target=QImage(view.viewport().size(),QImage.Format.Format_ARGB32_Premultiplied);times=[]
    for i in range(steps):
        if action:action(i)
        t=time.perf_counter();p=QPainter(target);view.render(p);p.end();times.append((time.perf_counter()-t)*1000)
    return round(statistics.mean(times),2),round(max(times),2)

def run(label):
    w=WorkspaceWindow(MainWindow);w.resize(1600,1000);w.show();app.processEvents();doc=w.current_document();v=doc.view
    results={'label':label}
    stall=Stall();pastes=[]
    for n in range(4):
        put_png(screenshot(seed=n));app.processEvents()
        count=len(v.all_items());stall.start();t=time.perf_counter()
        doc.paste();blocking=(time.perf_counter()-t)*1000
        wait(lambda:len(v.all_items())>count)
        # let the first frame paint
        for _ in range(5):app.processEvents();QTest.qWait(2)
        total=(time.perf_counter()-t)*1000;worst=stall.stop()
        pastes.append({'paste_call_ms':round(blocking,1),'until_visible_ms':round(total,1),'worst_freeze_ms':worst})
    results['paste_4k']=pastes
    v.focus_items();app.processEvents()
    import math
    def pan(i):v._begin_pan(QPointF(500,400)) if i==0 else None;v._pan_to(QPointF(500+math.sin(i/5)*300,400+math.cos(i/7)*200))
    results['pan_fit_view']=frame_ms(v,60,pan)
    def zoom(i):v._zoom_at(1.08 if (i//10)%2==0 else 1/1.08,QPointF(800,500).toPoint())
    results['zoom_cycle']=frame_ms(v,60,zoom)
    v.reset_zoom();app.processEvents();results['pan_100pct']=frame_ms(v,60,pan)
    for d in w.documents:d._confirm_unsaved=lambda:True
    w.close();app.processEvents()
    return results

if __name__=='__main__':
    label=sys.argv[1] if len(sys.argv)>1 else 'run'
    r=run(label);print(json.dumps(r,ensure_ascii=False))
    out=ROOT/'perf-results.jsonl'
    with open(out,'a',encoding='utf-8') as f:f.write(json.dumps(r,ensure_ascii=False)+'\n')
