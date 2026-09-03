# PKUBA 维护者与 Agent 接手指南

本文把 PKUBA 的接手、实现、验证、候选冻结、提交和远端闭环整理为可重复执行的维护
流程。仓库级不变量以 [`AGENTS.md`](../AGENTS.md) 为最高约束，团队评审与发布流程以
[`WORKFLOW.md`](../WORKFLOW.md) 为准；本文提供具体的执行顺序和交接格式。

## 1. 接手后的前十分钟

不要先修改文件。先在仓库根目录完成只读核对：

```powershell
Get-Content AGENTS.md
git status --short
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git log -1 --format='%H%n%P%n%s'
```

随后完成四件事：

1. 将任务中的最新用户决定写成范围、非目标、验收标准和授权边界。
2. 识别工作区中哪些改动属于本任务、哪些属于用户或其他任务；无关改动一律保留。
3. 找到受影响领域的权威文档和真实调用链，不能只依据任务标题、旧总结或构建结果。
4. 记录 base SHA、功能分支、任务范围、非目标和运行环境身份；候选文件范围由 Git diff 生成。

`docs/INDEPENDENT_TEST_PLAN_AND_RESULTS.md` 只由独立测试任务维护。普通开发、文档和
提交任务不得为了“了解现状”读取后顺手修改、暂存或提交它；需要独立结论时应由测试
任务提供绑定 SHA 的交接。

获取远端状态会更新 Git 元数据，应在当前任务允许仓库协作操作时执行：

```powershell
git fetch --no-tags origin main
git rev-parse HEAD
git rev-parse origin/main
git rev-parse FETCH_HEAD
```

如果 `HEAD`、`origin/main` 或工作区在任务期间发生非预期变化，停止写入并重新冻结。
共享工作区禁止使用 `reset --hard`、`clean`、覆盖式 restore 或批量删除来“恢复干净”。

## 2. 文档与事实的唯一来源

| 内容 | 权威位置 |
| --- | --- |
| 仓库级业务不变量与 Agent 禁令 | `AGENTS.md` |
| 公开项目定位、能力、入口与 License | `README.md` |
| 接手、验证、评审、候选、提交与发布流程 | 本文与 `WORKFLOW.md` |
| 架构决定、里程碑和日期化实施记录 | `Plan.md` |
| 用户可见行为、权限、期限与错误规则 | `docs/USER_GUIDE.md` |
| 调赛、赛程、记录表等领域协议 | 对应的 `docs/*.md` 专题规范 |
| API 幂等、分页、超时与客户端合同 | `docs/API_RELIABILITY.md` |
| 本地环境、微信工具和检查命令 | `docs/DEVELOPMENT.md` |
| 生产部署、回滚和恢复 | `docs/DEPLOYMENT.md`、`docs/BACKUP_AND_ARCHIVE.md` |
| 独立测试计划、证据和结论 | `docs/INDEPENDENT_TEST_PLAN_AND_RESULTS.md` |

日期化计划和历史验收数字只代表记录时点。判断当前状态时，应同时核对最新用户决定、
当前 SHA/tree、实际代码、运行制品身份、动态测试和该 SHA 的 CI，不能把历史“通过”或
“阻断”直接套用到新候选。

同一可变规则只写在一个权威位置，其他文档使用链接。公开 README 不保存 QA 账号、
本地卷名、临时路径、测试数据规模、候选 SHA 或发布阻断状态。

## 3. 变更影响矩阵

| 变更类型 | 必须同步核对 |
| --- | --- |
| 业务规则或权限 | 服务端模型/服务/API、数据库约束、双端显示与提交意图、测试、`USER_GUIDE.md` 和领域规范 |
| API 请求或响应 | Ninja schema、服务、OpenAPI 精确导出、生成客户端、管理后台、小程序、4xx/409 合同与测试 |
| 数据库模型或枚举 | expand/contract 迁移、既有数据审计、PostgreSQL 约束与并发、兼容/回切能力、备份恢复 |
| 管理后台页面 | 权限、加载/错误/空状态、窄屏与滚动、真实浏览器、类型检查、组件测试和生产构建 |
| 微信小程序页面 | 身份恢复、超时与迟到响应隔离、真实微信开发者工具、类型检查、测试、完整 `build:weapp` |
| 记录表 | 来源与名单快照、租约/版本、人工编辑、校验、publication、统计、网页与小程序相邻流程 |
| 调赛 | 日期关系、处理通道、审核认定、匿名预留、容量/冲突、幂等、任务箱和邮件 outbox |
| 媒体、归档或恢复 | 私有访问、原子文件操作、DB/媒体/归档同一恢复点、逐文件哈希、故障注入和隔离恢复 |
| 静态资源或品牌 | 唯一源文件、打包副本、`npm run assets:sync`、`npm run assets:check` 和双端显示 |
| 纯文档 | 事实来源、相对链接、GitHub 渲染、敏感信息、阶段性措辞和文档职责边界 |

模型、迁移、OpenAPI、生成客户端、页面、文档和测试是一个合同面。只更新其中一层不能
视为完成。

## 4. 实现与动态验证

先审计真实调用链，再做最小实现：

1. 从用户动作或 API 入口追到服务、事务、模型约束、审计和派生结果。
2. 同时追踪管理后台、小程序、生成客户端和权威文档的消费者。
3. 先写或运行能够复现问题的聚焦测试，再修复根因。
4. 运行相邻正常流程，确认修复没有改变未授权行为。
5. 最后运行完整门槛，并记录精确命令、退出码和测试数量。

标准本地门槛：

```powershell
./scripts/check.ps1
```

正式 WSL 验收基线：

```powershell
./scripts/check-wsl.ps1
```

完整门槛包含 PostgreSQL 后端测试、Ruff、迁移漂移、OpenAPI 与生成客户端同步、
TypeScript、领域及前端测试、管理后台生产构建和完整微信构建。涉及真实交互时，还要
分别使用浏览器和微信开发者工具；构建、组件测试或自动化确认弹窗不能冒充真机手势。

运行时验证必须绑定源码与制品身份。至少记录源码 commit、是否 dirty、容器或页面返回
的 tag/commit，以及测试数据库和媒体是否为隔离资源。依赖 readiness 只证明服务可用，
不能替代实际业务数据验收。

## 5. 候选冻结与独立验收

实现者在功能分支正常创建候选 commit 并推送 PR。只有工作区干净且 PR head 已固定时才交给
独立测试；Git commit/tree 本身就是内容寻址身份，不再手工计算逐文件大小、SHA-256 或
聚合指纹。交接包至少包含：

```text
base: <40 位 SHA>
pr_head: <40 位候选 commit SHA>
tree: <git rev-parse <pr_head>^{tree}>
scope: <本批解决的问题>
non-goals: <明确未改内容>
changed_paths: <git diff --name-status <base>...<pr_head>>
tests:
  - <命令> | <退出码> | <关键数量/结果>
runtime_identity: <容器、Web、小程序制品的 tag/commit>
known_boundaries: <未验证事项与原因>
```

开发者先运行受影响的聚焦和相邻测试；完整静态门槛与全量套件由 PR CI 对同一 SHA 运行
一次。独立测试必须审查全部差异和调用链，并按风险重放聚焦、迁移、并发或必要动态场景；
CI 已对同一 SHA 成功时，不再机械复制整套全量测试。发现问题时返回精确复现包。实现者
修正并新增 commit 后，旧接受结论失效，但只重跑受影响范围和该新 SHA 的 CI。

候选通过后，独立测试发送 `ACCEPTED_FOR_MERGE <PR_HEAD_SHA>`。这批准的是精确 PR head，
不是后续提交。纯文档等经用户明确授权的低风险任务可以不经过独立验收，但仍须核对差异、
链接、敏感信息和 CI。

## 6. 精确暂存、提交与推送

提交和推送只在用户明确要求时执行。功能分支可在独立验收前正常提交；每次提交仍只暂存
当前任务范围：

```powershell
git add -- <task-path-1> <task-path-2>
git diff --cached --name-only
git diff --cached --stat
git diff --cached --check
```

提交前核对：

- staged 路径与任务范围一致；
- 没有 `.env`、凭据、OpenID、真实名单、私有媒体、备份、临时证据或调试输出；
- 没有生成文件漂移、未登记迁移、意外删除或未跟踪文件夹；
- 独立测试报告和其他任务改动不在 staged diff 中。

提交应表达一个可回滚意图。推送 PR 后，以 PR head commit/tree 冻结候选；合并前 required
checks 和 `ACCEPTED_FOR_MERGE` 必须绑定同一 SHA。推荐 squash merge。

合并后核对：

```powershell
git rev-parse HEAD
git rev-parse origin/main
git ls-remote origin refs/heads/main
```

同时使用 GitHub API/CLI 核对 `main` SHA 和 tree。若 squash merge 后的 tree 与已验收 PR
head tree 完全相同，只等待 `main` SHA 的 `backend`、`frontend`、`openapi` required checks，
不重复完整独立验收；tree 不同则重新审查和测试差异。

## 7. 生产操作授权边界

发布级开发是默认质量标准，但下列动作永远需要当前任务的精确授权：

- 创建、移动或删除发布标签与 GitHub Release；
- 修改 branch/tag ruleset、Environment、Secrets 或部署开关；
- 连接生产 SSH、执行迁移、切换 Caddy/域名或启停生产 worker；
- 发送真实邮件、调用付费模型、上传微信正式版本；
- 删除、覆盖、迁移或恢复生产/共享 QA 的数据库、媒体、归档和备份。

执行前必须解析精确目标、记录前置状态与恢复点；执行后核对远端引用、运行版本、数据
完整性、健康接口、业务烟测和审计。普通应用回滚不恢复数据；只有确证数据损坏时才按
同一 manifest 成对恢复数据库、媒体和归档。

## 8. 交付前清单

- [ ] 最新用户决定、范围、非目标和授权边界已记录。
- [ ] base、HEAD、origin、工作区和运行制品身份已核对。
- [ ] 无关改动与独立测试报告已保留并排除。
- [ ] 代码、数据库、OpenAPI、客户端、测试和权威文档已按影响矩阵同步。
- [ ] 聚焦、相邻、完整门槛和必要动态验收均有实际证据。
- [ ] 候选 PR head commit/tree、base 和 Git diff 路径已记录。
- [ ] 需要独立验收时，测试任务已对同一 PR head 给出 `ACCEPTED_FOR_MERGE`。
- [ ] staged diff、敏感信息、生成文件、迁移和未跟踪范围已复核。
- [ ] 推送后本地、远端、GitHub SHA、最终 tree 和 CI 已闭环。
- [ ] 未执行任何超出当前授权的生产、付费、删除或发布动作。
