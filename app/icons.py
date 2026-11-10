"""手绘的 24 × 24 线性工具图标，无外部资源依赖。"""

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


_PATHS = {
    "eraser": '<path d="m3 14 10-11 8 8-9 10H8Z M8 9l8 8 M12 21h9"/>',
    "select": '<path d="m5 3 14 9-7 1-3 7Z"/><path d="m12 13 5 7"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8" cy="9" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>',
    "text": '<path d="M4 7V4h16v3M12 4v16M8 20h8"/>',
    "rect": '<rect x="4" y="5" width="16" height="14" rx="1"/>',
    "ellipse": '<ellipse cx="12" cy="12" rx="9" ry="7"/>',
    "line": '<path d="m5 19 14-14"/><circle cx="5" cy="19" r="1" fill="currentColor" stroke="none"/><circle cx="19" cy="5" r="1" fill="currentColor" stroke="none"/>',
    "arrow": '<path d="M4 20 20 4M9 4h11v11"/>',
    "pen": '<path d="m4 20 1-6L16 3a1.5 1.5 0 0 1 2 0l3 3a1.5 1.5 0 0 1 0 2L10 19Z"/><path d="m14 5 5 5M5 14l5 5"/>',
    "polyline": '<path d="m4 18 5-12 6 11 5-12"/><rect x="2.5" y="16.5" width="3" height="3" fill="currentColor" stroke="none"/><rect x="7.5" y="4.5" width="3" height="3" fill="currentColor" stroke="none"/><rect x="13.5" y="15.5" width="3" height="3" fill="currentColor" stroke="none"/><rect x="18.5" y="3.5" width="3" height="3" fill="currentColor" stroke="none"/>',
    "bezier": '<path d="M4 18C4 4 20 20 20 6"/><path d="M4 18V6m16 0v12" stroke-dasharray="2 3"/><circle cx="4" cy="5" r="1.5"/><circle cx="20" cy="19" r="1.5"/><rect x="2.5" y="16.5" width="3" height="3" fill="currentColor" stroke="none"/><rect x="18.5" y="4.5" width="3" height="3" fill="currentColor" stroke="none"/>',
    "number": '<circle cx="12" cy="12" r="9"/><path d="m9 9 3-2v10M9 17h6"/>',
    "region": '<rect x="3" y="5" width="18" height="16" rx="2" stroke-dasharray="3 3"/><path d="M6 3h7M6 9h5"/>',
    "magnifier": '<circle cx="10" cy="10" r="6.5"/><path d="m15 15 6 6M7 10h6m-3-3v6"/>',
    "redact": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M6 9h12M6 12h12M6 15h12" stroke-width="2.5"/>',
    "undo": '<path d="m8 5-5 5 5 5M3 10h11a6 6 0 0 1 0 12" transform="translate(0 -2)"/>',
    "redo": '<path d="m16 5 5 5-5 5m5-5H10a6 6 0 0 0 0 12" transform="translate(0 -2)"/>',
    "save": '<path d="M4 3h13l4 4v14H3V3Z"/><path d="M7 3v6h9V3M7 21v-8h10v8m-4-16v2"/>',
    "open": '<path d="M3 18V5h6l3 3h8v3M3 20l3-9h16l-3 9Z"/>',
    "export": '<path d="M5 13v7h14v-7M12 16V3m-5 5 5-5 5 5"/>',
    "capture": '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/><rect x="6" y="7" width="12" height="10" rx="2"/><circle cx="12" cy="12" r="2.5"/>',
    "fit": '<path d="M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6M8 8l-5-5m13 5 5-5M8 16l-5 5m13-5 5 5"/>',
    "lock": '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/>',
    "unlock": '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 7.5-2M12 15v2"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    "group": '<rect x="3" y="3" width="18" height="18" rx="2" stroke-dasharray="3 3"/><rect x="6" y="6" width="7" height="7" rx="1"/><path d="M15 11h3v7h-7v-3"/>',
    "compare": '<rect x="2" y="4" width="8" height="16" rx="1.5"/><rect x="14" y="4" width="8" height="16" rx="1.5"/><path d="m4 15 2-4 2 2m8 2 2-4 2 2"/>',
    "template": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 8h18M10 8v13m4-9h3m-3 4h3"/>',
    "more": '<circle cx="5" cy="12" r="1.5" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.5" fill="currentColor" stroke="none"/>',
    "plus": '<path d="M12 4v16M4 12h16"/>',
}


@lru_cache(maxsize=256)
def _render_icon(name, color):
    tint = QColor(color)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        f'viewBox="0 0 24 24" color="{tint.name()}">'
        f'<g fill="none" stroke="{tint.name()}" stroke-width="1.75" '
        f'stroke-linecap="round" stroke-linejoin="round" opacity="{tint.alphaF():.6f}">'
        f'{_PATHS[name]}</g></svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    result = QIcon()
    # 同时提供常见缩放级别，避免高 DPI 工具栏放大低分辨率位图。
    for ratio in (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0):
        size = round(24 * ratio)
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        result.addPixmap(pixmap, QIcon.Mode.Normal, QIcon.State.Off)
    return result


def icon(name: str, color='#c5c5c5') -> QIcon:
    """返回独立的图标句柄；未知名称使用更多图标，无效颜色使用默认色。"""
    tint = QColor(color)
    if not tint.isValid():
        tint = QColor("#c5c5c5")
    key = name if name in _PATHS else "more"
    return QIcon(_render_icon(key, tint.name(QColor.NameFormat.HexArgb)))
