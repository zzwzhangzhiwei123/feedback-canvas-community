"""BoardView API contract, implementation in board.py by main agent.
BoardView(QGraphicsView):
 signals changed(), selection_changed(), message(str), command(str), files_dropped(list), tool_changed(str)
 .board_scene (QGraphicsScene), .tool str, .style dict; .continuous bool
 .select_filter = 'all'|'images'|'annotations'; .annotations_visible bool
 .records()->list of dict synchronizes scene, .load_records(records), .add_records(records,select=True)
 .selected()->list[BoardItem] (selected unlocked or locked via sidebar), .all_items()->list[BoardItem]
 .select_items(items,extend=False), .by_id(id)->BoardItem|None
 .selection_records(expand=True): selected plus group and attached children (if owner selected)
 .set_tool(str), .focus_items(items=None): None means all, .reset_zoom(), .view_meta()->dict, .restore_view(meta)
 .edit_text(item), .edit_nodes(item), .set_property(key,value): applies to selected/style emits changed
 .update_links(): refresh magnifiers source image and computed connector records.
 .finish_path(), .cancel_gesture(), .delete_selection(), .duplicate(), .group_selection(), .ungroup_selection()
 .align_selection(direction), .normalize_selection(axis), .arrange_selection(), .flip_selection(axis)
 .reset_transform(), .reset_crop(), .toggle_lock(), .change_z(front=True), .select_all()
 .fitRegion(region): focus items geometrically inside region and region.
 .window(): owner main window for dragging via right mouse.
 Canvas coordinates use image original pixels. Coordinate transformations via Qt. PureRef mouse chords.
 command strings emitted keyboard: new,open,import,save,save_as,export,export_selection,copy,paste,undo,redo,fullscreen,always_top,new_text,minimize.
 Text edit/pen handles done by BoardView. State snapshots done by MainWindow via changed signal.
"""
