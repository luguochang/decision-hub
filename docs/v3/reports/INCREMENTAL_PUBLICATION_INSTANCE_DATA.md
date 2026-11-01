# Q035：原发布服务的实例数据目录

状态：D 隔离候选已由根精确提交 `d299070`（三生产源＋一个新测试）；Main 三源及新增测试已于 2026-10-24T08:55:52Z 核旧 SHA/缺席、备份后精确接回本地源码。没有部署原服务或改写正式数据。

factory 新增 `publication_data_root=None`。缺省保留原 stage/archive 布局；API/Worker 未设置环境项时仍使用原两个关键字调用，仅明确设置 `DECISION_HUB_PUBLICATION_DATA_DIR` 才传入私有数据根。
显式根须绝对、在源码 Root 外，拒相对路径、`..`、已有非目录、symlink/reparse 与解析别名；先检查根和两个子目录，再创建 `stage`/`archive`。真实 source Root、registry/issuer、契约、迁移、SQL/CAS 与归档协议不变。
基线提交 `1f9d0a8` 是当前 Main 三源逐字入 D；实现没有把旧 D 后端当成完整当前 Main，也没有用 D 假 Root 或放行 Main 写入。

## 实际命令与结果

```powershell
# RED，cwd D:/DecisionHub-legacy-incremental
E:/project/study/codex/dsh/decision-hub/.venv/Scripts/python.exe -X utf8 D:/DH-q035-5bf7141bc9/run_tests.py -q
# GREEN，同 cwd、同 runner 原字节；新 WORK 保留 RED 原件
E:/project/study/codex/dsh/decision-hub/.venv/Scripts/python.exe -X utf8 D:/DH-q035-green-9cf75f77/run_tests.py -q
# 最终新测试 strict，仍在 D cwd
E:/project/study/codex/dsh/decision-hub/.venv/Scripts/python.exe -m pyright --project D:/DH-q035-green-9cf75f77/pyright-tests.json --outputjson
```

- RED：`18 failed, 2 passed, 1 skipped in 3.47s`，退出 1；16项因 factory 缺新参数，2项因显式 env 未转发，没有部分 D 缺件导入错误。
- GREEN：`20 passed, 1 skipped in 3.65s`，退出 0；runner 与 RED 逐字相同，两次各自五份源上下文前后 SHA 一致。
- 真实目录 symlink 创建因 Windows `OSError` 跳过，未计通过；reparse 属性拒绝用显式 synthetic lstat seam 验证，不冒真实 junction 纵向。
- GREEN 对应新测试 SHA `472a6d08…aae8b`；随后仅修两处 E501 断行，最终 `ae2e502a…2e1bb` 与原 GREEN 完整 AST 相同，没有重跑业务。最终新测试 strict 1 file / 0 errors，Ruff 0。
- Ruff 使用 Main 配置＋对应虚拟 Main filename，D 字节从 stdin 输入；三旧模块 baseline/candidate 分别 `96→96`、`11→11`、`8→8`，诊断 `(code,message)` 多重集相等。既存问题保留；未执行 whole typing，不称整体零错误。
- C 最终四 pin／格式差量非作者只读复核已闭合 C0/I0；独立确认原测试至最终测试完整 AST 相同，未重跑测试/API/DB。见[最终 peer 收据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q035-runner-final-layout-delta-review.json)（SHA `5300a70a…96632`）。

## 原件与边界

[精确四文件／SHA／diff](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/publication-data-path-repair/scope-final.json)；[实际命令、退出、静态原件汇总](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/publication-data-path-repair/actual-checks-final.json)。
[RED 原日志](D:/DH-q035-5bf7141bc9/red.log)；[GREEN 原日志](D:/DH-q035-green-9cf75f77/green.log)；[最终 strict](D:/DH-q035-green-9cf75f77/strict-tests-final.json)；[Ruff 全命令／唯一原件路径](D:/DH-q035-green-9cf75f77/ruff-final-context.json)。
[GREEN 后仅格式变动](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/publication-data-path-repair/test-layout-only.json)；[基线与 D 旧字节备份](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/publication-data-path-repair/baseline.json)。
构造测试明确替代 registry/SQL lifecycle，真实执行 path/preparer/archive 私有文件操作；API/Worker 只执行原装配 AST 语句，避免模块级启动。没有真实 Main source/issuer 资格、完整 API/Host 启动或跨卷实际发布验收。
本切片仅提供实例数据位置接缝，不能宣称修复尚未定位的 API 启动访问冲突，也不证明原 45秒/5秒门、live-root/Provider、实际 PAPER、14天或四周目标通过；项目目标仍 ACTIVE。


## 本地源码集成续记

[Main 四文件集成原件](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/publication-data-main-integration/receipt.json)记录三旧源逐字备份、四个最终 SHA；受保护 8047/3097 当次无监听。临时集成 helper 已非作者只读复核 C0/I0。未设置新环境项、迁移旧归档、部署服务/profile 或写正式库，缺省行为仍原样。

API 两份 Main 源 SHA 已因此改变，旧官方 Host 执行器 `source-pins.json` 及其诊断包属于集成前的历史证据，不能直接重跑或改写旧原件。下一次需要保留原包、按真实当前源码另封明确差量的输入身份，不伪造完整 D Python 克隆。本切片不对 API 导入区间卡点作因果结论。
