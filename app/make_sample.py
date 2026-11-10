"""Generate original synthetic sample assets and a fully editable demonstration."""
from pathlib import Path
import sys
from PySide6.QtCore import QPointF,QRectF,Qt
from PySide6.QtGui import QColor,QImage,QPainter,QPen,QPainterPath,QLinearGradient,QRadialGradient,QFont
from PySide6.QtWidgets import QApplication
from items import new_record,image_to_base64,ensure_fonts
from storage import save_project,render_records

ROOT=Path(__file__).resolve().parent.parent

def bust(reference=False):
    im=QImage(620,680,QImage.Format.Format_ARGB32_Premultiplied); im.fill(QColor('#f6f6f2'))
    p=QPainter(im); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    grad=QLinearGradient(0,0,620,680); grad.setColorAt(0,QColor('#eceee8')); grad.setColorAt(1,QColor('#cdd8cf'))
    p.fillRect(im.rect(),grad)
    p.setPen(QPen(QColor('#bdc9c0'),1))
    for x in range(30,620,40): p.drawLine(x,440,x,680)
    for y in range(440,680,40): p.drawLine(0,y,620,y)
    p.setPen(Qt.PenStyle.NoPen)
    shadow=QRadialGradient(310,594,242); shadow.setColorAt(0,QColor(28,48,42,60)); shadow.setColorAt(1,QColor(28,48,42,0))
    p.setBrush(shadow); p.drawEllipse(QRectF(70,542,480,112))
    path=QPainterPath(); path.moveTo(102,580); path.cubicTo(108,464,178,455,238,413); path.lineTo(254,352); path.lineTo(366,352); path.lineTo(382,413); path.cubicTo(450,450,512,464,524,580); path.cubicTo(430,625,195,625,102,580)
    g=QLinearGradient(130,410,510,550); g.setColorAt(0,QColor('#ecede7'));g.setColorAt(.43,QColor('#bec6bb'));g.setColorAt(1,QColor('#7b8f84'));p.setBrush(g);p.drawPath(path)
    face=QPainterPath();face.moveTo(224,193);face.cubicTo(238,103,371,104,394,192);face.cubicTo(415,277,383,350,347,391);face.quadTo(310,417,275,389);face.cubicTo(238,341,204,272,224,193)
    g=QLinearGradient(213,200,411,280);g.setColorAt(0,QColor('#f5f4e9'));g.setColorAt(.5,QColor('#c6cdbe'));g.setColorAt(1,QColor('#829689'));p.setBrush(g);p.drawPath(face)
    p.setBrush(QColor('#a0afa0'));p.drawEllipse(QRectF(209,246,25,59));p.drawEllipse(QRectF(389,246,24,59))
    p.setPen(QPen(QColor('#657b6d'),5,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap));p.drawLine(QPointF(250,259),QPointF(286,254));p.drawLine(QPointF(336,254),QPointF(372,259))
    p.setPen(QPen(QColor('#a4b39f'),3));p.drawLine(QPointF(309,258),QPointF(301,308));p.drawLine(QPointF(301,308),QPointF(320,310))
    p.setPen(QPen(QColor('#728876'),3));p.drawLine(QPointF(282,337),QPointF(338,337))
    p.setPen(QPen(QColor('#d9e0d3'),3));p.drawLine(QPointF(284,344),QPointF(333,345))
    p.setPen(QPen(QColor('#91a38f'),4));p.drawLine(QPointF(169,492),QPointF(287,476));p.drawLine(QPointF(334,477),QPointF(449,495))
    if reference:
        p.setPen(QPen(QColor('#517867'),3)); q=QPainterPath();q.moveTo(240,305);q.quadTo(250,368,306,392);q.quadTo(365,366,383,305);p.drawPath(q)
        p.setPen(QPen(QColor('#e7efe2'),5));p.drawLine(QPointF(179,484),QPointF(282,465));p.drawLine(QPointF(340,465),QPointF(442,483))
    p.setPen(QColor('#5a7364'));p.setFont(QFont('Arial',10));p.drawText(24,32,'FORM STUDY  /  SYNTHETIC DEMO')
    p.setFont(QFont('Microsoft YaHei',10));p.drawText(24,653,'原创程序绘制示例 · 非用户工作素材')
    p.end();return im

def build():
    ensure_fonts()
    a,b=bust(),bust(True)
    records=[]
    def add(kind,**kw):
        r=new_record(kind,z=len(records),**kw);records.append(r);return r
    add('text',x=20,y=0,w=1300,h=1,text='角色反馈  /  体块与轮廓',font_size=30,bold=True,text_color='#24493d')
    add('text',x=22,y=57,w=1300,h=1,text='可编辑示例    ·    拖动图片    /    双击改字    /    选中箭头后滚轮改粗细',font_size=14,text_color='#7a8a7f')
    region=add('region',x=0,y=116,w=1380,h=796,text='01    头颈关系与肩线',font_size=15,stroke='#a9bbad',fill='#f9faf7',text_color='#52715f')
    region['z']=-100
    one=add('image',x=35,y=172,w=620,h=680,scale=.81,image=image_to_base64(a),name='当前版本 · 原创示意图')
    two=add('image',x=852,y=172,w=620,h=680,scale=.81,image=image_to_base64(b),name='修改参考 · 原创示意图')
    add('text',x=36,y=731,w=470,h=1,text='当前版本',font_size=15,bold=True,text_color='#547162')
    add('text',x=852,y=731,w=470,h=1,text='修改参考',font_size=15,bold=True,text_color='#547162')
    group='demo-note-1'
    add('ellipse',x=225,y=420,w=123,h=107,stroke='#e46b50',stroke_width=3,attached_to=one['id'])
    add('arrow',x=577,y=356,points=[{'x':0,'y':0},{'x':-255,'y':98}],stroke='#e46b50',stroke_width=3,group=group,attached_to=one['id'])
    add('number',x=572,y=269,text='1',fill='#e46b50',stroke_width=0)
    add('text',x=615,y=269,w=200,h=1,text='下颌轮廓\n收紧转折，保留\n清晰的大体块。',font_size=17,text_color='#355447',group=group,attached_to=one['id'])
    add('number',x=572,y=449,text='2',fill='#288b76',stroke_width=0)
    add('text',x=615,y=449,w=200,h=1,text='肩颈过渡\n减少横向堆积，\n让肩线更连贯。',font_size=17,text_color='#355447',attached_to=one['id'])
    add('bezier',x=160,y=560,points=[{'x':0,'y':0,'out':[45,-46],'in':[0,0]},{'x':170,'y':-16,'in':[-62,-28],'out':[0,0]}],stroke='#288b76',stroke_width=4,attached_to=one['id'])
    add('text',x=42,y=828,w=1250,h=1,text='试试：框选左图与标注 → Ctrl+C → 粘贴到聊天窗口。保存项目后，箭头、节点、文字仍可继续修改。',font_size=14,text_color='#687e6d')
    save_project(str(ROOT/'outputs'/'示例反馈.fbboard'),records,{'app':'反馈画布','sample':True})
    render_records(records,scale=1,background='#f3f5f0',padding=30).save(str(ROOT/'outputs'/'示例合图.png'))
    # Original application icon. No external artwork or font redistribution.
    im=QImage(256,256,QImage.Format.Format_ARGB32_Premultiplied);im.fill(Qt.GlobalColor.transparent)
    p=QPainter(im);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#197b69'));p.drawRoundedRect(QRectF(10,10,236,236),52,52)
    p.setPen(QPen(QColor('#f6fbf1'),14,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin));p.setBrush(Qt.BrushStyle.NoBrush);p.drawRoundedRect(QRectF(57,61,142,129),12,12)
    p.drawLine(87,104,141,104);p.drawLine(87,143,126,143);p.setPen(QPen(QColor('#ffd397'),17,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap));p.drawLine(155,157,203,109);p.end()
    im.save(str(ROOT/'app'/'feedback.ico'));im.save(str(ROOT/'app'/'feedback.png'))
    print('Sample:',len(records),'editable objects')
    return records

if __name__=='__main__':
    app=QApplication.instance() or QApplication(sys.argv)
    build()
