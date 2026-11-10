# v0.6.1 源码说明

Python 3.12+，PySide6 6.11，PyInstaller 6，cryptography。使用 requirements.txt 安装依赖后运行 main.py；运行 build_release.py 生成单文件 Windows EXE。

新增模块：
- lan_core.py：仅依赖标准库及 cryptography，负责加密传输、操作系统文件锁、编辑租约、版本和历史副本；不依赖画布或 Qt。
- lan_ui.py：Qt 适配器，负责共享面板、只读浏览、申请编辑、保存回传及安全断开。
- workspace.py 仅添加适配器初始化、按钮、文件路由及关闭保护。
- main.py 仅添加共享保存入口、极简模式处理及可选打包自检；文件格式和画布算法未因共享而变更。

验证：
- test_v6.py：已有画布、多文件和旧项目兼容回归。
- test_lan_core.py：本机 TLS、访问校验、单写者、失效权限、版本冲突、文件历史、截断传输及停服写入保护。
- test_lan_ui.py：两套编辑窗口的只读、抢占编辑、回传、停止共享期间保存、过期修改保留和另存流程。
- 测试使用合成项目与 127.0.0.1，不使用实际工作文件或公网服务。
- GUI 测试使用 offscreen，发行 EXE 另外使用 Windows 平台启动并完成 TLS 上传下载自检。

运行构建时使用当前 Python 环境的库，不混用系统 PATH 中的 Qt/OpenSSL DLL。若当前 Python 自带 SSL 策略文件，构建脚本会一并打包，保持原有 SSL 行为。
