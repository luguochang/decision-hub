# Q039：恢复 recipe 的有限归档声明契约

2026-10-24；精确两文件提交 `69854fd`，局部声明修复/回归已核验，项目目标保持 ACTIVE。原 Q038 build-only 的实际失败 `research_recipe_archive_initial_declaration_invalid` 保留；本报告不预测下一次原 build 的结果。

## 原因与最小修复

真实 Main 归档器 `research_recipe_archive.py::_recipe_initial_modules` 只读取 builder 内唯一的 `initial = <literal tuple>`，不执行归档生产者代码。Q028 memo 优化将六入口移到模块级，使原有限 AST 契约缺失。D 未采用的 `1641…` 实验先保全，逐字恢复 Main `5ce02ead…0f9d16` 基线（`fdbd252`）后才实施，未把实验源混进候选。

生产仅改 `application/research_graph_recipe.py`：恢复函数内原六入口 literal；正常路径用该 initial，模块级 global 被动态重绑时仍走原 global 的冷 builder fallback。没有新增运行时强制一致性拒绝；静态测试防止两个源码声明漂移。global memo、helper 身份、fingerprint/readset、完整归档/权限、SQL/CAS 和原数量/时限边界不改，归档 parser 本身只读。

为保持修改函数60行，原六行 docstring 压为同含义单句；literal 行内说明归档读取用途。完整模块 AST 在仅正规化此 docstring、local initial/roots 与 pending iterable 后等于基线；其它函数 AST 原样。新测试 `tests/research/test_graph_recipe_archive_contract.py` 45行，测试函数均不超过60行；原 memo 测试文件/断言未改。

## 实际证据

原件根目录：`D:/DecisionHub-v3-development/evidence/legacy-sdk-review/recipe-archive-contract-repair/`；[scope-final.json](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/recipe-archive-contract-repair/scope-final.json) SHA `352b8157…155cb` 保全部来源、命令及日志。

| 项目 | 实际结果 | 边界 |
| --- | --- | --- |
| 新测试 RED | 2 FAIL / 0.12s / exit1 | 实际 Main archive parser 读取物理 D 的实际5ce基线源，重现 assignment→initial declaration invalid；非模拟 parser |
| GREEN | 新2＋原memo26共 **28 PASS / 2.77s / exit0** | 包含原动态 roots fallback/自导入/重绑测试；旧断言保留，stderr 空 |
| Ruff | 两 owned 路径均 exit0 / `[]` | 实际 Main 配置＋虚拟 Main filename，检查确切候选字节 |
| 局部类型检查 | baseline/candidate/newtest 三静态快照，0 emitted errors / 1.507s / exit0 | 沿原 memo/project 选项，已有多项 suppression；不等于完整开启 strict 的门 |
| 非作者 A 复核 | Critical 0 / Important 0 | 只读25 pins及源码/AST/RED/GREEN原件，未重跑；`session-peer-final.json` SHA `6343a7bc…62315` |

实际单元命令为 Main Python `-B -X utf8 D:/DecisionHub-v3-development/temp/legacy-reuse/recipe-archive-contract-repair/run_tests.py --phase red -q`，随后同命令 `--phase green --memo -q`。新私有执行器复制适配旧 memo runner，不改封存旧包；canonical recipe 从真实 D 文件加载，archive/dependencies 来自真实只读 Main，核 `__file__`/code filename，没有伪造来源。memo26 使用 D 合成文件树，不代表完整实际 installed closure 已重建。

类型命令是 Main Python `-B -m pyright --project <新私有typecheck>/pyright-owned.json --pythonpath <Main>/.venv/Scripts/python.exe --outputjson`。源码快照逐字，只有测试快照的 import 为类型解析指向 candidate；快照没有作为运行时证明。配置明确继承 `reportUnknownMemberType/VariableType/ParameterType/ArgumentType=false`，以及 MissingImports/ModuleSource、UntypedBaseClass、UnusedFunction/Variable、TypedDictNotRequiredAccess 等既有关闭项。因此早先“局部strict0”的简写必须限为该继承政策下零输出，**不是全 Unknown 开启的 strict0，更非 Main 整体0**；本产品补丁没有新增 ignore、Any 或规则抑制，不能用此局部配置冒最终全门通过。

## Main 集成与未验收项

根于 **2026-10-24T09:58:49.101308Z** 按旧/新 SHA、备份及受保护服务条件，将 recipe `09c40117…45a3c2` 与新测试 `5b3ab87c…8d823` 精确接回 Main 本地源码，[integration receipt](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/recipe-archive-contract-main-integration/receipt.json) 记录 archive 未变、`deployed=false`。原 D 实验和 Q038 失败原件均保留；这不是服务/profile/installed pointer/正式库部署。

下一次原 `build-product.ps1` 尚无本报告可采用的终态，不写构建成功。此次未跑 UI98/native556、sameRun、Host/API/数据库/付费模型或性能验收；28项仅证明声明契约及 memo 回归，不能替代实际 build、原5秒门、金融/PAPER、14天或四周验收。

## 后续真实构建

Q039原正常build确已越过retention/assessment，随后UI默认约4GiB V8堆OOM、exit134，Host未开始，完整791.518s/exit1、无validated receipt。不是原声明错误再现。见 INCREMENTAL_NORMAL_BUILD_AFTER_ARCHIVE_FIX.md；不能把28项GREEN等同整体build成功。
