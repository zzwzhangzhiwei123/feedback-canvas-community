"""v0.6.4 regression: window chrome, minimal sync, custom colors, link-based LAN sharing.
Synthetic data, loopback only, never elevates or touches the real firewall/registry."""
import os,sys,json,socket,tempfile,time,unittest,urllib.request,urllib.error
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
TEMP=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent,prefix='v064-')
os.environ['FEEDBACK_DATA_DIR']=TEMP.name
from PySide6.QtCore import Qt,QPoint,QPointF,QEvent
from PySide6.QtGui import QColor,QMouseEvent
from PySide6.QtWidgets import QApplication,QMessageBox,QColorDialog
from PySide6.QtTest import QTest
app=QApplication.instance() or QApplication(['test-v064']);app.setStyle('Fusion')
from main import MainWindow
from workspace import WorkspaceWindow
from items import new_record
import storage,lan_core,lan_ui,lan_firewall,colorpicker
from lan_core import ShareService,ShareClient,make_link,find_link,parse_link,ShareError
errors=[];warnings=[]
sys.excepthook=lambda t,v,tb:(errors.append(str(v)),sys.__excepthook__(t,v,tb))
QMessageBox.warning=lambda *a:warnings.append(str(a[-1]))
QMessageBox.information=lambda *a:None
ANSWER={'value':QMessageBox.StandardButton.No}
QMessageBox.question=lambda *a,**k:ANSWER['value']

def settle(predicate,timeout=10):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        app.processEvents()
        if predicate():return True
        QTest.qWait(5)
    return False

def valid(path):storage.load_project(str(path))

class LinkFormat(unittest.TestCase):
    def setUp(self):
        self.root=Path(TEMP.name)/('link-'+str(time.time_ns()));self.root.mkdir()
        self.src=self.root/'a.fbboard';storage.save_project(str(self.src),[new_record('text',text='hi')],{})
    def test_roundtrip_and_extraction(self):
        secret='A'*32;fp='ab'*32
        link=make_link('192.168.3.7',48761,secret,fp,['10.0.0.5'])
        self.assertTrue(link.startswith('http://192.168.3.7:48761/fb#'));self.assertLess(len(link),110)
        for text in (link,'反馈画布共享：'+link+' 打开看看',
                     'fbcanvas:192.168.3.7:48761/'+link.split('#')[1],'fbcanvas://192.168.3.7:48761/'+link.split('#')[1]):
            self.assertEqual(find_link(text),link,text)
        address,port,s2,fp2,extra=parse_link(link)
        self.assertEqual((address,port,s2,extra),('192.168.3.7',48761,secret,['10.0.0.5']));self.assertEqual(fp2,fp[:32])
        self.assertIsNone(find_link('http://192.168.3.7:48761/fb#short'));self.assertIsNone(find_link('普通文字'))
        legacy='https://10.1.2.3:48761/#key='+'k'*43+'&fp='+'c'*64
        self.assertEqual(parse_link('看这个 '+legacy)[0],'10.1.2.3')
    def test_bind_all_link_landing_page_and_tls_same_port(self):
        service=ShareService(self.src,self.root/'srv',valid,'0.0.0.0',0,advertise=['127.0.0.1'])
        try:
            self.assertTrue(service.bind_all)
            client=ShareClient(service.connection_code,'guest');self.assertEqual(client.status()['revision'],0)
            self.assertEqual(ShareClient(service.local_code,'host').status()['revision'],0)
            # Browser / chat app opening the link gets a page, not credentials or content.
            page=urllib.request.urlopen(f'http://127.0.0.1:{service.port}/fb',timeout=5).read().decode('utf-8')
            self.assertIn('用反馈画布打开',page);self.assertNotIn(service.state.secret,page);self.assertNotIn('a.fbboard',page)
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{service.port}/v1/lease',data=b'{}',method='POST'),timeout=5)
            self.assertEqual(e.exception.code,403)
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(f'http://127.0.0.1:{service.port}/v1/project',timeout=5)
            lease=client.acquire()['lease'];client.release(lease)
        finally:service.stop()
    def test_busy_port_falls_back(self):
        blocker=socket.socket();blocker.bind(('0.0.0.0',0));blocker.listen();port=blocker.getsockname()[1]
        try:
            service=ShareService(self.src,self.root/'srv2',valid,'0.0.0.0',port,advertise=['127.0.0.1'])
            try:
                self.assertNotEqual(service.port,port);self.assertEqual(ShareClient(service.connection_code).status()['revision'],0)
            finally:service.stop()
        finally:blocker.close()
    def test_short_fingerprint_discovery_and_pin(self):
        from unittest.mock import patch
        service=ShareService(self.src,self.root/'srv3',valid,'0.0.0.0',0,advertise=['127.0.0.1'])
        try:
            moved=service.connection_code.replace('127.0.0.1:','127.0.0.2:')
            client=ShareClient(moved,'d');self.assertEqual(len(client.fingerprint),32)
            with patch('lan_discovery.locate',return_value=[('127.0.0.1',service.port)]):
                self.assertEqual(client.status()['revision'],0)
            from lan_discovery import message
            self.assertIsNotNone(message(json.dumps({'protocol':'feedback-lan-discovery-1','type':'locate','fingerprint':client.fingerprint,'nonce':'a'*24}).encode()))
            bad=ShareClient(service.connection_code,'x');bad.fingerprint='0'*32
            with self.assertRaises(ShareError) as e:bad.status()
            self.assertEqual(e.exception.status,403)
        finally:service.stop()

class Firewall(unittest.TestCase):
    def test_commands_cover_exe_and_ports(self):
        cmds=' & '.join(lan_firewall.commands(r'C:\apps\反馈画布-v0.6.4.exe'))
        self.assertIn(r'delete rule name=all dir=in program="C:\apps\反馈画布-v0.6.4.exe"',cmds)
        self.assertIn('profile=any',cmds);self.assertIn('localport=48761',cmds);self.assertIn('protocol=UDP localport=48762',cmds)
        self.assertFalse(lan_firewall.needs_setup(TEMP.name))  # never during development runs
        root=Path(TEMP.name)/'fw';lan_firewall.remember(root,r'C:\x.exe',True)
        self.assertFalse(lan_firewall.needs_setup(root,r'C:\X.exe') if os.name=='nt' else False)

class Shell(unittest.TestCase):
    def setUp(self):
        warnings.clear();errors.clear()
        self.w=WorkspaceWindow(MainWindow);self.w.resize(1300,820);self.w.show();app.processEvents()
    def tearDown(self):
        for d in self.w.documents:d._confirm_unsaved=lambda:True
        self.w.close();app.processEvents()
        while QApplication.overrideCursor():QApplication.restoreOverrideCursor()
        self.assertEqual(errors,[]);self.assertEqual(warnings,[])
    def move(self,point):
        handle=self.w.windowHandle();glob=self.w.mapToGlobal(point)
        event=QMouseEvent(QEvent.Type.MouseMove,QPointF(point),QPointF(glob),Qt.MouseButton.NoButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(handle,event)
    def test_edge_cursors(self):
        W,H=self.w.width(),self.w.height();C=Qt.CursorShape
        for point,shape in [((2,400),C.SizeHorCursor),((W-2,400),C.SizeHorCursor),((600,2),C.SizeVerCursor),((600,H-2),C.SizeVerCursor),
                            ((2,2),C.SizeFDiagCursor),((W-2,H-2),C.SizeFDiagCursor),((W-2,2),C.SizeBDiagCursor),((2,H-2),C.SizeBDiagCursor)]:
            self.move(QPoint(*point));self.assertIsNotNone(QApplication.overrideCursor(),point)
            self.assertEqual(QApplication.overrideCursor().shape(),shape,point)
        self.move(QPoint(600,400));self.assertIsNone(QApplication.overrideCursor())
        self.w.showMaximized();app.processEvents();self.move(QPoint(1,1));self.assertIsNone(QApplication.overrideCursor())
    def test_toolbar_empty_area_moves_window(self):
        doc=self.w.current_document();calls=[];toggles=[]
        self.w._begin_system_move=lambda:calls.append(1);self.w.toggle_maximized=lambda:toggles.append(1)
        bar=doc.top_toolbar
        # brand label / empty stretch / toolbar background are drag areas; buttons are not
        from PySide6.QtWidgets import QLabel,QPushButton,QToolButton
        label=next(l for l in bar.findChildren(QLabel) if l.text()=='反馈画布')
        QTest.mousePress(label,Qt.MouseButton.LeftButton,pos=QPoint(10,10));self.assertEqual(len(calls),1)
        QTest.mousePress(bar,Qt.MouseButton.LeftButton,pos=QPoint(bar.width()//2,bar.height()//2))
        self.assertGreaterEqual(len(calls),1)
        button=next(b for b in bar.findChildren(QToolButton) if b.isVisible())
        before=len(calls);self.assertFalse(self.w._is_drag_area(button));self.assertTrue(self.w._is_drag_area(label))
        QTest.mouseDClick(label,Qt.MouseButton.LeftButton,pos=QPoint(10,10));self.assertEqual(len(toggles),1)
    def test_minimal_is_window_wide(self):
        a=self.w.current_document();b=self.w.new_document();c=self.w.new_document()
        b.actions['minimal'].setChecked(True);app.processEvents()
        for d in (a,b,c):
            self.assertTrue(d.actions['minimal'].isChecked());self.assertFalse(d.top_toolbar.isVisibleTo(d))
        d4=self.w.new_document();self.assertTrue(d4.actions['minimal'].isChecked());self.assertFalse(d4.toolbox.isVisibleTo(d4))
        a.actions['minimal'].setChecked(False);app.processEvents()
        for d in (a,b,c,d4):
            self.assertFalse(d.actions['minimal'].isChecked());self.assertTrue(d.top_toolbar.isVisibleTo(d))
    def test_paste_link_opens_share(self):
        opened=[];self.w.lan.connect_share=lambda code,name:opened.append(code)
        link=make_link('127.0.0.1',48761,'B'*32,'cd'*32)
        QApplication.clipboard().setText('同事：'+link);doc=self.w.current_document()
        before=len(doc.view.records());self.assertTrue(doc.paste())
        self.assertEqual(opened,[link]);self.assertEqual(len(doc.view.records()),before)
    def test_clipboard_offer_once_and_single_instance(self):
        opened=[];self.w.lan.connect_share=lambda code,name:opened.append(code)
        link=make_link('127.0.0.1',48761,'C'*32,'ef'*32)
        QApplication.clipboard().setText(link);ANSWER['value']=QMessageBox.StandardButton.Yes
        try:
            self.w.lan.check_clipboard();self.w.lan.check_clipboard()
        finally:ANSWER['value']=QMessageBox.StandardButton.No
        self.assertEqual(opened,[link])
        if self.w.lan.link_server.isListening():
            import subprocess
            # Never hand a test link to the copy of the app the user is running.
            self.assertTrue(self.w.lan.link_server.serverName().startswith('FeedbackCanvas-links-') and
                            len(self.w.lan.link_server.serverName().rsplit('-',1)[-1])==12,'test pipe must be isolated')
            other=make_link('127.0.0.1',48761,'D'*32,'aa'*32)
            # A second launch (browser fbcanvas: link) hands the link to this window and exits.
            code=f"import sys;sys.path.insert(0,{str(Path(__file__).parent)!r});from PySide6.QtCore import QCoreApplication;a=QCoreApplication([]);import lan_ui;print(lan_ui.forward_link({other!r},3000))"
            proc=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,text=True)
            self.assertTrue(settle(lambda:proc.poll() is not None,20))
            self.assertEqual(proc.stdout.read().strip(),'True')
            self.assertTrue(settle(lambda:len(opened)==2));self.assertEqual(opened[-1],other)

class EndToEnd(unittest.TestCase):
    def test_one_click_share_then_paste_link_on_other_window(self):
        warnings.clear()
        root=Path(TEMP.name)/('e2e-'+str(time.time_ns()));root.mkdir()
        path=root/'角色反馈.fbboard';storage.save_project(str(path),[new_record('text',text='来自共享方')],{})
        host=WorkspaceWindow(MainWindow);host.show();guest=WorkspaceWindow(MainWindow);guest.show()
        try:
            self.assertTrue(host.open_document(str(path)))
            self.assertTrue(settle(lambda:not host.current_document()._loading_project))
            host.lan.quick_share()
            self.assertTrue(settle(lambda:host.lan.host_binding is not None and not host.lan.host_binding.busy,20),warnings)
            service=host.lan.service;self.assertTrue(service.bind_all)
            link=QApplication.clipboard().text();self.assertEqual(link,service.connection_code)
            self.assertTrue(link.startswith('http://') and '/fb#' in link)
            # Receiver: just Ctrl+V on the canvas (connects to this PC's real LAN address when available).
            QApplication.clipboard().setText('看下这个：'+link)
            self.assertTrue(guest.current_document().paste())
            self.assertTrue(settle(lambda:guest.lan.bindings and not next(iter(guest.lan.bindings)).initial,25),warnings)
            remote=next(iter(guest.lan.bindings))
            self.assertEqual(remote.doc.view.records()[0]['text'],'来自共享方')
            self.assertEqual(warnings,[])
        finally:
            for shell in (guest,host):
                settle(lambda:not shell.lan.busy and all(not b.busy and not b.polling for b in shell.lan.bindings))
                for d in shell.documents:d._confirm_unsaved=lambda:True
                shell.close();app.processEvents()

class Colors(unittest.TestCase):
    def test_slots_logic_and_global_persistence(self):
        QColorDialog.setCustomColor(3,QColor('#ff8800'))
        path=colorpicker.store_path()
        if path.exists():path.unlink()
        colorpicker._STORE=None
        d=colorpicker.ColorPicker(QColor('#472e1e'))
        self.assertEqual(d.colors.colors[3],'#ffff8800')       # migrated from Qt's old custom wells
        self.assertIsNone(d.colors.colors[0])                   # untouched wells stay empty, not white
        d.slot_clicked(0)
        self.assertEqual(d.current().name(),'#472e1e')          # clicking empty never changes the color
        self.assertEqual(d.colors.colors[0],'#ff472e1e')
        d.slot_clicked(3);self.assertEqual(d.current().name(),'#ff8800')
        d.picker.setCurrentColor(QColor('#3366cc'));d.save_current();self.assertEqual(d.colors.colors[3],'#ff3366cc')
        d.selected=None;d.picker.setCurrentColor(QColor('#00aa00'));d.save_current();self.assertEqual(d.colors.colors[1],'#ff00aa00')
        d.clear_slot(1);self.assertIsNone(d.colors.colors[1])
        stored=json.loads(path.read_text(encoding='utf-8'))['colors']
        self.assertEqual(stored[0],'#ff472e1e');self.assertEqual(stored[3],'#ff3366cc')
        # A second dialog (any project/tab) sees the same slots.
        e=colorpicker.ColorPicker(QColor('#000000'));self.assertEqual(e.colors.colors[3],'#ff3366cc')
        for w in (d,e):w.deleteLater()

if __name__=='__main__':
    result=unittest.main(verbosity=2,exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
