# Q036：补全 manual checkpoint 类型合同

2026-10-24；精确单生产文件候选已提交 `9461308`，根只读非作者复核 Critical 0 / Important 0。此次仅增加 `collections.abc.Callable` 导入及 `execute_intent` 的 `checkpoint: Callable[[], None] | None = None` 注解；模块原有 postponed annotations，默认值、函数体、调用位置和异常传播不变。

生产范围只有 `packages/kernel/decision_hub_kernel/application/perpetual_manual_execution.py`。剥除该导入及该注解后完整 AST 与旧源相同，CRLF 保留；真实 Main Ruff 配置＋虚拟 Main filename 检查 exit 0 / `[]`。没有添加 cast/Any/ignore、放宽 strict、改其他 checkpoint 或改 SQL/CAS/issuer/source guard/clock/原5秒门。

## 实际同配置 BEFORE / AFTER

两次均在真实 Main cwd，使用相同原配置、同三个绝对 CLI 路径；没有用不完整 D 依赖或 Main pyproject 中另设的 Unknown 放宽项。实际命令（变量仅缩写绝对路径）：

```powershell
$m = 'E:/project/study/codex/dsh/decision-hub'
$a = "$m/packages/kernel/decision_hub_kernel/application"
$cfg = 'D:/DecisionHub-v3-development/temp/legacy-reuse/manual-single-ro-bundle-repair/pyright-main.json'
& "$m/.venv/Scripts/python.exe" -B -m pyright -p $cfg --outputjson "$a/perpetual_manual_execution.py" "$a/perpetual_paper.py" "$a/perpetual_manual_preparation.py"
```

BEFORE：3 文件 / **1130 errors / 0 warnings / 2.084s / exit 1**，stderr 空；完整消息多重集与 Q033 历史1130逐项相同。AFTER：3 文件 / **1125 errors / 0 warnings / 2.059s / exit 1**，stderr 空；三源和配置在 AFTER 前后 SHA 相同。退出非零是仍存的旧类型债，不能写 strict 通过。

按 basename＋rule＋完整 message 多重集比较：**新增0，移除5**，目标 checkpoint Unknown 诊断已消失；不只以总数下降判断结果。移除的实际旧源位置如下：

| BEFORE 行 | rule | 消失的消息 |
| --- | --- | --- |
| 818 | reportUnknownParameterType | `Return type is unknown` |
| 818 | reportUnknownParameterType | checkpoint 参数为 `Unknown | None` |
| 818 | reportMissingParameterType | checkpoint 缺少类型注解 |
| 843 | reportUnknownArgumentType | 传给 lease 的 checkpoint 为 `Unknown | None`（原目标） |
| 845 | reportUnknownArgumentType | 传给 prepare_manual_execution 的 checkpoint 为 `Unknown | None` |

同文重复诊断不能仅凭贪心配对推定位置。位置收据按唯一 import 增行对齐，确认上述5项；例如旧 observe_episode:1528 的 checkpoint 提示仍在新1529，没有改该接口。原 `main-diagnostic-delta.json` 保留消息多重集结果，精确位置以 [diagnostic-location-attribution.json](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-checkpoint-type-repair/diagnostic-location-attribution.json) 为准。

## 原件、Main 集成与边界

原始 argv、时钟、exit、完整 JSON/stderr、源/config pins、AST、diff、Ruff 和非作者复核均保留在 [Q036 evidence](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-checkpoint-type-repair/scope-final.json) 同目录；BEFORE/AFTER 为 `main-before-strict.json` / `main-after-strict.json`，汇总为 `main-before-result.json` / `main-after-result.json`，差量为 `main-diagnostic-delta.json`。初始 scope 保持原字节，AFTER 是后续新增原件。

根于 **2026-10-24T09:10:11.533365Z** 核旧 `1ac620b1…3b77`、新 `d83c1227…621fb`、备份及8047/3097无监听后精确接回 Main 本地源码，[integration receipt](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-checkpoint-type-main-integration/receipt.json) 保留。这不是服务、profile 或 dist 部署，没有实际数据库变更。

本轮不运行业务测试或 whole-project 检查；纯注解不编写镜像实现的测试。原Q031“19项”不是全部消息新增口径，分类更正见 [只读类型债审计](D:/DecisionHub-v3-development/temp/legacy-reuse/q031-q033-typing-debt-readonly.md)；这些其它问题未据本卡宣称清除。`intent_id` 等 Unknown、其他 checkpoint 和既存1125条仍需按实际合同分批处理；sameRun原5秒门、Host/PAPER和长期真实验收未因此通过，项目目标保持 ACTIVE。
