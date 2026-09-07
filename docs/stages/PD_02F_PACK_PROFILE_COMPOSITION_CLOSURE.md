# PD-02F Pack / Profile / Composition Closure

版本：`PD-02F-2026-09-04.v1`  
状态：`completed / engineering exit passed`  
上游：`PD-02C..02E`  
下游：`PD-02G offline full replay and canary entry`

## 1. 目标

把 `crypto_macro` 的能力声明闭合为一条可检查的配置链：

```text
Domain Pack capability_refs
  = Provider binding manifests
  = Role profile allowed_tools
  -> composition registry / stable Router
  -> DSH research tool boundary
  -> 可供后续 PD-05 readiness 投影消费的 capability state
```

本卡解决“代码已经有 adapter，但某个 YAML、Role 或启动器没有接上”的配置漂移。它不增加
新的业务能力，不改变 DSH Agent Loop，不改变 Hub 账本，不把 candidate provider 变成 live。

## 2. 固定规则

- `capability_id` 是跨层唯一键；供应商名只能出现在 `ProviderRoute` 和 composition registry。
- `pack.yaml` 是能力集合真源，`bindings.yaml` 是 route/授权/域/成本真源，Role profile 只声明
  自己允许调用的 capability。
- `audit_status=approved` 且 `license_status=approved` 才可能执行；`candidate/review_required`
  必须保留在 manifest 中但在 Gateway 前拒绝。
- `run-product.sh` 的默认 allowlist 只启用已批准、无额外许可阻断的能力；不会因为本卡把
  Search、Tavily 或免费 proxy 无条件打开。
- `compose_capability_adapters()` 必须为每一个有 route 的 capability 装配恰好一个 stable
  Router；direct adapter 与 routed adapter 不能重复注册。
- 本卡只做配置/装配检查和离线测试；不联网、不读取聊天中暴露的 key、不进行 live canary。

## 3. Red 基线

之前测试只分别检查 Pack 或 derivatives Router，没有同时验证：

1. Pack capability 是否都有 binding；
2. Role 的 `allowed_tools` 是否引用了真实 capability；
3. 有 route 的 capability 是否有 composition adapter；
4. approved/candidate 状态是否在 Gateway 入口保持一致；
5. DSH profile 的 Hub tool 是否保持单一入口。

这种缺口会让页面看起来“有插件”，但运行时可能只加载部分能力，或把未授权 provider 错误地
当作可用。

## 4. 实现

### F1. 配置一致性测试

新增 `tests/contracts/test_pd02f_pack_profile_composition.py`，从真实 Pack、bindings、三个
Role profile 和 DSH preset 读取配置，断言：

- capability 集合完全相等；
- 每个 Role 的 allowed tool 都是 Pack capability 或 DSH 官方控制工具；
- 每个带 `provider_routes` 的 capability 都有注册 adapter；
- candidate capability 不能被默认 live allowlist 启用；
- DSH preset 只有一个 Decision Hub research capability tool。

### F2. Composition helper

保持 `apps/research_mcp/main.py` 为唯一 composition root，继续使用既有
`ProviderCapabilityRouter`。不在 Core、Graph、DSH profile 或前端增加供应商分支。测试使用
`ReplayResearchArchive` 和 fake adapter，不触网。

### F3. 文档和状态投影

同步 provider adapter、MCP、Pack、当前状态、路线图和执行日志，明确：

```text
composition_exit = pass
provider_live_exit = independent / blocked
```

## 5. BDD/TDD 验收

```gherkin
Scenario: Pack 与 binding 集合一致
  Given Pack 声明一组 capability_refs
  When 读取真实 bindings.yaml
  Then 两组 capability_id 完全相等
  And 缺失或额外能力立即失败

Scenario: candidate provider 不被默认启用
  Given macro intraday/expectation route 是 candidate/review_required
  When 用默认 live allowlist 创建 Gateway
  Then candidate route 不执行
  And 不产生 Evidence/Fact

Scenario: routed capability 只有一个稳定 Router
  Given 一个 capability 声明多个 provider routes
  When composition root 装配
  Then 结果只有一个该 capability 的 Router
  And 不存在 direct/routed 重复注册

Scenario: Role 不得越权
  Given Role profile 允许的工具集合
  When 与 Pack 和 DSH tool catalog 比较
  Then 不存在未知 capability 或未声明的业务写工具
```

## 6. 退出门

- [x] Pack/binding/Role capability 集合一致；
- [x] routed capability 均能解析到唯一 adapter-ref registry；
- [x] candidate/review_required capability 不进入默认 live execution；
- [x] DSH profile 仍只有官方 Harness + 单一 Hub research tool 边界；
- [x] 配置断言直接读取启动器默认 allowlist，并与 approved manifest 比较；
- [x] composition 测试离线通过；
- [x] 不修改 Core/Graph/UI/账本，不引入第二套插件系统；
- [x] 全量质量门和前端既有测试保持通过。

## 7. 保留项

本卡通过不代表宏观实时 Provider 已购买、已授权、已通过 canary；也不代表报告事实充分、预测
准确或产品可交易。真实 Provider 仍必须在单独 canary 中通过 domain、license、PIT、字段、offset、
cost 和稳定性检查，产品状态继续允许 `research_only/provider_blocked`。

本卡也没有新增人类可读 readiness UI。它只保证后续 PD-05 能从一致的 Pack/manifest/runtime
状态投影 readiness；若页面尚未展示，不能把本卡描述成用户可见验收。

下一唯一阶段为 `PD-02G`，只做全 capability replay、启动/readiness 演练和显式 live canary 入口
核验；不在 02G 中偷偷扩大业务范围。
