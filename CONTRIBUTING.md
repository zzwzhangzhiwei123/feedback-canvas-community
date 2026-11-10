# 参与开发

任何人可 Fork 并提交 PR，无需申请上游写权限。维护者审核采纳后才会影响上游 main。

## 第一次开始

1. 点击 GitHub 右上角 Fork，创建到自己的账号。
2. 将 YOUR_GITHUB_USER 替换为自己的用户名，在 PowerShell 执行：

```powershell
git clone https://github.com/YOUR_GITHUB_USER/feedback-canvas-community.git
cd feedback-canvas-community
git remote add upstream https://github.com/zzwzhangzhiwei123/feedback-canvas-community.git
git remote set-url --push upstream DISABLED
git config remote.pushDefault origin
git config push.default current
git fetch upstream
git switch -c feature/your-change upstream/main
git remote -v
```

origin 必须是你自己的 Fork。upstream 用于读取，推送地址设为 DISABLED 减少误操作；真正的隔离由 GitHub 权限保证。不要共享维护者凭据。

3. 阅读 README、AGENTS.md、docs/HANDOFF.md，按 README 安装环境并运行。
4. 每个功能单独建分支，提交前运行：

```powershell
git status --short --branch
git diff
.\.venv\Scripts\python.exe scripts\run_checks.py
# 换成真正修改的具体文件
git add app/your_changed_file.py docs/HANDOFF.md
git commit -m "fix: describe the user-visible change"
git push -u origin HEAD
```

your_changed_file.py 是占位名称。只添加本次任务文件，不添加虚拟环境、EXE、真实项目、日志或凭据。

## 提交 PR

GitHub 创建 PR，目标为 zzwzhangzhiwei123/feedback-canvas-community:main，来源为你的 Fork:功能分支。按模板写问题、改动、验证与限制。未完成可先创建 Draft PR。

维护者可要求调整或不采纳。提交或合并 PR 不会自动更新使用者电脑，也不自动同步独立仓库。

## 同步上游

先提交或妥善保存工作，再在自己的功能分支执行：

```powershell
git fetch upstream
git merge upstream/main
# 有冲突时逐项理解并解决，再测试
git push origin HEAD
```

没有未合并工作时，新任务从最新 upstream/main 新建分支。不要向上游 main 推送，不用 --mirror、--all 或 --force，不在多人分支随意重写历史。

## 修改约定

- 保留项目格式、原图、快捷键、剪贴板与恢复行为；有意变更需迁移说明。
- UI 操作在主线程，耗时工作放后台；回调对应最初文档。
- 使用合成项目，不上传真实公司素材、访问链接或私人代码。
- 更新受影响文档，区分本次测试通过、失败与未运行。
- 修改依赖、.github、保存格式或共享协议时重点说明影响。
- 安装包由维护者在 Releases 发布，贡献者不冒用正式发行版名称。
