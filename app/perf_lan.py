"""LAN sluggishness probe: worst UI freeze on host/guest during connect, refresh, edit and save.
Two in-process windows over loopback; synthetic project; real Windows backend; no clipboard."""
import os,sys,time,json,statistics
from pathlib import Path
here=Path(__file__).resolve().parent;sys.path.insert(0,str(here))
os.environ['QT_QPA_PLATFORM']=os.environ.get('PERF_QPA','offscreen');os.environ['FEEDBACK_DATA_DIR']=str(here.parent/'.perf-data'/str(time.time_ns()))
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtTest import QTest
app=QApplication([]);app.setStyle('Fusion')
from perf_probe import screenshot,Stall
from main import MainWindow
from workspace import WorkspaceWindow
from items import new_record,image_to_base64
import storage
QMessageBox.question=lambda *a,**k:QMessageBox.StandardButton.No
QMessageBox.warning=lambda *a:print('WARN',a[-1]);QMessageBox.information=lambda *a:None

def timed(label,start,done,timeout=60):
    stall=Stall();stall.start();t=time.perf_counter();start()
    end=time.monotonic()+timeout
    while not done() and time.monotonic()<end:app.processEvents();QTest.qWait(1)
    for _ in range(5):app.processEvents();QTest.qWait(2)
    return {'step':label,'total_ms':round((time.perf_counter()-t)*1000),'worst_freeze_ms':stall.stop(),'ok':done()}

def main(label):
    root=here.parent/'.perf-data'/('lan-'+str(time.time_ns()));root.mkdir(parents=True)
    path=root/'big.fbboard'
    recs=[new_record('image',x=(n%3)*4000,y=(n//3)*2300,w=3840,h=2160,image=image_to_base64(screenshot(seed=n))) for n in range(6)]
    recs+=[new_record('text',x=i*300,y=-400,text=f'反馈 {i}') for i in range(30)]
    storage.save_project(str(path),recs,{})
    host=WorkspaceWindow(MainWindow);host.resize(1400,900);host.show()
    guest=WorkspaceWindow(MainWindow);guest.resize(1400,900);guest.show();app.processEvents()
    steps=[{'project_mb':round(path.stat().st_size/1e6,1)}]
    steps.append(timed('host open',lambda:host.open_document(str(path)),lambda:not host.current_document()._loading_project))
    steps.append(timed('host share',lambda:host.lan.start_share(str(path),'127.0.0.1',0),lambda:host.lan.host_binding is not None and not host.lan.host_binding.busy and not host.lan.host_binding.initial))
    steps.append(timed('guest connect+load',lambda:guest.lan.connect_share(host.lan.service.connection_code,'Guest'),lambda:bool(guest.lan.bindings) and not next(iter(guest.lan.bindings)).initial and not next(iter(guest.lan.bindings)).busy))
    b=next(iter(guest.lan.bindings))
    steps.append(timed('guest acquire',b.acquire,lambda:not b.busy and b.editable))
    def edit():
        r=b.doc.view.records();t=[x for x in r if x['kind']=='text'][0];t['text']='已修改';b.doc.view.by_id(t['id']).apply_record(t);b.doc.checkpoint()
    edit()
    steps.append(timed('guest save upload',lambda:b.save(),lambda:not b.busy and not b.doc._manual_save_busy))
    hb=host.lan.host_binding
    steps.append(timed('host poll+refresh',lambda:hb.poll(),lambda:hb.revision==1 and not hb.busy and not hb.polling))
    # idle polling cost over 12 s (two heartbeat cycles)
    stall=Stall();stall.start();t=time.monotonic()
    while time.monotonic()-t<12:app.processEvents();QTest.qWait(1)
    steps.append({'step':'idle 12s connected (polling)','worst_freeze_ms':stall.stop()})
    for shell in (guest,host):
        end=time.monotonic()+20
        while (shell.lan.busy or any(x.busy or x.polling for x in shell.lan.bindings)) and time.monotonic()<end:app.processEvents();QTest.qWait(2)
        for d in shell.documents:d._confirm_unsaved=lambda:True
        shell.close();app.processEvents()
    return {'label':label,'steps':steps}
if __name__=='__main__':
    r=main(sys.argv[1] if len(sys.argv)>1 else 'run');print(json.dumps(r,ensure_ascii=False,indent=1))
    with open(here.parent.parent/'perf-results.jsonl','a',encoding='utf-8') as f:f.write(json.dumps(r,ensure_ascii=False)+'\n')
