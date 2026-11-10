"""Session image store. Encoding is deferred to file persistence, not paste."""
from collections import OrderedDict
from threading import RLock
import uuid
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QImageWriter

_lock=RLock()
_images={}
_pending=set()
_pins={}
_decoded=OrderedDict()
_decoded_bytes=0
PREFIX='memory-image:'

def is_ref(value): return isinstance(value,str) and value.startswith(PREFIX)

def register(image, encoded=None):
    key=PREFIX+uuid.uuid4().hex
    image=QImage(image);image.setDevicePixelRatio(1)
    preview=image.scaled(1536,1536,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation) if max(image.width(),image.height())>1536 else image
    with _lock:
        _images[key]=(image,preview,encoded)
        _pending.add(key)
    return key

def image(key, preview=False):
    with _lock:
        entry=_images.get(key)
        return QImage(entry[1 if preview else 0]) if entry else QImage()

def png(key):
    with _lock:
        entry=_images.get(key)
        if entry is None: raise ValueError('图片资源已失效，请重新载入项目。')
        original,preview,encoded=entry
    if encoded is not None: return encoded
    buffer=QBuffer();buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer=QImageWriter(buffer,b'png');writer.setCompression(1)
    if not writer.write(original): raise ValueError(writer.errorString())
    encoded=bytes(buffer.data())
    with _lock:
        if key in _images: _images[key]=(original,preview,encoded)
    return encoded

def release_pending(records):
    with _lock:
        for r in records: _pending.discard(r.get('image'))

def pin(records):
    keys={r.get('image') for r in records if is_ref(r.get('image'))}
    with _lock:
        for key in keys: _pins[key]=_pins.get(key,0)+1
    return keys

def unpin(keys):
    with _lock:
        for key in keys:
            count=_pins.get(key,0)-1
            if count>0:_pins[key]=count
            else:_pins.pop(key,None)

def collect(records):
    keep={r.get('image') for r in records if is_ref(r.get('image'))}
    with _lock:
        keep.update(_pending);keep.update(_pins)
        for key in list(_images):
            if key not in keep: del _images[key]

def cached_decode(key, decode):
    global _decoded_bytes
    with _lock:
        if key in _decoded:
            _decoded.move_to_end(key)
            return QImage(_decoded[key])
    result=decode()
    size=result.sizeInBytes()
    if size<=96*1024*1024:
        with _lock:
            if key not in _decoded:
                _decoded[key]=QImage(result);_decoded_bytes+=size
            while _decoded_bytes>96*1024*1024:
                _,old=_decoded.popitem(last=False);_decoded_bytes-=old.sizeInBytes()
    return result
