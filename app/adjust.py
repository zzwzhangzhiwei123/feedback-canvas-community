"""Non-destructive image adjustments (亮度/曝光/对比度/阴影/饱和度/暖度/淡色/色阶/曲线/锐化).

The record keeps the original pixels plus an "adjust" dict; the canvas shows a processed copy.
Interactive editing processes the 1536px preview synchronously; full resolution is computed on a
worker thread and swapped in when ready. Exports/copies compute full resolution synchronously.
"""
from __future__ import annotations

import json
import math
import threading
from collections import OrderedDict

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

DEFAULTS = {
    "brightness": 0, "exposure": 0, "contrast": 0, "shadows": 0,
    "saturation": 0, "warmth": 0, "tint": 0,
    "levels_black": 0, "levels_gamma": 100, "levels_white": 255,
    "curve": [[0, 0], [255, 255]],
    "sharpen_amount": 0, "sharpen_radius": 10,
}


def clean(value):
    """Validated, complete dict; None when everything is at default (no processing needed)."""
    if not isinstance(value, dict):
        return None
    out = {}
    for key, default in DEFAULTS.items():
        v = value.get(key, default)
        if key == "curve":
            try:
                pts = sorted((max(0, min(255, int(round(float(x))))), max(0, min(255, int(round(float(y)))))) for x, y in v)
            except (TypeError, ValueError):
                pts = [(0, 0), (255, 255)]
            dedup = []
            for x, y in pts:
                if dedup and dedup[-1][0] == x:
                    dedup[-1] = (x, y)
                else:
                    dedup.append((x, y))
            out[key] = [list(p) for p in (dedup if len(dedup) >= 2 else [(0, 0), (255, 255)])][:16]
        else:
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = default
            out[key] = v if math.isfinite(v) else default
    out["levels_black"] = max(0, min(253, out["levels_black"]))
    out["levels_white"] = max(out["levels_black"] + 2, min(255, out["levels_white"]))
    out["levels_gamma"] = max(10, min(999, out["levels_gamma"]))
    for k in ("brightness", "exposure", "contrast", "shadows", "saturation", "warmth", "tint"):
        out[k] = max(-100, min(100, out[k]))
    out["sharpen_amount"] = max(0, min(300, out["sharpen_amount"]))
    out["sharpen_radius"] = max(3, min(50, out["sharpen_radius"]))
    if all(out[k] == DEFAULTS[k] for k in DEFAULTS):
        return None
    return out


def signature(params):
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def _curve_lut(points):
    """Monotone cubic (Fritsch–Carlson) through the control points -> 256 LUT."""
    xs = np.array([p[0] for p in points], dtype=np.float64)
    ys = np.array([p[1] for p in points], dtype=np.float64)
    n = len(xs)
    if n == 2:
        lut = np.interp(np.arange(256), xs, ys)
    else:
        h = np.diff(xs)
        d = np.diff(ys) / h
        m = np.zeros(n)
        m[0], m[-1] = d[0], d[-1]
        for i in range(1, n - 1):
            m[i] = 0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2
        for i in range(n - 1):
            if d[i] == 0:
                m[i] = m[i + 1] = 0
            else:
                a, b = m[i] / d[i], m[i + 1] / d[i]
                s = a * a + b * b
                if s > 9:
                    t = 3 / math.sqrt(s)
                    m[i], m[i + 1] = t * a * d[i], t * b * d[i]
        x = np.arange(256, dtype=np.float64)
        idx = np.clip(np.searchsorted(xs, x, side="right") - 1, 0, n - 2)
        t = (x - xs[idx]) / h[idx]
        t2, t3 = t * t, t * t * t
        lut = ((2 * t3 - 3 * t2 + 1) * ys[idx] + (t3 - 2 * t2 + t) * h[idx] * m[idx]
               + (-2 * t3 + 3 * t2) * ys[idx + 1] + (t3 - t2) * h[idx] * m[idx + 1])
        lut = np.where(x < xs[0], ys[0], np.where(x > xs[-1], ys[-1], lut))
    return np.clip(lut, 0, 255) / 255.0


def curve_values(points):
    return _curve_lut(points)


def _tone_lut(p):
    """Per-channel tone curve on [0,1]: exposure, brightness, contrast, shadows, levels, curve."""
    x = np.linspace(0, 1, 256)
    x = x * (2.0 ** (p["exposure"] / 50.0))                      # ±2 stops
    x = x + p["brightness"] / 250.0                             # ±0.4
    c = p["contrast"] / 100.0
    x = (x - 0.5) * (1 + c if c >= 0 else 1 + c * 0.8) + 0.5
    x = np.clip(x, 0, 1)
    s = p["shadows"] / 100.0
    if s:
        weight = (1 - x) ** 2                                  # mostly the dark end
        x = np.clip(x + s * 0.5 * weight * (x if s < 0 else (1 - x) + x * 0.6), 0, 1)
    lo, hi = p["levels_black"] / 255.0, p["levels_white"] / 255.0
    x = np.clip((x - lo) / max(1e-6, hi - lo), 0, 1) ** (100.0 / p["levels_gamma"])
    curve = _curve_lut(p["curve"])
    return np.interp(x, np.linspace(0, 1, 256), curve)


def _box_blur(a, r):
    """Separable box blur via cumulative sums (float32, HxWxC)."""
    if r < 1:
        return a
    pad = np.pad(a, ((r + 1, r), (0, 0), (0, 0)), mode="edge")
    c = np.cumsum(pad, axis=0, dtype=np.float32)
    a = (c[2 * r + 1:] - c[:-2 * r - 1]) / (2 * r + 1)
    pad = np.pad(a, ((0, 0), (r + 1, r), (0, 0)), mode="edge")
    c = np.cumsum(pad, axis=1, dtype=np.float32)
    return (c[:, 2 * r + 1:] - c[:, :-2 * r - 1]) / (2 * r + 1)


def process(image: QImage, params, scale=1.0) -> QImage:
    """params must be clean(); scale = processed width / original width (keeps sharpen radius consistent)."""
    if image.isNull() or params is None:
        return image
    src = image.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = src.width(), src.height()
    stride = src.bytesPerLine()
    buf = np.frombuffer(src.constBits(), dtype=np.uint8, count=stride * h).reshape(h, stride)[:, :w * 4]
    px = buf.reshape(h, w, 4)
    alpha = px[:, :, 3].copy()
    rgb = px[:, :, 2::-1].astype(np.float32) / 255.0             # BGRA -> RGB

    lut = _tone_lut(params).astype(np.float32)
    idx = np.clip((rgb * 255.0 + 0.5).astype(np.int32), 0, 255)
    rgb = lut[idx]

    warm, tint = params["warmth"] / 100.0, params["tint"] / 100.0
    if warm or tint:
        rgb[:, :, 0] += warm * 0.12 + tint * 0.06
        rgb[:, :, 1] -= tint * 0.10
        rgb[:, :, 2] += -warm * 0.12 + tint * 0.06
    sat = params["saturation"] / 100.0
    if sat:
        lum = (rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32))[..., None]
        rgb = lum + (rgb - lum) * (1 + sat)
    amount = params["sharpen_amount"] / 100.0
    if amount:
        radius = max(1, int(round(params["sharpen_radius"] / 10.0 * max(scale, 0.1))))
        blur = _box_blur(_box_blur(np.clip(rgb, 0, 1), radius), radius)
        rgb = rgb + (rgb - blur) * amount

    out = np.empty((h, w, 4), dtype=np.uint8)
    out[:, :, 2::-1] = np.clip(rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[:, :, 3] = alpha
    result = QImage(out.data, w, h, w * 4, QImage.Format.Format_ARGB32).copy()
    if not image.hasAlphaChannel():
        result = result.convertToFormat(QImage.Format.Format_RGB32)
    return result


# ---- cache + background full-resolution work -------------------------------------------------
_lock = threading.Lock()
_cache = OrderedDict()
_cache_bytes = 0
_LIMIT = 512 * 1024 * 1024


def _cache_get(key):
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return QImage(_cache[key])
    return None


def _cache_put(key, image):
    global _cache_bytes
    size = image.sizeInBytes()
    if size > _LIMIT // 2:
        return
    with _lock:
        if key in _cache:
            return
        _cache[key] = QImage(image)
        _cache_bytes += size
        while _cache_bytes > _LIMIT and _cache:
            _, old = _cache.popitem(last=False)
            _cache_bytes -= old.sizeInBytes()


def source_id(encoded):
    return (hash(encoded), len(encoded)) if isinstance(encoded, str) else (0, 0)


def cached(image, params, sid, level, scale=1.0):
    key = (sid, level, signature(params))
    hit = _cache_get(key)
    if hit is None:
        hit = process(image, params, scale)
        _cache_put(key, hit)
    return hit


def peek(sid, level, params):
    return _cache_get((sid, level, signature(params)))


class _Hub(QObject):
    ready = Signal(object)        # cache key


_hub = None
_pool = None
_running = set()


def hub():
    global _hub, _pool
    if _hub is None:
        _hub = _Hub()
        _pool = QThreadPool(_hub)
        _pool.setMaxThreadCount(1)
    return _hub


class _Job(QRunnable):
    def __init__(self, key, image, params):
        super().__init__()
        self.key, self.image, self.params = key, image, params

    def run(self):
        try:
            _cache_put(self.key, process(self.image, self.params, 1.0))
        finally:
            with _lock:
                _running.discard(self.key)
            hub().ready.emit(self.key)


def request_full(image, params, sid):
    """Start a background full-resolution computation; returns the cache key to wait for."""
    key = (sid, "full", signature(params))
    hub()
    with _lock:
        if key in _cache or key in _running:
            return key
        _running.add(key)
    _pool.start(_Job(key, QImage(image), params))
    return key
