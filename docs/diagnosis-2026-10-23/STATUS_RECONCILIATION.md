# 状态文档与 PAPER 落地对照（2026-10-28）

> 最终恢复续记（2026-10-29T05:32:08.279422+00:00）：本轮主流程恢复/自测/需求核对完成，全项目未交付。start10全7服务ready，两个正常owner旧报告GET各13checks通过，原BTCdegraded/ETHcompleted及source/artifact/publication/概率不变；原409已闭合。R5j child装配已完成机械离线修复（169runtime+actualderived），不冒live新v5。R5k49=48PASS1SKIP，newhelpers strict2file0/Ruff5file0、96/model同、六具名正式保护文件SHA同；业务仍12原Runs。P3/P4完整band/P5/P6、多事件/其他期限/PAPER/14天/四周/正式发布仍未完成。下面旧表/旧待执行是历史，以[最终结果](E:/project/study/codex/dsh/decision-hub/docs/diagnosis-2026-10-23/RESEARCH_RECOVERY_RESULT_2026-10-29.md)与当前handoff为准；A4跨版本944P81F3E2S不是当前全仓绿。


> 最新状态（2026-10-29T04:17:46.243672+00:00）：start09局部编译6.35秒且7服务ready；R5j离线派生链已通过，但正常owner旧报告两个GET均409/perpetual_report_original_unavailable，已证首因保存v2 recipe与当前Python闭包不匹配。先前低层Source兼容控制漏覆盖公共publication graph，是执行者验证遗漏。R5k精确509原码可用；current PublicQuery+私有原publication consumer的隔离prototype由R1报告两Run通过，最终文件/独审待收尾，生产接线尚未实施。API兼容仍未修好，目标active，无新付费请求/完整打包。以本段及CODEX_LOG最新条目为准；下方较早Stage7、running及停止状态均为历史。

> 2026-10-29 当前续记：下方“10-28可证状态”是原快照，不代表最新运行结果。原系统最新 BTC 4h 已degraded保存、ETH 4h 已completed保存，两份正常owner API认证读回及VERIFICATION四技术项均通过；概率各自精确保留90d prior，未证明模型增益。BTC的child accepted数据漏纳入Python造成错误硬降级仍待R5j修复，ETH的日线数据在root实际返回，hardcoverage1.0，仍6soft gaps。start08已官方停止且recorded stopped/observed absent/listeners0，不沿旧running/PID恢复。

| 当前对象 | 最新可证进展 | 尚未完成 |
|---|---|---|
| T01/T02真实报告与生命周期 | BTC09fc/ETHdff两实际保存报告、Host→Python→artifact→正常读回通过；源码开发启动/停止可复用 | 其他周期/恢复全场景及正式候选；已知BTC child装配缺口R5j正在实施，不把技术保存等同数据完备 |
| T04概率与幅度 | 两报告不再固定40/20/40；BTC三实际幅度精确等于冻结90d各p50，原件p25/p75有序并保存 | P3确定性审核、完整P4价格band/锚价与展示未全证明；P6面板未齐 |
| T06评分 | 现在有两份真实终稿可按原到期规则后续评分 | P5 baseline log-loss/明确无对照及到期/四周真实评分，不由即时报告替代 |
| A4失败库存 | 原1030跨版本最新观测944PASS/81FAIL/3ERROR/2SKIP，0未观察；原250补段已完成 | 失败尚未全修，非同一当前源码全绿；不再重复原250长测 |
| T03/T05/T07/T08 | 原界面保留、开发与发布分离、事件覆盖差距已记账 | 多事件完整预测、通知/行动/PAPER真实资格、正式发布、14天六组合均未完成 |

当前唯一运行与代码续接点见 HANDOFF_RECOVERY_CURRENT_2026-10-29.md；具名真实证据为live/{btc,eth}-r5i1-final-verification.json及各正常owner读回、review/{btc,eth}-r5i1-delivery-acceptance.md。R5j测试/实施状态以CODEX_LOG最新追加为准，不把计划或RED当修复完成。

本页是只读核对结果，不改原状态，不宣布全项目完成。依据：10-28新交接、当前活动源码、`docs/v3/STATUS.md`、`docs/design/decision-product-control-2026-10-17/00_MASTER_CONTROL.md`、`docs/IMPLEMENTATION_STATUS.md`。执行原件目录 `artifacts/incremental-delivery-20261023/d28-research-recovery/`。

## 1. 三份状态文档的真实含义

| 文档 | 记录的范围/时点 | 矛盾或缺口 | 建议用途 |
|---|---|---|---|
| v3/STATUS | 10-23重建路线；Phase0 PLANNING，T-000历史推进，T-001—007 PLANNING，Phase1—8未开始 | 与原系统渐进修复不是同一任务命名空间；不能把T-001对应产品T01，也不能从T-000重启 | 保留重建历史账本，不代表当前原系统完成状态 |
| 00_MASTER_CONTROL | 原产品T01—T08与D1—D3交付范围；顶部10-27暂停说明“产品仍未交付”，更早行包含旧PID/成功构建 | 新开发入口、10-28两次失败Run没有进入该历史快照；旧正常运行行不是现场状态 | 需求、完成定义与任务范围依据；运行结果以具名新证据覆盖，不静默改旧事实 |
| IMPLEMENTATION_STATUS | 顶部主要是9月PD交付、783 passed/4 skipped及live canary研究记录 | 不包含本轮P1—P6/新Run；不能拿9月测试/研究证明10-28通过 | 历史模块能力与旧资格边界，不能升级本轮验收 |

当前基准率进度单一来源仍是 PROBABILITY_BASE_RATE_PLAN.md；当前执行记录追加 CODEX_LOG.md，具体真实研究验收按 VERIFICATION.md §4。新交接18:40的“服务已停/两run失败”优先于开发工作流交付时“在线/研究未执行/缺元数据”的旧快照。

## 2. 产品任务逐项对照

T任务名称取自 `05_IMPLEMENTATION_PLAN.md:118–274`；原MASTER顶部明确全部仍未整体完成。v3与9月状态均没有一一对应的新证据，以下“当前”是可证范围，不是重新授予ACCEPTED。

| 产品任务 | v3/STATUS对应 | MASTER/产品要求 | IMPLEMENTATION_STATUS对应 | 10-28可证状态/缺口 |
|---|---|---|---|---|
| T01真实组件集成 | 无同义条目 | 同版本真实输入→审核→同Run报告→PC | 旧live canary，不能替代 | 两个新4h Run终态failed；当前未通过，R1查责任 |
| T02运行/计时/认证/终态 | 无同义条目 | 运行合同与恢复 | 旧Provider deadline/fallback能力 | 开发入口曾受控启动/停止通过，当前PID不存在而文件running；R2尚在归因 |
| T03事件与宏观时点 | 无同义条目 | CPI/PCE/就业/FOMC及扩展议程，分阶段事前/事后 | 旧EventWatch及来源接线 | 多事件未全部交付，详EVENT_REQUIREMENTS_COVERAGE；日历存在不算预测 |
| T04研究到条件行动 | 无同义条目 | 场景、概率、主反证与合法动作 | 旧research_only链 | 新基准率已进admission；P3确定审核、P4幅度实证仍缺，真实终稿未验收 |
| T05监控/修订/提醒 | 无同义条目 | 真实条件触发、送达与后继研究 | 旧调度/outbox局部能力 | 不能据旧实现标真实触发通过；新交接仍列遗留 |
| T06结果/经验消费 | 无同义条目 | 原目标到期评分与真实后续消费 | 旧经验/Provider组件能力 | P5读取时对照已实现；当前两失败Run没有可评终稿，到期/实际消费不足证据 |
| T07原PC用户旅程 | 无同义条目 | 用户可读概率、依据、操作、版本和异常 | 旧PC投影/导航交付 | 原界面保留；P6基准率/偏离面板尚未完，事件入口覆盖需逐项核对 |
| T08正式与完整交付 | 无同义条目 | 同候选真实Provider/PC、恢复、六组合/长期要求 | 旧工程READY不等产品资格 | 本候选无正式通过回执；本轮不完整打包；14天六组合等保留未完成 |

整体结论：不能用三个旧状态文档任一“已完成”行推导当前需求全部完成。应该按需求对象、当前版本、运行证据和价值资格分别记状态。

## 3. PAPER_HORIZON_ALIGNMENT_FIX 落地核对（A5）

本节是活动代码/测试定义核对；没有真实纸面成交、没有重跑历史报价或改账本。完整新测试基线正在执行，结果另追加，不能把“测试存在”写成“本轮测试通过”。

| 要求 | 活动代码/测试证据 | 判定 |
|---|---|---|
| 新协议持仓T1对齐forecast window_end | perpetual_manual_execution.py:180–190，register_manual的end_at取execution.target.window_end | 已落地（代码） |
| 单独冻结入场截止，不延长为持仓截止 | 同文件:355–359、426、464–465，latest_entry取原许可/target/protocol最小值，并fresh now拒绝 | 已落地（代码） |
| DTO分别显示入场与持仓截止 | perpetual_manual_input_guide.py:201–202；PerpetualManualPaperPanel.tsx:173、204–205 | 已落地（代码/界面） |
| 未入场准备采样在许可过期停止 | perpetual_manual_sampling_demand.py:68–77，过期guide.latest_entry_at不再纳入采样需求 | 已落地（代码） |
| 既有仓位持续跟踪且验证后推进游标 | perpetual_manual_execution.py:1610–1669，无退出reason时验证prepared与quote，再append_episode更新sampling_generation | 已落地（代码）；本轮真实持仓未验收 |
| 512代上限不放大/不跳核验 | perpetual_quote_sampling.py:118–135仍拒绝超过512或1024行；:157–162遇未完成前代停止，不跳过 | 已落地（代码边界） |
| 跨512后仍退出的直接回归 | test_perpetual_manual_execution.py:427测试命名明确，:489–494先走512代并验证游标；需结合完整测试结果判PASS | 已有测试，当前新基线待结果 |
| 许可/持仓分离的回归 | test_perpetual_manual_input_guide.py:66–83；test_perpetual_manual_execution.py:389–421 | 已有断言，本轮结果待基线 |
| 旧协议不迁移、不延时 | 本次未写任何历史协议/正式库；代码仍按读到的protocol.T1处理 | 本轮无迁移；完整旧协议行为测试待基线核对 |
| 自定义止盈/止损/提前退出概率 | PAPER方案明确属后续独立能力；当前终点概率不能当本笔模拟胜率 | 未实现/不在该窄修范围，不据技术成交补金融资格 |
| ≥10笔真实PAPER成交/资金费 | 当前交接仍列遗留，代码/测试不能代替实际账本记录 | 未验收，不作已完成 |

## 4. 测试基线证据边界（A4）

仓库外旧 `E:/project/study/codex/_shared/temp/claude/selftest/baseline_pytest.txt` 实际仅200字节：进度点/F、43%行及`exit=124`，没有失败测试名或traceback。仅据这份文件不能逐名证明当前某个失败在10-23已存在；其文件mtime为10-27，也不等同源代码改动时点。

本轮命令/环境在 `d28-research-recovery/baseline/started.json`，全量decision_product测试开启verbose与JUnit，保留具体失败名、错误类型和摘要；短basetemp为未存在的新`artifacts/rb28`，避免长路径和清理旧记录。测试未终态前不宣称完成，也不写“全部已有失败”。新的源码修改须等待该取证基线完成或先保存相关测试前像对照，不与真实研究争用资源。

## 2026-10-29 A4中断与67项复测补充（不改原状态）

系统UTC：2026-10-28T17:16:43.1712656+00:00。最初decision_product收集1030项；原扫测中断于约75%，712PASS/1SKIP/65FAIL/2ERROR/250未终态。已保留完整原日志，不当作全量完成。F01只重测65FAIL+2ERROR，仍65FAIL/2ERROR，806.083秒，3个R5d生产源SHA前后相同。每个nodeID、异常类型、短错误、<=40行与源码mtime见 `artifacts/incremental-delivery-20261023/d28-research-recovery/baseline/f01-summary.json`，执行/分类边界见同目录classification.md。

F01共现分类：49项action_final_precheck_run_mismatch，5项总执行wall超过5秒，4项缺测试原件文件（含两ERROR缺manual-source-handoff.json），其余9项分散schema/recipe/权限/版本/条件/重复命令等。共同码不是已证共同根因；“本轮R5d修改前已观察到这些节点失败”也不等于“已证明10-23已有”。原250尚未执行，后续应按明确节点继续，未执行不算通过。

R5d单独相关23套20PASS/2FAIL/1ERROR；其中两个fixture输入codec/时钟失配有原件，PAPER 7.397秒>5秒仍是未归因性能FAIL。独立审查只认这些未走FOMC正向编码，不独立阻断窄R5d修复；不将失败改成PASS或将其混加到旧1030基线。R5d新增28项模块的覆盖矩阵/分次结果另见材料报告，不能把新增用例简单混进原分母。

R5d专项修复21聚焦PASS、私有材料逐字还原、strict/Ruff与最终独审通过；真实新BTC刚受理，仍待终态/独审/报告。多事件预测、PAPER真实成交/费用、14天/四周评分、正式发布均未据此升级资格。
2026-10-28T19:49:06.2929154Z
A4 F01捕获失败的只读因果细分：baseline/causality/f01-causal-classification.md。49条都经publication.py:490进入action_research_reader.py:95的真实run前置校验，早于fixture只mock的trusted reader；两个suite共享fixture未创建RunRecord。强支持“夹具缺Run与新增/提前precheck接缝不一致”这一直接阻断；捕获未打印行/OR分支，不能声称已证具体disjunct、修后49必全绿或全部都是10-23旧失败。未改fixture/断言/生产，未重跑。性能5、缺原件4、其他9分别保留；原250仍未终态。后续须在源冻结的独立窗口用一例最小判别试验后再裁决，不把静态审计计PASS。
2026-10-28T21:52:00.4941403Z
R01补测真实终态exit1：250节点，232PASS/16FAIL/1SKIP/1ERROR，pytest2850.79秒（wrapper2857.277秒），未中断/重跑。r01/terminal.json、results.xml/stdout/stderr/last40全保留；3monitoredPy源未变。Root另外核candidate-source-after.json全部17候选/SDK文件及modelsettings未变。初始原1030库存现250补段已有终态；原712PASS等历史与F01 67重复不得混加为当前全repo全绿，新R5d28另列。

解析器已一次成功：250XML一一映射，无missing/extra/重复/F01重叠/stdout冲突。ERROR actual_factory_native_carrier...的original_preparation_deadline_elapsed栈与保留R5d固定clock/未patch utcnow旧fixture问题同节点同边界，未据此证明新真实BTCETH阻断。16FAIL初分8个manual_execution:254 timing、2不同AttributeError、2UnicodeDecodeError fixture读取、1生成输出父目录缺失、1continuous command invalid、1observation locator invalid、1prepared implementation change；详细源位置/安全摘录由r01报告补齐，不猜CPU因果，不将它们标通过。

当前没有长测或研究，source仍按R5g冻结。下一start06官方源码模式，核现有SDK公共surface模块加载、局部插件编译与全部ready后，再新BTC-r5g1关联已取消46aec；先BTC后ETH，不改旧回执/不完整发布。辅助build recorder只新增可选before-manifest参数用于对应R5g前像，旧默认行为与旧报告保留，不影响产品源。
2026-10-28T21:52:58.2440050Z
A4取证收尾：r01/r01-terminal-causal-review.md、r01-diagnostic-details.json及parser输出已生成。250精确映射，无缺漏/重复/旧F01重叠/状态冲突；R01 232P16F1S1E。原1030跨版本最新观测944P81F3E2S、0未观测，F01不重复加，R5d新增28另列；不称当前1058/全repo全绿。8个timing6.224–6.438秒失败保留，不猜CPU因果；两个AttributeError不同（fixture output_schema_ref与receipt.revision）；两fixture解码；生成目录缺失；continuous400/locator/prepared身份各一；deadline setup错误与已保留R5d时钟fixture诊断同节点/栈/边界，R01无实际deadline值不借旧DB字段填事实，未证明新真实BTCETH阻断。A4“失败清单与原库存核对”完成，不是失败都修好。

固定generated outputs最终对照：ETH未变，24h两份相对Root旧前像确实更新、scan后没再改；review目标仍不存在；概率default报告21:46更新7a9ea457...→215aa5cc...，旧副本SHA完整。三份正常测试示例实际更新，原旧字节副本保全；具体路径与hash见r01副作用报告，禁止宣称所有artifact不变。现全部17候选与modelsettings复核不变、无长测，start06源码启动预热、暂无新研究。
