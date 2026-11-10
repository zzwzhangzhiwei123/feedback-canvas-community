import base64
import binascii
import copy
import hashlib
import json
import math
import os
import re
import tempfile
import uuid
import zipfile
import zlib
import assets_runtime as assets

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QMimeData, QRectF, QThread, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter
from PySide6.QtWidgets import QApplication, QGraphicsScene

import contracts
from items import BoardItem, new_record, image_to_base64, base64_to_image


_FORMAT = "fbboard"
_MANIFEST = "manifest.json"
_MAX_MANIFEST = 16 * 1024 * 1024
_MAX_ASSET = 128 * 1024 * 1024
_MAX_EXPANSION = 512 * 1024 * 1024
_MAX_ASSETS = 10000
_MAX_RECORDS = 100000
_MAX_IMAGE_PIXELS = 100_000_000
_MAX_RENDER_PIXELS = 60_000_000
_MAX_RENDER_DIMENSION = 16000
_KINDS = {
    "image", "text", "rect", "ellipse", "line", "arrow", "pen", "polyline",
    "bezier", "number", "region", "redact", "magnifier",
}
_GEOMETRY = ("x", "y", "w", "h", "rotation", "scale", "z", "stroke_width",
             "opacity", "font_size")
_ASSET_NAME = re.compile(r"assets/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}\Z")
_HASH_ASSET = re.compile(r"assets/([0-9a-f]{64})\.png\Z")


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}必须是有限数值。")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name}必须是有限数值。") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name}必须是有限数值。")
    return result


def _validate_records(records):
    if not isinstance(records, list) or len(records) > _MAX_RECORDS:
        raise ValueError(f"记录必须是列表，且不能超过 {_MAX_RECORDS} 条。")
    identifiers = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("记录必须是 JSON 对象。")
        identifier = record.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ValueError("记录 id 必须是非空且不重复的字符串。")
        identifiers.add(identifier)
        kind = record.get("kind")
        if not isinstance(kind, str) or kind not in _KINDS:
            raise ValueError("记录包含不支持的图元类型。")
        for key in _GEOMETRY:
            if key in record:
                _finite(record[key], key)
        for key in ("group", "attached_to", "source_id"):
            if key in record and not isinstance(record[key], str):
                raise ValueError(f"{key}必须是字符串。")
        image = record.get("image", "")
        if not isinstance(image, str) or len(image) > 4 * ((_MAX_ASSET + 2) // 3):
            raise ValueError("记录图片必须是大小不超过 128 MiB 的 Base64 图片。")
        points = record.get("points", [])
        if not isinstance(points, list):
            raise ValueError("路径节点必须是列表。")
        for point in points:
            if not isinstance(point, dict) or "x" not in point or "y" not in point:
                raise ValueError("路径节点必须包含 x 和 y。")
            _finite(point["x"], "节点 x")
            _finite(point["y"], "节点 y")
            for key in ("in", "out"):
                if key in point:
                    handle = point[key]
                    if not isinstance(handle, (list, tuple)) or len(handle) != 2:
                        raise ValueError("曲线控制柄必须包含两个有限数值。")
                    for value in handle:
                        _finite(value, "曲线控制柄")
        clips=record.get("selection_clips",[])
        if not isinstance(clips,list):raise ValueError("裁切记录必须是列表。")
        for clip in clips:
            if not isinstance(clip,dict) or not isinstance(clip.get("batch"),str):raise ValueError("裁切记录格式错误。")
            polygon=clip.get("polygon")
            if not isinstance(polygon,list) or len(polygon)!=4:raise ValueError("裁切区域必须包含四个顶点。")
            for vertex in polygon:
                if not isinstance(vertex,(list,tuple)) or len(vertex)!=2:raise ValueError("裁切坐标格式错误。")
                for value in vertex:_finite(value,"裁切坐标")
        erasures=record.get("erasures", [])
        if not isinstance(erasures,list):
            raise ValueError("擦除记录必须是列表。")
        for segment in erasures:
            if not isinstance(segment,(list,tuple)) or len(segment)!=5:
                raise ValueError("擦除记录格式错误。")
            for value in segment: _finite(value,"擦除坐标")
            if segment[4]<=0: raise ValueError("橡皮擦半径必须大于零。")
        crop = record.get("crop")
        if crop is not None:
            if not isinstance(crop, (list, tuple)) or len(crop) != 4:
                raise ValueError("裁剪区域必须包含四个有限数值。")
            for value in crop:
                _finite(value, "裁剪区域")


def _json_bytes(value, limit):
    try:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("数据不是有效的 JSON，或包含非有限数值。") from exc
    if len(data) > limit:
        raise ValueError(f"JSON 数据过大，上限为 {limit // (1024 * 1024)} MiB。")
    return data


def _reject_constant(value):
    raise ValueError(f"JSON 中不允许出现 {value}。")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 对象中存在重复字段。")
        result[key] = value
    return result


def _parse_json(data, limit):
    if len(data) > limit:
        raise ValueError("JSON 数据超过允许的大小。")
    try:
        return json.loads(data.decode("utf-8-sig"), parse_constant=_reject_constant,
                          parse_float=lambda value: _finite(float(value), "JSON 数值"),
                          object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("JSON 数据损坏或格式无效。") from exc


def _check_image_size(width, height):
    if width <= 0 or height <= 0:
        raise ValueError("无法读取图片的有效像素尺寸。")
    if width * height > _MAX_IMAGE_PIXELS:
        raise ValueError("图片超过 1 亿像素上限，请先缩小图片。")


def _check_reader(reader):
    if not reader.canRead():
        raise ValueError(f"无法读取图片或图片格式不受支持：{reader.errorString()}")
    size = reader.size()
    _check_image_size(size.width(), size.height())


def _check_image_bytes(data):
    if not data or len(data) > _MAX_ASSET:
        raise ValueError("图片资源为空或超过 128 MiB 上限。")
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    try:
        reader = QImageReader(buffer)
        _check_reader(reader)
    finally:
        buffer.close()


def _decode_base64(value):
    if assets.is_ref(value):
        data=assets.png(value)
        _check_image_bytes(data)
        return data
    if len(value) > 4 * ((_MAX_ASSET + 2) // 3):
        raise ValueError("图片资源超过 128 MiB 上限。")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("记录包含无效的 Base64 图片。") from exc
    _check_image_bytes(data)
    return data


def _migrate_manifest(manifest):
    if not isinstance(manifest, dict):
        raise ValueError("项目清单必须是 JSON 对象。")
    version = manifest.get("version", 0)
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("项目版本必须是整数。")
    if version < 0 or version > contracts.FORMAT_VERSION:
        raise ValueError("项目版本不受支持，请使用兼容版本打开。")
    if manifest.get("format") != _FORMAT:
        if version != 0 or manifest.get("format") is not None:
            raise ValueError("文件不是有效的 fbboard 项目。")
    if version == 0:
        # 旧版允许内嵌图片、items/metadata 字段和缺失的 id。
        records = manifest.get("records", manifest.get("items"))
        if not isinstance(records, list) or len(records) > _MAX_RECORDS:
            raise ValueError("旧版项目的记录列表无效或过大。")
        for record in records:
            if isinstance(record, dict) and "id" not in record:
                record["id"] = str(uuid.uuid4())
        manifest = {
            "format": _FORMAT, "version": contracts.FORMAT_VERSION,
            "records": records, "meta": manifest.get("meta", manifest.get("metadata", {})),
        }
    if "records" not in manifest or not isinstance(manifest.get("meta"), dict):
        raise ValueError("项目清单必须包含 records 列表和 meta 对象。")
    _validate_records(manifest["records"])
    return manifest, version == 0


def save_project(path: str, records: list[dict], meta: dict, progress=None) -> None:
    report=progress or (lambda value,message:None)
    report(-1,"正在整理项目…")
    _validate_records(records)
    if not isinstance(meta, dict):
        raise ValueError("项目 meta 必须是 JSON 对象。")
    disk_records = copy.deepcopy(records)
    assets = {}
    image_keys = {}
    expanded = 0
    total_images=len({r.get("image") for r in disk_records if r.get("image")})
    completed_images=0
    for record in disk_records:
        image = record.get("image", "")
        if not image:
            continue
        if image not in image_keys:
            report(-1,f"正在处理图片 {completed_images+1}/{total_images}…")
            data = _decode_base64(image)
            key = f"assets/{hashlib.sha256(data).hexdigest()}.png"
            if key not in assets:
                expanded += len(data)
                if expanded > _MAX_EXPANSION or len(assets) >= _MAX_ASSETS:
                    raise ValueError("项目图片资源过多或展开大小超过 512 MiB。")
                assets[key] = data
            image_keys[image] = key
            completed_images+=1
            report(round(65*completed_images/max(1,total_images)),f"图片处理完成 {completed_images}/{total_images}")
        record["image"] = image_keys[image]
    manifest = _json_bytes({"format": _FORMAT, "version": contracts.FORMAT_VERSION,
                            "records": disk_records, "meta": meta}, _MAX_MANIFEST)
    if expanded + len(manifest) > _MAX_EXPANSION:
        raise ValueError("项目展开大小超过 512 MiB 上限。")
    destination = os.path.abspath(os.fspath(path))
    report(65,"正在写入项目文件…")
    descriptor, temporary = tempfile.mkstemp(prefix=".fbboard-", suffix=".tmp",
                                             dir=os.path.dirname(destination))
    try:
        with os.fdopen(descriptor, "w+b") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6) as archive:
                archive.writestr(_MANIFEST, manifest)
                written=0
                for key, data in assets.items():
                    info=zipfile.ZipInfo(key)
                    info.compress_type=zipfile.ZIP_STORED
                    info.file_size=len(data)
                    with archive.open(info,"w") as member:
                        for offset in range(0,len(data),1024*1024):
                            chunk=memoryview(data)[offset:offset+1024*1024]
                            member.write(chunk);written+=len(chunk)
                            report(65+int(33*written/max(1,expanded)),f"已写入 {written/1048576:.1f} / {expanded/1048576:.1f} MB")
            report(99,"正在确认磁盘写入…")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        report(100,"项目文件已保存")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _archive_members(archive):
    infos = archive.infolist()
    if len(infos) > _MAX_ASSETS + 2:
        raise ValueError("项目压缩包的资源条目过多。")
    members = {}
    expanded = 0
    for info in infos:
        name = info.filename
        if name in members:
            raise ValueError("项目压缩包包含重复条目。")
        if name != _MANIFEST and name != "assets/" and not _ASSET_NAME.fullmatch(name):
            raise ValueError("项目压缩包包含无效的资源路径。")
        if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError("不支持加密项目或此压缩算法。")
        limit = _MAX_MANIFEST if name == _MANIFEST else _MAX_ASSET
        if info.file_size < 0 or info.file_size > limit:
            raise ValueError("项目清单或图片资源的展开大小超过上限。")
        if name == "assets/" and info.file_size:
            raise ValueError("项目资源目录条目无效。")
        expanded += info.file_size
        if expanded > _MAX_EXPANSION:
            raise ValueError("项目展开大小超过 512 MiB，已拒绝可能的压缩炸弹。")
        members[name] = info
    if _MANIFEST not in members:
        raise ValueError("项目缺少 manifest.json 清单。")
    return members


def _read_member(archive, info, limit):
    with archive.open(info, "r") as stream:
        data = stream.read(min(info.file_size, limit) + 1)
    if len(data) != info.file_size or len(data) > limit:
        raise ValueError("压缩条目的实际展开大小无效。")
    return data


# Decoded-pixel budget for background materialization on open/refresh. Beyond it, images stay
# as compressed PNG and decode lazily (old behaviour), so very large boards cannot exhaust RAM.
MATERIALIZE_BUDGET = 768 * 1024 * 1024


def load_project(path, materialize=False, progress=None):
    """materialize=True (call from a worker thread): decode images here and return in-memory
    references, so building the canvas on the UI thread needs no PNG decoding. The original PNG
    bytes are retained, so saving writes identical assets. Callers must assets.release_pending()."""
    records, meta, raw = _load_project(path)
    if materialize:
        _materialize(records, raw, progress)
    return records, meta


def _materialize(records, raw, progress=None):
    budget = MATERIALIZE_BUDGET
    refs = {}
    total = len(raw)
    try:
        for record in records:
            if progress and total:
                progress(len(refs), total)
            encoded = record.get("image", "")
            if not encoded or assets.is_ref(encoded):
                continue
            if encoded not in refs:
                data = raw.get(encoded)
                if data is None or budget <= 0:
                    continue
                image = QImage.fromData(data, "PNG")
                if image.isNull():
                    continue  # keep the compressed form; the canvas decodes it as before
                budget -= image.sizeInBytes()
                if budget < 0:
                    continue
                refs[encoded] = assets.register(image, data)
            record["image"] = refs[encoded]
    except Exception:
        assets.release_pending(records)
        raise


def _load_project(path):
    raw = {}
    try:
        with open(path, "rb") as stream:
            # 在解析中央目录前也限制容器大小，避免大量伪造条目占用内存。
            if os.fstat(stream.fileno()).st_size > _MAX_EXPANSION + 16 * 1024 * 1024:
                raise ValueError("项目压缩文件过大。")
            with zipfile.ZipFile(stream, "r") as archive:
                members = _archive_members(archive)
                manifest, legacy = _migrate_manifest(_parse_json(
                    _read_member(archive, members[_MANIFEST], _MAX_MANIFEST), _MAX_MANIFEST))
                images = {}
                inline_images = set()
                for record in manifest["records"]:
                    image = record.get("image", "")
                    if not image:
                        continue
                    if _ASSET_NAME.fullmatch(image):
                        if image not in members:
                            raise ValueError("项目引用的图片资源不存在。")
                        if image not in images:
                            data = _read_member(archive, members[image], _MAX_ASSET)
                            digest = _HASH_ASSET.fullmatch(image)
                            if digest and hashlib.sha256(data).hexdigest() != digest.group(1):
                                raise ValueError("项目图片资源的校验值不匹配。")
                            _check_image_bytes(data)
                            images[image] = base64.b64encode(data).decode("ascii")
                            raw[images[image]] = data
                        record["image"] = images[image]
                    elif legacy:
                        if image not in inline_images:
                            _decode_base64(image)
                            inline_images.add(image)
                    else:
                        raise ValueError("项目图片必须引用 assets 中的资源。")
                return manifest["records"], manifest["meta"], raw
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, RuntimeError,
            NotImplementedError, zlib.error) as exc:
        raise ValueError("项目压缩包损坏或使用了不支持的格式。") from exc


def _application():
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        raise RuntimeError("此操作需要已创建的 QApplication。")
    if QThread.currentThread() != app.thread():
        raise RuntimeError("画布渲染和剪贴板操作必须在界面主线程执行。")
    return app


def _scene_with_bounds(records):
    _application()
    _validate_records(records)
    scene = QGraphicsScene()
    scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
    try:
        items = []
        for record in records:
            if record.get("hidden", False):
                continue
            item = BoardItem(record)
            item.export_render = True
            item.setSelected(False)
            scene.addItem(item)
            items.append(item)
        bounds = None
        for item in items:
            # 由图元计算完整场景变换，保留旋转中心、缩放和世界坐标。
            rect = item.mapRectToScene(item.visual_bounds()).normalized()
            for value in (rect.left(), rect.top(), rect.right(), rect.bottom()):
                _finite(value, "图元世界边界")
            if bounds is None:
                bounds = QRectF(rect)
            else:
                left = min(bounds.left(), rect.left())
                top = min(bounds.top(), rect.top())
                right = max(bounds.right(), rect.right())
                bottom = max(bounds.bottom(), rect.bottom())
                bounds = QRectF(left, top, _finite(right - left, "世界边界宽度"),
                                _finite(bottom - top, "世界边界高度"))
        scene.clearSelection()
        return scene, bounds if bounds is not None else QRectF()
    except Exception:
        scene.clear()
        raise


def records_bounds(records) -> QRectF:
    scene, bounds = _scene_with_bounds(records)
    try:
        return QRectF(bounds)
    finally:
        scene.clear()


def render_records(records, scale=1.0, background="#202020", padding=0) -> QImage:
    scale = _finite(scale, "导出缩放")
    padding = _finite(padding, "导出边距")
    if scale <= 0 or padding < 0:
        raise ValueError("导出缩放必须大于零，边距不能为负数。")
    color = QColor(background)
    if not color.isValid():
        raise ValueError("导出背景颜色无效。")
    scene, bounds = _scene_with_bounds(records)
    try:
        source = bounds.adjusted(-padding, -padding, padding, padding)
        if source.width() <= 0:
            source.setWidth(1.0 / scale)
        if source.height() <= 0:
            source.setHeight(1.0 / scale)
        width = source.width() * scale
        height = source.height() * scale
        if not math.isfinite(width) or not math.isfinite(height) or max(width,height) >= 2147483647:
            raise ValueError("导出尺寸超出系统图像格式的范围，未降低分辨率。")
        pixel_width, pixel_height = max(1, math.ceil(width)), max(1, math.ceil(height))
        image = QImage(pixel_width, pixel_height, QImage.Format.Format_ARGB32_Premultiplied)
        if image.isNull():
            raise ValueError("可用内存不足，无法生成此尺寸 PNG；未自动缩小或压缩画质。")
        image.fill(color)
        painter = QPainter(image)
        try:
            painter.setRenderHints(QPainter.RenderHint.Antialiasing
                                   | QPainter.RenderHint.TextAntialiasing
                                   | QPainter.RenderHint.SmoothPixmapTransform)
            scene.render(painter, QRectF(0, 0, width, height), source,
                         Qt.AspectRatioMode.IgnoreAspectRatio)
        finally:
            painter.end()
        return image
    finally:
        scene.clear()


def clone_records(records, dx=40, dy=40) -> list[dict]:
    _validate_records(records)
    dx, dy = _finite(dx, "复制 x 偏移"), _finite(dy, "复制 y 偏移")
    result = copy.deepcopy(records)
    identifiers = {record["id"]: str(uuid.uuid4()) for record in result}
    groups = {record["group"]: str(uuid.uuid4()) for record in result if record.get("group")}
    for record in result:
        record["id"] = identifiers[record["id"]]
        record["x"] = _finite(record.get("x", 0) + dx, "复制后的 x")
        record["y"] = _finite(record.get("y", 0) + dy, "复制后的 y")
        if "group" in record:
            record["group"] = groups.get(record["group"], "")
        for key in ("attached_to", "source_id"):
            if key in record:
                record[key] = identifiers.get(record[key], "")
    return result


def _record_from_image(image, x=0, y=0, encoded=None):
    x, y = _finite(x, "图片 x"), _finite(y, "图片 y")
    if image.isNull():
        raise ValueError("图片为空或解码失败。")
    _check_image_size(image.width(), image.height())
    image = QImage(image)
    image.setDevicePixelRatio(1.0)
    record = new_record("image")
    record.update(x=x, y=y, w=image.width(), h=image.height(), source_width=image.width(), source_height=image.height(), image=assets.register(image, encoded))
    _validate_records([record])
    if not record["image"]:
        raise ValueError("图片编码失败。")
    return record


def _record_from_png(data, width, height, x=0, y=0):
    # Retain existing PNG bytes. No decode -> re-encode -> decode round trip.
    _check_image_size(width, height)
    image=QImage.fromData(data,"PNG")
    if image.isNull():raise ValueError("PNG 图片解码失败。")
    return _record_from_image(image,x,y,encoded=data)

def import_image(path, x=0, y=0) -> dict:
    _finite(x, "图片 x")
    _finite(y, "图片 y")
    reader = QImageReader(os.fspath(path))
    reader.setAutoTransform(True)
    _check_reader(reader)
    if bytes(reader.format()).lower() == b"png" and reader.transformation().value == 0:
        with open(path, "rb") as stream:
            data = stream.read(_MAX_ASSET + 1)
        if len(data) > _MAX_ASSET:
            raise ValueError("图片资源超过可用大小。")
        return _record_from_png(data, reader.size().width(), reader.size().height(), x, y)
    image = reader.read()
    if image.isNull():
        raise ValueError(f"图片解码失败：{reader.errorString()}")
    return _record_from_image(image, x, y)


def native_scale(records):
    scale = 1.0
    for record in records:
        if record.get("hidden") or record.get("kind") not in {"image", "magnifier"}:
            continue
        iw, ih = record.get("source_width"), record.get("source_height")
        if not iw or not ih:
            image = base64_to_image(record.get("image", ""))
            iw, ih = image.width(), image.height()
        _, _, cw, ch = record.get("crop") or [0, 0, 1, 1]
        factor = max(.000001, abs(record.get("scale", 1)))
        scale = max(scale, iw*cw/max(.000001,record["w"]*factor),
                    ih*ch/max(.000001,record["h"]*factor))
    return scale

_clipboard_token = None
_clipboard_records = None
_TOKEN_MIME = "application/x-feedback-studio-session"

class StudioMime(QMimeData):
    def __init__(self, records):
        super().__init__();self.records=copy.deepcopy(records)
    def formats(self):
        return list(dict.fromkeys(super().formats()+[contracts.MIME_TYPE]))
    def hasFormat(self,name):
        return name==contracts.MIME_TYPE or super().hasFormat(name)
    def retrieveData(self,name,preferred):
        if name==contracts.MIME_TYPE:
            records=copy.deepcopy(self.records)
            for r in records:
                if assets.is_ref(r.get("image")):
                    r["image"]=base64.b64encode(assets.png(r["image"])).decode("ascii")
            return QByteArray(_json_bytes(records,_MAX_EXPANSION))
        return super().retrieveData(name,preferred)

def clipboard_write(records, scale=None, background="#202020", padding=0):
    global _clipboard_token,_clipboard_records
    app=_application()
    _validate_records(records)
    bounds=records_bounds(records)
    scale=min(native_scale(records),1600/max(1,bounds.width(),bounds.height()))
    image=render_records(records,scale,background,padding)
    mime=StudioMime(records)
    _clipboard_token=uuid.uuid4().hex.encode("ascii")
    _clipboard_records=copy.deepcopy(records)
    mime.setData(_TOKEN_MIME,QByteArray(_clipboard_token))
    mime.setImageData(image)
    app.clipboard().setMimeData(mime)
    return image

def capture_clipboard():
    mime=_application().clipboard().mimeData()
    if mime is None:return ("empty",None)
    if _clipboard_token and mime.hasFormat(_TOKEN_MIME) and bytes(mime.data(_TOKEN_MIME))==_clipboard_token:
        return ("records",copy.deepcopy(_clipboard_records))
    if mime.hasFormat(contracts.MIME_TYPE):
        return ("records_json",bytes(mime.data(contracts.MIME_TYPE)))
    formats=[f for f in mime.formats() if f.startswith("image/")]
    formats.sort(key=lambda f:f!="image/png")
    for fmt in formats:
        data=bytes(mime.data(fmt))
        if data:return ("encoded",data)
    if mime.hasImage():
        # Windows screenshots: copy raw bytes now, decode on the worker (falls back to Qt below).
        import wincb
        fast=wincb.read_screenshot() if QApplication.platformName()=="windows" else None
        if fast is not None:
            kind,data=fast
            return ("encoded",data) if kind=="png" else ("dib",data)
        image=mime.imageData()
        if not isinstance(image,QImage) and hasattr(image,"toImage"):image=image.toImage()
        if isinstance(image,QImage):return ("bitmap",QImage(image))
    if mime.hasUrls():return ("files",[u.toLocalFile() for u in mime.urls() if u.isLocalFile()])
    if mime.hasText():return ("text",mime.text())
    return ("empty",None)

def decode_clipboard(payload):
    kind,value=payload
    if kind=="records":return value
    if kind=="records_json":
        data=_parse_json(value,_MAX_EXPANSION)
        records=data if isinstance(data,list) else _migrate_manifest(data)[0]["records"]
        _validate_records(records)
        # Keep external rich clipboard projects self-contained in this process.
        try:
            for r in records:
                if r.get("image"):
                    raw=_decode_base64(r["image"])
                    r["image"]=assets.register(QImage.fromData(raw),raw)
        except Exception:
            assets.release_pending(records)
            raise
        return records
    if kind=="encoded":
        _check_image_bytes(value)
        buffer=QBuffer();buffer.setData(QByteArray(value));buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader=QImageReader(buffer);reader.setAutoTransform(True);_check_reader(reader)
        original=value if bytes(reader.format()).lower()==b"png" and reader.transformation().value==0 else None
        return [_record_from_image(reader.read(),encoded=original)]
    if kind=="bitmap":return [_record_from_image(value)]
    if kind=="dib":
        import wincb
        return [_record_from_image(wincb.dib_to_image(value))]
    if kind=="files":return [import_image(p) for p in value]
    return []

def clipboard_read() -> list[dict]:
    try:
        clipboard = _application().clipboard()
    except RuntimeError:
        return []
    mime = clipboard.mimeData()
    if mime is None:
        return []
    if _clipboard_token and mime.hasFormat(_TOKEN_MIME) and bytes(mime.data(_TOKEN_MIME)) == _clipboard_token:
        return copy.deepcopy(_clipboard_records)
    if mime.hasFormat(contracts.MIME_TYPE):
        try:
            data = mime.data(contracts.MIME_TYPE)
            if data.size() > _MAX_EXPANSION:
                raise ValueError("剪贴板数据过大。")
            payload = _parse_json(bytes(data), _MAX_EXPANSION)
            records = payload if isinstance(payload, list) else _migrate_manifest(payload)[0]["records"]
            _validate_records(records)
            checked = set()
            for record in records:
                image = record.get("image", "")
                if image and image not in checked:
                    _decode_base64(image)
                    checked.add(image)
            return records
        except (ValueError, TypeError, OverflowError, RecursionError):
            pass
    # 优先检查编码图片的头部；Qt 的 imageData 可能会直接触发解码。
    raster_formats = [name for name in mime.formats() if name.startswith("image/")]
    raster_formats.sort(key=lambda name: name != "image/png")
    for name in raster_formats:
        if not mime.hasFormat(name):
            continue
        buffer = QBuffer()
        try:
            data = mime.data(name)
            if data.size() > _MAX_ASSET:
                continue
            buffer.setData(data)
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            reader = QImageReader(buffer)
            reader.setAutoTransform(True)
            _check_reader(reader)
            if bytes(reader.format()).lower() == b"png" and reader.transformation().value == 0:
                return [_record_from_png(bytes(data), reader.size().width(), reader.size().height())]
            return [_record_from_image(reader.read())]
        except (ValueError, TypeError, RuntimeError):
            pass
        finally:
            buffer.close()
    # 编码图片已被拒绝时，不让 imageData 绕过解码前的尺寸检查。
    if not raster_formats and mime.hasImage():
        try:
            image = mime.imageData()
            if not isinstance(image, QImage) and hasattr(image, "toImage"):
                image = image.toImage()
            if isinstance(image, QImage):
                return [_record_from_image(image)]
        except (ValueError, TypeError, RuntimeError):
            pass
    if mime.hasUrls():
        records = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            try:
                records.append(import_image(url.toLocalFile()))
            except (ValueError, OSError, RuntimeError):
                continue
            if len(records) >= _MAX_RECORDS:
                break
        return records
    return []
