# 原永续完整研究提交与V6读回候选

状态：D隔离实现与离线验证完成，非作者复核C0/I0/M0（reasoning-only时间遗漏已真实RED→GREEN闭合）；未采用Main、构建新normal或部署，未新增真实研究、PAPER或六条件合格记录。Task A依赖为D提交`327b6eb4a30be12ab84054d9d40a530a445b54ac`。原normal982只认证此前content/pooling，不能认证本候选。

## 来源与范围

三原源先逐字对齐物理Main。Main基线SHA：

- perpetual_research_report.py：`11338b7fc869ead5b269c039b1e3f8099159268d8bcb48260c63bd5bbe673dd2`；D原缺失。
- commit.py：`1826972d46130e41a5275e593e972d9a935ce92e72c80df7058bda6abdcedd3b`；D原旧文件先保存，未作为依赖。
- perpetual_saved_report.py：`afd9f72525c4a17f6a3535c34b970dfedacd82e7353f22c3d6ef3720c198e209`；D原同。

原字节及manifest保留在`temp/research-completion-task-b/baseline/`。生产仅以上三源及新`perpetual_report_delivery.py`；测试仅两定向测试及`candidate_perpetual_report_support.py`。运行依赖始终物理Main，候选模块按真实D文件加载，没有伪造`__file__`。测试child的真实subprocess边界仅将cwd改为D插件，三输出定位均为新D临时路径；不修改Main或旧fixture。

## 行为与协议

`prepare_content`先完成原Graph/mapped/native/Provider/原件目录鉴真，再从已鉴真的wrapper唯一双对象数组投影完整findings。helper按Task A完整findings.v2模型检查四项；approved仅是必要条件之一，v1/missing或任何incomplete/failed不补默认通过。

结构检查包括真实信息时点严格早于提交决策时点、且不晚于原声明`known_at_max`；事件manifest与独审check-only引用也不能隐藏更晚信息。保持原as_of、known_at_max和Graph data cutoff原值。原writer在原位置捕获的`now`用于最终判定、Artifact/Source/Run时点，历史重算使用原Artifact.created_at，不把数据cutoff当决策时刻。

普通V2只要求实际目标/期限与完整方向概率，允许事件发生概率未知。事件V3展开原manifest，检查p、结果分区、全部branch q及场景覆盖；不拿primary概率代全事件，不自行重算金融收益或另立概率容差。原typed合同与codec保留其质量检查。三关键证据与至少一反证按可回读原件去重；source_ref/original_ref别名不重复计数，同一事实允许支持不同机制，不额外要求正反证集合互斥。

新source为`research-publication-source.v6`，保留V5全部字段，并保存顶层`report_delivery`；同一事实亦在`perpetual_binding.v2.report_delivery`内。binding保留原六字段并增加`native_review_wrapper_sha256`。新packet为`perpetual-research-report.v2`，新增完整`review_findings`，其余内容和原件不缩减。

delivery为`perpetual-report-delivery.v1`：run_id、decision_at、complete/incomplete、reasons、六个input_hashes、原mapped/provider/synthesis refs、去重关键/反证refs及两个固定false权限字段。六hash绑定synthesis、review_request、authorization、findings、catalog、增加wrapper hash后的原v1 source_binding。所有字段留在原Source事务及其SHA闭包；V6读回从原件重算等价对象，不信任自报bool或sidecar。

仅该完整报告提交事实选择新Run.completed/degraded。Gate始终research_only，金融/执行资格与ExecutionPermission均不翻真，原forecast、Action、权限与最后lease CAS不扩权。`completed`不直接计六条件合格。

V5仍按旧renderer和旧degraded条件重放，不填V2/V6新字段。全V5候选对象、gate与binding逐字段和当前物理Main旧renderer一致。**这不是原07完整历史读取已通过的声明**：Root另项只读发现当前982缺真正7b7d旧implementation archive，07原reader因`research_graph_recipe_current_implementation_unavailable`失败；此既有前件由独立归档修复处理，本切片不换51e、不绕retained consumer。

## 真实RED与验证

- 初始renderer业务RED：完整Main模型/目录校验后`research_complete`仍固定False，1FAILED/1PASS。之前两次fixture截止/UTC格式错误保留，不计产品RED。
- Root指出普通/事件口径偏差后：普通未知event误拒、事件缺p/任一q/覆盖误complete，得到4FAILED/1PASS；修后通过。事件原件晚于声明freeze、独审check-only独立引用晚于freeze分别有实际AssertionError RED，未把别名fixture的既有拒绝PASS伪装RED。
- 定向结构最终29PASS；含独审发现reasoning-only时间遗漏的真实RED→GREEN、未使用目录原件不误算已用的正例、完整原模型、V5全对象不变、负面独审、approved+incomplete、别名去重、PIT/声明freeze、全部事件分支、跨request/ref/目录篡改。
- 原native两case实际exit0，2PASS/386.28秒：原Main Factory/SDK mapper/Graph/Provider持久层，D原prepare/reviewer/finalize与完整child proof，最后原Commit事务和SavedReportQuery。显式合成child四passed落completed/complete；默认四incomplete落degraded/incomplete；两者gate/权限均保持原false边界。测试明确是synthetic backend，不能证明真实模型理解或真实业务合格。该全链后只补类型/防御guard及V3/check-only时间负例；相应纯回归与最终reader控制分别验证，不声称重跑了付费业务。
- 新D只读一致副本控制`reader-integrity01/result.json`实际exit0：两个最终reader对象与上述fixture全等；合法重新计算Source SHA/output_id后篡改delivery.findings hash，均被原reader拒绝。源fixture DB/WAL前后SHA相同、network_attempts=[]。这不是实际API重启或PC验收。
- 新helper与三测试支撑/测试Ruff零诊断；沿原Main配置的定向类型检查，新四文件0诊断，三旧源基线/候选均45诊断、零新增。原配置的reportUnknown*等既有关闭项未改变；这不等于默认完整strict全部规则已0，最终严格质量门仍未通过。三旧源Ruff保留基线10个E501，无新增。静态闭包逐字取Main application、只overlay候选，其他kernel为只读Main定位，Task A合同使用其已封存静态闭包；不执行静态副本。

日志、静态配置、来源与结果集中于`temp/research-completion-task-b/`。复现主测试命令：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& 'E:/project/study/codex/dsh/decision-hub/.venv/Scripts/python.exe' -B -m pytest -c 'E:/project/study/codex/dsh/decision-hub/pyproject.toml' --rootdir='E:/project/study/codex/dsh/decision-hub' -o pythonpath='E:/project/study/codex/dsh/decision-hub' -p no:cacheprovider 'D:/DecisionHub-legacy-incremental/tests/runtime/test_perpetual_report_delivery.py' -q
```

native测试须另指定**新**D basetemp，不能覆盖封存结果；原两case位于`native-v6-final01/`。完整fixture `test_original_native_assessmen1/saved-report.v6.json` SHA`38d20d2ee0d0d271d4f4bd75acd32e363f535300360ffd1790b771754544e641`；incomplete对应`...assessmen0/...` SHA`bed2ba2c911dae48e2a3dd115da8483e57c362e2445fcef5d184ae553f00dc6d`，供原PC decoder定向验证，未作为真实研究样本。

## 待办与验收边界

非作者复核已完成，随后按本报告八文件精确本地提交；Root采用与新normal认证待完成。Task C的真实新Run、原PC展示/双导出、同报告API重启、六条件登记及全部长期门仍需实际取证。旧07不改状态，不因本切片补合格；原14自然日、四周评分、事件/PAPER/30完整失败回放等最终门不缩短。
