"""Multi-document shell: each pane owns its scene, history, workers and recovery."""
from pathlib import Path
from copy import deepcopy
import json
import os
import uuid
from PySide6.QtCore import Qt, QTimer, QSize, QPoint, QEvent, QLockFile, QByteArray, QSignalBlocker
from PySide6.QtGui import QAction, QKeySequence, QCursor
from PySide6.QtWidgets import (QMainWindow, QWidget, QStackedWidget, QTabBar,
    QHBoxLayout, QToolButton, QFileDialog, QMessageBox, QApplication,
    QAbstractButton, QLineEdit, QComboBox, QAbstractSpinBox, QMenu)
import storage
from jobs import Jobs

class DocumentTabs(QTabBar):
    def tabSizeHint(self, index):
        return QSize(max(88,min(210,self.width()//max(1,self.count()))),32)
    def minimumTabSizeHint(self,index):
        return QSize(88,32)

class WorkspaceWindow(QMainWindow):
    def __init__(self, document_class, project=None):
        super().__init__()
        # Read helpers from the entry module without importing it a second time.
        import sys
        self.api=sys.modules[document_class.__module__]
        self.document_class=document_class
        self.data_dir=self.api.runtime_directory()
        self.documents_dir=self.data_dir/'documents';self.documents_dir.mkdir(exist_ok=True)
        self.settings_path=self.data_dir/'settings.json'
        try:self.settings=self.api.read_json(self.settings_path)
        except (OSError,ValueError):self.settings={}
        self.documents=[];self._closing=False;self._switching=False;self._active=None
        self.jobs=Jobs(self);self._preferences_busy=False;self._preferences_pending=None
        self._reserved={};self._initial_path=project;self.minimal=False;self._edge_cursor=None
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.resize(1440,900);self.setMinimumSize(1000,680)
        self.stack=QStackedWidget();self.setCentralWidget(self.stack)
        self.setStyleSheet(self.api.STYLESHEET)
        self.overlay=QWidget(self);layout=QHBoxLayout(self.overlay)
        layout.setContentsMargins(0,0,0,0);layout.setSpacing(3)
        self.tabbar=DocumentTabs();self.tabbar.setMovable(True);self.tabbar.setTabsClosable(True)
        self.tabbar.setExpanding(True);self.tabbar.setUsesScrollButtons(True)
        self.tabbar.setElideMode(Qt.TextElideMode.ElideRight);self.tabbar.setDrawBase(False)
        self.tabbar.setStyleSheet('QTabBar::tab {background:#303030; color:#bcbcbc; border:1px solid #505050; border-radius:7px; padding:0 6px; min-width:0; margin-right:3px;} QTabBar::tab:selected {background:#4b4b4b;color:white;border-color:#858585;}')
        layout.addWidget(self.tabbar,1)
        self.add_button=QToolButton();self.add_button.setText('+');self.add_button.setToolTip('新建画布 · Ctrl+K')
        self.add_button.setFixedSize(28,30);self.add_button.clicked.connect(self.new_document);layout.addWidget(self.add_button)
        self.grip=QToolButton();self.grip.setText('⠿');self.grip.setToolTip('拖动窗口 · 双击最大化/还原')
        self.grip.setFixedSize(26,30);self.grip.installEventFilter(self);layout.addWidget(self.grip)
        self.controls=QWidget(self);buttons=QHBoxLayout(self.controls);buttons.setContentsMargins(0,0,0,0);buttons.setSpacing(2)
        for label,tip,callback in [('—','最小化',self.showMinimized),('□','最大化 / 还原',self.toggle_maximized),('×','关闭',self.close)]:
            button=QToolButton();button.setText(label);button.setToolTip(tip);button.setFixedSize(30,28);button.clicked.connect(callback);buttons.addWidget(button)
        self.controls.setFixedSize(98,30)
        self.tabbar.currentChanged.connect(self._tab_changed)
        self.tabbar.tabCloseRequested.connect(self.close_document)
        self.tabbar.tabMoved.connect(lambda *_:self.position_overlay())
        self._close_action=QAction(self);self._close_action.setShortcut(QKeySequence('Ctrl+W'))
        self._close_action.triggered.connect(lambda:self.close_document(self.tabbar.currentIndex()));self.addAction(self._close_action)
        for sequence,step in [('Ctrl+Tab',1),('Ctrl+Shift+Tab',-1)]:
            action=QAction(self);action.setShortcut(QKeySequence(sequence));action.triggered.connect(lambda checked=False,delta=step:self.cycle_tab(delta));self.addAction(action)
        geometry=self.settings.get('geometry')
        if isinstance(geometry,str):self.restoreGeometry(QByteArray.fromBase64(geometry.encode('ascii',errors='ignore')))
        if not any(s.availableGeometry().intersects(self.frameGeometry()) for s in QApplication.screens()):
            self.move(QApplication.primaryScreen().availableGeometry().center()-self.rect().center())
        if self.settings.get('always_top'):self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,True)
        from lan_ui import LanController
        self.lan=LanController(self)
        QApplication.instance().applicationStateChanged.connect(self._application_state)
        self.new_document()
        QApplication.instance().installEventFilter(self)
        QTimer.singleShot(0,self.startup)

    def _application_state(self,state):
        # Coming back from the chat window with a copied link offers to open it.
        if state==Qt.ApplicationState.ApplicationActive and not self._closing:
            QTimer.singleShot(250,self,self.lan.check_clipboard)

    def current_document(self):
        return self.stack.currentWidget()

    def new_document(self, checked=False, recovery_dir=None):
        folder=Path(recovery_dir) if recovery_dir else self.documents_dir/uuid.uuid4().hex
        folder.mkdir(parents=True,exist_ok=True)
        lock=QLockFile(str(folder/'session.lock'))
        if not lock.tryLock(0):return None
        doc=self.document_class(host=self,recovery_dir=folder)
        doc._recovery_lock=lock;doc._loading_project=False
        self.documents.append(doc)
        doc.setWindowFlags(Qt.WindowType.Widget)
        self.stack.addWidget(doc)
        self.lan.add_button(doc)
        # Native window controls overlay the right end of the application toolbar.
        spacer=QWidget();spacer.setFixedWidth(104);doc.top_toolbar.addWidget(spacer)
        doc.view.viewport().installEventFilter(self)
        # Minimal mode is a window-wide state: every tab follows the last toggle.
        doc.actions['minimal'].toggled.connect(lambda state,source=doc:self._minimal_changed(source,state))
        if self.minimal:
            with QSignalBlocker(doc.actions['minimal']):doc.actions['minimal'].setChecked(True)
            doc.set_minimal(True)
        self._switching=True
        index=self.tabbar.addTab('未命名画布');self.tabbar.setTabData(index,doc)
        self._switching=False
        self.tabbar.setCurrentIndex(index);self._tab_changed(index)
        return doc

    def _minimal_changed(self,source,state):
        self.minimal=bool(state)
        for doc in list(self.documents):
            if doc is source or doc._closing:continue
            action=doc.actions['minimal']
            if action.isChecked()!=self.minimal:
                with QSignalBlocker(action):action.setChecked(self.minimal)
                doc.set_minimal(self.minimal)
        self.position_overlay()
        current=self.current_document()
        if current is not None:current.view.setFocus()

    def _tab_changed(self,index):
        if self._switching or index<0:return
        doc=self.tabbar.tabData(index)
        if doc is None:return
        previous=self._active
        if previous is not None and previous is not doc and not previous._closing:
            previous.view.finish_text()
            if previous.view.active_path is not None:previous.view.finish_path()
            previous._flush_pending_wheel()
            previous.view.cancel_gesture()
            previous.checkpoint()
        self.stack.setCurrentWidget(doc);self._active=doc
        self.update_document(doc);self.position_overlay()
        doc.view.setFocus()
        QTimer.singleShot(0,self.position_overlay)

    def cycle_tab(self,delta):
        if self.tabbar.count():self.tabbar.setCurrentIndex((self.tabbar.currentIndex()+delta)%self.tabbar.count())

    def update_document(self,doc):
        if not hasattr(self,'tabbar'):return
        name=Path(doc.current_file).name if doc.current_file else getattr(doc,'_recovered_name','未命名画布')
        name=getattr(doc,'_lan_name',name)
        if getattr(doc,'_loading_project',False):name='正在打开… '+name
        label=name+(' *' if doc.dirty else '')
        for index in range(self.tabbar.count()):
            if self.tabbar.tabData(index) is doc:
                self.tabbar.setTabText(index,label.replace('&','&&'))
                self.tabbar.setTabToolTip(index,str(doc.current_file or name));break
        if self.current_document() is doc:self.setWindowTitle(label+' — 反馈画布 v0.6.5')
        self.position_overlay()

    def position_overlay(self):
        if not hasattr(self,'controls'):return
        self.controls.move(self.width()-self.controls.width()-6,5);self.controls.raise_();self.controls.show()
        doc=self.current_document()
        if doc is None:return
        # Tabs float over the canvas, not an extra toolbar row.
        target=doc.view.viewport() if doc.workspace_stack.currentWidget() is doc.board_workspace else doc.workspace_stack
        point=target.mapTo(self,QPoint(8,8))
        available=max(160,min(target.width()-16,self.width()-point.x()-114))
        width=min(available,self.tabbar.count()*210+64)
        self.overlay.setGeometry(point.x(),point.y(),width,34)
        self.overlay.raise_();self.overlay.show()

    def resizeEvent(self,event):
        super().resizeEvent(event);self.position_overlay()

    EDGE_MARGIN=6

    def _edges_at(self,global_pos):
        """Window border under a global position; empty when maximized/fullscreen."""
        if self.isMaximized() or self.isFullScreen():return Qt.Edge(0)
        pos=self.mapFromGlobal(global_pos);m=self.EDGE_MARGIN;edges=Qt.Edge(0)
        if not (-1<=pos.x()<=self.width() and -1<=pos.y()<=self.height()):return edges
        if pos.x()<m:edges|=Qt.Edge.LeftEdge
        if pos.x()>=self.width()-m:edges|=Qt.Edge.RightEdge
        if pos.y()<m:edges|=Qt.Edge.TopEdge
        if pos.y()>=self.height()-m:edges|=Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _edge_shape(edges):
        E=Qt.Edge;C=Qt.CursorShape
        left,right,top,bottom=(bool(edges&E.LeftEdge),bool(edges&E.RightEdge),bool(edges&E.TopEdge),bool(edges&E.BottomEdge))
        if (left and top) or (right and bottom):return C.SizeFDiagCursor
        if (right and top) or (left and bottom):return C.SizeBDiagCursor
        if left or right:return C.SizeHorCursor
        if top or bottom:return C.SizeVerCursor
        return None

    def _set_edge_cursor(self,shape):
        """Frameless windows get no native resize cursor: show it while hovering a border."""
        current=getattr(self,'_edge_cursor',None)
        if shape==current:return
        if current is not None:QApplication.restoreOverrideCursor()
        self._edge_cursor=shape
        if shape is not None:QApplication.setOverrideCursor(QCursor(shape))

    def _begin_system_move(self):
        if self.windowHandle():self.windowHandle().startSystemMove()

    def _is_drag_area(self,widget):
        """Empty or passive parts of the top bar move the window, like a title bar."""
        doc=self.current_document()
        if doc is None or not isinstance(widget,QWidget):return False
        bars={doc.top_toolbar}
        binding=getattr(doc,'_lan_binding',None)
        if binding is not None:bars.add(binding.bar)
        interactive=(QAbstractButton,QLineEdit,QComboBox,QAbstractSpinBox,QTabBar,QMenu)
        node=widget
        while node is not None:
            if node in bars:return True
            if isinstance(node,interactive) or node is self.controls or node is self.overlay:return False
            node=node.parentWidget()
        return False

    def eventFilter(self,watched,event):
        if self._closing:return False
        kind=event.type()
        if watched is self.grip:
            if kind==QEvent.Type.MouseButtonDblClick:
                self.toggle_maximized();return True
            if kind==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
                self._begin_system_move()
                return True
        if kind in (QEvent.Type.Resize,QEvent.Type.Show) and any(watched is d.view.viewport() for d in self.documents):
            QTimer.singleShot(0,self.position_overlay)
        handle=self.windowHandle()
        # Hover feedback for the borders: the window receives every move, even over passive children.
        if handle is not None and watched is handle:
            if kind==QEvent.Type.MouseMove:
                if event.buttons()==Qt.MouseButton.NoButton:
                    self._set_edge_cursor(self._edge_shape(self._edges_at(event.globalPosition().toPoint())))
            elif kind in (QEvent.Type.Leave,QEvent.Type.WindowStateChange,QEvent.Type.FocusOut):
                self._set_edge_cursor(None)
            return False
        if isinstance(watched,QWidget) and watched.window() is self:
            if kind==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
                # Resize native frameless window from any outer border, including corners.
                edges=self._edges_at(event.globalPosition().toPoint())
                if edges and handle:
                    self._set_edge_cursor(None);handle.startSystemResize(edges);return True
                if self._is_drag_area(watched):
                    self._begin_system_move();return True
            elif kind==QEvent.Type.MouseButtonDblClick and event.button()==Qt.MouseButton.LeftButton and self._is_drag_area(watched):
                self.toggle_maximized();return True
        return super().eventFilter(watched,event)

    def changeEvent(self,event):
        if event.type()==QEvent.Type.WindowStateChange:self._set_edge_cursor(None)
        super().changeEvent(event)

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() or self.isFullScreen() else self.showMaximized()
    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.setWindowState(getattr(self,'_before_fullscreen',Qt.WindowState.WindowNoState));self.show()
        else:self._before_fullscreen=self.windowState();self.showFullScreen()
        self.position_overlay()
    def set_always_top(self,enabled):
        state=self.windowState();self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,enabled);self.setWindowState(state);self.show()
        self.settings['always_top']=enabled
        from PySide6.QtCore import QSignalBlocker
        for doc in self.documents:
            with QSignalBlocker(doc.actions['always_top']):doc.actions['always_top'].setChecked(enabled)

    @staticmethod
    def path_key(path):return os.path.normcase(str(Path(path).resolve()))

    def open_document(self,path=None):
        if path is None or isinstance(path,bool):
            path,_=QFileDialog.getOpenFileName(self,'打开反馈项目',self.current_document()._dialog_dir(),'反馈画布项目 (*.fbboard)')
        if not path:return False
        if self.lan.route_open(path):return True
        key=self.path_key(path)
        for i in range(self.tabbar.count()):
            doc=self.tabbar.tabData(i)
            if doc.current_file and self.path_key(doc.current_file)==key:
                self.tabbar.setCurrentIndex(i);return True
        if not Path(path).is_file():
            QMessageBox.warning(self,'无法打开','文件不存在：\n'+str(path));return False
        doc=self.new_document()
        if doc is None:return False
        doc.current_file=str(path);doc._loading_project=True;doc.workspace_stack.setEnabled(False)
        doc.top_toolbar.setEnabled(False);self.update_document(doc)
        def loaded(result,error):
            if doc._closing:
                import assets_runtime as assets
                if result:assets.release_pending(result[0])
                return
            doc._loading_project=False;doc.workspace_stack.setEnabled(True);doc.top_toolbar.setEnabled(True)
            if error:
                doc.current_file=None;self.update_document(doc)
                QMessageBox.warning(self,'无法打开项目',str(path)+'\n'+error.strip().splitlines()[-1]);return
            records,meta=result
            try:
                doc._load_document(records,meta,path,clean=True)
                doc._remember_project(path);doc._remember_directory(path)
                doc.message('已打开 '+Path(path).name)
            except Exception as exc:
                doc.current_file=None;self.update_document(doc)
                QMessageBox.warning(self,'无法打开项目',str(exc))
            finally:
                import assets_runtime as assets
                assets.release_pending(records)
            if self.current_document() is doc:doc.view.setFocus()
        doc.jobs.submit(lambda:storage.load_project(str(path),materialize=True),loaded,10)
        return True

    def can_save(self,doc,path):
        if not self.lan.can_save(path):return False
        key=self.path_key(path)
        for other in self.documents:
            if other is not doc and ((other.current_file and self.path_key(other.current_file)==key) or self._reserved.get(other)==key):
                QMessageBox.warning(self,'项目已在另一个标签中打开','请切换到对应标签保存，或为当前项目使用另一个文件名。');return False
        self._reserved[doc]=key
        return True

    def save_preferences(self,doc):
        if self.current_document() is doc:
            self.settings.update(style=self.api.clean_style(doc._pen_style if doc._highlighter else doc.view.style),presets=doc.presets,
                select_filter=doc.view.select_filter,view=doc.view.view_meta(),last_project=(None if getattr(doc,'_lan_binding',None) else doc.current_file),
                sidebar=doc.actions['sidebar'].isChecked(),geometry=bytes(self.saveGeometry().toBase64()).decode('ascii'))
        self._preferences_pending=deepcopy(self.settings)
        if not self._closing:self._flush_preferences()

    def _flush_preferences(self):
        if self._preferences_busy or self._preferences_pending is None:return
        snapshot=self._preferences_pending;self._preferences_pending=None;self._preferences_busy=True
        def done(result,error):
            self._preferences_busy=False
            if error and self.current_document():self.current_document().message('设置保存失败，请检查本地磁盘空间。')
            if not self._closing:self._flush_preferences()
        self.jobs.submit(lambda:self.api.write_json(self.settings_path,snapshot),done)

    def close_document(self,index,confirmed=False):
        if index<0:return False
        doc=self.tabbar.tabData(index)
        if doc is None:return False
        if doc._loading_project or doc._import_next <= doc._import_seq or doc.desk.saving or getattr(doc,'_lan_starting',False) or (getattr(doc,'_lan_binding',None) and doc._lan_binding.busy):
            doc.message('正在打开、导入或导出内容，请完成后再关闭。');return False
        if not confirmed and not doc._confirm_unsaved():return False
        # No queued callbacks may outlive the pane. Wait for actual completion first.
        self.lan.close_doc(doc)
        doc._close_confirmed=True
        doc.close()
        if not doc._closing:return False
        self._reserved.pop(doc,None)
        self._switching=True;self.tabbar.removeTab(index);self.stack.removeWidget(doc);self.documents.remove(doc);self._switching=False
        if self._active is doc:self._active=None
        doc._recovery_lock.unlock()
        # Keep QObject alive until queued worker deliveries are drained.
        QTimer.singleShot(0,doc.deleteLater)
        if self.tabbar.count():self._tab_changed(self.tabbar.currentIndex())
        elif not self._closing:self.new_document()
        if self.documents:self.current_document()._collect_assets([])
        return True

    def startup(self):
        recoveries=[]
        for folder in sorted(self.documents_dir.iterdir()):
            if not folder.is_dir() or not (folder/'recovery.fbboard').is_file():continue
            lock=QLockFile(str(folder/'session.lock'))
            if not lock.tryLock(0):continue
            lock.unlock()
            try:marker=self.api.read_json(folder/'recovery.json')
            except (OSError,ValueError):marker={'dirty':True}
            if marker.get('dirty',True):recoveries.append(folder)
        if recoveries:
            answer=QMessageBox.question(self,'发现未保存的画布',f'发现 {len(recoveries)} 份自动恢复副本，是否分别恢复到文件标签？\n选择“否”会保留副本，下次仍可恢复。',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.Yes)
            if answer==QMessageBox.StandardButton.Yes:
                for folder in recoveries:self.restore_recovery(folder)
        if self._initial_path:self.open_document(self._initial_path)
        elif not recoveries and self.settings.get('last_project') and Path(self.settings['last_project']).is_file():self.open_document(self.settings['last_project'])
        self._initial_path=None;self.position_overlay()
        QTimer.singleShot(900,self,self.lan.check_clipboard)

    def restore_recovery(self,folder):
        doc=self.new_document(recovery_dir=folder)
        if doc is None:return False
        try:
            records,meta=storage.load_project(str(doc.recovery_path))
            doc._load_document(records,meta,None,clean=False)
            source=meta.get('recovered_from')
            doc._recovered_name=(Path(source).name if source else '未命名画布')+'（恢复）'
            doc._autosaved_digest=doc._current_digest
            import assets_runtime as assets
            assets.release_pending(records)
            self.update_document(doc)
            doc.message('已恢复，请另存项目；原文件未覆盖。',12000)
            return True
        except Exception as exc:
            doc._recovery_protected=True
            QMessageBox.warning(self,'恢复失败，副本已保留',str(exc));return False

    def closeEvent(self,event):
        if not self.lan.can_close():event.ignore();return
        # First obtain decisions for all tabs; cancellation leaves all documents open.
        self.setEnabled(False)
        try:
            for index in range(self.tabbar.count()):
                doc=self.tabbar.tabData(index)
                if doc._loading_project or doc._import_next <= doc._import_seq or doc.desk.saving or getattr(doc,'_lan_starting',False) or (getattr(doc,'_lan_binding',None) and doc._lan_binding.busy):
                    self.setEnabled(True);QMessageBox.information(self,'正在处理','请等待项目打开、图片导入或导出完成后关闭。');event.ignore();return
                self.tabbar.setCurrentIndex(index)
                self.setEnabled(True)
                if not doc._confirm_unsaved():event.ignore();return
                self.setEnabled(False)
            self._closing=True
            self.lan.shutdown()
            self.save_preferences(self.current_document())
            while self.tabbar.count():self.close_document(self.tabbar.count()-1,confirmed=True)
            self.jobs.pool.waitForDone()
            self.api.write_json(self.settings_path,self.settings)
            QApplication.instance().removeEventFilter(self)
            self._set_edge_cursor(None)
            event.accept()
        finally:self.setEnabled(True)
