"""Fast, read-only access to screenshot data on the Windows clipboard.

Qt converts a clipboard bitmap into a QImage on the UI thread (≈40-90 ms for a 4K screenshot)
before any background work can start. Here the UI thread only copies the raw bytes (a few ms);
decoding happens on a worker. Anything unusual returns None so callers keep the Qt path.
"""
from __future__ import annotations

import os
import struct

CF_DIB = 8
MAX_BYTES = 256 * 1024 * 1024


def _api():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    return ctypes, user32, kernel32


_cached = None


def api():
    global _cached
    if _cached is None:
        _cached = _api()
    return _cached


def _read(ctypes, user32, kernel32, fmt):
    handle = user32.GetClipboardData(fmt)
    if not handle:
        return None
    size = kernel32.GlobalSize(handle)
    if not 0 < size <= MAX_BYTES:
        return None
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        return None
    try:
        return ctypes.string_at(pointer, size)
    finally:
        kernel32.GlobalUnlock(handle)


def plain_dib(data):
    """Header facts for an uncompressed 24/32-bit DIB (what screenshot tools publish); else None."""
    if not data or len(data) < 40:
        return None
    size, width, height, planes, bits, compression = struct.unpack_from("<IiiHHI", data, 0)
    if size not in (40, 108, 124) or planes != 1 or bits not in (24, 32) or compression != 0:
        return None  # bitfields / alpha-bearing / palette / compressed: let Qt handle it
    if width <= 0 or height == 0 or width * abs(height) > 100_000_000:
        return None
    stride = ((width * bits + 31) // 32) * 4
    if len(data) < size + stride * abs(height):
        return None
    return width, abs(height), bits, size, height < 0


def read_screenshot():
    """('png', bytes) | ('dib', bytes) | None. Never modifies the clipboard."""
    if os.name != "nt":
        return None
    try:
        ctypes, user32, kernel32 = api()
        png_format = user32.RegisterClipboardFormatW("PNG")
        has_png = png_format and user32.IsClipboardFormatAvailable(png_format)
        if not has_png and not user32.IsClipboardFormatAvailable(CF_DIB):
            return None
        if not user32.OpenClipboard(None):
            return None
        try:
            if has_png:
                data = _read(ctypes, user32, kernel32, png_format)
                if data and data.startswith(b"\x89PNG\r\n\x1a\n"):
                    return ("png", data)
            data = _read(ctypes, user32, kernel32, CF_DIB)
            if data and plain_dib(data):
                return ("dib", data)
            return None
        finally:
            user32.CloseClipboard()
    except Exception:
        return None


def dib_to_image(data):
    """Worker-thread decode of a plain DIB via Qt's BMP reader (adds the 14-byte file header)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImage, QImageReader
    info = plain_dib(data)
    if info is None:
        raise ValueError("不支持的剪贴板位图格式。")
    width, height, bits, header_size, _top_down = info
    offset = 14 + header_size
    bmp = b"BM" + struct.pack("<IHHI", 14 + len(data), 0, 0, offset) + data
    buffer = QBuffer()
    buffer.setData(QByteArray(bmp))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    image = QImageReader(buffer, b"bmp").read()
    if image.isNull() or image.width() != width or image.height() != height:
        raise ValueError("剪贴板位图解码失败。")
    if image.format() != QImage.Format.Format_RGB32:
        image = image.convertToFormat(QImage.Format.Format_RGB32)
    return image
