"""Shared on-disk and scene contract for Feedback Studio."""
APP_NAME = "反馈画布"
APP_VERSION = "0.6.6"
MIME_TYPE = "application/x-feedback-studio-items+json"
FORMAT_VERSION = 1
# Each record is JSON serializable:
# id: uuid string; kind: image,text,rect,ellipse,line,arrow,pen,polyline,bezier,number,region,redact,magnifier
# x,y: position; rotation: degrees; scale: uniform scale; z: stacking order
# w,h: local size (image crop is displayed inside this rect)
# stroke: hex; fill: hex or 'transparent'; stroke_width: float; opacity: float
# text,font_family,font_size,bold,text_color,text_bg,align: left/center/right
# points: list of {x,y,in:[dx,dy],out:[dx,dy]} local path nodes; closed: bool; end_arrow: bool
# image: base64 PNG; crop: [normalized x,y,w,h] or None; flip_x,flip_y: bool
# locked: bool; hidden: bool; group: string or ''; attached_to: image id or ''
# region title is stored in text; number label likewise
# magnifier: source_id and crop normalized; image is retained fallback image
# No actual Qt parent-child groups: group/attached_to expansion is controlled by BoardView.
# Item API: BoardItem(record), .record (dict), .to_record(), .apply_record(record),
# .refresh(), .boundingRect(), .shape(), .setSelected(), .node_positions()
# BoardItem must not implement interaction: view owns gestures and undo transactions.
# Geometry painting must remain in local coordinates; selection handles are drawn by view foreground.
