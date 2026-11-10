# 测试与发布

## 标准测试

Windows x64 / Python 3.12，安装 app/requirements.txt；根目录执行：

```powershell
.\.venv\Scripts\python.exe scripts\run_checks.py
```

入口先编译检查，再分独立进程运行五组，每组超时 180 秒。不要同进程混用 QApplication，也不要忽略失败退出码。

| 测试 | 覆盖 |
|---|---|
| test_v065.py | 位图解码、回退、无损资源、后台加载、网格、共享中浏览 |
| test_v064.py | 链接、HTTP/TLS 共端口、端口回退、窗口/极简、颜色、共享入口 |
| test_lan_core.py | 认证、指纹、租约、冲突、文件锁、历史、截断和停服 |
| test_lan_ui.py | 只读/编辑、回传、断线及未提交修改保留 |
| test_v6.py | 变换、多文档、资源、保存恢复、异步加载和旧文件 |

历史基准 60 项，以本次实际输出为准。测试使用合成数据和 offscreen，不读取真实剪贴板或执行防火墙/注册表修改。部分网络测试使用本机网卡；受限环境可能阻止连接。

test_application.py、test_upgrade.py 是早期交互实验；test_theme_regression.py 依赖未发布的 .revision-v011-baseline 和历史输出，不属于干净克隆的标准套件。test_pan_regression.py 可作专项，先看参数。perf/preview 脚本不是完整验收。

CI 用 Windows 执行相同入口，仅 contents:read。外部 PR 不接触发布凭据，首次贡献可能需维护者批准运行。不用 pull_request_target 执行外来代码。

## v0.6.6 人工验收（待完成）

- 单图/多图的亮度、曝光、对比、阴影、饱和度、暖度、淡色、色阶、曲线、锐化。
- 连续调节、重置和撤销；预览与全分辨率结果、原图保留。
- 保存重开、跨标签复制、外部粘贴和导出，参数与效果一致。
- 大图导入/打开、共享下载/上传和保存的加载条；期间能平移缩放。
- 等待时切换/关闭标签，无串文档、崩溃和丢失修改。
- 真实剪贴板、聊天/绘图软件互通、不同 DPI 窗口行为。
- 两台实际电脑的共享、编辑权、冲突、断线和恢复。

自动测试不能代替这些项目。记录环境、版本、步骤、实际结果和未验证项。

## 打包

```powershell
.\.venv\Scripts\python.exe app\build_release.py
```

使用当前解释器的 PyInstaller/Qt，结果 outputs/，缓存 .build/。名称从 contracts.APP_VERSION 派生，已有同名包时加时间戳而非覆盖。传递依赖尚未做完整跨平台锁定。

EXE 自检参数：--smoke-test REPORT_PATH --lan-smoke-test。使用单独 FEEDBACK_DATA_DIR，等待退出并确认 JSON 报告的 ok、lan、frozen、version；只看进程退出码不够。自检需要本机监听权限，人工界面验收另做。

## 维护者发布

1. 审核 PR、通过 main CI，完成相应人工验收。
2. 版本号除了 contracts.py，也存在 main.py 的应用名和数据目录中，一起检查。
3. 构建、EXE 自检、人工验收；准备说明与 SHA256。
4. 在准确源码提交上创建 tag，上传对应 EXE。不要把 0.6.5 包挂在 0.6.6 源码 tag 下。
5. 未验收标 Pre-release，稳定版才设 Latest，保留旧包。
6. 校验远端下载摘要，更新 README、HANDOFF。

目前手动发布。PR、CI 或 tag 不自动上传 EXE，也不自动同步独立仓库。
