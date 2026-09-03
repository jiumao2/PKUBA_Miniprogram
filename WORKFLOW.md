# PKUBA 团队协作与发布工作流

本文是 2–4 人核心团队及协作 Agent 从需求、开发、评审、测试到上线和回滚的唯一流程规范。业务规则写入相应专题文档，具体接手与候选交接见 [`docs/MAINTAINER_GUIDE.md`](docs/MAINTAINER_GUIDE.md)，部署命令和服务器结构写入 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)，本文不复制容易漂移的实现细节。

`main` 是生产发布来源。所有变更默认按可发布、可审计、可回滚标准交付；这项质量要求不自动授予标签、生产环境、付费服务或数据操作权限。

## 1. 开始修改前

1. 先完整阅读 `AGENTS.md` 与 `docs/MAINTAINER_GUIDE.md`，确认最新用户决定，并找到权威规则：稳定系统合同在 `docs/SYSTEM_SPEC.md`，路线图在 `Plan.md`，用户可见规则在 `docs/USER_GUIDE.md`，领域规则在对应专题规范。安全清理、依赖升级、扫描告警处置或行为“改进”还必须先核对 `docs/FINDING_DISPOSITIONS.md`，记录命中的处置 ID 及是否触发重新打开条件；未命中的新发现先按 `REVIEW_REQUIRED` 诊断和报告，取得用户对是否修复及修复方式的明确决定前不得实施。
2. 在任务说明中写清范围、不做什么、验收场景、数据影响、迁移风险、旧版本兼容和回滚边界；未确定的产品语义先确认，不能由实现者自行补写规则。
3. 从最新 `origin/main` 建短生命周期分支：

   - `feat/<topic>`：新功能。
   - `fix/<topic>`：缺陷修复。
   - `docs/<topic>`：纯文档。
   - `hotfix/<topic>`：已上线故障的紧急修复。

4. 分支开始前获取远端最新状态；共享脏工作树不得 `reset`、`clean` 或覆盖式恢复。旧小程序与 `ScoresheetReader` 只读，禁止直接修改。
5. 不把 `.env`、AppSecret、OpenID、真实名单、生产数据库、备份、照片、令牌或私钥复制到分支、日志、截图和 PR。

## 2. 实现与兼容

- Django/PostgreSQL 是权威。权限、状态、版本、容量、比分、审计和并发冲突必须在服务端复核。
- 变更领域协议时同步检查：模型、迁移、服务、API、OpenAPI、生成客户端、管理站、小程序、测试和用户文档。
- 数据库和协议采用 expand / contract：先加入新字段与兼容读写，确认旧应用退出并完成回填后，再在独立改动中收紧约束或删除旧协议。nullable schema 只代表数据格式兼容，不代表旧应用理解新业务语义；首次激活新能力前必须用 bridge/capability 门禁验证两个可回切版本。
- 历史记录必须持续可读、可解释；明确废止的旧协议应在一次受控清理中删除入口、实现、夹具、测试和说明。
- 真实数据、破坏性迁移、并发和恢复演练使用隔离数据库或成套恢复副本；未经授权不修改生产或共享 QA 数据。
- 小步提交，一个提交只表达一个可回滚意图；提交信息说明结果，不使用“临时”“试试”等模糊描述。

## 3. 开发者验证

先运行受影响范围的聚焦测试并修复根因，再运行完整门槛：

1. PostgreSQL 后端测试、Ruff、迁移漂移检查。
2. OpenAPI 导出与生成客户端同步检查。
3. TypeScript 类型检查、领域与前端全量单测。
4. 管理站生产构建和小程序完整 `build:weapp`。
5. 涉及数据库约束、并发、迁移或恢复时做真实 PostgreSQL 动态验证。
6. 涉及页面或交互时做真实浏览器和微信开发者工具检查；构建成功不能替代动态验收。

小程序源码每次修改至少运行：

```powershell
npm --workspace @pkuba/miniapp run typecheck
npm --workspace @pkuba/miniapp run test
npm --workspace @pkuba/miniapp run build:weapp
```

随后在微信开发者工具中打开 `apps/miniapp`，检查本轮页面的文字、间距、滚动、图片、按钮状态和底栏。无法连接或截图时必须明确记录为待人工验收。

## 4. Pull Request

所有功能、修复、迁移和发布准备都通过 PR 进入 `main`；普通功能不得借 hotfix 或管理员权限绕过 PR。PR 使用 [`.github/pull_request_template.md`](.github/pull_request_template.md)，至少写明：

- 目的、范围和主要影响文件。
- 用户可见变化和明确未改内容。
- 数据迁移、旧版本兼容、回滚方式和隐私风险。
- 聚焦及全量测试证据；涉及 UI 时附截图，或说明视觉验收阻塞。
- OpenAPI、生成客户端、迁移和文档是否同步。
- 敏感信息与真实数据检查结果。

当前仓库没有可用的非作者审核人，GitHub ruleset 的 required approval 为 0，也不要求 CODEOWNER review；不得把不存在的审批写成门禁。所有 review conversation 仍必须解决。产品代码以绑定精确 PR head SHA 的独立测试结论 `ACCEPTED_FOR_MERGE` 作为人工门槛；未来有正式审核人后，再同步提高 ruleset 和本文要求。

推荐使用 **squash merge**：受保护 `main` 保持线性、每个 PR 对应一个可回滚提交；必要的中间提交仍保留在 PR 历史中。合并前分支必须基于最新 `main`，required checks 以严格、最新提交为准全部通过。独立测试优先在 PR head SHA 上完成并把报告绑定该 SHA，给出“允许合并”后方可合并。用户明确批准的直接提交例外仍须执行精确 allowlist、完整门槛、远端 SHA 和 CI 闭环，并记录未经过 PR 的授权来源；该例外不自动构成发布 GO。

## 5. GitHub 仓库门禁

生产发布来源仓库必须持续满足以下设置。每次候选发布前都要通过 GitHub API 或设置页核对实际状态；无法核对或任一项缺失时，停止发布并报告门禁缺口：

- `main` 只允许 PR 合并，禁止直接推送。
- required approval 当前为 0；以独立测试对精确 PR head SHA 的允许合并结论补足人工门槛。
- 要求所有 review conversations resolved。
- required checks 至少包含 `backend`、`frontend`、`openapi`。
- required checks 使用 strict / up-to-date 模式，PR 必须包含最新 `main` 后重新通过检查；需要 merge queue 时同时要求 `merge_group` 上的同名检查。
- 禁止 force-push 和删除 `main`。
- 限制 bypass；紧急 break-glass 只能由授权负责人使用，并必须补 PR、事故记录和事后审计。
- 启用线性历史，只允许 squash merge；合并后自动删除短期分支。
- 暂不要求 `CODEOWNERS`；有明确负责人后再配置，不能使用虚构账号。
- 启用 GitHub private vulnerability reporting、secret scanning、push protection、Dependabot、
  dependency graph、dependency review 与 CodeQL；工作流文件存在不等于平台功能已启用。
- 关闭、驳回或修改 GitHub 安全告警是独立外部变更，必须取得当前用户对精确告警的明确授权；代码、测试或 allowlist 通过不自动授予该权限。
- 为 `v*` 配置 tag ruleset：仅授权发布负责人可创建或删除，且 tag 必须指向受保护 `main` 上已经绑定独立验收结论的 SHA。当前 `production` Environment 只有 `v*` 分支策略，没有 required reviewer；当 `PRODUCTION_DEPLOYMENTS_ENABLED=true` 时，推送 Tag 会立即自动部署，因此创建并推送 Tag 本身就是生产部署授权。

## 6. 合并、候选版本与上线

1. 独立测试在 PR head SHA 上完成并给出 `ACCEPTED_FOR_MERGE <SHA>`；PR CI 对该 SHA 运行完整门槛。产品 PR 只链接结论，不复制整份报告。
2. squash merge 后比较 `main` 与已验收 PR head 的 tree；相同则只等待 `main` CI 并按风险做最小烟测，不机械重复完整测试。tree 不同则重新验收差异。
3. 用户或授权发布负责人确认版本号和 `main` SHA 后创建 annotated `vX.Y.Z` 标签。当前 Tag 推送会自动开始生产部署，没有后续 Environment 人工审批步骤。
4. 发布工作流按同一 manifest 生成并校验数据库、媒体和归档清单的一致恢复点，再执行兼容迁移、readiness、隔离或蓝绿候选烟测。
5. 候选验收通过后由 Caddy 切流；多文件状态提交必须由持久事务日志和启动恢复保护，不能只依赖进程内 trap。旧应用栈默认保留 2 小时，实际期限以权威 retained state 为准。普通应用故障只允许切回发布时已验证、且保留状态明确授权从当前 capability 回切的应用，不恢复数据；仅有 nullable schema、但不理解新业务语义的旧栈不得作为回切点。
6. 仅在确认数据库、媒体或归档数据损坏时，才按同一 manifest 成对恢复三类数据；禁止只恢复其中一部分。
7. 完成真实蓝绿、HTTPS、公开接口、管理站和关键写流程烟测，再由授权人员在微信平台人工上传、审核和发布，并完成真机登录、调赛、记录表和媒体冒烟检查；这些生产与真机证据齐全后，才能形成最终发布验收结论。
8. 最终验收后生成发布说明，记录 commit、镜像 digest、迁移、测试、切流、真机结果和回滚点。

具体自动部署、蓝绿拓扑、readiness、备份和恢复命令以 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) 为准。除非当前任务明确授权精确环境与动作，开发、评审、测试和文档任务不得连接或切换生产环境。

## 7. Hotfix、回滚与事故

- 生产事故先记录影响、时间线和当前数据状态；未经判断不得同时修改应用和恢复数据。
- `hotfix/<topic>` 从当前生产对应提交建立，只包含最小修复，仍需 PR、CI 和绑定精确 SHA 的独立测试结论；确需 break-glass 时先保服务，随后立即补齐 PR 和审计。
- 应用缺陷优先切回仍在保留窗内且 capability contract 明确兼容的旧栈；没有兼容回切点时保持维护并发布 bridge/hotfix。只有确证数据损坏时才执行 DB + media + archive 成套恢复。
- 恢复后重新运行 readiness、业务计数、媒体哈希和关键流程烟测，并在发布说明/事故记录中写明原因、执行者、证据和后续预防项。
- 事故修复必须再合并回 `main`，不能让生产形成永久旁支。

## 8. 接手与交接细则

本文只定义评审、批准、发布和事故责任。文档职责、工作区接手、影响矩阵、候选冻结、独立
验收、精确暂存及远端核对统一见
[`docs/MAINTAINER_GUIDE.md`](docs/MAINTAINER_GUIDE.md)，不在此重复。
