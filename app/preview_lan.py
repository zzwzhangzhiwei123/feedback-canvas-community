from pathlib import Path
import test_lan_ui as t
root=Path(__file__).resolve().parent.parent
case=t.UiTests();case.setUp()
t.app.processEvents();case.host.resize(1440,900);t.app.processEvents()
case.host.lan.show_dialog();t.app.processEvents()
# Hide the ephemeral test credentials in the screenshot.
case.host.lan.dialog.code.setText('连接信息由开启共享后生成，点击复制发送给同事')
case.host.grab().save(str(root/'lan-preview.png'))
case.host.lan.dialog.grab().save(str(root/'lan-dialog-preview.png'))
case.host.lan.dialog.hide();case.guest.resize(1440,900);t.app.processEvents()
case.guest.grab().save(str(root/'lan-reader-preview.png'))
case.tearDown()
