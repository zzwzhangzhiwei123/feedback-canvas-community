"""Optional LAN UI adapter. The canvas and file format do not depend on LAN."""
from pathlib import Path
import os
import socket
import ipaddress
import time
import uuid
import getpass
from PySide6.QtCore import QObject,QTimer,QEvent,Qt,QPointF
from PySide6.QtNetwork import QNetworkInterface,QAbstractSocket,QLocalServer,QLocalSocket
from PySide6.QtWidgets import (QApplication,QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QGroupBox,
    QLabel,QLineEdit,QPushButton,QComboBox,QSpinBox,QFileDialog,QMessageBox,QToolBar,QPlainTextEdit,QWidget,QToolButton)
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
import storage
import assets_runtime as assets
from jobs import Jobs
from lan_core import ShareClient,ShareService,ShareError,file_hash,find_link,parse_link,DEFAULT_PORT
import lan_firewall


def link_server_name():
    """Per-user pipe: a link opened from a browser/explorer goes to the already running window.
    Isolated data directories (tests, FEEDBACK_DATA_DIR) get their own pipe, so they can never
    hand links to the copy of the app the user is actually running."""
    try:user=getpass.getuser()
    except Exception:user='user'
    name='FeedbackCanvas-links-'+''.join(c for c in user if c.isalnum())[:40]
    override=os.environ.get('FEEDBACK_DATA_DIR')
    if override:
        import hashlib
        name+='-'+hashlib.sha256(os.path.abspath(override).encode('utf-8')).hexdigest()[:12]
    return name


def forward_link(link,timeout=800):
    """Client side of single-instance handoff. True when a running window accepted the link."""
    sock=QLocalSocket()
    sock.connectToServer(link_server_name())
    if not sock.waitForConnected(timeout):return False
    payload=link.encode('utf-8')
    sock.write(payload);sock.flush()
    deadline=time.monotonic()+timeout/1000
    while sock.bytesToWrite() and time.monotonic()<deadline:sock.waitForBytesWritten(50)
    # The receiver replies with one byte once it has the link; closing earlier can drop it.
    ok=sock.waitForReadyRead(max(200,timeout*2)) and bytes(sock.read(1))==b'1'
    sock.disconnectFromServer()
    if sock.state()!=QLocalSocket.LocalSocketState.UnconnectedState:sock.waitForDisconnected(timeout)
    return ok


def register_url_scheme(exe):
    """fbcanvas: links (browser landing page button) launch this exe. Per-user, no admin."""
    if os.name!='nt' or not exe:return False
    import winreg
    command=f'"{exe}" "%1"'
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Classes\fbcanvas') as key:
            winreg.SetValueEx(key,'',0,winreg.REG_SZ,'URL:反馈画布共享链接')
            winreg.SetValueEx(key,'URL Protocol',0,winreg.REG_SZ,'')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Classes\fbcanvas\DefaultIcon') as key:
            winreg.SetValueEx(key,'',0,winreg.REG_SZ,f'"{exe}",0')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Classes\fbcanvas\shell\open\command') as key:
            winreg.SetValueEx(key,'',0,winreg.REG_SZ,command)
        return True
    except OSError:return False


def network_interfaces():
    rows=[];seen=set()
    for iface in QNetworkInterface.allInterfaces():
        flags=iface.flags();F=QNetworkInterface.InterfaceFlag
        if not flags & F.IsUp or not flags & F.IsRunning or flags & F.IsLoopBack:continue
        name=iface.humanReadableName();lower=(name+' '+iface.name()).lower()
        virtual=any(token in lower for token in ('virtual','vmware','vethernet','hyper-v','wsl','docker','vpn','tun','tap','tailscale','zerotier','虚拟'))
        for entry in iface.addressEntries():
            if entry.ip().protocol()!=QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:continue
            value=entry.ip().toString();ip=ipaddress.ip_address(value)
            if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local or value in seen:continue
            seen.add(value);rows.append((virtual,name,value))
    return [(name,value) for _,name,value in sorted(rows)]

def prepare_client(code,name):
    client=ShareClient(code,name)
    client.local_addresses=[ip for _,ip in network_interfaces()][:8]
    return client


def outcome(function):
    try:return {'ok':True,'value':function()}
    except Exception as exc:return {'ok':False,'error':str(exc),'status':getattr(exc,'status',0)}


class ReadOnlyGate(QObject):
    def __init__(self,binding):
        super().__init__(binding);self.binding=binding;self.panning=False
        binding.doc.view.installEventFilter(self);binding.doc.view.viewport().installEventFilter(self)
    def eventFilter(self,watched,event):
        b=self.binding;v=b.doc.view
        if b.disposed:return False
        if b.editable and not b.busy and time.monotonic()>b.deadline:b.lose_connection('编辑权限需要重新确认；修改保留在本地。')
        # While saving/refreshing the canvas stays pannable and zoomable, but not editable.
        if b.editable and not b.busy:return False
        kind=event.type();M=Qt.KeyboardModifier;K=Qt.Key;B=Qt.MouseButton
        if kind in (QEvent.Type.ShortcutOverride,QEvent.Type.KeyPress):
            key=event.key();mods=event.modifiers();ctrl=bool(mods&M.ControlModifier)
            if ctrl and key==K.Key_V and kind==QEvent.Type.KeyPress and find_link(QApplication.clipboard().text() or ''):
                b.manager.open_link(QApplication.clipboard().text());event.accept();return True
            allowed=key in (K.Key_Tab,K.Key_Backtab) or ctrl and key in (K.Key_F,K.Key_W,K.Key_S)
            if allowed:return False
            if kind==QEvent.Type.KeyPress:
                if key==K.Key_Space:v.focus_items()
                elif ctrl and key==K.Key_0:v.reset_zoom()
            event.accept();return True
        if kind==QEvent.Type.KeyRelease:event.accept();return True
        if kind==QEvent.Type.Wheel:
            # Same zoom step as the editable canvas (1.15x per wheel notch).
            delta=(event.angleDelta().y() or event.angleDelta().x())/120
            if not delta and not event.pixelDelta().isNull():delta=event.pixelDelta().y()/40
            v._zoom_at(pow(1.15,delta),event.position().toPoint());event.accept();return True
        if kind==QEvent.Type.MouseButtonPress:
            if event.button() in (B.LeftButton,B.MiddleButton):
                v.setFocus();v._begin_pan(event.position());self.panning=True;v.gesture='lan_pan'
            event.accept();return True
        if kind==QEvent.Type.MouseMove:
            if self.panning:v._pan_to(event.position())
            event.accept();return True
        if kind==QEvent.Type.MouseButtonRelease:
            if self.panning:v._pan_to(event.position())
            self.panning=False;v._pan_origin=None;v.gesture=None;event.accept();return True
        if kind in (QEvent.Type.MouseButtonDblClick,QEvent.Type.ContextMenu,QEvent.Type.DragEnter,QEvent.Type.DragMove,QEvent.Type.Drop):
            event.accept();return True
        return False


class LanBinding(QObject):
    def __init__(self,manager,doc,client,info,owner_path=None):
        super().__init__(doc)
        self.manager=manager;self.doc=doc;self.client=client;self.info=info;self.owner_path=owner_path
        self.lease='';self.deadline=0;self.editable=False;self.connected=True;self.busy=False;self.polling=False;self.disposed=False
        self.revision=-1;self.initial=True;self.last_error='';self.generation=0
        self.folder=manager.workspace.data_dir/'lan-cache'/uuid.uuid4().hex;self.folder.mkdir(parents=True)
        self.cache=self.folder/'working.fbboard'
        self.name=Path(info['name']).name;doc._lan_binding=self;doc._lan_name=self.name
        self.bar=QToolBar('局域网状态',doc);self.bar.setMovable(False);self.bar.setFloatable(False)
        self.label=QLabel('正在读取共享项目…');self.label.setMinimumWidth(170);self.bar.addWidget(self.label)
        self.buttons={}
        for key,title,handler in [('edit','开始编辑',self.acquire),('save','保存更新',self.save),('finish','结束编辑',self.finish),('refresh','刷新',self.refresh),('copy','另存本地并断开',self.save_copy),('manage','共享管理',manager.show_dialog)]:
            button=QPushButton(title);button.clicked.connect(lambda checked=False,fn=handler:fn());self.bar.addWidget(button);self.buttons[key]=button
        doc.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea);doc.addToolBar(Qt.ToolBarArea.TopToolBarArea,self.bar)
        self.bar.setVisible(not doc.actions['minimal'].isChecked())
        self.gate=ReadOnlyGate(self)
        self.timer=QTimer(self);self.timer.setInterval(5000);self.timer.timeout.connect(self.poll);self.timer.start()
        self.sync_controls();self.refresh()
    def alive(self):return not self.disposed and not self.doc._closing and getattr(self.doc,'_lan_binding',None) is self
    def submit(self,function,callback,dropped=None):
        generation=self.generation
        def done(result,error):
            if not self.alive() or generation!=self.generation:
                if dropped:dropped(result['value'] if result and result.get('ok') else None)
                return
            if error:result={'ok':False,'error':error.strip().splitlines()[-1],'status':0}
            callback(result)
        self.doc.jobs.submit(lambda:outcome(function),done,5)
    def sync_controls(self):
        if not self.alive():return
        d=self.doc;can_edit=self.editable and not self.busy
        d.top_toolbar.setEnabled(can_edit);d.toolbox.setEnabled(can_edit);d.sidebar.setEnabled(can_edit)
        d.view.setEnabled(True)
        d.actions['save'].setEnabled(True);d.actions['save_as'].setEnabled(True)
        self.buttons['edit'].setEnabled(not self.busy and not self.lease and self.connected and not self.initial)
        self.buttons['save'].setEnabled(can_edit)
        self.buttons['finish'].setEnabled(bool(self.lease) and not self.busy)
        self.buttons['refresh'].setEnabled(not self.busy and self.connected and not d.dirty)
        self.buttons['copy'].setEnabled(not self.busy and not self.initial)
        state='正在处理' if self.busy else ('可编辑' if self.editable else ('只读' if self.connected else '已断开'))
        owner=self.info.get('editor','')
        detail=' · '+owner+' 正在编辑' if owner and not self.lease else ''
        self.label.setText(f'局域网 · {state} · 版本 {max(0,self.revision)}'+detail)
        self.label.setToolTip(self.last_error or '保存成功后才会更新共享电脑上的文件。只读时左键拖动画布，滚轮缩放。')
        d._lan_name=f'{self.name}〔{state}〕';d._update_title()
        self.manager.update_dialog()
    def freeze_edit(self):
        self.doc.view.finish_text()
        if self.doc.view.active_path is not None:self.doc.view.finish_path()
        self.doc._flush_pending_wheel();self.doc.checkpoint();self.doc.view.set_tool('select')
        self.editable=False
    def lose_connection(self,message):
        if self.editable:self.freeze_edit()
        self.lease='';self.connected=False;self.last_error=message
        self.doc.message(message+' 可另存本地副本。',12000);self.sync_controls()
    def poll(self):
        if not self.alive() or self.polling:return
        self.polling=True;lease=self.lease
        def done(result):
            self.polling=False
            if not result['ok']:
                # An old in-flight poll must not invalidate a newly obtained lease.
                if lease==self.lease:self.lose_connection(result['error'])
                return
            self.info=result['value'];self.connected=True
            if lease and lease==self.lease:self.deadline=time.monotonic()+max(5,self.info.get('lease_remaining',30)-5)
            self.sync_controls()
            if not self.busy and not self.doc.dirty and not self.doc.view.gesture and self.info['revision']!=self.revision:
                self.refresh()
        self.submit(lambda:self.client.heartbeat(lease) if lease else self.client.status(),done)
    def refresh(self,grant_edit=False,discard=False):
        if self.busy or not self.alive():return False
        if self.doc.dirty and not discard:
            self.doc.message('当前有未提交修改，请先保存更新或另存本地，刷新不会覆盖这些修改。',10000);return False
        self.busy=True;self.sync_controls();view=self.doc.view.view_meta();first=self.initial
        bar=self.doc.loading;token=bar.begin('正在读取共享项目…' if first else '正在同步同事的修改…',delay=150)
        def fetch():
            result=self.client.download(self.cache,progress=bar.reporter(token,'正在下载共享项目…'))
            bar.report(token,0,0,'正在载入图片…')
            records,meta=storage.load_project(str(self.cache),materialize=True,progress=bar.reporter(token,'正在载入图片…'))
            return result,records,meta
        def done(result):
            bar.end(token)
            self.busy=False
            if not result['ok']:self.lose_connection(result['error']);return
            details,records,meta=result['value']
            try:
                self.doc._load_document(records,meta,str(self.cache),clean=True);self.doc._legacy_source=None
                if not first:self.doc.view.restore_view(view)
                self.revision=details['revision'];self.info.update(details);self.initial=False;self.connected=True
                if grant_edit and self.lease and time.monotonic()<self.deadline:self.editable=True
                self.doc.message('已取得编辑权限。保存后更新到共享电脑。' if self.editable else '共享项目只读：拖动浏览，滚轮缩放；点击“开始编辑”申请修改。',10000)
            except Exception as exc:self.lose_connection(str(exc))
            finally:assets.release_pending(records)
            self.sync_controls();self.manager.workspace.position_overlay()
        self.submit(fetch,done,dropped=lambda value:(bar.end(token),value and assets.release_pending(value[1])));return True
    def acquire(self):
        if self.busy or self.lease or self.initial:return
        self.busy=True;self.sync_controls()
        def done(result):
            self.busy=False
            if not result['ok']:
                self.last_error=result['error'];self.doc.message(result['error'],10000);self.sync_controls();return
            self.info=result['value'];self.lease=self.info['lease'];self.deadline=time.monotonic()+max(5,self.info.get('lease_remaining',30)-5)
            self.connected=True
            if self.doc.dirty and self.revision!=self.info['revision']:
                token=self.lease;self.lease='';self.manager.release_later(self.client,token)
                self.doc.message('共享文件已有新版本。你的未提交修改已保留，请另存本地，不能覆盖他人的更新。',15000);self.sync_controls();return
            if self.revision==self.info['revision']:
                self.editable=True;self.sync_controls();self.doc.view.setFocus()
            else:self.refresh(grant_edit=True)
        self.submit(self.client.acquire,done)
    def save(self,path=None,save_as=False,wait=False):
        if save_as or (path and not isinstance(path,bool)):return self.save_copy(path=path,wait=wait)
        if self.doc._manual_save_busy:return self._wait() if wait else True
        if self.busy:return False
        if not self.editable or not self.lease:
            self.doc.message('请先取得编辑权限；已断开的修改可另存本地副本。',10000);return False
        return self._save_snapshot(None,wait)
    def _wait(self):
        result=self.doc._wait_for_save()
        if self.alive():self.sync_controls()
        else:self.doc.top_toolbar.setEnabled(True)
        return result
    def save_copy(self,path=None,wait=False):
        if self.busy or self.initial:return False
        if not path or isinstance(path,bool):
            path,_=QFileDialog.getSaveFileName(self.doc,'另存本地并断开共享',str(Path(self.doc._dialog_dir())/(Path(self.name).stem+'-本地副本.fbboard')),'反馈画布项目 (*.fbboard)')
        if not path:return False
        if not str(path).lower().endswith('.fbboard'):path=str(path)+'.fbboard'
        if self.owner_path and self.manager.workspace.path_key(path)==self.manager.workspace.path_key(self.owner_path):
            self.doc.message('共享期间不能用本地副本覆盖共享源文件，请使用其他名称。',10000);return False
        if not self.manager.workspace.can_save(self.doc,path):return False
        return self._save_snapshot(str(path),wait)
    def _save_snapshot(self,local_destination,wait):
        d=self.doc;d.view.finish_text()
        if d.view.active_path is not None:d.view.finish_path()
        d._flush_pending_wheel();d.checkpoint()
        records=d.view.records();digest=d._current_digest;meta=d._project_meta();pins=assets.pin(records)
        token=uuid.uuid4().hex;lease=self.lease;revision=self.revision
        destination=local_destination or str(self.cache)
        self.busy=True;d._manual_save_busy=True;d._save_outcome=False;d._save_token=token;d._saving_digest=digest
        d.selection_status.hide();d.guidance.hide();d.save_indicator.begin();self.sync_controls()
        bar=d.loading;bar_token=bar.begin('正在保存…',100,delay=150)
        def report(value,phase):
            d.save_events.progress.emit(token,value,phase)
            bar.report(bar_token,value if value>=0 else None,None,phase)
        def write():
            try:
                storage.save_project(destination,records,meta,progress=lambda n,s:report(int(n*.7) if n>=0 else n,s))
                if local_destination:return None
                report(72,'正在上传到共享电脑…')
                try:return self.client.upload(destination,lease,revision,token,lambda n,total:report(72+int(25*n/max(1,total)),'正在上传并等待共享电脑确认…'))
                except Exception:
                    # A lost acknowledgement does not mean the server rejected the save.
                    info=self.client.status()
                    if info['sha256']==file_hash(destination) and info['revision']>revision:return info
                    raise
            finally:assets.unpin(pins)
        def done(result,error):
            bar.end(bar_token)
            self.manager.workspace._reserved.pop(d,None)
            if not self.alive():return
            self.busy=False;d._manual_save_busy=False;d._save_outcome=not bool(error)
            if error:
                d.save_indicator.finish(False,'未确认同步成功；修改已保留，请重试或另存本地')
                self.last_error=error.strip().splitlines()[-1];d.message(self.last_error,15000)
            else:
                d._saved_digest=digest
                if local_destination:
                    self.dispose(release=True);d.current_file=local_destination;d._legacy_source=None
                    d._remember_project(local_destination);d._remember_directory(local_destination)
                else:
                    self.info=result;self.revision=result['revision'];self.connected=True
                    self.deadline=time.monotonic()+max(5,result.get('lease_remaining',30)-5)
                if not d.dirty:d._clear_recovery()
                d.save_indicator.finish(True,'已另存为本地文件' if local_destination else '已保存并更新到共享电脑')
                d.message('已另存本地，可独立编辑。' if local_destination else '共享电脑已确认保存。',8000)
                QTimer.singleShot(4500,d,lambda:d._hide_save_indicator(token))
            if self.alive():self.sync_controls()
            else:d.top_toolbar.setEnabled(True);d.toolbox.setEnabled(True);d.sidebar.setEnabled(True);d.view.setEnabled(True)
            d._update_title();d.save_events.finished.emit()
        d.jobs.submit(write,done,30)
        return self._wait() if wait else True
    def finish(self):
        if self.busy or not self.lease:return
        if not self.doc._confirm_unsaved():return
        # Discard means reload the committed server version after releasing the lease.
        discard=self.doc.dirty
        self.freeze_edit();token=self.lease;self.busy=True;self.sync_controls()
        def done(result):
            self.busy=False;self.lease=''
            if result['ok']:
                self.info=result['value']
                self.doc.message('已结束编辑，其他人现在可以申请修改。',8000)
                self.sync_controls()
                if discard:self.refresh(discard=True)
            else:self.lose_connection(result['error'])
        self.submit(lambda:self.client.release(token),done)
    def dispose(self,release=True):
        if self.disposed:return
        self.timer.stop();self.disposed=True;self.generation+=1
        if release and self.lease:self.manager.release_later(self.client,self.lease)
        self.lease='';self.editable=False
        self.doc.view.removeEventFilter(self.gate);self.doc.view.viewport().removeEventFilter(self.gate)
        self.doc.removeToolBar(self.bar);self.bar.deleteLater()
        self.doc._lan_binding=None
        if hasattr(self.doc,'_lan_name'):del self.doc._lan_name
        self.manager.bindings.discard(self)
        if self.manager.host_binding is self:self.manager.host_binding=None
        self.doc.top_toolbar.setEnabled(True);self.doc.toolbox.setEnabled(True);self.doc.sidebar.setEnabled(True);self.doc.view.setEnabled(True)
        self.manager.workspace.position_overlay()


class LanDialog(QDialog):
    def __init__(self,manager):
        super().__init__(manager.workspace);self.manager=manager;self.setWindowTitle('局域网共享');self.setMinimumWidth(640);self.resize(660,470)
        column=QVBoxLayout(self);column.setSpacing(10)
        group=QGroupBox('分享给同事');box=QVBoxLayout(group)
        self.state=QLabel('点下面的按钮：自动保存当前画布、开启共享并复制链接。');self.state.setWordWrap(True);box.addWidget(self.state)
        row=QHBoxLayout()
        self.start=QPushButton('分享当前画布并复制链接');self.start.setObjectName('primary');self.start.setMinimumHeight(40)
        self.start.clicked.connect(lambda:manager.quick_share())
        self.choose_button=QPushButton('分享其他项目…');self.choose_button.clicked.connect(self.choose)
        row.addWidget(self.start,1);row.addWidget(self.choose_button);box.addLayout(row)
        row=QHBoxLayout();self.code=QLineEdit();self.code.setReadOnly(True);self.code.setPlaceholderText('开启分享后显示链接')
        self.copy_button=QPushButton('复制链接');self.copy_button.clicked.connect(lambda:manager.copy_link())
        row.addWidget(self.code,1);row.addWidget(self.copy_button);box.addLayout(row)
        self.how=QLabel('同事收到链接后：在反馈画布里直接 Ctrl+V 就能打开；也可以在浏览器里点开链接，再点“用反馈画布打开”。');self.how.setWordWrap(True);self.how.setObjectName('muted');box.addWidget(self.how)
        row=QHBoxLayout();self.stop=QPushButton('停止共享');self.history=QPushButton('打开历史副本');self.firewall=QPushButton('修复防火墙')
        self.firewall.setToolTip('同事连不上时用：请求一次管理员确认，允许本软件接收局域网连接。')
        self.stop.clicked.connect(manager.stop_share);self.history.clicked.connect(manager.open_history);self.firewall.clicked.connect(lambda:manager.fix_firewall(force=True))
        for b in (self.stop,self.history,self.firewall):row.addWidget(b)
        row.addStretch();box.addLayout(row)
        column.addWidget(group)
        group=QGroupBox('打开同事的链接');box=QVBoxLayout(group)
        row=QHBoxLayout();self.link=QLineEdit();self.link.setPlaceholderText('粘贴同事发来的链接（也可以直接在画布里按 Ctrl+V）')
        self.connect_button=QPushButton('打开');self.connect_button.setMinimumWidth(90)
        self.connect_button.clicked.connect(lambda:manager.connect_share(self.link.text(),self.nickname.text()))
        self.link.returnPressed.connect(self.connect_button.click)
        row.addWidget(self.link,1);row.addWidget(self.connect_button);box.addLayout(row)
        column.addWidget(group)
        self.more=QToolButton();self.more.setText('高级设置与诊断（展开）');self.more.setCheckable(True);self.more.setStyleSheet('QToolButton{border:none;background:transparent;color:#a8a8a8;padding:2px 0}')
        column.addWidget(self.more)
        self.advanced=QWidget();form=QFormLayout(self.advanced);form.setContentsMargins(0,0,0,0)
        self.file=QLineEdit();self.file.setReadOnly(True);form.addRow('共享文件',self.file)
        self.address=QComboBox()
        self.address.addItem('自动（所有网卡，推荐）','auto')
        for name,value in network_interfaces():self.address.addItem(name+' · '+value,value)
        self.address.addItem('仅本机测试 · 127.0.0.1','127.0.0.1')
        form.addRow('共享网卡',self.address)
        self.port=QSpinBox();self.port.setRange(0,65535);self.port.setSpecialValueText('自动选择');self.port.setValue(DEFAULT_PORT);form.addRow('端口',self.port)
        self.nickname=QLineEdit(manager.nickname());self.nickname.setMaxLength(80);form.addRow('你的名称',self.nickname)
        self.nickname.editingFinished.connect(lambda:manager.set_nickname(self.nickname.text()))
        self.test_button=QPushButton('测试链接连接');self.test_button.clicked.connect(lambda:manager.test_connection(self.link.text() or self.code.text(),self.nickname.text()))
        form.addRow(self.test_button)
        self.diagnostic=QPlainTextEdit();self.diagnostic.setReadOnly(True);self.diagnostic.setMaximumHeight(110)
        self.diagnostic.setPlaceholderText('测试结果会显示尝试的地址、端口和具体错误，不包含访问密钥。');form.addRow(self.diagnostic)
        copy_report=QPushButton('复制诊断结果');copy_report.clicked.connect(lambda:QApplication.clipboard().setText(self.diagnostic.toPlainText()))
        form.addRow(copy_report)
        self.advanced.setVisible(False);column.addWidget(self.advanced)
        self.more.toggled.connect(self._toggle_more)
        column.addStretch()
        note=QLabel('只在局域网内直连，不经过任何服务器；共享电脑需保持软件运行、不休眠。同一时间只有一人可编辑。');note.setWordWrap(True);note.setObjectName('muted');column.addWidget(note)
    def _toggle_more(self,open_):
        self.advanced.setVisible(open_);self.more.setText('高级设置与诊断（收起）' if open_ else '高级设置与诊断（展开）')
        self.resize(max(self.width(),660),self.sizeHint().height())
    def choose(self):
        path,_=QFileDialog.getOpenFileName(self,'选择需要共享的本地项目',self.manager.workspace.current_document()._dialog_dir(),'反馈画布项目 (*.fbboard)')
        if path:
            self.file.setText(path);self.manager.start_share(path,self.address.currentData(),self.port.value())


class LanController(QObject):
    def __init__(self,workspace):
        super().__init__(workspace);self.workspace=workspace;self.service=None;self.host_binding=None;self.bindings=set();self.dialog=None;self.busy=False;self.closing=False
        self.jobs=Jobs(self);self.last_history=None;self.starting_path=None;self.offered=set();self._asking=False
        self.link_server=QLocalServer(self);self.link_server.newConnection.connect(self._incoming_link)
        self.link_server.listen(link_server_name())
    # -- small helpers -------------------------------------------------------
    def nickname(self):
        value=self.workspace.settings.get('lan_name')
        return value if isinstance(value,str) and value.strip() else socket.gethostname()
    def set_nickname(self,value):
        if value.strip():self.workspace.settings['lan_name']=value.strip()[:80]
    def shared_root(self):
        import colorpicker
        return colorpicker.store_path().parent
    def _incoming_link(self):
        while self.link_server.hasPendingConnections():
            sock=self.link_server.nextPendingConnection();buffer=bytearray();handled=[False]
            def finish(sock=sock,buffer=buffer,handled=handled):
                if handled[0]:return
                buffer.extend(bytes(sock.readAll()))
                text=bytes(buffer[:4096]).decode('utf-8',errors='ignore')
                # Act as soon as a complete link arrived; the sender may already be gone.
                if not find_link(text) and sock.state()==QLocalSocket.LocalSocketState.ConnectedState:return
                handled[0]=True
                if not find_link(text):sock.deleteLater();return
                if sock.state()==QLocalSocket.LocalSocketState.ConnectedState:
                    sock.write(b'1');sock.flush();sock.disconnected.connect(sock.deleteLater)
                else:sock.deleteLater()
                w=self.workspace
                if w.isMinimized():w.showNormal()
                w.raise_();w.activateWindow()
                QTimer.singleShot(0,self,lambda:self.open_link(text))
            sock.readyRead.connect(finish);sock.disconnected.connect(finish)
            finish()
    def own_link(self,link):
        if not self.service or not link:return False
        try:return parse_link(link)[2]==self.service.state.secret
        except ShareError:return False
    def open_link(self,text):
        """Open a colleague's share from any text containing a link. Returns True when handled."""
        link=find_link(text or '')
        if not link:return False
        self.offered.add(link)
        if self.own_link(link):
            if self.host_binding and self.host_binding.alive():self._focus(self.host_binding.doc)
            return True
        self.connect_share(link,self.nickname());return True
    def _focus(self,doc):
        for i in range(self.workspace.tabbar.count()):
            if self.workspace.tabbar.tabData(i) is doc:self.workspace.tabbar.setCurrentIndex(i)
    def check_clipboard(self):
        """Like LocalSend's 'just works': coming back to the window with a copied link offers to open it."""
        if self.closing or self.busy or self._asking or self.workspace._closing:return
        if QApplication.activeModalWidget() is not None:return
        link=find_link(QApplication.clipboard().text() or '')
        if not link or link in self.offered:return
        self.offered.add(link)
        if self.own_link(link) or any(getattr(b.client,'link',None)==link for b in self.bindings):return
        try:address=parse_link(link)[0]
        except ShareError:return
        self._asking=True
        try:
            answer=QMessageBox.question(self.workspace,'打开同事的共享画布？',f'剪贴板里有一个反馈画布共享链接（来自 {address}）。\n现在打开吗？',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.Yes)
        finally:self._asking=False
        if answer==QMessageBox.StandardButton.Yes:self.connect_share(link,self.nickname())
    def copy_link(self):
        if self.service:
            link=self.service.connection_code;self.offered.add(link)
            QApplication.clipboard().setText(link)
            doc=self.workspace.current_document()
            if doc:doc.message('分享链接已复制，直接发给同事即可。',8000)
    def fix_firewall(self,force=False):
        """One UAC prompt so the sharing computer accepts LAN connections (frozen Windows build only)."""
        exe=lan_firewall.executable()
        if not exe:
            if force:QMessageBox.information(self.workspace,'防火墙','开发运行模式不修改防火墙；正式版 exe 中可用。')
            return True
        root=self.shared_root()
        if not force and not lan_firewall.needs_setup(root,exe):return True
        answer=QMessageBox.question(self.workspace,'允许局域网连接',
            '同事要连到这台电脑，需要 Windows 防火墙放行“反馈画布”。\n\n接下来 Windows 会弹出一次管理员确认（只需一次，以后的版本也通用）。\n如果你没有管理员权限，可以点“跳过”，同网段同事仍可能连得上。',
            QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Ok)
        if answer!=QMessageBox.StandardButton.Ok:
            lan_firewall.remember(root,exe,False);return False
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:ok,text=lan_firewall.run_elevated(exe,int(self.workspace.winId()))
        finally:QApplication.restoreOverrideCursor()
        lan_firewall.remember(root,exe,ok)
        self.report(text)
        doc=self.workspace.current_document()
        if doc:doc.message(text,10000)
        if not ok and force:QMessageBox.information(self.workspace,'防火墙',text)
        return ok
    def quick_share(self):
        """One click: save the current canvas if needed, share it on every adapter, copy the link."""
        if self.busy:return
        if self.service:
            self.copy_link();self.show_dialog(auto=False);return
        doc=self.workspace.current_document()
        if doc is None:return
        if getattr(doc,'_lan_binding',None):
            QMessageBox.information(self.workspace,'这是同事的共享','这个标签是别人共享给你的画布，不能再次分享。');return
        if not doc.current_file or doc.dirty:
            if not doc.save_project(save_as=not doc.current_file,wait=True) or not doc.current_file or doc.dirty:
                doc.message('需要先保存为 .fbboard 文件才能分享。',8000);return
        self.start_share(doc.current_file,'auto',DEFAULT_PORT)
    def add_button(self,doc):
        button=QPushButton('局域网共享');button.setToolTip('一键分享当前画布（自动复制链接），或打开同事的链接');button.clicked.connect(self.show_dialog)
        action=next((a for a in doc.top_toolbar.actions() if doc.top_toolbar.widgetForAction(a) is doc.pack_button),None)
        doc.top_toolbar.insertWidget(action,button) if action else doc.top_toolbar.addWidget(button)
    def show_dialog(self,auto=False):
        if self.dialog is None:self.dialog=LanDialog(self)
        doc=self.workspace.current_document()
        if not self.service and doc and doc.current_file and not getattr(doc,'_lan_binding',None):self.dialog.file.setText(doc.current_file)
        clip=find_link(QApplication.clipboard().text() or '')
        if clip and not self.own_link(clip) and not self.dialog.link.text():self.dialog.link.setText(clip)
        self.update_dialog();self.dialog.show();self.dialog.raise_();self.dialog.activateWindow()
    def update_dialog(self):
        if self.dialog is None:return
        dialog=self.dialog
        dialog.start.setEnabled(not self.busy and self.service is None)
        dialog.start.setText('正在分享 · 链接已复制' if self.service else ('正在准备…' if self.busy else '分享当前画布并复制链接'))
        dialog.choose_button.setEnabled(not self.busy and self.service is None)
        dialog.copy_button.setEnabled(self.service is not None)
        dialog.stop.setEnabled(not self.busy and self.service is not None)
        dialog.address.setEnabled(not self.busy and self.service is None);dialog.port.setEnabled(not self.busy and self.service is None)
        dialog.connect_button.setEnabled(not self.busy)
        dialog.test_button.setEnabled(not self.busy)
        dialog.history.setEnabled(bool(self.last_history))
        if self.service:
            dialog.file.setText(str(self.service.state.project))
            index=dialog.address.findData('auto' if (self.service.bind_all or self.service.automatic) else self.service.address)
            if index>=0:dialog.address.setCurrentIndex(index)
            dialog.port.setValue(self.service.port)
            if dialog.code.text()!=self.service.connection_code:
                dialog.code.setText(self.service.connection_code);dialog.code.setCursorPosition(0)
            try:
                info=self.service.state.status();dialog.state.setText('正在分享「'+self.service.state.project.name+'」 · 版本 '+str(info['revision'])+' · '+(info['editor']+' 正在编辑' if info['editor'] else '无人编辑，同事可申请修改'))
            except ShareError:dialog.state.setText('正在停止共享…')
        elif not self.busy:dialog.code.clear();dialog.state.setText('点下面的按钮：自动保存当前画布、开启共享并复制链接。')
    def start_share(self,path,address,port):
        if self.busy or self.service:return
        if not path or not Path(path).is_file():QMessageBox.information(self.workspace,'选择项目','请先选择一个已经保存的 .fbboard 文件。');return
        advertise=None
        if address=='auto':
            advertise=[ip for _,ip in network_interfaces()][:8]
            if not advertise:QMessageBox.information(self.workspace,'无可用网卡','请连接局域网后再分享。');return
            address='0.0.0.0'
        if not address:QMessageBox.information(self.workspace,'无可用网卡','请连接局域网后重新打开共享窗口。');return
        if address!='127.0.0.1':self.fix_firewall()
        matched=next((d for d in self.workspace.documents if d.current_file and self.workspace.path_key(d.current_file)==self.workspace.path_key(path)),None)
        if matched:
            if getattr(matched,'_lan_binding',None):return
            matched.view.finish_text()
            if matched.view.active_path is not None:matched.view.finish_path()
            matched._flush_pending_wheel();matched.checkpoint()
            if matched._manual_save_busy or matched._loading_project or matched._import_next<=matched._import_seq:
                QMessageBox.information(self.workspace,'正在处理','请等待当前文件保存或导入完成。');return
            if matched.dirty:
                QMessageBox.information(self.workspace,'先保存项目','该文件在画布中有未保存修改，请先保存，再开启共享。');return
            matched._lan_starting=True;matched.board_workspace.setEnabled(False);matched.top_toolbar.setEnabled(False)
        self.busy=True;self.starting_path=str(Path(path).resolve());self.update_dialog()
        if self.dialog:self.dialog.state.setText('正在准备安全连接和初始历史副本…')
        directory=self.workspace.data_dir/'lan-host'/uuid.uuid4().hex
        def done(result,error):
            self.busy=False
            self.starting_path=None
            if matched:
                matched._lan_starting=False;matched.board_workspace.setEnabled(True);matched.top_toolbar.setEnabled(True)
            if error or not result['ok']:
                QMessageBox.warning(self.workspace,'无法开启共享',error or result['error']);self.update_dialog();return
            self.service=result['value'];self.last_history=self.service.state.history
            client=prepare_client(self.service.local_code,self.nickname())
            self.copy_link()
            self.report('共享监听地址：'+', '.join(f'{ip}:{self.service.port}' for ip in self.service.addresses)+'\n'+('局域网发现已开启。' if self.service.discovery else '组播发现未开启，仍可通过连接信息直连。')+('\n'+'\n'.join(self.service.bind_errors) if self.service.bind_errors else ''))
            doc=matched or self.workspace.new_document()
            binding=LanBinding(self,doc,client,self.service.state.status(),owner_path=str(self.service.state.project))
            self.bindings.add(binding);self.host_binding=binding;self.update_dialog()
        current=self.workspace.current_document();share_token=current.loading.begin('正在开启共享（复制历史副本、生成加密证书）…') if current else None
        def finished(result,error):
            if current is not None and share_token:current.loading.end(share_token)
            done(result,error)
        self.jobs.submit(lambda:outcome(lambda:ShareService(path,directory,storage.load_project,address,port,advertise=advertise)),finished)
    def connect_share(self,code,name):
        if self.busy:return
        try:client=prepare_client(code,name)
        except Exception as exc:QMessageBox.warning(self.workspace,'链接无效',str(exc));return
        if client.link:self.offered.add(client.link)
        for binding in self.bindings:
            if binding.client.fingerprint==client.fingerprint and binding.client.secret==client.secret:
                for i in range(self.workspace.tabbar.count()):
                    if self.workspace.tabbar.tabData(i) is binding.doc:self.workspace.tabbar.setCurrentIndex(i)
                if self.dialog:self.dialog.hide()
                return
        self.busy=True;self.update_dialog()
        def done(result,error):
            self.busy=False
            if error or not result['ok']:
                self.report(error or result['error'])
                QMessageBox.warning(self.workspace,'连接失败',(error or result['error'])+'\n\n请确认：同事的反馈画布还开着并且没有停止共享；两台电脑在同一局域网；同事在“局域网共享”里点过“修复防火墙”。');self.update_dialog();return
            self.report(client.last_connection_report+'\n共享权限验证通过，正在读取项目。')
            doc=self.workspace.new_document();binding=LanBinding(self,doc,client,result['value']);self.bindings.add(binding)
            if self.dialog:self.dialog.hide()
            self.update_dialog()
        doc=self.workspace.current_document()
        if doc:doc.message('正在连接同事的共享…',6000)
        self.jobs.submit(lambda:outcome(client.status),done)
    def report(self,text):
        if self.dialog:self.dialog.diagnostic.setPlainText('反馈画布 v0.6.6\n'+text)
    def test_connection(self,code,name):
        if self.busy:return
        try:client=prepare_client(code,name)
        except Exception as exc:self.report(str(exc));return
        self.busy=True;self.report('正在测试地址、TLS 证书和共享访问权限…');self.update_dialog()
        def done(result,error):
            self.busy=False
            self.report((error or result['error']) if error or not result['ok'] else client.last_connection_report+'\n共享权限验证通过，可以连接。')
            self.update_dialog()
        self.jobs.submit(lambda:outcome(client.status),done)
    def stop_share(self):
        if self.busy or not self.service:return
        self.busy=True;service=self.service;binding=self.host_binding;self.update_dialog()
        # Revoke write permission immediately, even while a transfer is in progress.
        service.state.stop()
        if binding:
            binding.freeze_edit();binding.timer.stop();binding.sync_controls()
        def stopped(result,error):
            # Let any in-flight save callback finish before removing its document adapter.
            if binding and binding.alive() and (binding.busy or binding.polling):
                QTimer.singleShot(50,self,lambda:stopped(result,error));return
            if binding and binding.alive():binding.busy=True;binding.generation+=1;binding.sync_controls()
            self.jobs.submit(lambda:outcome(lambda:storage.load_project(str(service.state.project),materialize=True)),loaded)
        def loaded(result,error):
            self.service=None;self.busy=False
            reloaded=result['value'][0] if (not error and result and result.get('ok')) else []
            if not (binding and binding.alive()) or binding.doc.dirty:assets.release_pending(reloaded)
            if binding and binding.alive():
                doc=binding.doc;dirty=doc.dirty;view=doc.view.view_meta();binding.dispose(release=False)
                doc.current_file=str(service.state.project);doc._legacy_source=None
                if not dirty and not error and result['ok']:
                    records,meta=result['value']
                    try:
                        doc._load_document(records,meta,str(service.state.project),clean=True)
                        doc._legacy_source=None;doc.view.restore_view(view)
                    finally:assets.release_pending(records)
                else:
                    # Unsynced work becomes a separate local document, never an implicit overwrite.
                    doc.current_file=None;doc._saved_digest=None
                    doc.message('共享已停止。未提交修改或缓存保留在当前画布，请另存本地副本。',15000)
                doc._update_title();doc.view.setEnabled(True);doc.view.setFocus()
            self.host_binding=None;self.update_dialog()
            if self.dialog:self.dialog.state.setText('已停止共享，远端不能再写入；已成功保存的修改保留在本地。')
        self.jobs.submit(lambda:outcome(service.stop),stopped)
    def open_history(self):
        if self.last_history:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_history)))
    def release_later(self,client,lease):
        if lease and not self.closing:self.jobs.submit(lambda:outcome(lambda:client.release(lease)),lambda *_:None)
    def route_open(self,path):
        if not self.service or self.workspace.path_key(path)!=self.workspace.path_key(self.service.state.project):return False
        if self.host_binding and self.host_binding.alive():
            for i in range(self.workspace.tabbar.count()):
                if self.workspace.tabbar.tabData(i) is self.host_binding.doc:self.workspace.tabbar.setCurrentIndex(i)
        else:self.connect_share(self.service.local_code,self.nickname())
        return True
    def can_save(self,path):
        if self.starting_path and self.workspace.path_key(path)==self.workspace.path_key(self.starting_path):
            QMessageBox.information(self.workspace,'正在开启共享','请等待共享准备完成。');return False
        if self.service and self.workspace.path_key(path)==self.workspace.path_key(self.service.state.project):
            QMessageBox.information(self.workspace,'文件正在共享','请在共享标签中申请编辑并保存，或停止共享后再保存本地文件。');return False
        return True
    def close_doc(self,doc):
        binding=getattr(doc,'_lan_binding',None)
        if binding:binding.dispose(release=True)
    def can_close(self):
        if self.busy:
            QMessageBox.information(self.workspace,'正在处理局域网操作','请等待开启、连接或停止共享完成后关闭。');return False
        return True
    def shutdown(self):
        self.closing=True
        for binding in list(self.bindings):
            # Release remote leases before removing their UI; failed releases expire automatically.
            if binding.lease:self.jobs.submit(lambda c=binding.client,t=binding.lease:outcome(lambda:c.release(t)),lambda *_:None)
            binding.timer.stop()
        if self.service:self.service.stop();self.service=None
        self.link_server.close()
        self.jobs.pool.waitForDone()
