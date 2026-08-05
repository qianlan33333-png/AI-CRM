# ADR: Trusted Release Governance and Forward-Compatible Rollback

- 状态：Accepted，分阶段启用
- 日期：2026-08-04
- 所有者：Platform / Release Governance / Insights Data Health
- 取代：`2026-07-17-cross-repository-production-promotion.md` 中“main push CI 成功后直接部署”的触发决策；旧入口在三次双槽成功前仅作为禁用的人工回退入口保留

## 背景

旧链路把代码验收、生产环境状态和部署后数据健康混在一次有生产写权限的
事务中。PR 可以全绿，但 migration head、历史外部效果或 runtime owner 只在
代码切换后才被发现。失败时应用代码会回滚，数据库不会降级；旧运行时又把
任何 ahead revision 判成不健康，从而产生“服务仍可用、发布已回滚、readiness
仍失败”的半发布语义。

## 决策

普通发布采用一个持有全局串行锁的可信状态机：

1. `CI Fast` 验证候选代码、当前 migration head、release manifest、架构与测试；
2. `production-readiness` 只运行当前已部署的受信代码和受限只读凭证，不 checkout 或执行 PR 内容；
3. `merge-controller` 没有生产凭证，只在 PR head、base、tested tree 和生产 SHA 均未漂移时创建 merge commit；
4. `release-and-deploy` 只部署该 exact merge SHA 到非活动槽；
5. 候选槽通过内部验收后原子切流，再启动该槽的唯一 worker/timer；
6. 下一条 PR 只有在该 SHA 成为生产 SHA 后才能合并。

若 merge 后出现不可预测的基础设施故障，生产继续服务旧 SHA，并把发布状态
标记为 `main_ahead_of_production`。队列暂停，后续 PR 不得继续自动合并。
普通发布不依赖 `GITHUB_TOKEN` 产生的二次 push/workflow 事件。

GitHub Free 私有仓库的仓库所有者仍能手工合并或直接 push。这是明确保留的
残余风险；任何未通过可信状态机产生的 main commit 标记为
`unattested_main_commit`，不得自动部署。

## 唯一门禁合同

`deploy/release_gate_manifest.json` 是 gate ID、阶段、责任域、网络和写入策略、
CI 合同及修复动作的唯一声明源。CI、生产预检、部署、callback proof 和回滚
验证不得保存独立的 migration head、检查数量或测试文件指针。

每个结果使用 `ReleaseGateResult v1`，至少包含 decision、reason code、实际值、
阈值、责任域、候选/生产 SHA、候选归因、时间、修复动作、重跑方式和脱敏证据。
PR 上只更新一条合并决策评论，并明确区分：

- 候选代码问题：修改 PR；
- 当前生产 blocker：不合并，走运营或数据修复；
- 已分类业务终态：warning，不阻止发布；
- 恢复或 contract migration：进入人工恢复通道。

## 数据健康与候选归因

外部效果健康拆成 retry progress、未知 terminal 和未知 blocked 三个 gate：

- 未到重试时间为正常；
- 到期但在 SLA 内为 warning；
- 超过 SLA、worker 无进展、未知 terminal 或未知 blocked 为 block；
- 通过严格供应商与持久化证据确认的业务终态（包括企微 41051）为审计 warning，禁止自动重放。

每次发布在 mutation 前保存基线。切流后的历史 warning/blocker 不归因给候选；
只有候选 `processed_release_sha` 新产生或新处理出的 blocker，以及启动、路由、
schema、owner 或 exact-SHA 回归，才能触发回切。

候选 `/api/system/health` 的同步队列聚合只提供有界观测，不重复承担 data-health
发布判定。该查询因 statement/lock budget 耗尽时输出
`queue_probe_budget_exhausted` warning，并指向已在切换前执行的
`data_health_registry` 权威门禁；未知 SQL、连接、schema 错误仍 fail-closed。

## Migration 与回滚

`deploy/migration_compatibility.json` 和 `schema_release_compatibility` 共同定义线性
兼容链：

- 候选运行时必须匹配自己的 exact head；
- 上一运行时可以把同一 compatibility epoch 内、标记为 previous-runtime-compatible 的 expand revision 识别为 `compatible_ahead`；
- 自动 migration 只能是 expand-only、事务化且与上一运行时兼容；
- contract migration 必须使用两次发布协议和人工确认；
- 自动回滚永不执行 schema downgrade。

0170 之前的既有历史迁移在本决策中视为 baseline；策略从 release-governance
foundation revision 开始前向执行，不逐条重写历史声明。

## 同机双槽

服务器保留不可变 Blue/Green release slot：Blue Web/Callback 使用 5001/5002，
Green 使用 5011/5012。候选槽启动时 worker/timer 关闭，active-slot guard 禁止
外部副作用。内部 health、system health、管理页、callback 和 SHA 通过后，
`nginx -t` 并原子更新 upstream 与 active marker；随后先停旧单例消费者，再启
新槽 worker/timer。

回切只恢复 Nginx、active marker 和单例服务，不降级 schema。上一槽至少保留
到下一次成功发布。启用前必须证明双槽同时运行后仍有至少 30% 内存余量。

## 恢复通道

只有当前生产 blocker 必须由候选修复、main 已领先生产、release control-plane
变更或 contract migration 才进入 `production-recovery`。输入必须包含 PR、完整
head SHA、声明 blocker ID 和确认短语；完整 CI 仍是前置条件。真实队列重排、
生产数据修复和 provider retry 使用独立、可预览、可审计的运维工作流，不能
伪装成代码部署。

## 启用顺序

1. 确认当前应用与 schema exact baseline；
2. 上线 manifest、结构化结果、数据健康拆分与归因，shadow 三次发布；
3. 上线 compatibility ledger 与双槽，人工执行并连续成功三次；
4. 启用可信自动合并控制器，停用旧 main-push promotion 监听。

任一阶段未达到验收次数，下一阶段不得启用。代码存在不等于控制面已激活。
