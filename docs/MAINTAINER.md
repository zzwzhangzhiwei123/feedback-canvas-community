# 维护者同步说明

## 主线

任何人可 Fork 并提 PR，普通访问者无权直接推送本仓库。不要为开放贡献添加全员写权限。main 要求 PR、负责人审核、desktop-checks 成功、会话解决，禁止强推和删除。CODEOWNERS 必须配合 GitHub 规则才生效。

建议 Squash merge，使一个 PR 对应一项易采纳提交，自动合并保持关闭。管理员例外区别于普通贡献权限；维护者不能批准自己的 PR，可由其他授权 reviewer 审核，或在核实测试后使用所有者例外。凭据不共享给外部贡献者。

## 独立仓库之间

本仓库无自动同步，不继承其他仓库的历史、权限、分支。公共桌面功能优先在这里协作，未公开工作留在独立私有仓库。

在维护者的私有仓库本地克隆中，确认工作区干净后执行：

```powershell
git status --short --branch
git remote add community https://github.com/zzwzhangzhiwei123/feedback-canvas-community.git
git remote set-url --push community DISABLED
git fetch community main
git switch -c integrate/community-feature main
git cherry-pick PUBLIC_COMMIT_SHA
```

remote 已存在时检查地址，不重复添加。PUBLIC_COMMIT_SHA 换为后续功能 PR 的 Squash 提交，**不要选首次全量导入快照**，否则出现大量 add/add 冲突。

独立历史不妨碍 cherry-pick，双方改同一位置会冲突。解决后添加具体文件并 git cherry-pick --continue；放弃本次挑选可 git cherry-pick --abort。依赖多个提交时按顺序采纳。测试/审核后在私有库按自己的流程合入主线。

## 私有桌面改进转到公开

在公开仓库的干净功能分支中重做或应用所需桌面改动。也可选取已审查的纯桌面提交 cherry-pick；内容、提交信息、作者信息都要检查。推送前看：

```powershell
git diff --stat upstream/main...HEAD
git diff upstream/main...HEAD
git log --oneline upstream/main..HEAD
git remote -v
```

按实际 remote 命名调整 upstream。只包含希望公开的桌面代码、合成测试和文档，再推自己的 Fork 并提 PR。

禁止整体合并私有历史、--allow-unrelated-histories、--mirror、push --all、push --tags；不复制私人目录、日志或配置。cherry-pick 不是隐私过滤器，必须审查内容。

合并公开 PR、发布 Release、同步到独立仓库是三个动作。公开 PR 合并后可选择性同步到私有集成分支，由维护者决定各自主线采纳。
