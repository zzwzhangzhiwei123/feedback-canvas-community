"""Temporary compression workspace. Scene data never replaces the source board."""
from pathlib import Path
from datetime import datetime
import re, uuid
from PySide6.QtCore import Qt, QTimer, QBuffer, QIODevice, QMimeData, QByteArray, QUrl, Signal, QSaveFile
from PySide6.QtGui import QImage, QImageWriter, QPixmap, QPainter
from PySide6.QtWidgets import (QWidget,QHBoxLayout,QVBoxLayout,QLabel,QLineEdit,QComboBox,
    QDoubleSpinBox,QSpinBox,QPushButton,QGraphicsScene,QGraphicsView,QApplication,QFrame,QFileDialog)
import storage
import assets_runtime as assets

def filesize(size):
    return f'{size/1024/1024:.2f} MB' if size>=1024*1024 else f'{size/1024:.1f} KB'

class Preview(QGraphicsView):
    def __init__(self):
        super().__init__();self.setScene(QGraphicsScene(self));self.item=None
        self.setBackgroundBrush(Qt.GlobalColor.darkGray)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    def set_image(self,image,fit=False):
        pixmap=QPixmap.fromImage(image)
        if self.item is None:self.item=self.scene().addPixmap(pixmap);fit=True
        else:self.item.setPixmap(pixmap)
        self.scene().setSceneRect(self.item.boundingRect())
        if fit:self.fitInView(self.item,Qt.AspectRatioMode.KeepAspectRatio)
    def wheelEvent(self,event):
        factor=1.2 if event.angleDelta().y()>0 else 1/1.2
        zoom=self.transform().m11()*factor
        if .01<=zoom<=32:self.scale(factor,factor)
        event.accept()
    def actual_size(self):self.resetTransform()

class CompressionDesk(QWidget):
    leave=Signal()
    copied=Signal(str)
    def __init__(self,jobs,directory,parent=None):
        super().__init__(parent);self.jobs=jobs;self.directory=Path(directory)
        self.serial=0;self.records=[];self.pins=set();self.busy=False;self.requested=False
        self.result=None;self.rendered=None;self.render_key=None;self.active=False;self.saving=False
        layout=QHBoxLayout(self);layout.setContentsMargins(12,12,12,12)
        self.preview=Preview();layout.addWidget(self.preview,1)
        side=QFrame();side.setFixedWidth(286);controls=QVBoxLayout(side);layout.addWidget(side)
        title=QLabel('打包压缩台');title.setStyleSheet('font-size:20px;font-weight:600');controls.addWidget(title)
        controls.addSpacing(14);controls.addWidget(QLabel('图片名称'))
        self.name=QLineEdit();controls.addWidget(self.name)
        controls.addWidget(QLabel('格式'))
        self.format=QComboBox()
        for label,value in [('JPEG · 体积小','jpeg'),('PNG · 无损编码','png'),('WebP · 体积小','webp')]:self.format.addItem(label,value)
        self.format.setCurrentIndex(self.format.findData("png"))
        controls.addWidget(self.format)
        controls.addWidget(QLabel('尺寸保留比例'))
        self.resolution=QDoubleSpinBox();self.resolution.setRange(.1,100);self.resolution.setDecimals(1);self.resolution.setSuffix(' %');self.resolution.setKeyboardTracking(False)
        controls.addWidget(self.resolution)
        controls.addWidget(QLabel('画质百分比'))
        self.quality=QSpinBox();self.quality.setRange(1,100);self.quality.setValue(85);self.quality.setSuffix(' %');self.quality.setKeyboardTracking(False)
        controls.addWidget(self.quality)
        self.note=QLabel('JPEG / WebP 的画质百分比不是文件体积百分比。');self.note.setWordWrap(True);controls.addWidget(self.note)
        self.dimensions=QLabel();self.dimensions.setWordWrap(True);controls.addWidget(self.dimensions)
        self.size=QLabel('');self.size.setStyleSheet('font-size:18px;font-weight:600');controls.addWidget(self.size)
        self.status=QLabel();self.status.setWordWrap(True);controls.addWidget(self.status)
        actual=QPushButton('查看 100% 像素');actual.clicked.connect(self.preview.actual_size);controls.addWidget(actual)
        fit=QPushButton('适应窗口');fit.clicked.connect(lambda:self.preview.fitInView(self.preview.scene().sceneRect(),Qt.AspectRatioMode.KeepAspectRatio));controls.addWidget(fit)
        controls.addStretch()
        hint=QLabel('滚轮缩放预览，拖动查看细节。\n确认后可 Ctrl+V 粘贴到聊天软件或文件夹。');hint.setWordWrap(True);controls.addWidget(hint)
        self.confirm=QPushButton('确定并复制');self.confirm.setObjectName('primary');self.confirm.clicked.connect(self.commit);controls.addWidget(self.confirm)
        self.direct=QPushButton('直接导出 100% PNG');self.direct.clicked.connect(self.export_native_png);controls.addWidget(self.direct)
        self.cancel=QPushButton('返回工作画布');self.cancel.clicked.connect(self.cancel_stage);controls.addWidget(self.cancel)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(280);self.timer.timeout.connect(self.generate)
        self.resolution.valueChanged.connect(self.changed);self.quality.valueChanged.connect(self.changed);self.format.currentIndexChanged.connect(self.changed)

    def open_records(self,records,background):
        self.serial+=1;self.active=True;self.result=None;self.rendered=None;self.render_key=None
        assets.unpin(self.pins);self.records=records;self.pins=assets.pin(records);self.background=background
        self.bounds=storage.records_bounds(records);self.native=storage.native_scale(records)
        self.name.setText('反馈-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
        self.format.setCurrentIndex(self.format.findData("png"))
        self.resolution.setValue(50.0)
        self.preview.scene().clear();self.preview.item=None
        self.dimensions.setText(f'原像素合图约 {round(self.bounds.width()*self.native)} × {round(self.bounds.height()*self.native)}')
        self.changed()

    def changed(self,*args):
        self.serial+=1;self.result=None;self.confirm.setEnabled(False)
        png=self.format.currentData()=='png';self.quality.setEnabled(not png)
        self.note.setText('PNG 编码无损；降低尺寸比例可以减小体积。' if png else '画质百分比不是文件体积百分比，以预览和实际大小为准。')
        if self.active:
            self.status.setText('正在更新预览…');self.size.setText('正在计算文件大小…');self.timer.start()

    def generate(self):
        if not self.active or self.saving:return
        if self.busy:self.requested=True;return
        self.requested=False;serial=self.serial;self.busy=True
        scale=self.native*self.resolution.value()/100
        key=round(scale,9)
        try:
            if self.render_key!=key:
                self.rendered=storage.render_records(self.records,scale,self.background,0);self.render_key=key
            image=QImage(self.rendered)
        except Exception as exc:
            self.busy=False;self.status.setText(f'无法生成此尺寸：{exc}');return
        fmt=self.format.currentData();quality=self.quality.value()
        def encode():
            buffer=QBuffer();buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            writer=QImageWriter(buffer,fmt.encode('ascii'))
            if fmt=='png':writer.setCompression(6)
            else:writer.setQuality(quality)
            if not writer.write(image):raise ValueError(writer.errorString())
            data=bytes(buffer.data());decoded=QImage(image) if fmt=='png' else QImage.fromData(data)
            if decoded.isNull():raise ValueError('无法读取压缩结果。')
            return data,decoded,fmt
        def finished(result,error):
            self.busy=False
            if self.active and serial==self.serial:
                if error:
                    self.status.setText('预览失败：'+error.strip().splitlines()[-1]+'；可直接导出 100% PNG。')
                    self.size.setText('预览文件大小不可用')
                else:
                    self.result=result;data,decoded,_=result
                    resize=self.preview.item is None or self.preview.item.pixmap().size()!=decoded.size()
                    self.preview.set_image(decoded,fit=resize)
                    self.size.setText('文件大小：'+filesize(len(data)))
                    self.dimensions.setText(f'{decoded.width()} × {decoded.height()} 像素 · {self.resolution.value():g}% 尺寸')
                    self.status.setText('这里显示的是实际压缩后的图片。');self.confirm.setEnabled(True)
            if self.active and not self.saving and (self.requested or serial!=self.serial):self.timer.start(20)
        self.jobs.submit(encode,finished,5)

    def export_native_png(self):
        if self.saving or not self.active:return
        name=re.sub(r'[\\/:*?"<>|]', '_', self.name.text()).strip(' .') or '反馈图片'
        filename,_=QFileDialog.getSaveFileName(self,'直接导出 100% PNG',name+'.png','PNG 图片 (*.png)')
        if not filename:return
        if not filename.lower().endswith('.png'):filename+='.png'
        self.saving=True;self.serial+=1;self.timer.stop();self.requested=False
        for control in (self.direct,self.confirm,self.cancel,self.format,self.resolution,self.quality):control.setEnabled(False)
        self.status.setText('正在准备原像素导出，请稍候…')
        def complete(result,error):
            self.saving=False
            for control in (self.direct,self.cancel,self.format,self.resolution):control.setEnabled(True)
            self.quality.setEnabled(self.format.currentData()!='png')
            self.confirm.setEnabled(self.result is not None)
            if error:
                self.status.setText('导出失败：'+error.strip().splitlines()[-1])
            else:
                self.status.setText('100% PNG 已保存：'+filename)
                self.size.setText('导出文件大小：'+filesize(result))
        def render_and_write():
            if self.busy:
                QTimer.singleShot(80,render_and_write);return
            self.result=None;self.rendered=None;self.render_key=None
            self.preview.scene().clear();self.preview.item=None
            self.status.setText('正在生成 100% 原像素图片…');self.status.repaint()
            try:
                image=storage.render_records(self.records,self.native,self.background,0)
            except Exception as exc:
                complete(None,str(exc));return
            self.status.setText('正在写入无损 PNG 文件…')
            def write():
                output=QSaveFile(filename);output.setDirectWriteFallback(False)
                if not output.open(QIODevice.OpenModeFlag.WriteOnly):raise ValueError(output.errorString())
                writer=QImageWriter(output,b'png');writer.setCompression(6)
                if not writer.write(image):
                    output.cancelWriting();raise ValueError(writer.errorString())
                if not output.commit():raise ValueError(output.errorString())
                return Path(filename).stat().st_size
            self.jobs.submit(write,complete,10)
        QTimer.singleShot(0,render_and_write)

    def commit(self):
        if self.result is None or self.saving:return
        data,image,fmt=self.result
        base=re.sub(r'[\\/:*?"<>|\x00-\x1f]','_',self.name.text()).strip(' .') or '反馈图片'
        if Path(base).suffix.lower() in {'.jpg','.jpeg','.png','.webp'}:base=Path(base).stem
        reserved={'CON','PRN','AUX','NUL'}|{f'{prefix}{i}' for prefix in ('COM','LPT') for i in range(1,10)}
        if base.upper() in reserved:base='图片_'+base
        ext='jpg' if fmt=='jpeg' else fmt
        path=self.directory/'clipboard_exports'/uuid.uuid4().hex/(base[:120]+'.'+ext)
        self.saving=True;self.confirm.setEnabled(False);self.cancel.setEnabled(False);self.status.setText('正在保存到剪贴板…')
        def write():
            path.parent.mkdir(parents=True,exist_ok=False);path.write_bytes(data)
            return str(path)
        def finished(result,error):
            self.saving=False;self.cancel.setEnabled(True)
            if error:
                self.status.setText('保存失败：'+error.strip().splitlines()[-1]);self.confirm.setEnabled(True);return
            mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(result)])
            mime.setData('image/'+fmt,QByteArray(data));mime.setImageData(image)
            mime.setData('application/x-qt-windows-mime;value="Preferred DropEffect"',QByteArray((1).to_bytes(4,'little')))
            QApplication.clipboard().setMimeData(mime)
            self.copied.emit(result);self.cancel_stage()
        self.jobs.submit(write,finished,10)

    def cancel_stage(self):
        if self.saving:return
        self.active=False;self.serial+=1;self.timer.stop();self.requested=False
        assets.unpin(self.pins);self.pins=set();self.records=[];self.result=None;self.rendered=None;self.render_key=None
        self.preview.scene().clear();self.preview.item=None
        self.leave.emit()
