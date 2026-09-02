# Crypto Macro Pack

状态：`candidate / R2-R-00..06E complete / Fixed active`
版本：`crypto_macro.v1`
产品扩展：`decision.v1`

本目录是加密宏观决策领域的版本化资产，不是独立服务，也不是 DSH
会话目录。它声明研究方法、证据要求、角色能力、工具绑定、确定性
Gate 和评测规则；业务账本、PIT、发布权和历史迁移仍由 Decision
Extension 与 Kernel 持有。

## 目录职责

- `pack.yaml`：Pack 唯一入口和预算，必须通过 `DomainPackManifest` 校验。
- `doctrine/`：事实、推论、情景和根因链语义。
- `evidence/`：最低证据包、来源优先级、鲜度和置信度上限。
- `profiles/`：Manager/Reviewer 的声明式角色配置，不包含业务 Python。
- `tools/`：逻辑能力到 DSH/MCP/Provider/Replay adapter 的候选绑定；默认拒绝执行。
- `gates/`：代码 Gate 应执行的分周期约束，不能由 LLM 自行放宽。
- `evaluations/`：回放、shadow 和前瞻观察的评分标准。
- `fixtures/`：PIT 输入和已知失败基线，不保存密钥或原始 Provider payload。

## 修改约束

1. 先修改 canonical schema 或本 Pack policy，再生成镜像和补测试；禁止在
   Graph、DSH profile、API 或前端复制同一规则。
2. `audit_status: candidate` 的工具不能因写入本目录就自动启用；必须经过
   capability audit、owner enable 和 readiness canary。
3. 新领域应新建 Pack，不得在本目录加入 PPT、A 股、美股或通用平台逻辑。
4. 新增来源先作为受限工具绑定；只有真实使用证明需要稳定精确字段后，
   才沉淀 typed connector。
5. 修改规则时同步维护本文件、Stage 状态、测试证据和 `CHANGELOG.md`。

## 验证

```bash
./.venv/bin/python -m tools.contract_codegen check
./.venv/bin/pytest tests/contracts tests/research
```

DSH 精确版本和 restricted profile 已在 R2-R-01 锁定；R2-R-04 已完成
durable candidate path、自动 discovery、scheduled recheck 和进程恢复。工具仍按
manifest 默认拒绝；R2-R-06A 只批准了实测 `web.search`，并未修改默认 allowlist。
R2-R-06B 的 12-case 数据集不含未来标签。R2-R-06E 已完成 value gate，但结论为
`retain_baseline / pending_owner_review`；不声称 candidate 已 active、预测更准或可以盈利。
