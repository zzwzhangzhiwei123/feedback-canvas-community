from pathlib import Path
import test_v6 as t
root=Path(__file__).resolve().parent.parent
records,meta=t.storage.load_project(str(Path(__file__).parent/'assets'/'demo.fbboard'))
doc=t.shell.current_document();doc._load_document(records,meta,'示例反馈.fbboard',True)
t.shell.new_document();t.shell.current_document().current_file='另一个文件.fbboard';t.shell.current_document()._update_title()
t.shell.tabbar.setCurrentIndex(0);t.app.processEvents()
doc.view.focus_items();t.app.processEvents()
t.shell.grab().save(str(root/'workspace-preview.png'))
doc.actions['minimal'].setChecked(True);t.app.processEvents();t.shell.position_overlay();t.app.processEvents()
t.shell.grab().save(str(root/'minimal-preview.png'))
for d in t.shell.documents:d._confirm_unsaved=lambda:True
t.settle(lambda:all(not d.jobs.callbacks for d in t.shell.documents),5)
t.shell.close();t.app.processEvents()
