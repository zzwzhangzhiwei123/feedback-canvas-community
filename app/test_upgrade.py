"""v0.2 interaction, geometry and persisted-document regression tests."""
import os, sys, json, unittest, tempfile, hashlib
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['FEEDBACK_DATA_DIR'] = str(ROOT / '.test-upgrade')
from PySide6.QtCore import Qt, QPointF, QRectF, QPoint, QTimer
from PySide6.QtGui import QImage, QColor, QPainter, QWheelEvent, QPen
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest
from main import MainWindow, runtime_directory
from items import new_record, BoardItem, image_to_base64
from board import point
import storage

app=QApplication.instance() or QApplication(['upgrade'])
app.setStyle('Fusion')
errors=[]
QMessageBox.critical=lambda *a:errors.append(str(a[2]))
QMessageBox.warning=lambda *a:errors.append(str(a[2]))
w=MainWindow(); w.resize(1440,960); w.show(); app.processEvents()
v=w.view
B=Qt.MouseButton; M=Qt.KeyboardModifier; K=Qt.Key

def click(pos):
    QTest.mouseClick(v.viewport(),B.LeftButton,M.NoModifier,v.mapFromScene(QPointF(*pos)))
    app.processEvents()

def drag(a,b):
    a=v.mapFromScene(a if isinstance(a,QPointF) else QPointF(*a))
    b=v.mapFromScene(b if isinstance(b,QPointF) else QPointF(*b))
    QTest.mousePress(v.viewport(),B.LeftButton,M.NoModifier,a)
    QTest.mouseMove(v.viewport(),b,5)
    QTest.mouseRelease(v.viewport(),B.LeftButton,M.NoModifier,b)
    app.processEvents()

def image_record(**kw):
    image=QImage(600,300,QImage.Format.Format_ARGB32); image.fill('#f4f4f4')
    p=QPainter(image)
    p.fillRect(0,0,200,100,QColor('#ff0000'))
    p.fillRect(200,0,200,100,QColor('#00ff00'))
    p.fillRect(400,0,200,100,QColor('#0000ff'))
    p.setPen(QPen(QColor('#202020'),3));p.drawEllipse(QRectF(240,150,80,80));p.end()
    values=dict(image=image_to_base64(image),w=600,h=300)
    values.update(kw)
    return new_record('image',**values)

class Upgrade(unittest.TestCase):
    def setUp(self):
        errors.clear()
        v.set_tool('select')
        w._load_document([],{},None,True)
        v.style.update(stroke_width=3,stroke='#147d72',magnifier_shape='rect',arrow_style='standard')
        v.reset_zoom();v.centerOn(180,100);app.processEvents()

    def tearDown(self): self.assertEqual(errors,[])

    def source(self,**kw):
        r=image_record(**kw)
        w._load_document([r],{},None,True)
        return v.by_id(r['id'])

    def lens(self): return next(i for i in v.all_items() if i.record['kind']=='magnifier')

    def test_01_arrow_tip_and_proportional_head(self):
        for style in ['standard','slim','chevron','double']:
            sizes=[]
            for width in [4,2,1,.5,.1]:
                item=BoardItem(new_record('arrow',w=200,h=0,stroke_width=width,arrow_style=style))
                sizes.append(item._arrow.boundingRect().height()/width)
                self.assertLess(item._shaft.boundingRect().right(),200-width*.5)
            for value in sizes: self.assertAlmostEqual(value,sizes[0])
        zero=BoardItem(new_record('arrow',stroke_width=0))
        self.assertTrue(zero._arrow.isEmpty())
        # Render at 8x: a former round cap leaked pixels to the right of the tip.
        item=BoardItem(new_record('arrow',w=200,h=0,stroke_width=8))
        im=QImage(1800,300,QImage.Format.Format_ARGB32);im.fill(Qt.GlobalColor.transparent)
        p=QPainter(im);p.translate(40,150);p.scale(8,8);item.paint(p,None);p.end()
        self.assertFalse(any(im.pixelColor(x,y).alpha() for x in range(1642,1680) for y in range(120,180)))
        self.assertTrue(im.pixelColor(1620,150).alpha()>0)

    def test_02_arrow_style_variants_and_roundtrip(self):
        records=[new_record('arrow',arrow_style=s,stroke_width=2) for s in ['standard','slim','chevron','double']]
        paths=[BoardItem(r)._arrow for r in records]
        self.assertEqual(len(set((p.elementCount(),p.boundingRect().width(),p.boundingRect().height()) for p in paths)),4)
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            f=Path(td)/'arrows.fbboard';storage.save_project(f,records,{})
            self.assertEqual(storage.load_project(f)[0],records)

    def test_03_lens_drag_off_center_rectangle(self):
        self.source(); w.choose_tool('magnifier');v.style['magnifier_shape']='rect'
        self.assertEqual(len(v.all_items()),1)
        drag((10,10),(130,70))
        lens=self.lens();crop=lens.record['crop']
        for actual,expected in zip(crop,[10/600,10/300,120/600,60/300]):self.assertAlmostEqual(actual,expected,places=2)
        self.assertAlmostEqual(lens.record['w']/lens.record['h'],crop[2]*600/(crop[3]*300))
        self.assertEqual(v.tool,'magnifier')  # Tools stay active after drawing since v0.5.1.
        im=storage.render_records([lens.to_record()],padding=0)
        self.assertEqual(im.pixelColor(im.width()//2,im.height()//2),QColor('#ff0000'))
        w.undo();self.assertEqual(len(v.all_items()),1)
        w.redo();self.assertEqual(len(v.all_items()),3)

    def test_04_lens_circle_pixel_square(self):
        self.source();w.choose_tool('magnifier');v.style['magnifier_shape']='circle'
        drag((410,10),(530,70))
        lens=self.lens();r=lens.record
        self.assertAlmostEqual(r['crop'][2]*600,r['crop'][3]*300)
        self.assertAlmostEqual(r['w'],r['h'])
        self.assertAlmostEqual(lens._source_rect.width(),lens._source_rect.height())

    def test_05_lens_transformed_flipped_cropped_source(self):
        source=self.source(crop=[.1,.1,.8,.8],w=480,h=240,rotation=20,scale=.6,flip_x=True)
        w.choose_tool('magnifier');v.style['magnifier_shape']='rect'
        drag(source.mapToScene(QPointF(20,20)),source.mapToScene(QPointF(160,100)))
        lens=self.lens();r=lens.record
        self.assertAlmostEqual(r['crop'][0],.1+(1-160/480)*.8,delta=.006)
        self.assertAlmostEqual(r['crop'][1],.1+20/240*.8,delta=.006)
        self.assertTrue(r['flip_x'])
        v.update_links()
        connector=next(i for i in v.all_items() if i.record.get('link_source'))
        expected=source.mapToScene(QPointF(90,60))
        self.assertLess((connector.pos()-expected).manhattanLength(),3)

    def test_06_lens_cancel_and_click_without_drag(self):
        self.source();w.choose_tool('magnifier');click((50,30))
        self.assertEqual(len(v.all_items()),1)
        QTest.mousePress(v.viewport(),B.LeftButton,M.NoModifier,v.mapFromScene(QPointF(30,20)))
        QTest.mouseMove(v.viewport(),v.mapFromScene(QPointF(100,80)),5)
        QTest.keyClick(v,K.Key_Escape)
        QTest.mouseRelease(v.viewport(),B.LeftButton,M.NoModifier,v.mapFromScene(QPointF(100,80)))
        self.assertEqual(len(v.all_items()),1);self.assertIsNone(v.lens_source)

    def test_07_blank_and_escape_clear_nodes_once(self):
        item=v.add_records([new_record('bezier',curve_mode='auto',points=[point(0,0),point(100,60),point(200,0)])])[0]
        self.assertIs(v.node_item,item)
        click((-100,-100));self.assertFalse(v.selected());self.assertIsNone(v.node_item)
        v.select_items([item]);QTest.keyClick(v,K.Key_Escape)
        self.assertFalse(v.selected());self.assertIsNone(v.node_item);self.assertEqual(v.tool,'select')

    def test_08_curve_clicks_auto_smooth_and_drag_after_reselect(self):
        v.set_tool('bezier')
        click((0,0));click((100,80));click((200,0))
        curve=v.active_path
        self.assertEqual(curve.record['curve_mode'],'auto')
        self.assertGreater(abs(curve.record['points'][1]['out'][0]),1)
        # Edit any existing anchor before finishing.
        drag((100,80),(100,110))
        self.assertAlmostEqual(curve.record['points'][1]['y'],110,delta=1)
        QTest.keyClick(v,K.Key_Return)
        click((-100,-100));click((100,110))
        self.assertIs(v.node_item,curve)
        drag((100,110),(120,90))
        self.assertAlmostEqual(curve.record['points'][1]['x'],120,delta=1)
        w.undo();restored=v.all_items()[0]
        self.assertAlmostEqual(restored.record['points'][1]['x'],100,delta=1)
        w.redo();self.assertAlmostEqual(v.all_items()[0].record['points'][1]['x'],120,delta=1)

    def test_09_curve_insert_delete_and_legacy(self):
        curve=v.add_records([new_record('bezier',curve_mode='auto',points=[point(0,0),point(100,80),point(200,0)])])[0]
        v._insert_node(QPointF(40,50));self.assertEqual(len(curve.record['points']),4)
        v._delete_node();self.assertEqual(len(curve.record['points']),3)
        record=new_record('bezier',points=[dict(point(0,0),out=[20,40]),dict(point(100,0),**{'in':[-20,40]})])
        legacy=BoardItem(record)
        self.assertEqual(legacy.record['curve_mode'],'manual')
        self.assertEqual(legacy.record['points'],record['points'])

    def test_10_rect_lens_resize_crop_preserves_ratio(self):
        source=self.source();w.create_magnifier(source,QRectF(20,20,160,80))
        lens=self.lens();r=lens.to_record()
        r.update(w=500,h=500);lens.apply_record(r)
        self.assertAlmostEqual(lens.record['w']/lens.record['h'],2)
        r=lens.to_record();r['crop']=[0,0,.5,.5];lens.apply_record(r)
        self.assertAlmostEqual(lens.record['w']/lens.record['h'],2)

    def test_11_new_document_save_load_and_source_unchanged(self):
        source=self.source();w.create_magnifier(source,QRectF(240,140,100,100))
        v.add_records([new_record('bezier',curve_mode='auto',points=[point(0,0),point(40,30),point(80,0)])])
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            f=Path(td)/'new.fbboard';storage.save_project(f,v.records(),{})
            records,_=storage.load_project(f)
            before=hashlib.sha256(f.read_bytes()).hexdigest()
            w._load_document(records,{},str(f),True)
            v.select_items(v.all_items());v.set_property('stroke_width',4);w.autosave()
            self.assertEqual(hashlib.sha256(f.read_bytes()).hexdigest(),before)
            self.assertTrue(any(r.get('curve_mode')=='auto' for r in records))

    def test_12_style_controls_and_degenerate_arrow_click(self):
        combo=w.style_controls['arrow_style'];self.assertEqual(combo.count(),4)
        self.assertEqual(w.style_controls['magnifier_shape'].count(),2)
        v.set_tool('arrow');click((20,20))
        self.assertFalse(v.all_items());self.assertFalse(v.selected())

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Upgrade)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    (ROOT/'outputs'/'升级回归-v0.2.0.json').write_text(json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful()},indent=2),encoding='utf-8')
    w._closing=True;w.close()
    sys.exit(0 if result.wasSuccessful() else 1)
