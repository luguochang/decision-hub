# Q040：recipe 路径集合的精确类型

2026-10-24；状态为 D 单行候选已验证，等待根非作者复核/采纳；尚未接 Main，不是完整项目 strict 或业务验收。

## 实证与唯一改动

关闭旧政策的 Unknown 抑制后，fresh strict 实际检查 Main recipe `09c40117…45a3c2` 为3条错误，新 archive-contract test 为0；逐字 `5ce02ead…0f9d16` 基线也同3条。完整规则/消息无新增或变化，3项都源于 `prepare_graph_recipe_manifest_v2` 内空 `paths` 集合：append Unknown1、tuple(paths) 参数 Unknown2。原 RED 为3文件6 errors/exit1（包括可选旧基线），实际 Main 两目标合计3。

仅 D `packages/kernel/decision_hub_kernel/application/research_graph_recipe.py` 一行改为 `paths: list[tuple[str, str]] = []`。每个 append 实际写入 `(item.path, str(path.resolve(strict=True)))`，既有 `PreparedGraphRecipeManifest.source_paths` 本就要求该 pair 类型；没有改变值、控制流、默认值、源校验或数量/时间门。原09c401完整字节已独占备份。

候选 SHA `8d79a1994d9437d596d8bc42eaf52809cdaacaf561c1bab618a3e4c02e452487`，35782B。CRLF保留；去掉这唯一局部变量注解后完整模块 AST 与旧源相同。只 compile、未 exec：递归可执行字节码/常量/名称/flags/异常表等字段相同；原始 code 对象比较为 false，不能冒称含位置元数据的原始对象逐字相等。

## 本轮实际 GREEN

沿用 RED 的同一 fresh JSON 配置：`typeCheckingMode=strict`，五个 `reportUnknown*` 规则显式 error，无 extends、无 Main pyproject suppression。绝对 CLI 路径明确纳入分析，include为空不意味着未分析。

实际命令为 Main Python `-B -m pyright --project D:/DecisionHub-v3-development/evidence/legacy-sdk-review/recipe-archive-all-unknown-diagnostic/pyright-all-unknown.json --pythonpath <Main>/.venv/Scripts/python.exe --outputjson <本轮证据>/candidate.py <Main>/tests/research/test_graph_recipe_archive_contract.py`。

- 真实结果：**2 files / 0 errors / 0 warnings / 1.499s / exit0**，diagnostics为空；不是沿旧 suppressed policy 换取0。
- `candidate.py` 是 D 候选逐字静态快照，依赖实际 Main；另一目标是实际 Main 新测试。快照不执行、没有伪造 `__file__`，也不能充当 Main recipe 已更新后的最终证明。
- Ruff：实际 Main 配置＋虚拟 Main filename，候选 stdin，exit0 / `[]`。没有产品 ignore/Any/cast 或新增配置抑制。
- Main recipe 仍09c401、新测试及 fresh config 前后 SHA 不变；旧政策、RED、Q039构建来源与日志未改。没有重跑28测试、业务/性能测量或 build。

原 RED 及逐项比较见 [全Unknown诊断](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/recipe-archive-all-unknown-diagnostic/diagnostic-comparison.json)；本轮 backup/diff/AST-code等价、实际 argv/exit/时钟、完整输出与源pins位于 `D:/DecisionHub-v3-development/evidence/legacy-sdk-review/recipe-strict-type-repair/`，汇总为 [scope-final.json](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/recipe-strict-type-repair/scope-final.json)。

## 未验收边界

当前正常 build/官方Host来源仍绑定09c401，不能用其回执认证本候选。若未来采纳，应先核旧SHA/备份，再对实际 Main 两文件重取同 fresh strict 结果，并为新源码取得相应正常构建回执；本报告不预告那些结果。全项目 strict、原5秒门、Host/PAPER、14天和四周等目标仍未据此通过，项目保持 ACTIVE。

## 后续实际Main验证

单行候选本地提交c8d2aeb，Root非作者C0/I0；10:16:25.946092Z核旧09c401/新8d79/完整备份/8047及3097无监听后已接Main源码。随后直接对实际Main recipe＋实际新test运行同fresh strict配置（无extends/抑制、五Unknown error），实际2files0errors0warnings/1.381s/exit0，源和配置前后相同，stderr0。原件 recipe-strict-type-main-integration/main-pyright-result.json；这才是实际Main两目标的严格静态结果，仍不是完整项目0。Q039失败build身份保09c401，不能改称8d79；新字节正常回执尚未取得。
