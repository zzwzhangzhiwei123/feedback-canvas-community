# 下游 Fork 维护记录

本页记录 Fork `partpark0079/feedback-canvas-community` 的维护方式、分支状态和与上游的同步情况。目的是让任何接手的人（同事或 agent）只看仓库就能知道：现在在哪个版本、有哪些支线、上游有没有新东西、哪些改动还没被上游采纳。

## 角色

| 角色 | 仓库 / 账号 | 说明 |
|---|---|---|
| 上游（upstream） | `zzwzhangzhiwei123/feedback-canvas-community` | 原作者 @zzwzhangzhiwei123 继续维护；本 Fork 的 PR 是否合并、是否发布 Release 由他决定 |
| 下游 Fork（origin） | `partpark0079/feedback-canvas-community` | 接手维护者 @partpark0079 的工作仓库，所有功能分支推送到这里 |
| 本地构建 | 由功能分支源码构建的 EXE | 维护者自用；不冒用上游正式发行版名称，不上传到上游 Releases |

本 Fork 对上游没有写权限（只读），这是预期状态，不需要申请。

## 本地 remote 配置

按 CONTRIBUTING.md 配置，接手时用 `git remote -v` 核对：

```powershell
git remote -v
# origin    https://github.com/partpark0079/feedback-canvas-community.git (fetch/push)
# upstream  https://github.com/zzwzhangzhiwei123/feedback-canvas-community.git (fetch)
# upstream  DISABLED (push)
git config remote.pushDefault origin
git config push.default current
```

提交署名使用 GitHub 隐私邮箱：`partpark0079 <358099618+partpark0079@users.noreply.github.com>`。

## 每次修改的固定流程

1. **先同步，再动手**
   ```powershell
   git fetch upstream
   git fetch origin
   git log --oneline HEAD..upstream/main      # 上游新提交
   git branch -r                              # 是否有他人新支线
   ```
   上游或他人支线有新提交时，先在下方“同步记录”登记，再合入。
2. **开支线**：新任务从最新 `upstream/main` 建分支；接着已有支线做时，先 `git merge upstream/main` 再改。命名：`feature/…`、`fix/…`、`docs/…`。
3. **本地修改与验证**：运行 `scripts/run_checks.py`，相关人工验收逐项记录“已做 / 未做”。
4. **文档同提交**：README、对应 CHANGELOG、`docs/HANDOFF.md` 交接记录、本页分支表，与代码一起提交。
5. **推送并发 PR**：`git push -u origin HEAD`，向 `zzwzhangzhiwei123/feedback-canvas-community:main` 发 PR，按模板写问题、改动、验证与限制。
6. **PR 结果回填**：上游合并、拒绝或要求修改后，更新下方分支表的状态。

禁止：向上游 main 推送、`--force` / `--mirror` / `--all`、在多人分支重写历史、提交 `.venv`、EXE、真实项目、日志、凭据或完整共享链接。

## 分支登记表

| 分支 | 起点 | 目的 | 状态 | PR |
|---|---|---|---|---|
| `docs/fork-maintenance-handoff` | `upstream/main@90e16a8`（v0.6.6） | 建立 Fork 维护流程与交接记录，记录 Python 3.13 环境基准 | 已推送待审 | [上游 #1](https://github.com/zzwzhangzhiwei123/feedback-canvas-community/pull/1) |

状态取值：开发中 / 已推送待审 / 上游已合并 / 上游未采纳（仅 Fork 使用）/ 已废弃。

## 与上游的同步记录

| 日期 | 上游 main | 本 Fork 动作 | 备注 |
|---|---|---|---|
| 2026-11-18 | `90e16a8` v0.6.6 稳定版 | 创建 Fork，基于此提交开始维护 | 上游当时只有 `main`、`release/v0.6.5` 两个分支，无他人支线 |

## 仅存在于本 Fork 的改动

上游未采纳但本 Fork 继续使用的改动登记在这里，避免后续同步时被覆盖或遗忘。

| 改动 | 分支 / 提交 | 上游状态 | 同步注意 |
|---|---|---|---|
| （暂无） | | | |
