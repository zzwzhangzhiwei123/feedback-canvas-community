# 架构与数据

| 模块 | 职责 |
|---|---|
| app/main.py | 启动、单文档 MainWindow、动作、属性面板、撤销、保存恢复、导入导出 |
| app/workspace.py | 多标签 WorkspaceWindow、窗口外壳、极简同步、共享适配 |
| app/board.py | BoardView、选择/绘制手势、平移缩放、裁剪、变换与节点 |
| app/items.py | BoardItem 的几何、绘制与记录转换 |
| app/contracts.py / view_contract.py | 对象数据和 BoardView 接口约定 |
| app/storage.py | .fbboard 读写校验、资源、渲染、剪贴板 |
| app/assets_runtime.py | 会话图片引用、延迟编码、缓存、pin/unpin |
| app/jobs.py | 后台任务和主线程结果分发 |
| app/adjust.py / image_panel.py | 非破坏性调色、NumPy 计算、缓存与面板 |
| app/progress_ui.py / save_progress.py | 加载和保存进度界面 |
| app/lan_core.py | TLS 客户端/服务、凭据、锁、租约、修订、历史 |
| app/lan_ui.py | Qt 共享适配、只读/编辑、同步、断线、链接 |
| app/lan_discovery.py / lan_firewall.py | 发现和 Windows 防火墙辅助 |
| app/wincb.py | Windows 位图剪贴板和解码，异常回退 Qt |
| app/colorpicker.py | 全局自定义颜色 |
| app/extras.py / packing.py / compressor.py | 截图比较导出、布局和压缩辅助 |
| app/build_release.py | 当前 Python 环境的单文件构建 |

## 操作和线程

手势 → BoardView 修改记录 → changed 信号 → MainWindow 撤销/脏状态 → storage 保存。
输入捕获 → 后台解码 → 主线程构建画布。异步结果须落在发起任务的文档，不能落到后来切换的标签。

对象几何采用局部坐标，选择手柄由视图绘制。group/attached_to 是记录关系，不是 Qt 父子分组。复制/变换时考虑 source_id、分组、附件与 ID 重映射。后台可计算 QImage；widget、scene、QPixmap 操作留在主线程。

## 文件和资源

.fbboard 是 ZIP，含 manifest.json 和图片资源；storage 负责字段/大小校验、迁移、资源路径约束和原子写入。memory-image: 仅是进程内引用，持久化时物化为图片资源。

撤销和其他文档仍可能引用图片，资源回收考虑 pin、待处理工作和全部历史。调色参数不得覆盖原图，导出要应用效果。

storage.py 的主要限制：manifest 16 MiB、单资源 128 MiB、总解压预算 512 MiB、10000 资源、100000 记录、单图 1 亿像素；导出另有限制。改限制前评估内存和异常输入。

格式变更同时检查 contracts 与迁移逻辑。旧版可能忽略并在重存时丢失调色参数，不能覆盖唯一新版副本。

## 设置和恢复

Qt AppLocalDataLocation 决定默认版本数据目录，FEEDBACK_DATA_DIR 可覆盖。目录含 settings、恢复副本/标记、文档、共享缓存与日志。首次启动尝试迁移前版设置，恢复项目不随设置迁移。

恢复副本不同于原项目。异步保存、关闭和恢复保护需一起检查，不把清空数据目录当作通用修复。

## 共享

主机持文件锁，客户端默认只读；获得单写者租约后上传，同时检查凭据、证书指纹、租约、修订号和校验和。外部修改、断线、停服、过期或截断不得覆盖原文件/丢失未提交工作。

HTTP 说明页和 TLS 服务共端口，链接 fragment 携带访问凭据，发现协议不能替代认证。lan_core 不依赖 Qt，界面适配在 lan_ui，保持分层。
