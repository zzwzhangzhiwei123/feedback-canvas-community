"""Editor + LAN lifecycle integration, isolated synthetic projects on loopback."""
import os,sys,tempfile,time,unittest,threading
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
TEMP=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent,prefix='lan-ui-')
os.environ['FEEDBACK_DATA_DIR']=TEMP.name
from PySide6.QtCore import Qt,QPointF
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtTest import QTest
from main import MainWindow
from workspace import WorkspaceWindow
from items import new_record
import storage
from lan_core import file_hash
app=QApplication.instance() or QApplication(['test-lan']);app.setStyle('Fusion')
errors=[];warnings=[]
sys.excepthook=lambda t,v,tb:(errors.append(str(v)),sys.__excepthook__(t,v,tb))
QMessageBox.warning=lambda *args:warnings.append(str(args[-1]))
QMessageBox.information=lambda *args:None
QMessageBox.question=lambda *args:QMessageBox.StandardButton.No

def settle(predicate,timeout=12):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        app.processEvents()
        if predicate():return True
        QTest.qWait(5)
    return False

class UiTests(unittest.TestCase):
    def setUp(self):
        warnings.clear();errors.clear()
        self.root=Path(TEMP.name)/str(time.time_ns());self.root.mkdir()
        self.path=self.root/'source.fbboard';storage.save_project(str(self.path),[new_record('text',text='original')],{'app_version':'0.6.0'})
        self.host=WorkspaceWindow(MainWindow);self.host.show();app.processEvents()
        self.host.lan.start_share(str(self.path),'127.0.0.1',0)
        self.assertTrue(settle(lambda:self.host.lan.host_binding is not None and not self.host.lan.host_binding.busy),warnings)
        self.owner=self.host.lan.host_binding
        self.guest=WorkspaceWindow(MainWindow);self.guest.show();app.processEvents()
        self.guest.lan.connect_share(self.host.lan.service.connection_code,'Guest')
        self.assertTrue(settle(lambda:len(self.guest.lan.bindings)>0 and not next(iter(self.guest.lan.bindings)).busy),warnings)
        self.remote=next(iter(self.guest.lan.bindings))
        self.assertFalse(self.owner.initial);self.assertFalse(self.remote.initial)
    def tearDown(self):
        for shell in (self.guest,self.host):
            settle(lambda:not shell.lan.busy and all(not b.busy and not b.polling for b in shell.lan.bindings))
            for doc in shell.documents:doc._confirm_unsaved=lambda:True
            shell.close();app.processEvents()
        self.assertEqual(errors,[]);self.assertEqual(warnings,[])
    def acquire(self,b):
        b.acquire();self.assertTrue(settle(lambda:not b.busy));self.assertTrue(b.editable,b.last_error)
    def modify(self,b,text):
        d=b.doc;record=d.view.records()[0];record['text']=text
        d.view.by_id(record['id']).apply_record(record);d.checkpoint();self.assertTrue(d.dirty)
    def test_edit_save_view_and_release(self):
        b=self.remote;d=b.doc
        self.assertFalse(b.editable);self.assertFalse(d.toolbox.isEnabled())
        before=d.view.records();d.view.setFocus();QTest.keyClick(d.view,Qt.Key.Key_Delete);QTest.keyClick(d.view,Qt.Key.Key_B)
        self.assertEqual(d.view.records(),before)
        self.acquire(b);self.owner.acquire();self.assertTrue(settle(lambda:not self.owner.busy));self.assertFalse(self.owner.editable)
        self.modify(b,'guest update');self.assertTrue(d.save_project(wait=True));self.assertFalse(d.dirty)
        self.assertEqual(storage.load_project(str(self.path))[0][0]['text'],'guest update')
        self.owner.poll();self.assertTrue(settle(lambda:self.owner.revision==1 and not self.owner.busy))
        self.assertEqual(self.owner.doc.view.records()[0]['text'],'guest update')
        b.finish();self.assertTrue(settle(lambda:not b.busy and not b.lease))
        self.acquire(self.owner);self.modify(self.owner,'owner update');self.assertTrue(self.owner.doc.save_project(wait=True))
        self.assertEqual(storage.load_project(str(self.path))[0][0]['text'],'owner update')
    def test_stop_keeps_unsent_work_and_local_copy(self):
        b=self.remote;self.acquire(b);self.modify(b,'unsent')
        self.host.lan.stop_share();self.assertTrue(settle(lambda:self.host.lan.service is None and not self.host.lan.busy))
        b.poll();self.assertTrue(settle(lambda:not b.connected));self.assertFalse(b.editable)
        self.assertTrue(b.doc.dirty);self.assertEqual(b.doc.view.records()[0]['text'],'unsent')
        self.assertEqual(storage.load_project(str(self.path))[0][0]['text'],'original')
        copy=self.root/'local.fbboard';self.assertTrue(b.save_copy(str(copy),wait=True))
        self.assertIsNone(b.doc._lan_binding);self.assertTrue(b.doc.toolbox.isEnabled());self.assertTrue(b.doc.top_toolbar.isEnabled())
        self.assertEqual(storage.load_project(str(copy))[0][0]['text'],'unsent')
    def test_stop_during_host_save_does_not_hang(self):
        b=self.owner;self.acquire(b);self.modify(b,'not submitted')
        started=threading.Event();release=threading.Event();original=b.client.upload
        def delay(*args,**kwargs):started.set();release.wait(3);return original(*args,**kwargs)
        b.client.upload=delay;b.save();self.assertTrue(settle(started.is_set))
        self.host.lan.stop_share();release.set()
        self.assertTrue(settle(lambda:self.host.lan.service is None and not self.host.lan.busy))
        self.assertFalse(b.doc._manual_save_busy);self.assertIsNone(b.doc._lan_binding);self.assertTrue(b.doc.dirty)
        self.assertIsNone(b.doc.current_file);self.assertEqual(storage.load_project(str(self.path))[0][0]['text'],'original')
    def test_disconnect_then_revision_conflict_preserves_work(self):
        b=self.remote;self.acquire(b);self.modify(b,'stale guest')
        state=self.host.lan.service.state
        with state.lock:state.lease['expires']=time.monotonic()-1
        b.poll();self.assertTrue(settle(lambda:not b.polling and not b.editable))
        self.acquire(self.owner);self.modify(self.owner,'new owner');self.owner.save(wait=True);self.owner.finish()
        self.assertTrue(settle(lambda:not self.owner.busy and not self.owner.lease))
        b.poll();self.assertTrue(settle(lambda:not b.polling and b.connected));b.acquire()
        self.assertTrue(settle(lambda:not b.busy));self.assertFalse(b.editable);self.assertTrue(b.doc.dirty)
        self.assertEqual(b.doc.view.records()[0]['text'],'stale guest')
        self.assertEqual(storage.load_project(str(self.path))[0][0]['text'],'new owner')

if __name__=='__main__':unittest.main(verbosity=2)
