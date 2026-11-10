# 反馈画布 / Feedback Studio

Windows 桌面视觉反馈工具：无限画布、可编辑图片与标注、.fbboard 项目保存、局域网共享。欢迎使用，以及在自己的 Fork 中开发并提交 Pull Request。

## 下载

- **稳定版 v0.6.5**：[下载 Windows EXE](https://github.com/zzwzhangzhiwei123/feedback-canvas-community/releases/download/v0.6.5/FeedbackCanvas-v0.6.5.exe)。
- **预览版 v0.6.6**：[说明与下载](https://github.com/zzwzhangzhiwei123/feedback-canvas-community/releases/tag/v0.6.6)，新增非破坏性调色与加载条，仍待人工功能验收。
- [全部发行版](https://github.com/zzwzhangzhiwei123/feedback-canvas-community/releases) · [源码 ZIP](https://github.com/zzwzhangzhiwei123/feedback-canvas-community/archive/refs/heads/main.zip) · [报告问题](https://github.com/zzwzhangzhiwei123/feedback-canvas-community/issues)

下载 Release 中的 .exe 后运行，不需要 Python。Source code ZIP 是源码。程序没有自动更新器；升级时下载新版本，保留旧程序和项目备份。

.fbboard 保留原图、标注和编辑信息；外部粘贴或导出得到合成图片。旧版会忽略 v0.6.6 的调色效果，再次保存可能丢失新字段，因此保留新版项目副本。

## 功能与操作

支持图片、矩形、椭圆、直线、箭头、文字、画笔、荧光笔、折线、贝塞尔曲线、编号、区域、放大镜及色块；支持多标签、撤销、恢复副本与全局自定义颜色。

| 操作 | 方法 |
|---|---|
| 导入图片 | 拖入、Ctrl+I 或粘贴截图 |
| 平移 / 缩放 | 中键或 Alt+左键拖动 / 滚轮 |
| 聚焦 / 极简界面 | Space / Tab |
| 裁剪图片 | C+左键 |
| 选择 / 文字 / 箭头 / 画笔 | Q / Ctrl+N / A / B |
| 保存 / 撤销 | Ctrl+S / Ctrl+Z |
| 复制 / 粘贴 | Ctrl+C / Ctrl+V |

选中文字时滚轮调字号，选中线条时调粗细。内部复制保留可编辑对象，外部粘贴使用合成图。

## 源码运行

支持目标：Windows x64、Python 3.12。以下 PowerShell 命令在仓库根目录执行，无需激活虚拟环境：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r app\requirements.txt
.\.venv\Scripts\python.exe app\main.py
```

依赖以 app/requirements.txt 为准：PySide6-Essentials、PyInstaller、cryptography、NumPy。无需账号、API key、云服务器或 .env。其他操作系统未承诺支持。

开发时隔离数据并打开合成示例：

```powershell
$env:FEEDBACK_DATA_DIR = Join-Path $PWD '.dev-data'
.\.venv\Scripts\python.exe app\main.py app\assets\demo.fbboard
```

源码模式显式传入示例路径；--demo 主要用于打包版本。不要用真实工作项目测试。

## 测试与打包

```powershell
.\.venv\Scripts\python.exe scripts\run_checks.py
.\.venv\Scripts\python.exe app\build_release.py
```

构建输出在 outputs/。标准测试分别运行于独立进程，使用合成数据和 Qt offscreen，不碰真实系统剪贴板，不修改防火墙或注册表。网络测试主要用本机回环，部分端到端用例会使用本机网卡。环境限制导致失败也必须记录。详见[测试与发布](docs/TESTING_AND_RELEASE.md)。

## 同事和 agent 接手入口

| 文档 | 内容 |
|---|---|
| [贡献指南](CONTRIBUTING.md) | Fork、个人分支、同步上游、提交 PR |
| [AGENTS.md](AGENTS.md) | agent 工作边界、读取顺序和交接要求 |
| [架构与数据](docs/ARCHITECTURE.md) | 模块、格式、线程、资源和共享 |
| [测试与发布](docs/TESTING_AND_RELEASE.md) | 测试命令、已知限制、打包与验收 |
| [当前交接状态](docs/HANDOFF.md) | 当前版本、待办与后续交接格式 |
| [维护者同步](docs/MAINTAINER.md) | 审核、合并、独立仓库间选择性采纳 |

**贡献者只推送到自己的 Fork 和功能分支。** 公开可读不代表有写入本仓库的权限。PR 由维护者决定是否合并，不自动合并或发布。本仓库独立提供桌面开发所需源码、依赖、测试和文档，不依赖私人仓库或聊天记录。

## 局域网共享与排障

分享方点击“局域网共享”→“分享当前画布并复制链接”；接收方在反馈画布中 Ctrl+V。默认只读，申请编辑后可修改，“保存更新”回传分享方。同一时间只允许一人编辑。

- 项目通过 TLS 传输并校验证书指纹，HTTP 浏览器页面只是说明页。
- 默认 TCP 48761、发现服务 UDP 48762；端口占用时服务可能换用其他端口。
- 首次共享会请求管理员权限配置 Windows 防火墙，无权限可跳过并请 IT 协助。
- 链接 # 后含访问凭据，不要发到公开 Issue、日志或截图。停止共享后旧链接失效。
- VLAN、访客 Wi-Fi、网络隔离或受管防火墙可能导致失败；使用“高级设置与诊断”，不要靠关闭安全防护解决。
- 运行数据由 Qt AppLocalDataLocation 定位，也可用 FEEDBACK_DATA_DIR 覆盖。目录含设置、恢复项目、日志和缓存，排障时不要直接清空。

更新说明：[v0.6.6](CHANGELOG-v0.6.6.txt) · [v0.6.5](CHANGELOG-v0.6.5.txt) · [v0.6.4](CHANGELOG-v0.6.4.txt)。
