"""v0.6.5 smoothness changes: each optimisation must be output-identical and fail safe.
Synthetic data only; offscreen; never touches the real clipboard."""
import os,sys,math,struct,tempfile,time,unittest,zipfile,hashlib
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
TEMP=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent,prefix='v065-')
os.environ['FEEDBACK_DATA_DIR']=TEMP.name
from PySide6.QtCore import Qt,QPointF,QRectF,QEvent,QPoint
from PySide6.QtGui import QImage,QColor,QPainter,QPen,QLinearGradient,QWheelEvent
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtTest import QTest
app=QApplication.instance() or QApplication(['test-v065']);app.setStyle('Fusion')
from main import MainWindow
from workspace import WorkspaceWindow
from items import new_record,image_to_base64
import storage,wincb,assets_runtime as assets
errors=[];warnings=[]
sys.excepthook=lambda t,v,tb:(errors.append(str(v)),sys.__excepthook__(t,v,tb))
QMessageBox.warning=lambda *a:warnings.append(str(a[-1]));QMessageBox.information=lambda *a:None
QMessageBox.question=lambda *a,**k:QMessageBox.StandardButton.No

def picture(w,h,seed=0):
    im=QImage(w,h,QImage.Format.Format_RGB32);p=QPainter(im)
    g=QLinearGradient(0,0,w,h);g.setColorAt(0,QColor(20+seed,90,160));g.setColorAt(1,QColor(240,200-seed,40));p.fillRect(im.rect(),g)
    for i in range(60):p.fillRect((i*37)%w,(i*23)%h,9,5,QColor((i*53)%255,(i*97)%255,(i*11)%255))
    p.end();return im

def dib(image,bits=32,top_down=False,header=40):
    im=image.convertToFormat(QImage.Format.Format_RGB32 if bits==32 else QImage.Format.Format_RGB888)
    w,h=im.width(),im.height();stride=((w*bits+31)//32)*4;rows=[]
    for y in (range(h) if top_down else range(h-1,-1,-1)):
        line=bytes(im.constScanLine(y))[:w*bits//8]
        if bits==24:line=b''.join(line[i:i+3][::-1] for i in range(0,len(line),3))  # RGB888 -> BGR
        rows.append(line+b'\0'*(stride-len(line)))
    bits_data=b''.join(rows)
    head=struct.pack('<IiiHHIIiiII',header,w,-h if top_down else h,1,bits,0,len(bits_data),2835,2835,0,0)
    head+=b'\0'*(header-40)
    return head+bits_data

def same_pixels(a,b):
    a=a.convertToFormat(QImage.Format.Format_RGB32);b=b.convertToFormat(QImage.Format.Format_RGB32)
    return a.size()==b.size() and all(bytes(a.constScanLine(y))[:a.width()*4]==bytes(b.constScanLine(y))[:b.width()*4] for y in range(a.height()))

def settle(predicate,timeout=15):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        app.processEvents()
        if predicate():return True
        QTest.qWait(3)
    return False

class Clipboard(unittest.TestCase):
    def test_dib_decode_matches_source(self):
        for w,h in [(1,1),(7,5),(641,359),(1920,1080)]:
            src=picture(w,h,w%50)
            for bits in (32,24):
                for top in (False,True):
                    for header in (40,124):
                        data=dib(src,bits,top,header)
                        self.assertIsNotNone(wincb.plain_dib(data),(w,h,bits,top,header))
                        out=wincb.dib_to_image(data)
                        self.assertEqual(out.format(),QImage.Format.Format_RGB32)
                        self.assertTrue(same_pixels(out,src),(w,h,bits,top,header))
    def test_unusual_bitmaps_fall_back_to_qt(self):
        good=dib(picture(8,8))
        bitfields=good[:16]+struct.pack('<I',3)+good[20:]
        sixteen=good[:14]+struct.pack('<H',16)+good[16:]
        for data in (b'',good[:30],bitfields,sixteen,good[:60]):
            self.assertIsNone(wincb.plain_dib(data))
        with self.assertRaises(ValueError):wincb.dib_to_image(bitfields)
    def test_dib_payload_goes_through_background_decode(self):
        src=picture(320,200);rec=storage.decode_clipboard(('dib',dib(src)))[0]
        self.assertTrue(assets.is_ref(rec['image']));self.assertEqual((rec['w'],rec['h']),(320,200))
        self.assertTrue(same_pixels(assets.image(rec['image']),src));assets.release_pending([rec])
    def test_offscreen_never_reads_real_clipboard(self):
        from unittest.mock import patch
        from PySide6.QtCore import QMimeData
        mime=QMimeData();mime.setImageData(picture(10,10));QApplication.clipboard().setMimeData(mime)
        with patch('wincb.read_screenshot',side_effect=AssertionError('must not be called offscreen')):
            kind,value=storage.capture_clipboard()
        self.assertEqual(kind,'bitmap')

class Loading(unittest.TestCase):
    def setUp(self):
        self.root=Path(TEMP.name)/('load-'+str(time.time_ns()));self.root.mkdir()
        self.images=[picture(800,450,i*20) for i in range(3)]
        recs=[new_record('image',x=i*900,w=800,h=450,image=image_to_base64(im)) for i,im in enumerate(self.images)]
        recs.append(new_record('image',x=0,y=600,w=800,h=450,image=recs[0]['image']))  # shared asset
        recs.append(new_record('text',text='标注'))
        self.path=self.root/'p.fbboard';storage.save_project(str(self.path),recs,{'k':1})
    def assets_of(self,path):
        with zipfile.ZipFile(path) as z:return sorted((n,hashlib.sha256(z.read(n)).hexdigest()) for n in z.namelist() if n.startswith('assets/'))
    def test_materialize_is_lossless_and_save_identical(self):
        plain,meta=storage.load_project(str(self.path))
        refs,meta2=storage.load_project(str(self.path),materialize=True)
        self.assertEqual(meta,meta2);self.assertTrue(all(not assets.is_ref(r.get('image','')) for r in plain))
        imgs=[r for r in refs if r['kind']=='image'];self.assertTrue(all(assets.is_ref(r['image']) for r in imgs))
        self.assertEqual(imgs[0]['image'],imgs[3]['image'])        # shared asset decoded once
        for r,src in zip(imgs,self.images):self.assertTrue(same_pixels(assets.image(r['image']),src))
        for a,b in zip(plain,refs):
            self.assertEqual({k:v for k,v in a.items() if k!='image'},{k:v for k,v in b.items() if k!='image'})
        out=self.root/'resaved.fbboard';storage.save_project(str(out),refs,meta2)
        self.assertEqual(self.assets_of(out),self.assets_of(self.path))  # byte-identical PNG assets
        assets.release_pending(refs)
    def test_budget_falls_back_to_previous_behaviour(self):
        old=storage.MATERIALIZE_BUDGET
        try:
            storage.MATERIALIZE_BUDGET=800*450*4+10   # room for one image only
            refs,_=storage.load_project(str(self.path),materialize=True)
            kinds=[assets.is_ref(r['image']) for r in refs if r['kind']=='image']
            self.assertEqual(kinds,[True,False,False,True])
            out=self.root/'mixed.fbboard';storage.save_project(str(out),refs,{})
            self.assertEqual(self.assets_of(out),self.assets_of(self.path));assets.release_pending(refs)
        finally:storage.MATERIALIZE_BUDGET=old
    def test_open_document_builds_canvas_without_ui_decoding(self):
        from unittest.mock import patch
        import items
        w=WorkspaceWindow(MainWindow);w.show()
        try:
            with patch.object(items,'_decoded_png',side_effect=AssertionError('PNG decoded on UI thread')):
                self.assertTrue(w.open_document(str(self.path)))
                self.assertTrue(settle(lambda:not w.current_document()._loading_project and len(w.current_document().view.all_items())==5))
                doc=w.current_document();doc.view.focus_items();doc.view.viewport().repaint()
            self.assertFalse(doc.dirty)
            out=self.root/'from-ui.fbboard';self.assertTrue(doc.save_project(str(out),wait=True))
            self.assertEqual(self.assets_of(out),self.assets_of(self.path))
        finally:
            for d in w.documents:d._confirm_unsaved=lambda:True
            w.close();app.processEvents()
        self.assertEqual(errors,[]);self.assertEqual(warnings,[])

class Grid(unittest.TestCase):
    def test_background_dots_pixel_identical(self):
        from board import BoardView
        def old(view,painter,rect):
            painter.fillRect(rect,QColor("#202020"));spacing=24
            while spacing*view.zoom()<16:spacing*=4
            while spacing*view.zoom()>80:spacing/=2
            painter.setPen(QPen(QColor("#292929"),0))
            left=math.floor(rect.left()/spacing)*spacing;top=math.floor(rect.top()/spacing)*spacing
            cols=min(180,int(rect.width()/spacing)+2);rows=min(140,int(rect.height()/spacing)+2)
            painter.drawPoints([QPointF(left+x*spacing,top+y*spacing) for x in range(cols) for y in range(rows)])
        v=BoardView();v.resize(900,600)
        for zoom in (0.02,0.3,1.0,2.7,9.0):
            v.resetTransform();v.scale(zoom,zoom);v.centerOn(123.4,-56.7)
            rect=v.mapToScene(v.viewport().rect()).boundingRect()
            images=[]
            for fn in (old,type(v).drawBackground):
                im=QImage(900,600,QImage.Format.Format_ARGB32_Premultiplied);im.fill(0);p=QPainter(im)
                p.setRenderHints(v.renderHints());p.setTransform(v.viewportTransform());fn(v,p,rect);p.end();images.append(im)
            self.assertEqual(images[0],images[1],zoom)

class LanNavigation(unittest.TestCase):
    def test_busy_share_stays_navigable_but_not_editable(self):
        root=Path(TEMP.name)/('lan-'+str(time.time_ns()));root.mkdir();path=root/'s.fbboard'
        storage.save_project(str(path),[new_record('text',text='原文'),new_record('image',x=300,w=400,h=300,image=image_to_base64(picture(400,300)))],{})
        host=WorkspaceWindow(MainWindow);host.show();guest=WorkspaceWindow(MainWindow);guest.show()
        try:
            host.lan.start_share(str(path),'127.0.0.1',0)
            self.assertTrue(settle(lambda:host.lan.host_binding is not None and not host.lan.host_binding.busy and not host.lan.host_binding.initial),warnings)
            guest.lan.connect_share(host.lan.service.connection_code,'G')
            self.assertTrue(settle(lambda:guest.lan.bindings and not next(iter(guest.lan.bindings)).initial and not next(iter(guest.lan.bindings)).busy),warnings)
            b=next(iter(guest.lan.bindings));v=b.doc.view
            self.assertTrue(any(assets.is_ref(r.get('image','')) for r in v.records()))   # refresh decoded off the UI thread
            b.acquire();self.assertTrue(settle(lambda:not b.busy and b.editable))
            b.busy=True;b.sync_controls()
            self.assertTrue(v.isEnabled())                        # was disabled for the whole save/refresh
            before=v.records();v.setFocus();QTest.keyClick(v,Qt.Key.Key_Delete);self.assertEqual(v.records(),before)
            zoom=v.zoom();p=v.viewport().rect().center()
            ev=QWheelEvent(QPointF(p),QPointF(v.viewport().mapToGlobal(p)),QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
            QApplication.sendEvent(v.viewport(),ev);self.assertAlmostEqual(v.zoom()/zoom,1.15,places=3)
            b.busy=False;b.sync_controls()
            QTest.keyClick(v,Qt.Key.Key_Delete);self.assertNotEqual(len(v.records()),len(before)) if v.selected() else None
        finally:
            for shell in (guest,host):
                settle(lambda:not shell.lan.busy and all(not x.busy and not x.polling for x in shell.lan.bindings))
                for d in shell.documents:d._confirm_unsaved=lambda:True
                shell.close();app.processEvents()
        self.assertEqual(errors,[]);self.assertEqual(warnings,[])

if __name__=='__main__':
    result=unittest.main(verbosity=2,exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
