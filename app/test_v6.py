"""Focused v0.6 regressions. Uses only synthetic files and isolated runtime data."""
import os,sys,tempfile,unittest,time,json,hashlib
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
TEMP=tempfile.TemporaryDirectory(prefix='test-v6-',dir=Path(__file__).resolve().parent.parent)
os.environ['FEEDBACK_DATA_DIR']=TEMP.name
from PySide6.QtCore import Qt,QPointF,QPoint,QRectF
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtTest import QTest
from main import MainWindow
from workspace import WorkspaceWindow
from items import new_record,BoardItem
from board import point,union
import assets_runtime as assets
import storage
from packing import pack_rectangles
app=QApplication.instance() or QApplication(['test']);app.setStyle('Fusion')
errors=[]
sys.excepthook=lambda typ,value,tb:(errors.append(str(value)),sys.__excepthook__(typ,value,tb))
QMessageBox.warning=lambda *args:errors.append(str(args[-1]))
QMessageBox.information=lambda *args:None
QMessageBox.question=lambda *args:QMessageBox.StandardButton.No
shell=WorkspaceWindow(MainWindow);shell.show();app.processEvents()
B=Qt.MouseButton;M=Qt.KeyboardModifier

def settle(predicate=lambda:False,timeout=5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        app.processEvents()
        if predicate():return True
        QTest.qWait(5)
    return False

def image_record(**kwargs):
    im=QImage(120,80,QImage.Format.Format_ARGB32);im.fill('#586574')
    return new_record('image',image=assets.register(im),w=120,h=80,**kwargs)

def gesture(view,start,end=None,mods=M.NoModifier):
    a=view.mapFromScene(QPointF(*start));b=view.mapFromScene(QPointF(*(end or start)))
    QTest.mousePress(view.viewport(),B.LeftButton,mods,a)
    if end:QTest.mouseMove(view.viewport(),b,10)
    QTest.mouseRelease(view.viewport(),B.LeftButton,mods,b);app.processEvents()

class ReleaseTests(unittest.TestCase):
    def setUp(self):
        errors.clear();self.doc=shell.new_document();self.v=self.doc.view
        self.v.centerOn(100,100);app.processEvents()
    def tearDown(self):self.assertEqual(errors,[])
    def load(self,records):
        self.doc._load_document(records,{'app_version':'0.6.0'},None,True)
        self.v.reset_zoom();self.v.centerOn(100,100);app.processEvents()
        return [self.v.by_id(r['id']) for r in records]
    def test_image_corner_scale_and_undo(self):
        for zoom in [.25,1,2.43]:
            item=self.load([image_record()])[0]
            self.v.restore_view({'zoom':zoom,'center':[60,40]})
            self.v.select_items([item]);app.processEvents()
            corner=self.v._transform_handles()[2]
            gesture(self.v,(corner.x(),corner.y()),(corner.x()+60,corner.y()+40))
            self.assertGreater(item.scale(),1.2)
            self.assertEqual(len(self.v.selected()),1)
            self.doc.undo();self.assertAlmostEqual(self.v.all_items()[0].scale(),1)
    def test_multi_corner_in_empty_space_keeps_selection(self):
        a,b=self.load([image_record(x=0,y=100),image_record(x=200,y=0)])
        self.v.select_items([a,b]);app.processEvents()
        corner=self.v._transform_handles()[2]
        self.assertIsNone(self.v._hit(corner))
        gesture(self.v,(corner.x(),corner.y()),(corner.x()+80,corner.y()+45))
        self.assertEqual(len(self.v.selected()),2)
        self.assertGreater(a.scale(),1);self.assertAlmostEqual(a.scale(),b.scale())
    def test_rectangle_corner_resize(self):
        item=self.load([new_record('rect',x=0,y=0,w=100,h=60)])[0]
        self.v.select_items([item]);corner=self.v._transform_handles()[2]
        gesture(self.v,(corner.x(),corner.y()),(corner.x()+50,corner.y()+30))
        self.assertGreater(item.record['w'],120);self.assertGreater(item.record['h'],70)
    def test_native_size_and_bounds(self):
        for zoom in [.25,1,2]:
            self.v.restore_view({'zoom':zoom,'center':[0,0]})
            record=self.doc._layout_images([image_record()],QPointF(20,40))[0]
            self.assertEqual(record['scale'],1)
            item=BoardItem(record)
            self.assertEqual(union([item]).size(),QRectF(0,0,120,80).size())
            self.assertEqual(union([item]).center(),QPointF(20,40))
    def test_click_single_and_drag_multi(self):
        a,b=self.load([image_record(x=0,y=0,z=1),image_record(x=160,y=0,z=2)])
        self.v.select_items([a,b]);gesture(self.v,(60,40))
        self.assertEqual(self.v.selected(),[a]);self.assertGreater(a.zValue(),b.zValue())
        self.v.select_items([a,b]);gesture(self.v,(60,40),(85,65))
        self.assertEqual(len(self.v.selected()),2)
        self.assertAlmostEqual(a.x(),25,delta=1);self.assertAlmostEqual(b.x(),185,delta=1)
    def test_marquee_no_raise_and_annotations_above(self):
        a,b,mark=self.load([image_record(x=0,y=0,z=1,w_override=1),image_record(x=30,y=20,z=2),new_record('text',x=20,y=15,text='test',z=-100)])
        before=(a.zValue(),b.zValue());gesture(self.v,(-30,-30),(200,130))
        self.assertEqual((a.zValue(),b.zValue()),before)
        self.assertEqual(len(self.v.selected()),3)
        gesture(self.v,(10,65));self.assertGreater(a.zValue(),b.zValue())
        self.assertIs(self.v._hit(QPointF(30,25)),mark)
    def test_opacity_undo_and_tab_minimal(self):
        a=self.load([image_record()])[0];self.v.select_items([a])
        mods=M.ControlModifier|M.AltModifier|M.ShiftModifier
        gesture(self.v,(80,40),(-70,40),mods)
        self.assertAlmostEqual(a.opacity(),.5,delta=.02)
        self.doc.undo();self.assertAlmostEqual(a.opacity(),1)
        self.v.setFocus();QTest.keyClick(self.v,Qt.Key.Key_Tab);app.processEvents()
        self.assertTrue(self.doc.actions['minimal'].isChecked());self.assertTrue(self.doc.statusBar().isHidden())
        self.assertTrue(shell.overlay.isVisible());QTest.keyClick(self.v,Qt.Key.Key_Tab);app.processEvents()
        self.assertFalse(self.doc.actions['minimal'].isChecked())
    def test_packing_no_overlap_and_group_atomic(self):
        import random
        rng=random.Random(6)
        sizes=[(rng.randint(20,500),rng.randint(20,300)) for _ in range(150)]
        positions=pack_rectangles(sizes)
        rects=[QRectF(x,y,w+7.99,h+7.99) for (x,y),(w,h) in zip(positions,sizes)]
        self.assertFalse(any(a.intersects(b) for i,a in enumerate(rects) for b in rects[i+1:]))
        a,b,c=self.load([image_record(x=0,y=0,group='g'),image_record(x=130,y=0,group='g'),image_record(x=800,y=600)])
        self.v.select_items([a,b,c]);delta=b.pos()-a.pos();self.v.arrange_selection()
        self.assertEqual(b.pos()-a.pos(),delta)
        self.assertEqual(a.scale(),1);self.assertLess(union([a,b,c]).width(),800)
    def test_round_arrows_preserve_old(self):
        old=BoardItem(new_record('arrow',arrow_style='tapered',stroke_width=6,w=300,h=0))
        new=BoardItem(new_record('arrow',arrow_style='tapered_round',stroke_width=6,w=300,h=0))
        self.assertEqual(old._arrow.elementAt(0).y,0)
        self.assertGreater(new._arrow.elementAt(0).y,0)
        self.assertGreater(new._arrow.elementCount(),old._arrow.elementCount())
        f=Path(TEMP.name)/'arrows.fbboard';storage.save_project(f,[old.to_record(),new.to_record()],{})
        r,_=storage.load_project(f);self.assertEqual(r[0]['arrow_style'],'tapered');self.assertEqual(r[1]['arrow_style'],'tapered_round')
    def test_multidoc_history_assets_save_and_recovery(self):
        d1=self.doc;v1=self.v
        r=image_record();d1._add_records([r]);assets.release_pending([r]);key=r['image']
        d2=shell.new_document();d2._add_records([new_record('text',text='second')])
        self.assertFalse(assets.image(key).isNull())
        d2.undo();self.assertEqual(len(d1.view.all_items()),1);self.assertEqual(len(d2.view.all_items()),0)
        d2.redo();self.assertEqual(len(d2.view.all_items()),1)
        f1=Path(TEMP.name)/'one.fbboard';f2=Path(TEMP.name)/'two.fbboard'
        self.assertTrue(d1.save_project(path=f1));self.assertTrue(d2.save_project(path=f2))
        self.assertTrue(settle(lambda:not d1._manual_save_busy and not d2._manual_save_busy))
        self.assertEqual(storage.load_project(f1)[0][0]['kind'],'image');self.assertEqual(storage.load_project(f2)[0][0]['text'],'second')
        d1._add_records([new_record('text',text='recover-one')]);d2._add_records([new_record('text',text='recover-two')])
        self.assertTrue(d1.autosave());self.assertTrue(d2.autosave())
        self.assertTrue(settle(lambda:not d1._autosave_busy and not d2._autosave_busy))
        self.assertNotEqual(d1.recovery_path,d2.recovery_path)
        self.assertIn('recover-one',[r['text'] for r in storage.load_project(d1.recovery_path)[0]])
        self.assertIn('recover-two',[r['text'] for r in storage.load_project(d2.recovery_path)[0]])
        # Simulate an interrupted previous process with a copied recovery directory.
        import shutil
        recovered=shell.documents_dir/'synthetic-crash';recovered.mkdir()
        shutil.copy2(d1.recovery_path,recovered/'recovery.fbboard');shutil.copy2(d1.recovery_marker,recovered/'recovery.json')
        self.assertTrue(shell.restore_recovery(recovered))
        restored=shell.current_document();self.assertTrue(restored.dirty);self.assertIsNone(restored.current_file)
        self.assertEqual(len(restored.view.all_items()),2)
    def test_old_file_roundtrip_and_no_overwrite(self):
        f=Path(TEMP.name)/'old.fbboard';records=[image_record(),new_record('arrow',arrow_style='tapered'),new_record('text',text='旧项目')]
        storage.save_project(f,records,{'app_version':'0.5.2'});before=hashlib.sha256(f.read_bytes()).hexdigest()
        self.assertTrue(shell.open_document(f));doc=shell.current_document()
        self.assertTrue(settle(lambda:not doc._loading_project));self.assertEqual(doc._legacy_source,str(f))
        count=len(shell.documents);shell.open_document(f);self.assertEqual(len(shell.documents),count)
        record=doc.view.all_items()[-1];record.setPos(55,88);doc.checkpoint()
        target=Path(TEMP.name)/'new-v6.fbboard';self.assertTrue(doc.save_project(path=target));self.assertTrue(settle(lambda:not doc._manual_save_busy))
        self.assertEqual(hashlib.sha256(f.read_bytes()).hexdigest(),before)
        read,_=storage.load_project(target);self.assertEqual(len(read),3)
        self.assertIn('tapered',[r['arrow_style'] for r in read])
    def test_background_import_stays_in_original_tab(self):
        from threading import Event
        ready=Event();d1=self.doc;d2=shell.new_document()
        d1._queue_import(lambda:(ready.wait(3),[image_record()])[1],QPointF(50,50))
        index=next(i for i in range(shell.tabbar.count()) if shell.tabbar.tabData(i) is d1)
        self.assertFalse(shell.close_document(index))
        self.assertIs(shell.current_document(),d2)
        ready.set();self.assertTrue(settle(lambda:d1._import_next>d1._import_seq))
        self.assertEqual(len(d1.view.all_items()),1);self.assertEqual(len(d2.view.all_items()),0)
    def test_newer_recovery_survives_older_manual_save(self):
        from threading import Event
        original=storage.save_project;started=Event();release=Event()
        destination=Path(TEMP.name)/'delayed.fbboard';doc=self.doc
        doc._add_records([new_record('text',text='first')])
        def delayed(path,*args,**kwargs):
            if str(path)==str(destination):started.set();release.wait(3)
            return original(path,*args,**kwargs)
        storage.save_project=delayed
        try:
            doc.save_project(path=destination)
            self.assertTrue(settle(started.is_set))
            doc._add_records([new_record('text',text='newer')])
            # A recovery already on disk must not be removed by an older snapshot finishing.
            original(doc.recovery_path,doc.view.records(),doc._project_meta())
            doc.recovery_marker.write_text('{"dirty":true}',encoding='utf-8')
            release.set();self.assertTrue(settle(lambda:not doc._manual_save_busy))
            self.assertTrue(doc.dirty);self.assertTrue(doc.recovery_path.is_file())
            self.assertEqual(len(storage.load_project(destination)[0]),1)
            self.assertEqual(len(storage.load_project(doc.recovery_path)[0]),2)
        finally:release.set();storage.save_project=original

    def test_close_after_save_cancels_delayed_ui_timer(self):
        doc=self.doc;doc._add_records([new_record('text',text='closing')])
        doc.save_project(path=Path(TEMP.name)/'close-after-save.fbboard')
        self.assertTrue(settle(lambda:not doc._manual_save_busy))
        index=next(i for i in range(shell.tabbar.count()) if shell.tabbar.tabData(i) is doc)
        self.assertTrue(shell.close_document(index))
        QTest.qWait(4700);app.processEvents()
        self.assertNotIn(doc,shell.documents)

    def test_tab_reorder_and_close(self):
        doc=self.doc;doc._add_records([new_record('text',text='keep')])
        other=shell.new_document();old=len(shell.documents)
        index=next(i for i in range(shell.tabbar.count()) if shell.tabbar.tabData(i) is doc)
        shell.tabbar.moveTab(index,0);shell.tabbar.setCurrentIndex(0);app.processEvents()
        self.assertIs(shell.current_document(),doc)
        other._confirm_unsaved=lambda:False
        other_index=next(i for i in range(shell.tabbar.count()) if shell.tabbar.tabData(i) is other)
        self.assertFalse(shell.close_document(other_index));self.assertEqual(len(shell.documents),old)
        other._confirm_unsaved=lambda:True
        self.assertTrue(shell.close_document(other_index));app.processEvents()
        self.assertEqual(len(doc.view.all_items()),1)
        self.assertEqual(len(shell.documents),old-1)

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ReleaseTests))
    for doc in shell.documents:
        doc._confirm_unsaved=lambda:True
    settle(lambda:all(not d.jobs.callbacks for d in shell.documents),8)
    shell.close();app.processEvents()
    print('UNEXPECTED_ERRORS:',errors)
    sys.exit(0 if result.wasSuccessful() and not errors else 1)


