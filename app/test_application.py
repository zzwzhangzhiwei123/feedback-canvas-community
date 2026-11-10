"""Bounded offscreen integration tests; never reads/writes the system clipboard."""
import os,sys,unittest,tempfile,json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['FEEDBACK_DATA_DIR']=str(ROOT/'.test-app-v011')
from PySide6.QtCore import Qt,QPoint,QPointF,QRectF,QTimer
from PySide6.QtGui import QImage,QColor,QWheelEvent,QKeyEvent
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtTest import QTest
from main import MainWindow
from items import new_record,image_to_base64,BoardItem
from board import point
import storage,contracts

app=QApplication.instance() or QApplication(['test'])
app.setStyle('Fusion')
failures=[]
QMessageBox.critical=lambda *a: failures.append(str(a[2]))
QMessageBox.warning=lambda *a: failures.append(str(a[2]))
w=MainWindow(); w.resize(1440,960); w.show(); app.processEvents()
QTimer.singleShot(90000,lambda:os._exit(80))
v=w.view
B=Qt.MouseButton; M=Qt.KeyboardModifier; K=Qt.Key

def raster():
    im=QImage(640,480,QImage.Format.Format_ARGB32);im.fill(QColor('#acc8be'))
    return new_record('image',x=-250,y=-160,w=640,h=480,scale=.65,image=image_to_base64(im))

def load(records):
    w._load_document(records,{},None,clean=True)
    v.reset_zoom();v.centerOn(0,0);app.processEvents()

def wheel(delta=120):
    p=v.viewport().rect().center()
    e=QWheelEvent(QPointF(p),QPointF(v.viewport().mapToGlobal(p)),QPoint(),QPoint(0,delta),B.NoButton,M.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
    QApplication.sendEvent(v.viewport(),e)

def drag(a,b,mods=M.NoModifier,button=B.LeftButton):
    a=v.mapFromScene(QPointF(*a));b=v.mapFromScene(QPointF(*b))
    QTest.mousePress(v.viewport(),button,mods,a)
    QTest.mouseMove(v.viewport(),b,10)
    QTest.mouseRelease(v.viewport(),button,mods,b)
    app.processEvents()

class Integration(unittest.TestCase):
    def setUp(self):
        failures.clear();load([])

    def tearDown(self):
        self.assertEqual(failures,[])

    def test_01_all_kinds_roundtrip(self):
        records=[new_record(k,x=idx*45,y=idx*20,text='中文反馈 ABC',image=raster()['image']) for idx,k in enumerate(['image','text','rect','ellipse','line','arrow','pen','polyline','bezier','number','region','redact','magnifier'])]
        with tempfile.TemporaryDirectory(dir=ROOT,delete=False) as td:
            path=str(Path(td)/'roundtrip.fbboard');storage.save_project(path,records,{'view':{'zoom':1}})
            back,meta=storage.load_project(path)
            self.assertEqual(records,back);self.assertEqual(meta['view']['zoom'],1)
        image=storage.render_records(records)
        self.assertFalse(image.isNull())

    def test_02_clipboard_internal_and_raster(self):
        r=raster();t=new_record('text',text='仍可编辑',x=300,y=0,attached_to=r['id']);r['group']=t['group']='g'
        out=storage.clipboard_write([r,t]);mime=app.clipboard().mimeData()
        self.assertTrue(mime.hasImage());self.assertTrue(mime.hasFormat('image/png'));self.assertTrue(mime.hasFormat(contracts.MIME_TYPE))
        back=storage.clipboard_read();self.assertEqual(back,[r,t]);clone=w._clone(back)
        self.assertNotEqual(clone[0]['id'],r['id']);self.assertEqual(clone[1]['attached_to'],clone[0]['id'])
        self.assertEqual(clone[0]['group'],clone[1]['group']);self.assertGreater(out.width(),600)

    def test_03_single_copy_native_pixels(self):
        r=raster();im=storage.clipboard_write([r]);self.assertEqual((im.width(),im.height()),(640,480))
        load([r]);v.setTransform(v.transform().scale(.1,.1));im2=storage.clipboard_write(v.records())
        self.assertEqual(im.size(),im2.size())

    def test_04_font_wheel_and_undo(self):
        t=new_record('text',text='调字号',x=0,y=0,font_size=18);load([t]);v.select_items(v.all_items())
        zoom=v.zoom();wheel();self.assertEqual(v.selected()[0].record['font_size'],19);self.assertEqual(v.zoom(),zoom)
        QTest.qWait(300);w.undo();self.assertEqual(v.by_id(t['id']).record['font_size'],18);w.redo();self.assertEqual(v.by_id(t['id']).record['font_size'],19)

    def test_05_stroke_and_mixed_wheel(self):
        a=new_record('arrow',x=-50,y=0,stroke_width=3);t=new_record('text',text='混选',x=0,y=0)
        load([a,t]);v.select_items([v.by_id(a['id'])]);wheel();self.assertEqual(v.selected()[0].record['stroke_width'],3.5)
        QTest.qWait(300);v.select_items(v.all_items());before=v.zoom();wheel();self.assertGreater(v.zoom(),before)
        self.assertEqual(v.by_id(t['id']).record['font_size'],18)

    def test_06_straight_shapes_and_move(self):
        v.set_tool('rect');drag((-100,-100),(100,60));self.assertEqual(len(v.all_items()),1)
        r=v.all_items()[0];self.assertAlmostEqual(r.record['w'],200,delta=2);self.assertEqual(v.tool,'select')
        # Border hit keeps interior transparent for accessing the image underneath.
        before=QPointF(r.pos());drag((-100,-20),(-40,20));self.assertGreater(r.pos().x(),before.x()+50)

    def test_07_arrow_endpoint_edit(self):
        v.set_tool('arrow');drag((-150,-50),(100,60));item=v.all_items()[0]
        v.edit_nodes(item);start=item.mapToScene(item.node_positions()[-1]);drag((start.x(),start.y()),(180,100))
        last=item.mapToScene(item.node_positions()[-1]);self.assertAlmostEqual(last.x(),180,delta=2)
        self.assertAlmostEqual(last.y(),100,delta=2)

    def test_08_bezier_nodes(self):
        v.set_tool('bezier')
        drag((-120,30),(-70,-30))
        QTest.mouseClick(v.viewport(),B.LeftButton,M.NoModifier,v.mapFromScene(QPointF(120,70)))
        QTest.keyClick(v,K.Key_Return)
        self.assertEqual(len(v.all_items()),1)
        item=v.all_items()[0];self.assertEqual(item.record['kind'],'bezier');self.assertEqual(len(item.record['points']),2)
        self.assertNotEqual(item.record['points'][0]['out'],[0,0])
        v.edit_nodes(item);v._insert_node(item.mapToScene(QPointF(120,30)))
        self.assertEqual(len(item.record['points']),3);v._smooth_node(False);self.assertEqual(item.record['points'][v.active_node]['in'],[0,0])
        v._delete_node();self.assertEqual(len(item.record['points']),2)

    def test_09_path_cancel(self):
        v.set_tool('polyline');QTest.mouseClick(v.viewport(),B.LeftButton,M.NoModifier,v.mapFromScene(QPointF(0,0)))
        QTest.keyClick(v,K.Key_Escape);self.assertEqual(len(v.all_items()),0)

    def test_10_navigation(self):
        load([raster()]);before=v.mapToScene(v.viewport().rect().center());drag((0,0),(100,50),button=B.MiddleButton)
        after=v.mapToScene(v.viewport().rect().center());self.assertGreater((after-before).manhattanLength(),100)
        before=v.zoom();QTest.keyPress(v,K.Key_Z);drag((-40,0),(60,0));QTest.keyRelease(v,K.Key_Z);self.assertGreater(v.zoom(),before)
        QTest.keyClick(v,K.Key_0,M.ControlModifier);self.assertAlmostEqual(v.zoom(),1)

    def test_11_scale_rotate_attachment(self):
        r=raster();t=new_record('text',x=200,y=0,text='附着',attached_to=r['id'])
        load([r,t]);v.select_items([v.by_id(r['id'])]);v._snapshot();v.pivot=QPointF(0,0);v._apply_transform(2,30);v._commit()
        self.assertAlmostEqual(v.by_id(r['id']).scale(),1.3);self.assertAlmostEqual(v.by_id(t['id']).scale(),2)
        self.assertAlmostEqual(v.by_id(t['id']).rotation(),30)

    def test_12_crop_recovery(self):
        r=raster();load([r]);i=v.by_id(r['id']);v.select_items([i]);target=i.mapRectToScene(QRectF(100,100,250,200));v._crop_to_rect(i,target)
        self.assertIsNotNone(i.record['crop']);self.assertAlmostEqual(i.record['w'],250);v.reset_crop();self.assertIsNone(i.record['crop']);self.assertAlmostEqual(i.record['w'],640)

    def test_13_text_input_isolation_and_save(self):
        t=new_record('text',text='旧内容');load([t]);v.edit_text(v.by_id(t['id']));v.editor.setPlainText('新内容可以保存')
        commands=[];v.command.connect(commands.append)
        for key in (K.Key_N,K.Key_K,K.Key_L,K.Key_F): QTest.keyClick(v.editor,key,M.ControlModifier)
        self.assertEqual(commands,[])
        with tempfile.TemporaryDirectory(dir=ROOT,delete=False) as td:
            self.assertTrue(w.save_project(str(Path(td)/'new.fbboard')))
            records,_=storage.load_project(str(Path(td)/'new.fbboard'));self.assertEqual(records[0]['text'],'新内容可以保存')
        v.command.disconnect(commands.append)

    def test_14_layout_retains_pixels(self):
        r=raster();r['w']=4000;r['h']=3000
        out=w._layout_images([r])[0];self.assertEqual(out['w'],4000);self.assertLessEqual(out['w']*out['scale'],600)

    def test_15_magnifier_and_clone_links(self):
        r=raster();load([r]);v.select_items(v.all_items());w.dispatch_command('magnifier')
        self.assertEqual(len(v.all_items()),1)  # Choosing the tool waits for a region.
        drag((-220,-130),(-100,-30))
        self.assertEqual(len(v.all_items()),3)
        allids={i.record['id'] for i in v.all_items()};v.select_items(v.all_items());v.duplicate()
        copies=v.selected();ids={i.record['id'] for i in copies};self.assertTrue(ids.isdisjoint(allids))
        link=next(i for i in copies if i.record.get('link_source'));self.assertIn(link.record['link_source'],ids);self.assertIn(link.record['link_target'],ids)

    def test_16_exports_exclude_nonselection(self):
        a=new_record('rect',x=0,y=0,w=100,h=80,fill='#ffffff');b=new_record('rect',x=900,y=0,w=50,h=50,fill='#ff0000')
        load([a,b]);v.select_items([v.by_id(a['id'])]);im=storage.render_records(v.selection_records(),padding=0)
        self.assertLess(im.width(),110);self.assertEqual(im.pixelColor(50,40),QColor('#ffffff'))
        with self.assertRaises(ValueError):storage.render_records([a],scale=1e10)  # Native exports have no arbitrary size cap; reject format overflow.

    def test_17_group_lock_arrange(self):
        a=raster();b=raster();b['id']='image2';b['x']=500
        load([a,b]);v.select_items(v.all_items());v.group_selection();self.assertEqual(len({i.record['group'] for i in v.all_items()}),1)
        v.ungroup_selection();self.assertTrue(all(not i.record['group'] for i in v.all_items()))
        v.align_selection('top');bounds=[i.sceneBoundingRect().top() for i in v.all_items()];self.assertAlmostEqual(*bounds)
        v.toggle_lock();self.assertTrue(all(i.record['locked'] for i in v.all_items()));v.delete_selection();self.assertEqual(len(v.all_items()),2)

    def test_18_autosave_source_untouched(self):
        t=new_record('text',text='原稿');load([t]);v.select_items(v.all_items())
        with tempfile.TemporaryDirectory(dir=ROOT,delete=False) as td:
            p=Path(td)/'source.fbboard';w.save_project(str(p));before=p.read_bytes()
            v.set_property('text','已修改');self.assertTrue(w.autosave());self.assertEqual(p.read_bytes(),before)
            records,_=storage.load_project(str(w.recovery_path));self.assertEqual(records[0]['text'],'已修改')
            w._clear_recovery()

    def test_19_no_invented_hotkeys(self):
        expected={'new':'Ctrl+K','open':'Ctrl+L','new_text':'Ctrl+N','redo':'Ctrl+Shift+Z','fullscreen':'Ctrl+F'}
        for key,value in expected.items():self.assertEqual(w.actions[key].shortcut().toString(),value)
        for key in ('sidebar','minimal','help'):self.assertTrue(w.actions[key].shortcut().isEmpty())

    def test_20_screenshot_preview(self):
        records,meta=storage.load_project(str(ROOT/'outputs'/'示例反馈.fbboard'))
        w._load_document(records,meta,None,clean=True);v.focus_items();v.select_items([])
        app.processEvents();QTest.qWait(100);app.processEvents()
        w._view_tick();w.message('PureRef 操作  ·  可编辑标注  ·  本地离线工作台',0)
        self.assertTrue(w.grab().save(str(ROOT/'outputs'/f'界面预览-v{contracts.APP_VERSION}.png')))

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Integration))
    summary={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful(),'platform':'Qt offscreen on Windows','coverage':'model, gestures, node editing, clipboard MIME, project, autosave, UI','not_tested':['WeChat/QQ/Photoshop live paste','physical mixed-DPI multi-monitor capture']}
    (ROOT/'outputs'/f'测试结果-v{contracts.APP_VERSION}.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    w._closing=True;w.hide();app.clipboard().clear();app.processEvents()
    sys.exit(0 if result.wasSuccessful() else 1)
