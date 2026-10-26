# 技术方案：[邀请码直开 Beta 与异常 Guest 清理修复]

---

## 0. 元信息

| **字段** | **内容** |
| --- | --- |
| **文档状态** | Draft |
| **作者** | @Codex |
| **创建日期** | 2026-06-25 |
| **最后更新** | 2026-06-25 |
| **Reviewers** | @Platform @Backend |
| **关联链接** | `docs/platform-invite-backend-design.md`、`docs/platform-invite-claim-access-plan.md`、`platform/web/src/lib/register-handler.ts`、`platform/web/src/lib/platform-access-invite-open.ts` |

---

## 1. TL;DR

当前线上“用户邀请码注册”链路与产品语义不一致：有效用户邀请码注册后并不总是直接成为 `beta_user`，而是可能先落成 `guest`，再因 AI Relay 开通失败残留为异常账号。  
本方案将邀请码链路收敛为两条明确语义：无邀请码注册可成为 `guest`；有效用户邀请码注册或补码必须原子化开通为 `beta_user`。  
实现上统一注册与补码入口的开通编排，禁止 `200 + guest` 的中间态，并对现有 `guest + user邀请码` 异常账号做审计删除。  
本地与线上邮箱确认门槛不同只影响“是否更容易走到开通步骤”，不改变最终语义：一旦用户邀请码有效，最终结果仍必须是 `beta_user`。  
影响范围覆盖 Web 注册接口、补邀请码接口、AI Relay 开通编排、历史数据清理、测试与上线治理。  

---

## 2. 背景与问题

当前产品规则已经明确变更为：

- 无邀请码注册：允许登录为 `guest`。
- 有效用户邀请码注册：不需要审核，必须直接成为 `beta_user`。
- `guest` 后续补充有效用户邀请码：必须直接升级为 `beta_user`。

但线上现状与此不一致。

### 2.1 线上已确认事实

只读排查结果显示：

- `profiles` 共 155 条，其中 `beta_user` 66 条，`guest` 89 条。
- `guest + invite_code_used` 共 43 条。
- 其中 `guest + user邀请码` 共 5 条。
- `guest + official 邀请码` 共 2 条。
- 其中 1 条 `guest` 甚至已经写入了真实 `user_relations`。

最近注册事件也已直接证明故障存在：

- `events.type='funnel.registered'` 中，多条用户邀请码注册事件的 `invite_code_type='user'`。
- 同时 `resource_status='guest'`。
- `open_failure_reason='open_access_failed'`。
- `open_failure_error` 明确指向 `AI Relay ensure failed status=500`。

这说明：

1. 邀请码识别成功。
2. 用户注册成功。
3. 自动开通步骤失败。
4. 接口仍返回成功，并保留 `guest` 账号。

本地测试看起来正常，主要是因为本地环境在邮箱确认与旧激活路径上更容易走通；但这只是放大器，不是根因。真正需要修的是“用户邀请码成功后必须原子化开通”的链路本身。

### 2.2 当前问题本质

当前实现的核心问题不是“邀请码不可用”，而是“注册成功”和“资源开通成功”被拆成了两步，且第二步失败后没有整体失败。

在 [register-handler.ts](D:/PROJECT/star-coplay/platform/web/src/lib/register-handler.ts) 中，当前行为是：

1. 创建 Auth 用户。
2. 先写 `profiles.user_type='guest'`。
3. 若存在用户邀请码，再调用 `openPlatformAccessByUserInvite(...)`。
4. 若开通失败，仅记录事件，最终仍返回成功响应。

这会持续制造一类错误语义：

- 用户侧认知：我用了邀请码注册。
- 系统实际状态：我只是一个能登录的 `guest`。

### 2.3 为什么现在必须处理

- 产品规则已明确废弃审核心智，“有效用户邀请码 = 直接开通”已是当前设计，而不是未来设计。
- 现在线上还在持续制造 `guest + user邀请码` 残留账号。
- 这会污染邀请关系、测试数据、额度判断和客服解释成本。
- 如果不先收口，历史清理越晚越难，后续补码、奖励、邀请码额度都将继续失真。

---

## 3. 目标与非目标

### Goals

- 100% 保证“有效用户邀请码注册”只有两种结果：成功成为 `beta_user`，或整体失败；不再产生新的 `guest + user邀请码` 残留。
- 100% 保证“guest 补充有效用户邀请码”只有两种结果：成功升级为 `beta_user`，或保持原 `guest`；不再出现写入邀请码但未开通的中间态。
- 100% 清理当前线上 `guest + user邀请码` 异常账号，执行语义为审计后删除，不保留脏关系。
- 100% 保留“无邀请码注册 = guest 可登录但无 Mod 链接密钥 / 无 AI Relay 权益”的现有能力。

### Non-Goals

- 不重做完整权限系统，不新增独立 `platform_access_grants` 作为本期事实源。
- 不处理 `official` 邀请码的大批量历史邀请语义，本次只聚焦 `type='user'` 邀请码。
- 不试图自动修复所有历史邀请奖励歧义；删除的异常 guest 账号不参与奖励补发。

---

## 4. 方案概览

**核心一句话**：把“用户邀请码注册/补码”统一收敛为一条原子开通链路，成功即 `beta_user`，失败即整体失败或保持 `guest`，并清除现有 `guest + user邀请码` 异常残留。

**系统图**：

暂时无法在飞书文档外展示此内容

可用文字版心智模型表示为：

- 入口 A：无邀请码注册
  - 创建账号
  - 建立 guest 基础资料
  - 返回可登录 `guest`

- 入口 B：用户邀请码注册
  - 校验邀请码
  - 创建账号
  - 执行统一开通编排
  - 成功则 `beta_user`
  - 失败则补偿删除账号并返回失败

- 入口 C：guest 补邀请码
  - 校验邀请码
  - 执行同一条开通编排
  - 成功则升级 `beta_user`
  - 失败则保持 `guest`

---

## 5. 方案详解

### 5.1 模块 A：注册语义拆分

**子问题**：如何同时支持“无邀请码注册为 guest”和“有邀请码注册必须直开 beta_user”，且两条链路互不污染。

**关键决策**：按是否提供有效用户邀请码，在注册入口处分叉为两条不同的事务语义，而不是统一先写 guest。

具体规则：

- 无邀请码注册：
  - 允许创建 Auth 用户与业务用户。
  - `profiles.user_type='guest'`。
  - 不创建 AI Relay account。
  - 不生成 Mod 链接密钥可用权限。
  - 允许后续走补邀请码升级。

- 有效用户邀请码注册：
  - 不允许返回成功 guest。
  - 成功条件必须包含：
    - `profiles.user_type='beta_user'`
    - AI Relay account 开通成功
    - 邀请关系绑定成功
    - 新用户自己的邀请码生成成功
  - 奖励与名额副作用完成或按幂等成功确认
  - 任一步失败即整体失败。

补充说明：

- 线上旧的邮箱激活页/激活接口仍可作为兼容入口，但它不应再成为用户邀请码主路径的语义依据。
- 无邀请码注册仍可保留 guest 语义，不与“邀请码直开 beta_user”冲突。

实现要求：

- 修改 [register-handler.ts](D:/PROJECT/star-coplay/platform/web/src/lib/register-handler.ts)：
  - 去掉“先写 guest，再尝试升级”的默认行为。
  - 改为：
    - 无邀请码：走 `createGuestRegistration(...)`
    - 有用户邀请码：走 `registerAndOpenByUserInvite(...)`

### 5.2 模块 B：统一开通编排

**子问题**：注册带邀请码与 guest 补邀请码如何共享同一套成功标准，避免两套逻辑再次漂移。

**关键决策**：复用并增强 `openPlatformAccessByUserInvite(...)`，使其成为唯一的用户邀请码开通编排服务。

统一开通编排必须完成以下步骤：

1. 校验 `type='user'` 邀请码有效性。
2. 校验是否自邀请。
3. 校验是否还有剩余名额。
4. 创建或修复 AI Relay account。
5. 升级 `profiles.user_type='beta_user'`。
6. 绑定 `user_relations`。
7. 提交邀请码使用名额。
8. 生成新用户自己的用户邀请码。
9. 触发奖励发放。

关键改动：

- 对注册入口：
  - 如果上述任一步失败，回滚或补偿删除本次新建账号。
- 对 guest 补码入口：
  - 如果任一步失败，保持原 guest，不写半成品关系。

### 5.3 模块 C：失败补偿与异常边界

**子问题**：当前最大问题不是失败本身，而是失败后还保留脏数据。

**关键决策**：注册带邀请码场景下，失败补偿以“删除新建账号”为主；guest 补码场景下，失败补偿以“保持原状”为主。

注册带邀请码失败补偿语义：

1. 如果 Auth 用户尚未创建成功，直接返回失败。
2. 如果 Auth 用户已创建、但业务开通失败：
   - 删除 `profiles`
   - 删除 `users`
   - 删除 `user_relations`
   - 删除 `auth.users`
   - 删除可能已写入的 `ai_relay_accounts`
   - 不回滚 `invite_codes.used_count`，除非已确认本次成功 commit 过名额
3. 对所有补偿动作写审计日志。

guest 补码失败语义：

- 不升级 `user_type`
- 不写入真实邀请关系
- 不保留脏邀请码绑定状态
- 返回明确错误给前端

### 5.4 模块 D：历史异常账号清理

**子问题**：线上已存在 `guest + user邀请码` 异常账号，如何与新规则保持一致。

**关键决策**：对这类异常账号不做“清空邀请码继续保留”，也不做“继续猜测补开通”；按你当前决策，统一视为异常残留，审计后删除。

本次删除范围：

- 全部 `profiles.user_type='guest'`
- 且 `users.invite_code_used` 指向 `invite_codes.type='user'`
- 以及你已确认的测试账号名单

删除内容：

- `profiles`
- `users`
- `user_relations`
- `auth.users`
- `ai_relay_accounts`（若存在）

保留内容：

- `events` 审计记录保留
- 不回滚 `invite_codes.used_count`
- 不回滚官方邀请码历史使用量

当前线上只读数据：

- `guest + user邀请码` 共 5 个账号
- 仅涉及 2 个用户邀请码：
  - `SC-DP77-TWA5`：2 个 guest
  - `SC-U5LV-YATW`：3 个 guest

### 5.5 模块 E：AI Relay ensure 500 故障收口

**子问题**：当前实际炸点在 `ensureAIRelayAccount -> backend /internal/ai/relay/users/:id/ensure`。

**关键决策**：先修失败可观测性，再修开通逻辑。

要做的事：

1. backend 内部接口必须把所有可预期异常统一转换为结构化 JSON，而不是裸 `500 Server got itself in trouble`。
2. 对 `ensure_account()` 内部每个关键步骤打阶段化日志：
   - `resolve_group`
   - `ensure_newapi_user`
   - `ensure_newapi_user_access_token`
   - `ensure_default_token`
   - `persist_ai_relay_account`
3. Web 侧保留 `open_failure_reason/open_failure_error`，但新增更细粒度阶段字段。
4. 对 backend 500 增加错误码分类，区分：
   - NewAPI transport / timeout
   - NewAPI payload invalid
   - config invalid
   - DB persist failed

### 5.6 接口契约

| **接口** | **输入** | **输出** | **错误码** | **幂等性** |
| --- | --- | --- | --- | --- |
| `POST /api/register` | `email,password,invite_code?` | 无邀请码：`guest`；有用户邀请码：`beta_user` | `invalid_invite_code`,`invite_limit_reached`,`open_access_failed`,`register_compensation_failed` | 同邮箱不可重复；邀请码开通幂等 |
| `POST /api/me/invite-code/claim` | `invite_code` | 成功后返回 `beta_user` 最新状态 | `not_guest`,`invalid_invite_code`,`invite_limit_reached`,`open_access_failed` | 对已成功开通用户幂等 |
| `POST /internal/ai/relay/users/:id/ensure` | `email,display_name,...` | `ai_relay_account` | `NEWAPI_*`,`AI_RELAY_CONFIG_*`,`DB_*` | 对同 `user_id` 幂等 |
| `POST /api/admin/invite-users/cleanup` 或脚本 | 审计名单 / 查询条件 | 删除结果 | `user_not_found`,`delete_failed` | 可重复执行 |

### 5.7 状态机 / 数据流

用户状态机：

- `unregistered`
- `guest`
- `beta_user`

合法迁移：

- `unregistered -> guest`：无邀请码注册
- `unregistered -> beta_user`：有效用户邀请码注册
- `guest -> beta_user`：guest 补邀请码

禁止状态：

- `guest + valid user invite_code_used + 未开通`
- `guest + 真实 user_relations`
- `beta_user 注册成功但无必要开通副作用`

---

## 6. 备选方案

| **方案** | **描述** | **优势** | **劣势** | **为什么没选** |
| --- | --- | --- | --- | --- |
| **方案 A（采用）** | 用户邀请码注册/补码统一走原子开通；异常 guest 审计后删除 | 语义最清晰；不再制造脏数据；与当前产品规则一致 | 需要实现补偿删除和更多测试 | - |
| 方案 B | 保留当前“注册成功先 guest，失败后用户再补码” | 改动较小 | 持续制造脏数据；用户心智错误；客服成本高 | 不符合“有效邀请码即 beta_user” |
| 方案 C | 失败后自动后台重试开通，不对用户报错 | 用户表面感知好 | 后端状态复杂；重试窗口内仍有中间态；更难审计 | 仍保留不一致状态，不符合本次收口目标 |

---

## 7. 风险与权衡

| **风险** | **影响范围** | **触发条件** | **缓解措施** |
| --- | --- | --- | --- |
| 注册补偿删除不完整 | 继续残留半成品账号 | Auth 已创建但业务删除失败 | 补偿动作阶段化；失败写告警；提供后台重扫脚本 |
| AI Relay / NewAPI 波动导致注册失败率上升 | 邀请码注册转化下降 | backend / NewAPI 短时不稳定 | 增加明确错误提示；先修可观测性；灰度上线 |
| 删除脚本误删正式账号 | 用户损失与投诉 | 过滤条件写错 | 先导出审计名单；人工确认；仅删除 `guest + type='user' invite_code_used` |

**关键权衡**：

本方案选择“强一致语义优先”，牺牲的是带邀请码注册时的可用性缓冲。也就是说，过去用户邀请码注册即使开通失败也还能留下一个可登录 guest；现在改为失败即失败。这会让短期注册错误更显性，但这是正确的产品语义，因为当前设计已经明确“有效用户邀请码 = 直接 beta_user”，不是“先注册再说”。

另一处权衡是对历史异常账号不做复杂自动修复，而是按你的决策统一删除。这样放弃了“尽量保住测试残留”的宽松性，但显著降低了名额、关系、奖励账本的歧义。

---

## 8. 失败模式与容错

| **组件** | **失败场景** | **影响** | **降级/容错策略** | **监控指标** |
| --- | --- | --- | --- | --- |
| Web Register API | 用户邀请码开通失败 | 注册失败 | 补偿删除新建账号；返回明确错误 | `register_with_user_invite_fail_rate` |
| Backend AI Relay ensure | 返回裸 500 / NewAPI 异常 | 无法开通 beta_user | 统一结构化错误；日志分阶段 | `ai_relay_ensure_5xx_rate` |
| NewAPI | 管理接口超时或 5xx | 无法建用户/发 token | 明确失败，不创建 guest 残留 | `newapi_admin_http_error_rate` |
| 清理脚本 | 删除中途失败 | 数据半删 | 按 user_id 幂等重试；失败名单重跑 | `invite_guest_cleanup_failed_count` |

---

## 9. 测试计划 + DoD

### 测试计划

| **测试类型** | **覆盖范围** | **关键用例** |
| --- | --- | --- |
| 单元测试 | `register-handler`、`openPlatformAccessByUserInvite`、补偿删除逻辑 | 带邀请码成功为 `beta_user`；带邀请码失败时整体失败；无邀请码仍为 `guest` |
| 集成测试 | `/api/register`、`/api/me/invite-code/claim`、backend ensure | NewAPI 失败时不留下 guest 残留；guest 补码成功升级 |
| E2E 测试 | 注册页、我的页补邀请码 | 无邀请码注册后可登录 guest；有邀请码注册直接进 beta；guest 补码成功 |
| 数据迁移测试 | 清理脚本 | 只删 `guest + user邀请码`；不删 `official` 邀请码用户；重复执行幂等 |

### Definition of Done

- [ ] `POST /api/register` 满足两种明确语义：无邀请码为 guest；有效用户邀请码成功即 beta_user，失败即整体失败。
- [ ] 不再产生新的 `guest + user邀请码` 账号。
- [ ] `POST /api/me/invite-code/claim` 与注册带邀请码共用同一开通编排。
- [ ] backend `/internal/ai/relay/users/:id/ensure` 不再返回裸 500，所有已知失败均带结构化错误码。
- [ ] 线上现有 `guest + user邀请码` 异常账号完成审计与清理。
- [ ] 监控指标与告警已接入。
- [ ] 本文档与相关 README / API 行为说明同步更新。

---

## 10. 上线计划

### 阶段划分

| **阶段** | **范围** | **验证内容** | **回滚条件** |
| --- | --- | --- | --- |
| 阶段 1：修 backend 可观测性 | backend ensure 错误结构化、日志 | 能区分失败阶段与错误码 | 出现新增 5xx 峰值 |
| 阶段 2：修注册与补码语义 | Web register / claim 新编排 | 无邀请码 guest 正常；带邀请码失败不留残留 | 注册成功率异常下降 |
| 阶段 3：只读审计 | 导出 `guest + user邀请码` 名单 | 名单与预期一致 | 名单出现非预期正式用户 |
| 阶段 4：执行清理 | 删除异常 guest | 删除幂等，数据一致 | 误删风险未消除 |
| 阶段 5：全量发布 | 完整新链路 | 连续观察失败率与清理后残留数 | 新增残留再次出现 |

### 数据迁移

本次不做传统 schema migration，做数据修复脚本：

1. 只读导出 `guest + type='user' invite_code_used` 账号列表。
2. 输出删除预览。
3. 执行按 `user_id` 的幂等删除。
4. 删除后复跑审计 SQL，确认归零。

### 回滚方案

- 代码回滚：
  - 回退 register / claim 新逻辑。
- 运行回滚：
  - 暂时关闭用户邀请码注册入口。
  - 保留无邀请码 guest 注册。
- 数据回滚：
  - 清理脚本必须先产出删除名单备份。
  - 若误删，只能基于备份和审计记录人工恢复，不承诺自动回滚。

---

## 11. 影响评估

| **维度** | **评估** |
| --- | --- |
| **业务影响** | 用户邀请码语义从“可能 guest 残留”收敛为“成功即 beta_user”；减少用户困惑与客服解释成本 |
| **用户范围** | 影响所有新用户邀请码注册、所有 guest 补邀请码用户，以及当前 5 个 `guest + user邀请码` 异常账号 |
| **工程工时** | 约 3-5 人天，含排障收口、Web/Backend 修改、测试、审计脚本、上线验证 |
| **长期维护成本** | 中等偏低；核心收益是语义统一，后续维护成本显著低于当前双态残留 |
| **依赖方 / 团队** | Platform Web、Backend、运营/测试协作确认清理名单 |

---

## 12. 未解问题 Open Questions

- 是否需要在 Admin 后台增加“按用户邀请码注册失败详情”查看页，而不是只查 `events`。
- 对未来正式用户如果再次出现“带邀请码注册失败”，是否需要前端提示更产品化的错误文案，而不仅是技术错误。
- 删除异常 guest 账号后，是否需要对对应邮箱做短期防重复注册提示优化，避免测试人员误以为账号仍有效。

---

## 提交评审前自检清单

- [x] TL;DR 可在 30 秒内读完并理解
- [x] Goals / Non-Goals 明确且可验证
- [x] 当前线上事实与问题陈述一致
- [x] 接口语义已区分“无邀请码 guest”和“有邀请码直开 beta_user”
- [x] 方案包含失败补偿与异常清理
- [x] 方案包含测试、上线、回滚和风险
- [x] 已明确只处理 `type='user'` 邀请码异常账号
- [x] 已记录当前仍需评审确认的开放问题
