# PKUBA 现有微信小程序审计与重写 V1 功能逻辑规格

## 0. 文档定位

本文同时承担两项职责：一是记录旧项目 `C:\Users\jiumao\Desktop\北大篮协小程序` 截至 2026-08-18 的**现状审计**；二是记录已经确认、供新系统实现和验收的 **V1 重写规格**。旧系统事实不会因重写决定而被改写；当两者冲突时，以本文 A 章、6.6、9 章和 11 章中标注的重写规则为准。

审计基线：

- Git 分支：`master`
- HEAD：`a165509470007cc37cc37a5f5fb6706da0941b08`（短哈希 `a165509`）
- 工作区原有未提交内容：`downloadPhotos.m` 已修改、`~$schedule.xlsx` 未跟踪；审计全程未改动它们。
- 共核对 29 个已注册页面、38 个云函数、全局文件、项目配置、根目录数据与 MATLAB 脚本、当前数据库备份、历年备份、3 份 DOCX 说明和赛程 Excel 布局。
- 页面四件套共 116 个文件、7,806 行；`miniprogram/` 内文本文件共 121 个、8,303 行。
- 38 个云函数入口 `index.js` 共 2,153 行；云函数第一方文件（不含 `node_modules`）共 117 个、4,471 行。
- 5 个根目录 MATLAB/Live Script 共约 751 行等价源码。
- 68 个第一方 JavaScript 文件已做语法检查，0 个失败；所有 WXML 中的 `bindtap`、`bindchange`、`bindinput` 等事件均能在对应页面 JS 中找到处理函数。
- 2026-08-18 已针对调赛、容量、场地占用、锁定与释放路径完成第二轮专项复核，并把确认后的产品决定写入本文。
- 规则参考另包括 `C:\ShareCache (2)\黄越_2201110520\篮协\章程\2025年北大杯男子篮球甲级联赛参赛手册.docx`；已只读提取其全部 368 个段落和 9 张表格进行结构化核对。

边界说明：

1. `cloudfunctions/send_email/node_modules/` 是第三方依赖快照，记录版本与用途，不将其每一行视为本项目业务逻辑。
2. 旧 DOCX 因本机缺少 LibreOffice，无法进行页面渲染；已只读提取全部段落并与代码、数据库备份交叉核对。
3. 本文不会抄录管理员密码、SMTP 密码、OpenID 或其他秘密。旧代码中出现的秘密不应迁移到新前端或新仓库。
4. 文中“当前行为”只描述旧系统；“重写规则”是新系统必须实现的规范性要求。不能把旧缺陷当成兼容目标，也不能把重写规则倒写成旧系统已经具备的能力。
5. 参赛手册是规则参考，不是本规格的上位覆盖文件；与已确认产品决定冲突时，以本规格为准。已确认的主要差异见 11.2。

---

## A. 已确认的重写 V1 规格

本章是规范性要求。后文的旧页面、云函数和数据库说明用于解释来源与迁移风险，不能覆盖本章。

### A.1 范围与规则来源

- V1 迁移并服务**当前赛季**；数据仍保存 `seasonId`，为未来多赛季留出扩展位置，但不提供历史赛季查询或批量导入界面。
- 抽签与特殊原因调赛只在小程序中保留文字说明。申请、证明材料、实际抽签和个案协商均通过邮件线下进行；V1 不上传附件、不在系统内执行抽签，也不建立对应审批状态机。超级管理员只负责把最终结果录入系统。
- 不迁移 `schema_viewer`、旧 `apply_cross_round` 和通用 `referee_schedule` 页面。V1 也不实现裁判注册、登录、个人赛程或管理员排班，仅在数据模型中保留未来扩展位置。
- 旧代码、当前赛季数据、参赛手册和本次产品确认之间发生冲突时，执行顺序为：**本章确定规则 > 数据迁移专项判定 > 旧系统现状审计 > 参赛手册参考**。

### A.2 领队调赛资格与截止时间

每场比赛从当季赛程导入一个永久政策字段 `leaderAdjustable`。它只表达该场比赛是否允许领队发起调赛；运行时不得根据“决赛”“末轮”等描述文字重新推断或改写该字段。

领队发起申请时必须同时满足：

1. 调用者经服务端认证为比赛一方的领队。
2. `leaderAdjustable=true`。
3. 比赛尚未开始。
4. 比赛没有活动中的调赛申请。
5. 当前时间没有超过提交截止时间。
6. 客户端所见比赛版本与服务端当前版本一致。

设原比赛日期与目标比赛日期中较早的日历日为 `D`，所有日期均按 `Asia/Shanghai` 的本地日历日计算：

- 提交申请截止：`D` 提前三日的 24:00。
- 对手以及管理员指定参与投票的球队确认截止：`D` 提前两日的 24:00。
- 不再用“当前时间加 72 小时”、day-of-year 或客户端本地时钟近似代替上述日历规则。

### A.3 容量、场地与提交事务

容量属于不可在赛季中修改的 `SeasonMeta`。普通管理员不能修改；超级管理员的单次例外也不得改写容量元信息。V1 默认容量为：

| 日期类型 | 标准可用时段 | 每时段容量 |
|---|---|---:|
| 工作日 | period 1、period 6 | 1 |
| 周末 | period 1、period 2、period 3 | 3 |
| 周末 | period 4、period 5 | 2 |

未列出的日期类型/时段组合在标准申请通道中的容量为 0。周末容量为 2 时，可使用赛季配置的三个场地中的任意两个；系统按 `SeasonMeta` 的有序场地列表选择第一个可用场地。

创建申请必须在**一个服务端事务**中完成：

1. 重新读取调用者、比赛、赛季元信息和比赛版本，复核领队关系、永久政策、截止时间、比赛未开始且没有活动申请。
2. 统计目标时段的正式比赛与有效预留，检查固定容量。
3. 同时检查场地唯一性，并按赛季场地顺序选择第一个可用场地。
4. 创建 `RescheduleRequest`。
5. 把原比赛的 `activeRescheduleRequestId` 指向新申请并递增比赛版本。
6. 创建占用具体 date/period/venue 的有效 `SlotReservation`。

任一步失败都必须整体回滚。申请提交后立即占用一场容量和具体场地；不能等到最终批准才检查，也不能只在客户端展示“看起来可用”的地点。

### A.4 调赛状态机与锁生命周期

V1 状态至少包括：等待对手、等待管理员决定、等待指定球队、等待管理员终审、通过、拒绝、撤回、过期、管理员取消。所有迁移都由服务端按 request ID 重新读取当前数据并验证调用者权限、当前状态、截止时间和版本；客户端上传的整份 Request 不能作为权威数据。

普通调赛流程：

```text
等待对手
├─ 对手按时同意 → 事务复核并自动生效 → 通过
├─ 对手拒绝 → 拒绝
├─ 申请方撤回 → 撤回
└─ 对手确认超时 → 过期
```

跨周以**周一至周日**为一周边界：

```text
等待对手
└─ 对手按时同意 → 等待管理员决定
   ├─ 管理员直接批准 → 通过
   ├─ 管理员拒绝 → 拒绝
   └─ 管理员指定球队投票 → 等待指定球队
      ├─ 任一要求确认的球队拒绝/超时 → 拒绝/过期
      └─ 所有要求确认的球队按时同意 → 等待管理员终审
         ├─ 管理员批准 → 通过
         └─ 管理员拒绝/取消 → 拒绝/管理员取消
```

管理员可以自行决定直接审核还是指定球队投票，不要求固定为全部同组球队。球队按时完成确认后，申请可无限期停留在“等待管理员终审”，不会因为管理员尚未处理而自动过期。

申请方可在申请生效前撤回任何非终态申请；管理员也可显式取消非终态申请。两种操作都必须进入各自终态并执行同一套幂等清锁/释放事务，不能直接删除 Request。

原比赛的活动锁与目标 `SlotReservation` 在等待对手、等待管理员决定、等待指定球队和等待管理员终审期间必须持续存在。只有终态可以改变二者：

- 通过：在同一事务中把预留转换为正式赛程占用、更新比赛时间/时段/场地、清除 `activeRescheduleRequestId`；永久的 `leaderAdjustable` 保持导入值，因此仍符合条件时可以再次申请。
- 拒绝、撤回、球队确认超时或管理员取消：在同一事务中清除活动锁并释放目标容量/场地预留。
- 自动过期、终态处理、清锁和释放预留都必须幂等；重复任务或网络重试不得解锁另一份申请、重复释放或把永久政策改为 true。

### A.5 超级管理员例外通道

只有 `Manager.type=0` 的超级管理员可以使用例外通道。它可以绕过领队调赛政策、截止时间、周次/轮次、固定容量、对手确认和投票，直接新增或修改比赛，但必须满足：

- 操作前二次确认；不强制填写理由。
- 自动记录操作者、时间、对象、修改前后完整快照、被取消的申请及被释放的预留。
- 编辑存在活动申请的比赛时，确认后在同一事务中先把该申请设为“管理员取消”、释放目标预留，再执行比赛修改。
- 超容量是单次例外，不改变 `SeasonMeta`。例外不得静默取消无关申请、抢占其场地或制造未明确处理的场地冲突；若有冲突，必须让管理员显式选择处理对象并留痕。

### A.6 其他 V1 业务决定

- 正式比分不允许平局；相等比分必须拒绝保存。弃权固定记 20:0，胜方积分 2、弃权方积分 0。
- 组内同分顺序沿用旧规则：相互积分、相互净胜分、相互总得分、总净胜分、总得分。不实现跨组排名或扣除末名球队的计算。
- 淘汰赛首轮由管理员录入；后续轮次在非平局正式比分产生后自动晋级胜者。超级管理员可纠错并明确重置受影响的下游对阵。
- 技术统计照片按稳定 `gameId` 隔离。只有已登录领队和管理员可查看；管理员可上传、删除、替换和排序，所有变更留痕。

---

## 1. 系统概览

### 1.1 技术结构

旧系统是原生微信小程序：

- 前端：`miniprogram/`，WXML + WXSS + JavaScript。
- 后端：38 个微信云函数，使用 `wx-server-sdk` 访问云数据库和云存储。
- 数据库：以 `Schedule`、`Team`、`Request`、`Leader`、`Manager`、`Referee`、`Photo`、`Private` 为核心；并存在并发控制集合 `ScheduleSlotLock`。
- 赛季数据生产：维护者在 Excel 中编辑赛程，由 MATLAB 脚本生成导入文本，再通过云开发工具导入或人工维护。
- 消息通知：部分申请流程通过 `send_email` 云函数发送邮件。

`app.json` 注册 29 个页面，窗口标题为 `PKUBA`，样式版本为 `v2`，启用了 WeUI/kbone 扩展库；所有页面 `usingComponents` 均为空，没有自定义组件体系。

### 1.2 角色

| 角色 | 身份来源 | 主要能力 | 当前权限实质 |
|---|---|---|---|
| 普通访客 | 无登录 | 看赛程、积分表、淘汰赛 | 前端公开入口 |
| 领队 | `Leader.openID == 当前 OPENID` | 查看本队赛程、发起调赛、查看/处理申请、申请抽签 | 主要由页面入口限制；云函数缺少完整的服务端角色校验 |
| 裁判 | `Referee.openID == 当前 OPENID` | 查看按姓名匹配到的执裁安排 | 只读页面，姓名是匹配关键字 |
| 普通管理员 | `Manager.type == 1` | 查看/处理调赛申请、上传技术统计照片 | 前端按钮限制 |
| 超级管理员 | `Manager.type == 0` | 普通管理员能力 + 编辑比赛 + 分配裁判 | 前端按钮限制 |

关键安全事实：许多写云函数相信客户端传入的姓名、队伍、比赛字段，没有重新按 OPENID 验证调用者身份。因此，“按钮不可见”目前不等于后端权限不可绕过。新系统若要一模一样复现业务功能，可以保持角色能力，但不应复现这种安全缺口。

### 1.3 可见导航图

```text
首页 index
├─ 查询赛程 → schedule
├─ 查询积分 → scoretable
├─ 淘汰赛 → knockout
└─ 登录 → login_select
   ├─ 领队登录 → leader_register / leader_home
   │  ├─ 查看申请 → view_apply → view_apply_details
   │  ├─ 申请调整 → apply
   │  └─ 申请抽签 → drawing_description   （止于说明页）
   ├─ 管理员登录 → manager_login / manager_home
   │  ├─ 更新比赛信息（仅 type=0）→ schedule_edit → manager_edit
   │  ├─ 查看/上传技术统计 → schedule_scoresheet → edit_scoresheet
   │  ├─ 裁判分配（仅 type=0）→ schedule_referee → edit_referee
   │  └─ 查看赛程调整 → manager_view_apply → manager_view_apply_details
   └─ 裁判登录 → referee_register / referee_home

已注册但从当前可见 UI 不可达：
apply_cross_round、drawing、referee_schedule、schema_viewer
```

`error_page` 和 `success_page` 是流程反馈页，不是首页入口。

---

## 2. 全局初始化、配置与时间模型

### 2.1 `miniprogram/app.js`

启动过程：

1. 调用 `wx.cloud.init({ env: wx.cloud.DYNAMIC_CURRENT_ENV, traceUser: true })`。
2. 调用云函数 `get_private`，传入 `needed: 'META'`。
3. 将返回的整个 META 文档直接赋给 `app.globalData`。
4. 如果其他页面已经通过 `waitForGlobalData` 排队等待，则依次解析等待回调。

只有淘汰赛页明确等待全局数据。多数页面直接访问 `app.globalData.GROUP_NAMES` 等字段，理论上存在页面启动快于 META 返回的初始化竞态。

全局时间函数：

- `date_to_time(dayOfYear, hour, minute)`：用**当前年份**的 1 月 1 日加 day-of-year 和时分构造日期。
- `period_to_time(period)`：查 META 中的 `PERIOD_TO_TIME[period]`。
- `get_date_period(time)`：把日期转换为 day-of-year，并按以下硬编码时段判断 period：

| 条件 | period |
|---|---:|
| 时间不晚于 13:20 | 1 |
| 13:50–14:50 | 2 |
| 15:20–16:20 | 3 |
| 17:50–18:50 | 4 |
| 19:20–20:20 | 5 |
| 不早于 20:30 | 6 |
| 其余间隙 | 0 |

小时通过 `getUTCHours() + 8` 获得，分钟使用本地 `getMinutes()`。这依赖服务器/客户端时区环境，且 period 1 会把所有更早时间也归为 1。

### 2.2 当前 META

当前数据库备份中的核心配置：

| 字段 | 当前值/含义 |
|---|---|
| `GAME_NAME` | 北大杯 |
| `GAME_START_DATE` / `GAME_END_DATE` | 80 / 118（当年第几天） |
| `GROUP_NAMES` | 男甲、男乙、女甲、女乙 |
| `SEXES` | 男甲/男乙为 true，女甲/女乙为 false |
| `LITTLEGROUPS` | 男甲 A/B；男乙 A/B/C/D/E；女甲 A；女乙 A/B/C |
| `PLACE_NAMES` | 五四东一、五四东二、五四东三 |
| `ROUND_START_DAY` / `ROUND_END_DAY` | 1 / 7，即按周一至周日计算同轮范围 |
| `MANAGER_TYPES` | 超级管理员、普通管理员 |

`PERIOD_TO_TIME` 的索引 0–10 为：12:10、12:50、14:20、15:50、18:20、19:50、20:40、12:00、13:20、14:40、18:10。当前主要容量逻辑使用 period 1–6。

容量 `MAX_GAMES_NUM` 与云函数硬编码规则一致：

- 周一至周五：period 1 可排 1 场，period 6 可排 1 场，其他为 0。
- 周六、周日：period 1/2/3 各 3 场，period 4/5 各 2 场，period 6 为 0。

---

## 3. 逐页面功能规格

以下 29 节与 `app.json` 注册页面一一对应。

### 3.1 `pages/index`

首页显示 Logo 和四个按钮：查询赛程、查询积分、淘汰赛、登录。跳转时积分页默认 `group=0&littlegroup=0`，淘汰赛默认 `group=0`。

JS 还保留 `login_manager`、`login_leader`、`login_referee` 三套重复登录方法，但 WXML 没有绑定，实际登录统一进入 `login_select`。

### 3.2 `pages/login_select`

只负责角色分流：领队、管理员、裁判。没有身份逻辑。

### 3.3 `pages/schedule`

公开赛程页调用 `search_future_schedule` 获取全部 `Schedule`，把云数据库时间解析为本地 `Date`，按时间升序排列。

展示规则：

- 以本地当天 00:00 为界，今天及以后归入“未来比赛”，之前归入“过去比赛”。因此今天已经结束的比赛仍在未来区。
- 相邻比赛按“月 + 日”分组；标题不显示年份。
- `home_team_score >= 0` 时显示比分，否则显示 `VS`。
- 男女组使用不同颜色；显示组别、描述、地点。
- 加载后 1 秒滚动到 `Today` 分隔位置。
- 查询失败没有显式 fail 提示。
- 分享文案仍写“2024年北大杯”，属于硬编码过期文案。

### 3.4 `pages/scoretable`

提供大组和小组选项，选择后调用 `make_table`，展示交叉积分矩阵与排名摘要。

后端矩阵逻辑：

1. 查询所选 `group + littlegroup` 的 `Team` 和 `Schedule`。
2. 假设组内 `Team.id` 是从 1 开始连续编号，用 `id - 1` 作为矩阵下标。当前备份满足这一假设。
3. 未赛比赛以 `score == -1` 判断并忽略。
4. 已赛比赛把双方得分、积分写入交叉矩阵，并累计总积分、净胜分、总得分。

前端递归排名 `rerank`：

1. 先按总积分降序。
2. 同分球队组成小联赛，依次比较：相互比赛积分、相互比赛净胜分、相互比赛总得分、全赛程净胜分、全赛程总得分。
3. 若所有球队在最高层全部同分，代码在一次全局排序后直接返回。
4. 总积分为 0 的球队统一显示在“非零积分队数 + 1”名。

代码还计算 `cross_group_point` 和 `cross_group_score`：当组内超过 4 队时，从每队总数据中扣除对阵最后一名的成绩；当前 UI 不展示，也不用于最终排序。

后端会在返回前把部分联合队名缩写，如光华-经济、工学-材料等；缩写发生在统计完成之后。

### 3.5 `pages/knockout`

按组别展示固定结构的淘汰赛对阵图，支持 8 队、4 队或 2 队布局。

当前硬编码种子：

- 男甲：4 个空字符串。
- 女甲：2 个空字符串。
- 男乙：光华、计算机、环科、法学、新传、地集、国关、电子。
- 女乙：城环、国关、生历、中文、工材、光经、心理、地空。

`get_knockout` 只按**完全相同的主队、客队顺序**，并限定描述为淘汰赛/半决赛/决赛进行查询；不会尝试主客倒置。分数为 -1 或队名为空时显示占位。只有合法、非平分结果才会推进胜者，平局或未赛不产生晋级者。

风险：硬编码缩写与 `Team`/常规赛中的完整队名未必相同；男甲/女甲为空种子也依赖后续人工数据形态。此页是“固定模板 + 精确字符串查询”，不是从小组排名自动生成对阵。

### 3.6 `pages/leader_register`

页面先按当前 OPENID 调 `search_leader`：已有记录直接进入领队首页；没有则显示注册表单。

注册字段：姓名、组别、队伍。队伍随组别从 META/队伍表切换。

`check_leader` 拒绝：

- 姓名、队伍或组别为空；
- 已存在相同 `{team, sex}` 的领队；
- 已存在当前 OPENID 的领队。

注意：云函数接收 `group`，但队伍唯一性查询没有使用 group。`leader_register` 自身只执行插入，没有事务级唯一约束，所以并发注册仍可能重复。

### 3.7 `pages/leader_home`

显示领队姓名、球队和组别，并调用 `search_future_games` 获取本队未来比赛。`for_request=false` 时不要求比赛可调整，也不要求距离现在超过三天。比赛按时间排序。

可见按钮：查看申请、申请调整、申请抽签。JS 中另有 `apply_cross_round()`，但没有对应按钮。

页面 `onShow` 再调用 `onLoad`，首次进入可能重复发起云调用。

### 3.8 `pages/apply`

这是当前实际使用的调赛申请页，包含“普通调赛”和“跨周调赛”选择。

比赛候选：调用 `search_future_games(for_request=true)`，只返回：

- 与本队相关；
- `adjustable == true`；
- 比赛时间晚于“当前时间 + 3 个日历日”。

可选时段：调用 `search_available_date_period`，从当前日期 +3 天扫描到 `GAME_END_DATE`，按硬编码容量扣除已有 `Schedule`。

- 普通调赛：只保留原比赛所属周的周一至周日。
- 跨周调赛：只保留该周之外的日期。
- 同时要求日期不早于 `GAME_START_DATE`。

选择时段后，`search_available_place` 按 META 中地点顺序返回第一个未被同一 date/period 使用的地点。前端直接取结果 `[0]`，没有“无可用地点”保护。

提交时调用 `make_request_new`：

- 普通调赛：`type=1`；跨周：`type=3`。
- 初始 `state=1`（等待对手确认）。
- 写入原/目标时间、日期、period、地点、比赛 `_id`、双方、申请方等。
- 把原 `Schedule.adjustable` 设为 false，防止重复申请。
- 页面立即跳转成功，再异步调用 `send_email` 通知。

成功提示“调整成功”实际只表示“申请已提交”，不是赛程已完成调整。

### 3.9 `pages/apply_cross_round`

旧版跨周申请页面，已注册但当前 UI 不可达。

其算法与新 `apply` 不同：从当前 +3 天扫描到原比赛 +31 天；通过 META `MAX_GAMES_NUM`、`search_available_date` 和已有地点逐项扣减；调用 `check_request` 防重复；取首个可用地点；最后走旧 `make_request`。

它没有新页面“同周/跨周”统一入口的交互，属于保留的历史实现。重写前应决定是保留可访问兼容页，还是只保留新流程。

### 3.10 `pages/drawing_description`

只显示抽签申请规则说明。页面没有“继续”“提交”或跳转到 `drawing` 的按钮，所以用户从领队首页进入后到此终止。

### 3.11 `pages/drawing`

实际抽签申请表单，已注册但从说明页不可达。

行为：

- 选择图片时可累计多张，但界面标注 `/1`，提交只上传第一张。
- 必须填写非空理由。
- 第一张图片上传到云存储，文件名按时间戳并强制使用 `.png` 后缀，不保留真实格式。
- 调用旧 `make_request` 创建 `type=2`、`state=3` 的 Request，把目标时间/地点字段写为“无”，并锁定原比赛。
- 之后通过邮件发送理由和附件。

没有删除已选图片的交互，也没有可靠地限制选择总数为 1。

### 3.12 `pages/view_apply`

领队申请列表调用 `search_request`，返回与本队相关的申请，以及需要本队参与同组投票的申请。

列表分为：

- 我发起的：`requester == 本队`。
- 对方/待处理：其他相关申请。

只有满足 `to_vote_in_same_group == true`、`state == 5`、本队在 `teams_to_vote` 中且尚未投票时，才标记为当前可投票。状态和类型显示文字来自 META。

页面同样用 `onShow → onLoad` 刷新，首次进入可能重复调用。

### 3.13 `pages/view_apply_details`

领队查看申请详情，并根据身份/状态显示操作：

- 对手在 state 1 可接受或拒绝。
- 申请方在 state 1 可撤回：删除 Request，并恢复 `Schedule.adjustable=true`。
- 同组投票队伍在 state 5 可投接受或拒绝。

截止判断：以“本地今天 00:00 +2 天”为基准，必须同时早于原比赛时间和目标比赛时间，否则申请被设为 state 0 并解锁赛程。

普通调赛对手接受后，调用 `update_request(new_state=2)`，在事务中改赛程并完成申请。

跨周调赛对手接受后，通常进入 state 4（管理员审核）。代码用 `teams_to_vote.length == voted_accept.length` 判断是否已完成投票；初始两个数组均为空时条件成立，因此直接进入 state 4。

同组投票接受时把本队加入 `voted_accept`；页面随后再次检查是否所有 `teams_to_vote` 都接受，若是则转回 state 4。任何拒绝转 state 0 并解锁。

云函数 `vote_request` 内部另有一处错误比较：`voted_accept.length == request.teams_to_vote`，右侧缺 `.length`；当前主要依赖客户端随后的再次检查完成状态转换。

### 3.14 `pages/manager_login`

先按 OPENID 查询管理员。已有 Manager 记录可直接进入后台，不再输入密码。

新管理员注册：

1. 客户端通过 `get_private('PASSWORD')` 取得共享密码并在前端比较。
2. 名称不能等于密码，也不能包含“九毛”。
3. `check_manager` 只检查当前 OPENID 是否已有记录，不检查重名。
4. `manager_register` 插入 `type=1` 的普通管理员。

安全问题：共享密码被下发到前端；新系统必须改为服务端验证或正式认证，不应复制秘密本身。

页面在嵌套 `search_manager` 完成前就可能导航到 `manager_home`，存在 `manager_info` 尚未写入全局数据的竞态。

### 3.15 `pages/manager_home`

显示管理员姓名。四个入口：

- 更新比赛信息：仅 `type=0` 超级管理员。
- 查看/上传技术统计：普通/超级管理员均可。
- 裁判分配：仅 `type=0`。
- 查看赛程调整：普通/超级管理员均可。

JS 还保留 `to_referee_schedule_view`，但 WXML 没有按钮。

### 3.16 `pages/schedule_edit`

管理员比赛列表，数据与公开赛程页相似，但每场比赛都可点击进入 `manager_edit`，不区分过去/未来的编辑权限。点击时把比赛对象写入 `app.globalData`。

### 3.17 `pages/manager_edit`

可编辑：日期、period/时间、地点、组别、描述、主队、客队、双方比分、弃权标记、是否可调整。

提交前 `check_edit`：

- 若勾选弃权，只接受 0:20 或 20:0；条件使用位运算 `|`。
- 非弃权时，仅按**原始**主队、客队、组别确认至少存在一条 Schedule；不验证唯一性。
- 弃权分支不检查比赛是否存在。

`edit_score` 按原始主队、客队、组别更新所有匹配记录，而不是按 `_id` 更新一条。积分计算为：

- 普通比赛：胜者 2 分，败者 1 分。
- 弃权：胜者 2 分，败者 0 分。
- 未赛 `-1:-1`：双方 0 分。
- 平局且非负：当前条件会给双方各 2 分。

同时更新 `date`、`period`、`update_time`、`updated_by` 等审计字段。

### 3.18 `pages/schedule_scoresheet`

管理员选择要上传技术统计照片的比赛。列表与 `schedule_edit` 基本重复，点击进入 `edit_scoresheet`。

### 3.19 `pages/edit_scoresheet`

先按 `home_team + away_team + group` 查询 Photo，**不使用比赛时间**，因此同一对阵在同组多次相遇时照片可能混在一起。云文件 ID 转临时 URL 后展示。

上传规则：

- 微信选择最多 9 张。
- 文件名为 `YYYYMMDD_组_主队_VS_客队_序号.扩展名`。
- 每张上传成功后向 Photo 插入 creator、fileID、比赛双方、组别、时间等。
- 没有删除照片功能。

异步循环使用 `var i`，回调执行时索引已改变；每个上传回调还可能独立返回或跳成功页，最后一张判断很可能永远不成立，导致 loading 状态和多文件完成提示不可靠。

### 3.20 `pages/schedule_referee`

超级管理员查看全部赛程和六个岗位：主裁 CC、副裁 U1/U2、记录台 Recorder、计时员 Timer、24 秒 ShotClock24。点击比赛进入 `edit_referee`。

### 3.21 `pages/edit_referee`

按 Schedule 文档 `_id` 更新六个岗位。所有姓名先 `trim`，允许为空；同时写 `referee_update_time` 和 `referee_updated_by`。

### 3.22 `pages/referee_register`

按当前 OPENID 查询 Referee。已有记录进入裁判首页；否则输入姓名注册。

- 姓名必须 trim 后非空。
- 一个 OPENID 只能有一条记录。
- 不检查姓名唯一性，也没有审批。
- 后续排班检索依赖姓名精确匹配，因此同名或录入差异会直接影响结果。

### 3.23 `pages/referee_home`

按已注册姓名调用 `search_referee_schedule`，在六个裁判岗位中做精确匹配。展示过去/未来比赛、自己的岗位和高亮地点。

过去/未来仍按当天 00:00 分界。`onLoad` 与 `onShow` 都调用加载函数，首次打开会重复查询。

### 3.24 `pages/referee_schedule`

旧的通用裁判查询页：允许手工输入任意姓名，精确搜索六个岗位，并把姓名缓存到本地。管理员代码可以预置姓名，但当前后台没有可见入口。页面已注册但普通导航不可达。

### 3.25 `pages/manager_view_apply`

管理员申请列表调用 `get_all_requests`，按 `request_time` 降序，再按类型/状态分组：

- “已通过的申请”实际同时包含 state 2 和 state 0，即通过与拒绝。
- 普通申请：type 1、非 0/2、目标日期不早于今天。
- 跨周申请：type 3、非 0/2、目标日期不早于今天。
- type 2 抽签申请完全未加入任何显示列表。

`get_all_requests` 没有分页，云数据库默认单次上限可能只返回前 100 条。页面 `onShow` 为空，返回列表时不主动刷新。

### 3.26 `pages/manager_view_apply_details`

管理员查看申请、填写 notes，并按状态执行：

- 未确认的 state 0/2/3：确认/标记已阅。
- state 1/5：只能拒绝。
- state 4：直接接受、拒绝，或发起同组其他领队确认。
- 已确认：可撤销“已阅”状态。

直接接受进入 state 2，由 `review_request` 事务改赛程；拒绝进入 state 0 并解锁；确认只写 `is_reviewed/reviewed_by/reviewed_time`。

“需要同组确认”是两步操作：先通过 `get_other_teams_in_same_little_group` 获取同小组除比赛双方外的球队供管理员选择，再将申请设为 state 5，重置接受/拒绝数组。

`review_request` 对任何 `new_state != 2` 都会把关联 Schedule 的 `adjustable` 恢复为 true，包括 state 5。这意味着同组投票期间原比赛会被重新允许发起其他申请。

页面接受成功后误写 `app.globalData.request_detail.state=2`，而本页面实际使用 `manager_request_detail`；如果 `request_detail` 不存在，可能报错。

### 3.27 `pages/schema_viewer`

内部数据库结构查看器，已注册但无可见入口。默认集合为 Schedule、Request、Team、Leader、Manager、Photo、Private，也允许输入任意集合名；样本数限制 1–20。

`inspect_schema` 对样本文档递归统计字段类型、出现次数和数组首项示例。没有管理员身份验证，也没有默认包含 Referee 和 ScheduleSlotLock。

### 3.28 `pages/success_page`

显示 `app.globalData.errInfo` 中的成功信息，确认按钮调用 `navigateBack({ delta: 0 })`。微信导航通常要求正整数 delta，当前行为可能依赖运行时容错。

### 3.29 `pages/error_page`

与成功页结构相同，用于展示错误；确认按钮同样使用 `navigateBack({ delta: 0 })`。

---

## 4. 核心业务流程与状态机

### 4.1 登录/注册

系统没有传统会话 token。云函数通过微信上下文获得 OPENID，并在 Leader/Manager/Referee 表中查询身份。已有身份通常免密码进入；管理员共享密码只用于第一次普通管理员注册。

### 4.2 Request 类型与状态

| type | 含义 |
|---:|---|
| 1 | 普通调赛 |
| 2 | 抽签申请 |
| 3 | 跨周调赛 |

| state | 当前 META 文案 | 业务含义 |
|---:|---|---|
| 0 | 拒绝 | 已拒绝/已失效 |
| 1 | 等待对手确认 | 新申请已锁定原比赛 |
| 2 | 通过 | 已批准并完成赛程修改 |
| 3 | 抽签申请中 | 抽签申请等待人工处理 |
| 4 | 审核中 | 等待管理员审核 |
| 5 | 等待同组其他领队确认 | 管理员发起的小组投票 |

普通调赛实际流程：

```text
创建 state 1 + Schedule.adjustable=false
├─ 申请方撤回 → 删除 Request + 解锁
├─ 对手拒绝/超时 → state 0 + 解锁
└─ 对手接受 → update_request 事务检查容量
   ├─ 有容量 → 改 Schedule → state 2 + 解锁
   └─ 无容量 → state 0 + 解锁 + TARGET_SLOT_FULL
```

跨周调赛实际流程：

```text
创建 state 1 + 锁定
├─ 对手拒绝/超时 → state 0 + 解锁
└─ 对手接受 → state 4
   ├─ 管理员拒绝 → state 0 + 解锁
   ├─ 管理员直接接受 → 事务改赛程 → state 2
   └─ 管理员要求同组确认 → state 5
      ├─ 任一队拒绝 → state 0
      └─ 全部接受 → state 4 → 管理员最终接受/拒绝
```

抽签流程当前断裂：领队可见入口只到说明页；真正提交页不可达；即使通过直接路径提交 type 2/state 3，管理员列表又不显示 type 2。因此当前只能依靠直接页面路径、邮件和人工数据库处理。

### 4.3 调赛容量与并发

以下均为旧系统当前行为。`update_request` 和 `review_request` 使用 `ScheduleSlotLock` 做目标时段的最终批准串行锁：

1. 锁文档 ID 为 `${year}_${date}_${period}`。
2. 事务读取并递增 revision，统计目标 date/period 的 Schedule 数，排除正在移动的原比赛。
3. 按工作日/周末硬编码容量判定。
4. 最多重试 3 次。
5. 成功后更新 Schedule 的时间、date、period、place，并把 Request 设为 state 2。
6. 如果目标时段已满，把当前申请设为 state 0、解锁原比赛并向客户端抛出 `TARGET_SLOT_FULL`。
7. 当批准后该时段已满，会取消其他目标相同且 state 1 或 `>=3` 的申请，解锁它们的原比赛并附加说明。

第二轮专项审计确认了以下问题：

1. `Schedule.adjustable` 同时表达“赛季导入政策不允许领队调赛”和“已有活动申请造成临时锁定”，永久政策与运行时锁互相覆盖。
2. `make_request_new` 创建 Request 与随后把 Schedule 设为 `adjustable=false` 不是同一事务；它也没有在服务端原子复核当前 `adjustable`、重复申请、容量和具体场地，可能产生重复申请或半完成记录。
3. 原比赛在 state 1 和 state 4 通常保持锁定，但 `review_request` 进入 state 5 时因为 `new_state != 2` 会错误设置 `adjustable=true`。任何拒绝、撤回、过期或取消路径也都无条件恢复 true，无法恢复导入时的原始政策。
4. `search_available_date_period` 与 `search_available_place` 只查询正式 Schedule，不统计待处理 Request；目标容量和场地在提交时没有预留。
5. `ScheduleSlotLock` 只在最终批准时把同一 date/period 的容量统计串行化。它不是容量预留，不检查具体场地唯一性，容量规则也硬编码在函数中而不是赛季元信息。
6. 最终批准只统计正式比赛总数，排除当前原比赛；既不计待处理申请，也不验证请求中的 `place_new` 是否已经被占用。
7. 当一个时段在批准后变满，系统才批量取消目标相同的其他申请，并把这些申请关联比赛全部设为 `adjustable=true`；这既太晚，也可能错误改变永久政策。
8. `update_request`、`review_request` 等接口接受客户端上传的整份 Request 快照，没有统一按 request ID 重读当前状态并完整验证角色，容易受陈旧数据、重复提交或伪造字段影响。

2026 当前快照为上述风险提供了直接证据：

- 8 场 `Schedule.adjustable=false` 可拆为 4 场赛季导入限制和 4 场 type1/state1 活动申请的临时锁；迁移时不得把后 4 场误当成永久不可调。
- 4 个活动申请中，已有 2 个目标时段按正式赛程统计处于满额，另有 1 个申请指定的场地已被正式比赛占用。它们仍能进入待处理状态，证明旧系统没有在提交时预留容量和场地。
- Request 的日期字段还存在数字/字符串混用，同槽比较和自动任务不能依赖宽松类型转换。

`update_request/review_request` 的备用 date 计算为 `10000 * 零起始月份 + 日`，与系统其他地方的 day-of-year 不一致；通常因为 Request 已有 `date_new` 而未触发。

上述行为只作为迁移与回归依据。新系统必须采用 A.2–A.5 和 6.6 定义的永久政策、活动锁、容量/场地预留和服务端状态机，不能复刻本节的字段混用。

### 4.4 比分与积分

比分初始为 -1:-1。积分由管理员编辑函数写入，而不是积分表实时按规则重新推导：

- 胜 2、负 1；弃权负方 0；未赛双方 0。
- 当前实现没有平局特例，平局双方得到 2。
- 积分表直接信任 Schedule 中已存的 `home_team_point/away_team_point`。

重写规则：正式比分相等时拒绝保存；弃权固定为 20:0、胜方 2 分、弃权方 0 分。积分应由受验证的比赛结果一致生成，不能接受客户端任意上传积分。

### 4.5 裁判排班

Schedule 直接保存六个姓名字符串。裁判注册仅保存一个姓名；个人排班通过该姓名与六个字段精确相等检索。没有独立 Referee ID 关联、模糊匹配、别名或冲突检查。

重写规则：V1 不迁移裁判注册、登录、个人赛程、六岗位排班及管理员排班页面，只预留未来数据模型扩展位置。

### 4.6 技术统计照片

照片存云存储，Photo 表保存 fileID 和比赛元数据。查询键没有比赛 `_id`/time，主要是组别 + 主客队；上传无删除、替换、顺序调整与事务补偿。云文件上传成功但数据库插入失败时可能产生孤儿文件。

重写规则：照片必须按稳定 `gameId` 关联；仅已登录领队和管理员可查看，管理员可上传、删除、替换、排序，文件与元数据保持一致并记录审计。

---

## 5. 38 个云函数目录

下表覆盖每个 `cloudfunctions/*/index.js`。除特别说明，当前函数没有系统化的服务端角色授权。

| 云函数 | 调用方/状态 | 输入与读写 | 当前逻辑 |
|---|---|---|---|
| `add_photo_manager` | `edit_scoresheet` | Photo 新增 | 把客户端传入的照片元数据插入 Photo；不验证管理员身份 |
| `batch_update_referee` | 运维手工调用 | Schedule 批量更新 | 使用函数内置 JSON 排班，按 time+主客队+地点精确定位并写六岗位；报告缺失/冲突 |
| `check_edit` | `manager_edit` | 查 Schedule | 弃权只校验 0:20/20:0；普通编辑按原主队+客队+组别检查存在性 |
| `check_leader` | `leader_register` | 查 Leader | 校验必填、相同 team+sex、相同 OPENID；未使用 group 做队伍唯一性 |
| `check_manager` | `manager_login` | 查 Manager | 仅检查当前 OPENID 是否已注册 |
| `check_request` | 旧 `apply_cross_round` | 查 Request | 检查同一场比赛是否已有活动申请 |
| `clear` | 运维手工调用 | 更新 Schedule | 把 `home_team_score=-1` 的比赛积分清零；异步更新未完整 await/return |
| `edit_score` | `manager_edit` | 更新 Schedule | 按原主队+客队+组别批量更新比赛与积分、时间、队伍、审计字段 |
| `get_all_photo_IDs` | 旧 MATLAB 备份流程 | 分页查 Photo | 返回全部 fileID，供手工把云文件下载到本地 |
| `get_all_requests` | `manager_view_apply` | 查 Request | 一次 `.get()` 返回申请，未分页，可能截断于默认上限 |
| `get_date_period` | 无当前页面直接调用 | 时间转换 | 把时间转 day-of-year 和 period；重复实现 app 时间分段 |
| `get_knockout` | `knockout` | 查 Schedule | 按硬编码种子、固定主客顺序和比赛描述生成淘汰赛树 |
| `get_other_teams_in_same_little_group` | 管理员详情 | 查 Team | 找到某队所在小组，返回除比赛双方之外的同组球队 |
| `get_private` | app 启动、管理员注册 | 查 Private | 按 `_id` 返回 META 或 PASSWORD 整个文档；无访问控制 |
| `inspect_schema` | `schema_viewer` | 任意集合只读 | 采样 1–20 条并递归生成字段类型摘要；无访问控制 |
| `leader_register` | `leader_register` | Leader 新增 | 用调用者 OPENID 插入姓名、组别、队伍、性别、注册时间 |
| `login` | 首页遗留方法 | 微信上下文 | 返回 OPENID、APPID、UNIONID；当前可见登录不依赖它 |
| `make_request_new` | `apply` | Request 新增 + Schedule 更新 | 创建 type1/3 state1 申请，并锁定原比赛 adjustable=false |
| `make_request` | 旧跨周/抽签页 | Request 新增 + Schedule 更新 | 旧格式申请创建器；抽签写 type2/state3，调赛走旧字段 |
| `make_table` | `scoretable` | 查 Team/Schedule | 生成比分/积分矩阵和汇总，依赖连续 Team.id |
| `manager_register` | `manager_login` | Manager 新增 | 用当前 OPENID 插入普通管理员 `type=1` |
| `referee_register` | `referee_register` | Referee 新增 | 用当前 OPENID 插入姓名和注册时间 |
| `review_request` | 管理员详情 | Request/Schedule/锁 | 管理员审核、目标时段事务检查、改赛程或解锁、取消同槽冲突申请 |
| `search_available_date_period` | `apply` | 查 Schedule | 扫描日期与时段，按硬编码容量返回尚可排场次的 `[date,period]` |
| `search_available_date` | 旧跨周页 | 查 Schedule | 返回日期范围内已有比赛，用于客户端自行扣容量 |
| `search_available_place` | `apply` | 查 Schedule | 返回某 date/period 未使用的地点，顺序来自 META `PLACE_NAMES` |
| `search_future_games` | `leader_home`/`apply` | 查 Schedule | 查本队未来比赛；申请模式再要求 +3 天且 adjustable=true |
| `search_future_schedule` | 多个赛程页 | 分页查 Schedule | 分页返回全部 Schedule；函数名虽含 future，实际不筛未来 |
| `search_leader` | 领队登录 | 查 Leader | 按当前 OPENID 返回领队记录 |
| `search_manager` | 管理员登录 | 查 Manager | 按当前 OPENID 返回管理员记录 |
| `search_photos` | `edit_scoresheet` | 查 Photo | 按主队+客队+组别返回照片；未分页、不含比赛时间 |
| `search_referee_schedule` | 裁判页面 | 查 Schedule | 姓名精确匹配六岗位任意一个，返回相关比赛 |
| `search_referee` | 裁判登录 | 查 Referee | 按当前 OPENID 返回裁判记录 |
| `search_request` | `view_apply` | 查 Request | 返回涉及本队或要求本队投票的申请；未分页 |
| `send_email` | 调赛/抽签流程 | 外部 SMTP | 用硬编码邮箱凭据发送内部通知，可附抽签图片 |
| `update_referee` | `edit_referee` | 更新 Schedule | 按 `_id` 写六岗位与更新者/时间 |
| `update_request` | 领队详情 | Request/Schedule/锁 | 处理对手接受/拒绝、投票后状态与普通调赛事务落地 |
| `vote_request` | 领队详情 | 更新 Request | 写接受/拒绝队伍数组；含缺少 `.length` 的比较错误 |

云函数依赖版本不统一：`wx-server-sdk` 的声明横跨约 2.5.x、2.6.x、3.0.1 和 `latest`；`send_email` 另依赖 `nodemailer ^6.10.0`。各 `config.json` 的 openapi 权限列表为空。重写时应统一运行时和锁文件，并把邮件凭据迁移到安全环境变量/秘密管理。

---

## 6. 数据模型与当前数据快照

以下字段来自 2026 当前备份，而不是只依赖旧说明文档。

### 6.1 `Schedule`（146 条）

字段：

```text
_id, sex, group, littlegroup,
home_team, away_team,
home_team_score, away_team_score,
home_team_point, away_team_point,
description, is_given_up, adjustable,
place, time, date, period,
updated_by, update_time,
CC, U1, U2, Recorder, Timer, ShotClock24,
referee_update_time, referee_updated_by
```

当前分布：男甲 35、男乙 49、女甲 29、女乙 33；小组赛 98、循环赛 28、淘汰赛 8、半决赛 6、决赛 4、保级赛 2。已出比分 138，弃权 4，未赛 4；`adjustable=true` 138；至少有一名工作人员的比赛 132。

### 6.2 `Team`（57 条）

字段：`_id, group, littlegroup, name, id`。

`id` 只在所属大组/小组内有意义，并供积分矩阵作为 1 起始连续下标。当前连续性：男甲 A/B 各 1–6；男乙 A/B 各 1–4、C/D/E 各 1–5；女甲 A 为 1–8；女乙 A 为 1–4、B/C 为 1–5。

当前队伍分组与赛程转换脚本使用的 2026 名称包括：

- 男甲 A：医学、城环、经济、信科、叉院、软微；B：数学、化学、工学、元培、生科、外院。
- 男乙 A：信管、哲学、材料、环科；B：智能、力工-先机、中文-艺术、光华；C：心理、新传、电子、社会、历史；D：物理、未来-现代、政管、地空-集电、法学；E：计算机、国关、马院、考古、燕京。
- 女甲 A：外院、化学、法学、燕京、信科、元培、医学、物理。
- 女乙：地空、生科-历史、信管、工学-材料、城环、新传、数学、马院、中文、光华-经济、国关、社会-政管、环科-哲学、心理，分布于当前 A/B/C 三个小组。

### 6.3 `Request`（40 条）

字段：

```text
_id, game_id, type, state, sex, group,
home_team, away_team, requester,
time, date, period, place,
time_new, date_new, period_new, place_new,
request_time, notes,
is_reviewed, reviewed_by, reviewed_time,
to_vote_in_same_group, teams_to_vote,
voted_accept, voted_reject
```

当前分布：type1/state0 1 条、type1/state1 4 条、type1/state2 19 条、type3/state0 3 条、type3/state2 13 条；没有 type2。`is_reviewed=true` 16 条，曾进入同组投票标记 14 条。

数据类型并不完全稳定：个别旧 Request 的 `date` 是字符串而新记录多为数字，严格比较时可能导致同槽判断失败。

### 6.4 身份与辅助集合

| 集合 | 数量 | 字段 |
|---|---:|---|
| `Leader` | 54 | `_id,name,sex,group,team,openID,register_date` |
| `Manager` | 128 | `_id,name,openID,type,register_date`；type0=13、type1=115 |
| `Referee` | 37 | `_id,name,openID,register_date` |
| `Photo` | 207 | `_id,creator,create_time,fileID,home_team,away_team,group,time` |
| `Private` | 2 | META 文档和 PASSWORD 文档；秘密不在本文记录 |
| `ScheduleSlotLock` | 动态 | `_id/revision` 等并发锁信息；旧结构说明未记录但现代码依赖 |

### 6.5 根目录静态数据与云备份不一致

根目录 `schedule.json` 是 Excel 转换后的**预导入文本**，146 场、全部未赛；它不是当前云数据库的完整镜像，缺少 `_id`、date/period、更新审计和裁判字段。其组别数量男乙 51、女乙 31，与当前备份男乙 49、女乙 33 不同。

根目录 `team.json` 有 57 队，但只有 group/littlegroup/name，没有积分表需要的 `id`；女乙小组被脚本写为 B/C/D，而当前云 Team 是 A/B/C。原因是春季脚本对 f/g/h 使用偏移公式映射成 B/C/D。重写的数据迁移必须以明确选定的云备份/数据库为准，不能把根目录这两个文件直接当线上真相。

### 6.6 重写所需的核心数据接口

下表是概念级强制模型；实现可以调整命名，但不能丢失语义和约束。

| 实体 | 必要字段/关系 | 强制约束 |
|---|---|---|
| `Game` | 稳定 `gameId`、`seasonId`、比赛双方与赛程字段、导入的 `leaderAdjustable`、`activeRescheduleRequestId`、`scheduleVersion` | 永久政策与活动锁分离；按 ID 修改；版本用于乐观并发控制；正式比分不可相等 |
| `RescheduleRequest` | request ID、game ID、原比赛快照、目标 date/period/venue、普通/跨周类型、提交与确认截止、参与球队及各自确认、当前状态、`reservationId`、创建/更新时间 | 服务端权威；一个活动申请只能对应一场被锁比赛和一个有效预留；状态迁移必须校验当前状态与权限 |
| `SlotReservation` | reservation ID、season/date/period/venue、request ID、状态、创建/转换/释放时间 | season+date+period+venue 对未释放占用唯一；状态至少为有效、已转换、已释放；转换和释放幂等 |
| `SeasonMeta` | `seasonId`、固定容量表、period 定义与本地时间、有序场地列表 | 赛季中不可由普通管理员修改；超级管理员例外也不改写容量表 |
| `AdminAuditLog` | 操作者、角色、时间、动作、对象、修改前后快照、取消的 request、释放的 reservation、确认信息 | 追加写；用于超级管理员例外、直接改赛程、取消活动申请、淘汰赛纠错和照片管理 |

数据库或事务层必须同时保证两个不变量：

1. 标准流程的某 date/period 占用数等于正式比赛加有效预留，且不超过 `SeasonMeta` 容量；已转换预留与其对应正式比赛只能计数一次。
2. 同一 season/date/period/venue 不能同时被正式比赛或有效预留重复占用。若使用 `SlotReservation` 作为统一占用标记，已转换记录应继续关联正式比赛；迁移现有比赛时需要补齐对应占用记录。

建议的事务边界：

- **申请提交事务**：重读权限与 Game → 校验版本/政策/截止/活动锁 → 校验容量与场地 → 创建 Request 和 Reservation → 写 Game 活动锁与新版本。
- **批准事务**：按 request ID 重读 → 校验状态与确认 → 把 Reservation 原子转换为正式占用 → 修改 Game → 清活动锁 → Request 进入通过。
- **终止事务**：按 request ID 重读 → Request 进入拒绝/撤回/过期/管理员取消 → 仅当 Game 的活动 request ID 与当前申请相等时清锁 → 幂等释放 Reservation。
- **超级管理员编辑事务**：二次确认 → 显式取消该 Game 的活动申请并释放其 Reservation → 执行修改 → 写完整审计；无关申请不在事务修改集合中。

迁移 2026 数据时，不能把 `Schedule.adjustable` 直接复制到 `leaderAdjustable`。应先按活动 Request 关联拆出 4 场临时锁，再把其余 4 场导入限制映射为永久政策，并在迁移报告中逐场校验。

---

## 7. Excel、MATLAB 与运维逻辑

### 7.1 `schedule.xlsx` 与模板

当前 `schedule.xlsx`：工作表 `北大杯 (2)`，使用区域 A1:Z80。

- A 列是 Excel 日期序列。
- B:Q 的第 1 行是时刻；当前包含 12:50、14:20、15:50、18:00、18:20、19:50、20:00、20:40。
- 场地列映射：场地 1 = B/E/H/L/N/Q；场地 2 = C/F/I/M/O；场地 3 = D/G/J；场地 4 = K/P。
- 比赛单元格写代码式对阵，如 `A3vsA4`；女子比赛使用 `（女）` 或“女”标记。
- T:V 存说明性内容，X:Z 有校验/公式；观察到 Z22 为 `SUM(Z17:Z21)`。
- 第 61–80 行目前只有日期。

`template.xlsx` 的 Sheet1 使用 A1:Q55，采用相同的日期、时段与场地网格结构。

### 7.2 `xls2Json_spring.m`

春季“北大杯”转换器：

1. `xlsread` 读取 Excel，遍历包含 `VS` 的格子并去空格。
2. 根据女子后缀、代码大小写和占位符判断性别、大组、小组、淘汰/半决赛/决赛等描述。
3. 将 A1、a、f 等队伍代码替换为脚本内置队名。
4. 初始化比分 -1、积分 0、`is_given_up=false`。
5. 决赛默认 `adjustable=false`，其他通常 true；代码中对邱德拔场地的特殊赋值可能随后被通用场地映射覆盖。
6. 时间按本地赛程减 8 小时写为 UTC。
7. 直接拼接每个 JSON 对象写入 `schedule.json`，不是标准 JSON 数组，也没有对象间逗号。
8. 同样拼接 `team.json`；没有生成 `Team.id`。

### 7.3 `xls2Json_fall.m`

秋季“新生杯”转换器，整体结构相同，组别简化为男篮/女篮，包含另一套队伍代码映射，并对最后一轮/决赛做不可调设置。

### 7.4 其他脚本

- `add_new_field.mlx`：逐行读取旧 Schedule 导出，补双方比分 -1，把 `x_id`/`x_date` 替换回 Mongo 风格 `_id`/`$date`，写 `schedule_new.json`。
- `delay.mlx`：读取 2021 赛程，把 2021-10-29 之后的比赛整体延后 7 天并重新写 JSON。
- `downloadPhotos.m`：配合 `get_all_photo_IDs` 获取临时 URL，使用粘贴到脚本中的 fileID/URL 大文本，推导本地文件名并 `websave` 到 `Backup/Photo`。这是人工备份流程，不是自动同步。

### 7.5 旧赛季操作说明

`doc/每赛季应该检查的事项.txt` 记录了历史操作习惯：轮次按前一周末归属、检查注册与分组名、锁定最后一轮、给若干云函数设 `Asia/Shanghai` 时区、创建/导入集合、配置数据库/存储权限和开发者名单。

该清单仍写着替换云环境名，但当前代码使用 `DYNAMIC_CURRENT_ENV`；说明部分步骤已经过期。3 份旧 DOCX 也只列 state 0–4，并把抽签/跨周描述为人工处理；当前代码和 META 已有 state 5 与部分自动事务。因此旧文档只能用于理解历史意图，现行代码和当前备份才是现状依据。

### 7.6 历史备份资产

仓库保存了 2021–2026 多赛季的 Leader、Manager、Schedule、Request、Photo、Team、Private 等 JSON 备份。当前可见主快照数量演进包括：

- 2021 新生杯：Leader 36、Schedule 86、Request 42。
- 2022 北大杯：Leader 55、Schedule 144、Request 37；新生杯另有 Leader 43、Schedule 71、Request 26。
- 2023 北大杯：Leader 55、Schedule 144、Request 46。
- 2024/2025：同时保留北大杯与新生杯多集合快照。
- 2026：Leader 54、Manager 128、Schedule 146、Request 40、Photo 207、Team 57、Referee 37、Private 2。

迁移时应把这些视为档案，不要无条件合并进当前赛季集合。

---

## 8. 当前缺陷、冲突与重写处置

以下列表保留旧系统审计发现，便于重写时逐项防回归。产品语义已经确认的项目按 A 章实施；未涉及业务选择的技术缺陷按“保留用户功能、修正实现缺陷”处理，不复刻漏洞。

1. **抽签流程不可闭环**：说明页不能进入提交页，管理员列表又忽略 type2。
2. **多个注册路由不可达**：`apply_cross_round`、`drawing`、`referee_schedule`、`schema_viewer` 没有正常可见入口。
3. **全局 META 初始化竞态**：除淘汰赛外多数页面不等待配置完成。
4. **客户端授权替代后端授权**：关键写云函数未按 OPENID/角色复核。
5. **管理员共享密码下发前端**，SMTP 密码硬编码在云函数。
6. **调赛成功文案误导**：提交申请即显示“调整成功”。
7. **普通调赛由对手接受后直接生效**，跨周才进入管理员审核；需确认是否仍是规则。
8. **state 5 会解锁原比赛**：`review_request(new_state!=2)` 统一设置 adjustable=true。
9. **并发事务只控总容量，不控地点冲突**。
10. **待审批申请不占场地/容量**，多个申请可选择同一目标。
11. **地点查询结果无空数组保护**。
12. **时间模型混用 UTC+8、本地分钟、当前年份和 day-of-year**，跨年与时区行为脆弱。
13. **period 1 包含所有 13:20 之前时间**，分段间还有 period 0 空隙。
14. **日期备用编码错误**：部分云函数备用 date 不是 day-of-year。
15. **Request 日期数字/字符串混用**。
16. **比分编辑按主客队+组别批量更新，不按比赛 `_id`**。
17. **弃权校验不验证比赛存在；普通存在性不验证唯一性**。
18. **平局双方得到 2 分**；若篮球业务禁止平局，应明确报错而不是赋分。
19. **积分表依赖手工存储积分和连续 Team.id**，Team 根文件却不含 id。
20. **积分表计算了跨组指标但不显示/不排序**。
21. **总积分为 0 的球队全部显示同一名次**。
22. **淘汰赛是硬编码种子和精确主客查询**，不从排名生成，也不查反向对阵。
23. **淘汰赛缩写可能与数据全名不一致**。
24. **赛程今天已结束的比赛仍归“未来”**。
25. **公开赛程分享文案硬编码 2024**。
26. **照片按主客队+组查询，不按比赛 ID/time**，重赛会混合。
27. **多照片上传异步闭包错误**，完成状态和跳转不稳定；无删除和失败补偿。
28. **裁判靠姓名精确匹配**，同名、空格或改名不可控。
29. **多个页面首次加载重复请求**：`onLoad` 与 `onShow` 互相调用。
30. **管理员申请查询和照片查询未分页**；Request 超过 100 条时可能遗漏。
31. **管理员“已通过”列表含拒绝项**，且不显示抽签申请。
32. **管理详情写错全局字段**，接受后可能访问 undefined。
33. **成功/错误页使用 `navigateBack(delta=0)`**。
34. **根 `schedule.json`/`team.json` 不是标准数组，且与当前云数据不一致**。
35. **旧 DOCX/赛季清单已落后于 state5、META 和事务锁实现**。
36. **无自动化测试**；各云函数 package 的默认 `test` 脚本不能验证业务。

### 8.1 已确认处置矩阵

| 审计项 | V1 处置 |
|---|---|
| 1–2 抽签和不可达路由 | 抽签仅保留文字说明、全流程线下；退役 `schema_viewer`、旧 `apply_cross_round`、通用 `referee_schedule`，不恢复死入口 |
| 3–5 初始化、授权与秘密 | 配置加载有明确就绪状态；所有权限在服务端按微信身份复核；密码和邮件凭据只在服务端秘密管理 |
| 6–15 调赛、容量、锁与时间 | 按 A.2–A.5 和 6.6 重建。`leaderAdjustable`、活动 request 锁和目标 Reservation 分离；截止按本地日历日；申请即原子预留容量和场地 |
| 16–18 比分与弃权 | 全部按稳定 `gameId` 修改；拒绝平局；弃权固定 20:0、积分 2:0 |
| 19–21 积分与排名 | 服务器从合法比赛结果生成积分；保留已确认的组内同分顺序；不实现跨组指标；修正零积分并列显示缺陷 |
| 22–23 淘汰赛 | 首轮管理员录入，胜者自动晋级；超级管理员纠错时显式重置下游，不依赖名称缩写和单向主客查询 |
| 24–25 赛程时间与分享 | 以明确时区和实际结束时间分类，分享文案不得硬编码年份 |
| 26–27 技术统计照片 | 按 `gameId` 隔离；仅领队/管理员查看；管理员支持增删替换排序；文件与元数据一致性和审计纳入验收 |
| 28 裁判姓名关联 | V1 整体不迁移裁判模块，只保留未来扩展位置，因此不保留姓名匹配逻辑 |
| 29–33 页面重复请求、分页和错误跳转 | 作为实现缺陷修正；不改变对应用户功能；列表统一分页并确保返回刷新和失败可恢复 |
| 34–35 静态数据与旧文档漂移 | 当前赛季迁移使用明确冻结的数据源和迁移报告；参赛手册只作参考，冲突按 11.2 处理 |
| 36 自动化测试 | 以 9 章场景建立状态机、事务并发、迁移和权限测试，不能以默认空测试替代 |

### 8.2 调赛实现的剩余高风险点

- **迁移误判风险**：当前 8 场 false 必须按活动 Request 关系拆成 4 场永久限制与 4 场临时锁；不得只复制布尔值。
- **跨集合唯一性风险**：如果正式比赛与预留分表存储，数据库必须仍能保证同一场地不会跨表重复占用。
- **幂等风险**：自动过期、客户端重试和管理员重复点击可能并发；清锁时必须比较 `activeRescheduleRequestId`，释放时必须比较 reservation 状态。
- **例外越权风险**：容量绕过仅限超级管理员的显式通道；普通接口不能靠客户端标志开启例外。
- **无关申请受损风险**：管理员处理冲突必须选中具体对象并写审计，禁止沿用“时段满后批量取消其他申请”的旧逻辑。

---

## 9. 重写时的功能验收清单

### 9.1 V1 功能范围

- [ ] 公开查看完整赛程、过去/未来分组、比分与地点。
- [ ] 按大组/小组查看积分矩阵，并按相互积分、相互净胜分、相互总得分、总净胜分、总得分排名；不提供跨组排名。
- [ ] 展示淘汰赛树：首轮由管理员录入，后续胜者自动晋级，超级管理员可以纠错并重置下游。
- [ ] 领队、普通管理员和超级管理员按微信身份注册/登录，权限全部由服务端验证。
- [ ] 领队看到本队未来比赛和申请列表。
- [ ] 普通/跨周调赛遵守导入政策、比赛未开始、无活动申请、较早比赛日的 D-3/D-2 截止和周一至周日边界。
- [ ] 申请提交事务同时创建 Request、锁原比赛并预留目标容量与具体场地；所有非终态持续持有两种资源。
- [ ] 普通调赛由对手按时同意后自动生效；跨周由管理员选择直批或指定球队投票，完成投票后可无限期等待管理员终审。
- [ ] 撤回、拒绝、确认超时和管理员取消在同一事务中释放活动锁与预留，且重复执行安全。
- [ ] 超级管理员通过有二次确认和完整审计的例外通道直接新增/修改比赛；可以超容量但不改变容量元信息，也不静默抢占无关申请。
- [ ] 管理员按 `gameId` 管理技术统计图片，领队和管理员可查看；支持上传、删除、替换、排序和审计。
- [ ] 比分拒绝平局；弃权固定 20:0、积分 2:0。
- [ ] 抽签和特殊原因调赛只显示线下邮件说明；超级管理员可录入线下决定后的赛程结果。
- [ ] 只迁移当前赛季；所有新数据保留 `seasonId`。
- [ ] 不提供裁判模块、历史赛季查询、`schema_viewer`、旧跨周页或通用裁判查询页。

### 9.2 强制不变量

- 每场比赛使用稳定 `gameId`；所有编辑、照片、申请都按 ID 关联。
- 身份授权在服务端执行；管理员类型和领队所属队不可由客户端伪造。
- 秘密只存在于服务端安全配置。
- 时间存一个明确时区的时间戳，同时存可查询的赛季/比赛日/时段 ID；不依赖当前年份重建。
- Team 在赛季/组/小组内有唯一键和稳定排序，不把数组下标当身份。
- `leaderAdjustable` 只保存导入政策，`activeRescheduleRequestId` 只保存活动锁；任何申请终态都不得修改前者。
- 一场比赛最多一个活动 Request；一个活动 Request 恰有一个有效 Reservation；活动锁与预留的生命周期一致。
- Request 状态迁移按 request ID 由服务端重读并验证，不接受客户端整份对象覆盖，不允许任意 state 跳转。
- 标准流程按正式比赛加有效预留计算容量；容量、场地唯一性、Request 和 Game 锁在事务中一起检查。
- 赛季容量、period 和场地顺序是固定元信息；例外操作不改变它们。
- 自动过期、锁释放、预留转换/释放和重复回调全部幂等。
- 上传采用“文件 + Photo 元数据”一致性处理，支持删除与孤儿清理。
- 全部列表分页；所有失败路径给出可恢复提示。

### 9.3 调赛、容量与锁定的必测场景

1. 导入为 `leaderAdjustable=false` 的比赛不出现在领队可申请列表；其他申请失败、撤回或过期不改变该政策。
2. 同一比赛并发提交两次，只有一个事务取得 `activeRescheduleRequestId` 并创建 Request/Reservation，另一事务无半成品。
3. 多个申请争抢最后一个容量或同一场地时，只允许一个成功预留；失败者不创建申请、不锁比赛。
4. 申请在等待对手、等待管理员决定、等待指定球队和等待管理员终审期间，原比赛锁与目标预留始终存在。
5. 拒绝、撤回、球队确认超时和管理员取消同时释放两个资源；重复执行过期任务或终态回调不产生额外修改。
6. 普通申请由对手按时接受后，预留原子转换为正式占用；`leaderAdjustable` 保持导入值，满足条件时可再次发起调赛。
7. 跨周申请的指定球队在 D-2 24:00 前全部同意后，即使越过该时间也继续保留锁和预留，直到管理员终审。
8. 在提交截止、确认截止前后各一秒测试原日期早、目标日期早、同日和跨周情况，截止均取两日期较早者。
9. 工作日标准时段容量 1；周末前三时段容量 3、后两时段容量 2；周末容量 2 时可自动选择三个场地中的任意两个空闲场地。
10. 超级管理员修改有活动申请的比赛时，界面二次确认；事务明确取消该申请、释放其预留、修改比赛并生成完整审计。
11. 标准流程不能超过固定容量；超级管理员例外可以超过，但容量元信息前后完全一致。
12. 超级管理员遇到无关申请占用目标场地时不能静默抢占；只有显式选择处理冲突并审计后才可继续。

### 9.4 冻结快照与其他回归场景

用冻结的 2026 数据快照验证迁移和非调赛功能：

1. 146 场赛程的排序、过去/未来归类与显示文案。
2. 每个大组/小组的 57 队积分矩阵、积分、净胜分与名次。
3. 8 场旧 `adjustable=false` 被正确拆成 4 场导入限制和 4 场活动申请锁；迁移报告逐场可核对。
4. 已有 Request 的类型、状态、参与方与目标数据被一致迁移；旧日期字符串/数字被规范化。
5. 138 场已有比分、4 场弃权和 4 场未赛的结果一致；新增平局保存测试必须失败。
6. 淘汰赛首轮录入、胜者晋级和超级管理员下游重置均可追踪。
7. 207 条 Photo 能按唯一比赛 ID 映射；重复对阵不会混图，增删替换排序均有权限和审计。
8. type0/type1 管理员按钮和每个对应服务端接口的权限一致；客户端伪造角色、球队、Request 字段均失败。
9. 抽签/特殊原因说明页可达并给出线下邮件流程，但不存在附件上传或伪闭环审批入口。
10. 风险清单、状态机、数据模型和验收用例中不再出现用单一 `adjustable` 同时表达政策与活动锁的逻辑。

---

## 10. 源码覆盖附录

### 10.1 页面四件套行数

| 页面 | JS | WXML | WXSS | JSON | 合计 |
|---|---:|---:|---:|---:|---:|
| apply | 289 | 69 | 1 | 3 | 362 |
| apply_cross_round | 365 | 58 | 1 | 3 | 427 |
| drawing | 205 | 74 | 1 | 3 | 283 |
| drawing_description | 66 | 17 | 13 | 3 | 99 |
| edit_referee | 134 | 105 | 4 | 3 | 246 |
| edit_scoresheet | 197 | 25 | 6 | 3 | 231 |
| error_page | 75 | 13 | 1 | 3 | 92 |
| index | 142 | 23 | 7 | 3 | 175 |
| knockout | 214 | 60 | 270 | 3 | 547 |
| leader_home | 161 | 42 | 5 | 3 | 211 |
| leader_register | 170 | 60 | 1 | 3 | 234 |
| login_select | 121 | 20 | 8 | 3 | 152 |
| manager_edit | 272 | 136 | 7 | 3 | 418 |
| manager_home | 148 | 24 | 14 | 3 | 189 |
| manager_login | 178 | 45 | 7 | 3 | 233 |
| manager_view_apply | 109 | 39 | 1 | 3 | 152 |
| manager_view_apply_details | 392 | 116 | 1 | 3 | 512 |
| referee_home | 178 | 72 | 43 | 3 | 296 |
| referee_register | 79 | 34 | 1 | 3 | 117 |
| referee_schedule | 231 | 85 | 43 | 3 | 362 |
| schedule | 173 | 51 | 54 | 4 | 282 |
| schedule_edit | 178 | 48 | 55 | 3 | 284 |
| schedule_referee | 135 | 54 | 22 | 3 | 214 |
| schedule_scoresheet | 179 | 48 | 55 | 3 | 285 |
| schema_viewer | 109 | 48 | 100 | 3 | 260 |
| scoretable | 218 | 102 | 134 | 3 | 457 |
| success_page | 75 | 12 | 1 | 3 | 91 |
| view_apply | 138 | 29 | 5 | 3 | 175 |
| view_apply_details | 329 | 83 | 5 | 3 | 420 |

### 10.2 云函数入口行数

```text
add_photo_manager 23              batch_update_referee 93
check_edit 36                     check_leader 32
check_manager 29                  check_request 40
clear 23                          edit_score 44
get_all_photo_IDs 28              get_all_requests 16
get_date_period 45                get_knockout 101
get_other_teams_in_same_little_group 44
get_private 15                    inspect_schema 127
leader_register 21                login 36
make_request_new 83               make_request 95
make_table 97                     manager_register 19
referee_register 44               review_request 301
search_available_date_period 72   search_available_date 37
search_available_place 24         search_future_games 48
search_future_schedule 27         search_leader 23
search_manager 23                 search_photos 16
search_referee_schedule 38        search_referee 16
search_request 28                 send_email 63
update_referee 36                 update_request 268
vote_request 42
```

35 个云函数被页面/全局代码调用。仅运维或手工调用的三个入口是 `batch_update_referee`、`clear`、`get_all_photo_IDs`；最后一个只出现在注释化的 MATLAB 备份流程中。

### 10.3 其他已核对文件类别

- `miniprogram/app.js`、`app.json`、`app.wxss`、`sitemap.json`。
- `project.config.json`、`project.private.config.json` 及全部页面 JSON。
- 38 个云函数的 `index.js`、`package.json`、`package-lock.json`、`config.json`。
- `schedule.xlsx`、`template.xlsx`、`schedule.json`、`team.json`。
- `xls2Json_spring.m`、`xls2Json_fall.m`、`downloadPhotos.m`、`add_new_field.mlx`、`delay.mlx`。
- `doc/` 下赛季检查说明与 3 份 DOCX。
- 2021–2026 数据备份的集合、字段、数量与当前 2026 结构。
- `send_email/node_modules` 仅作为第三方依赖与锁文件校验，不抽取其内部实现为 PKUBA 业务规则。

---

## 11. 已确认产品决策记录

### 11.1 原待确认问题的结论

| 主题 | 已确认结论 |
|---|---|
| 抽签 | 只保留规则文字；申请、证明和抽签均在线下邮件完成，不上传附件、不建系统审批流；超级管理员录入最终结果 |
| 普通调赛 | 对手按时同意后由服务端自动验证并生效，不增加管理员终审 |
| 跨周调赛 | 周一至周日为边界；对手同意后管理员可直批或指定球队投票；按时完成球队确认后可无限期等待管理员终审 |
| 调赛截止 | 取原比赛与目标比赛日期较早者；提交为提前三日 24:00，球队确认为提前两日 24:00，均按上海本地日历日 |
| 容量与场地 | 固定为赛季元信息；标准申请提交即预留一场容量和第一个可用场地；周末容量 2 可从三个场地任取两个 |
| 永久不可调政策 | 每场从赛季导入 `leaderAdjustable`，运行时不从决赛/末轮文字推断；与活动申请锁分离 |
| 特殊原因 | 线下邮件一事一议，V1 无证明附件流程；超级管理员录入最后决定 |
| 管理员例外 | 仅超级管理员；可绕过政策、截止、周次/轮次、容量和确认，二次确认并自动审计；不强制理由，不得静默损害无关申请 |
| 比分与弃权 | 正式比分禁止平局；弃权固定 20:0、积分 2:0 |
| 积分同分 | 依次相互积分、相互净胜分、相互总得分、总净胜分、总得分；不做跨组排名 |
| 淘汰赛 | 首轮管理员录入，胜者自动晋级；超级管理员可以纠错并重置下游 |
| 技术统计照片 | 按 game ID 隔离，仅领队/管理员查看；管理员可上传、删除、替换、排序并留痕 |
| 裁判 | V1 不实现裁判注册、登录、个人赛程或管理员排班，只留未来扩展位置 |
| 赛季 | V1 只迁移当前赛季；保留 season ID，不提供历史查询 |
| 旧页面 | 退役 `schema_viewer`、旧 `apply_cross_round` 和通用 `referee_schedule` |

### 11.2 与参赛手册的有意差异

参赛手册路径：`C:\ShareCache (2)\黄越_2201110520\篮协\章程\2025年北大杯男子篮球甲级联赛参赛手册.docx`。该文件用于理解制度背景；下列差异是产品明确选择，不应在实现时被“纠正”回手册版本：

- 跨周边界采用周一至周日，而不是手册中的周六至周五。
- 普通调赛由对手确认后自动生效，不再增加管理员终审。
- 跨周是否投票以及指定哪些球队由管理员裁量，不强制全部同组球队参与。
- 不在小程序中实现跨组排名/扣除小组末名成绩；相关特殊排名由赛事委员会线下处理。
- 抽签和特殊原因材料继续走邮件线下流程，V1 不把附件和抽签执行搬入小程序。

### 11.3 实施前仍需配置、但不改变产品语义的项目

以下内容应在具体赛季部署时由数据/配置提供，不属于未决产品问题：

- 当前赛季标识、起止日期、period 的准确起止时间和有序场地名称。
- 领队、普通管理员和超级管理员的账号迁移/重新绑定方案。
- 线下抽签与特殊原因调赛说明页展示的赛事官方邮箱。
- 2026 当前赛季冻结迁移快照、4 场永久不可调比赛和 4 个活动申请锁的逐场映射清单。

这些配置不得改变 A 章规定的截止规则、容量表、状态机、锁生命周期和超级管理员审计要求。
