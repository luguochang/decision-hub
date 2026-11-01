# Q042：正常构建的 plugin 输入合同失败

Q042 唯一原 build-only 在 `2026-10-24T10:23:48.707459Z` 开始，`10:40:44.401126Z` 结束；exit **1**，耗时 **1015.6933119 秒**。
原 PowerShell PID `21248`，创建 FILETIME `134373110287098399`；未自动重试。
实际 Main recipe 为 `8d79a1994d9437d596d8bc42eaf52809cdaacaf561c1bab618a3e4c02e452487`。

启动前实测总物理内存 `34191310848` 字节、空闲 `15180537856` 字节，满足 ≥10 GiB。
继承 NODE_OPTIONS 不存在，仅向构建子环境追加 `--max-old-space-size=8192`；子值只记录 hash/presence，没有改全局环境。
实际子值 SHA `560d91cc597a59774228b503f751332375555956411b827dcea4defa95e0878c`。

原 retention、assessment 均实际通过；registry `e729bbbd0d2853a43be638798f62cdbbef343ba2ef27cd2b6bcbe4c9554fbf80` 有 175 项 registrations。
原 UI attempt **1 / exit 0**，原 Host attempt **1 / exit 0**，builds **2**；本次不是先前约4 GiB heap OOM。
Host Vite 完成日志为 `built in 6.30s`；UI 只有原500 kB chunk warning，没有改其阈值。

失败发生在最终 `product_release_provenance.py::verify_product_receipt_v2:772`：
`product_provenance_plugin_inputs_invalid` → `validated-product-builds-v2.ps1:10` → `product_validated_build_v2_rejected`。
该验证器仍从旧8入口、5控制文件及host目录得到 **43** 项 expected inputs；当前真实生成器声明 **71** 项。
纯只读集合复核：新集合比旧集合多 **28** 项，旧集合缺于新集合的项目为 **0**；不是授权接受任意superset。
按原 `path + NUL + byte_length + ':' + bytes + NUL` 算法独立重算71项，仍精确为 `a925ff980f20075b56d40910046775aab70530bf78bb5f1dcf3531c20701ec64`。
生成器来源范围已扩大而最终消费者合同未同步，不能把此失败改名为有效回执。

原临时回执曾观测为 `245212` 字节（< `2097152`），最终验证拒绝后原producer没有保留该tmp。
未重造tmp原件；正式 `resource-build/product-validated-builds.json` **不存在**。2 MiB 上限不是本次失败原因。

Main lib/dist 当前544项、`118973432`字节已保留；相较旧清单10项改SHA、6旧文件名消失、27新文件名出现。
D中原525项、`113753561`字节回滚备份保留，没有恢复或删除产物。
71项输入、18项prior旧源、producer/package/lock/installed pointers前后不变；Q038/Q039失败目录保留。
node_modules没有完整before快照，不宣称零自动协调或依赖动作；未显式执行install命令。

[终态核验原件](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q042-normal-build/final-receipt.json)
SHA `17ad93eb5e15ece1664c532bd5b3ce99c0c040ff59c4ec49dc6b5dc02dfc3fb2`。
[原stderr与完整stack](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q042-normal-build/build.stderr.log) SHA `83d2d3acfc37c2979d5c5069d5574a4df449a8e334afe561f0c046c212658170`。
[原stdout](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q042-normal-build/build.stdout.log) SHA `97ee1f58d2ee78eef5a67635778f46696f33150a169713eb17562f9ee8c6ba45`。
[输入差异原件](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q042-normal-build/plugin-input-contract-readonly.json)仅为只读诊断，不替代完整guard。

未取得有效正常receipt，未部署API/Host/Worker、迁移数据库、提交模型或改写安装指针。
本报告仅写D；后续Q044先独立推导真实输入集合和定向RED，不更改历史retained codec，不重跑完整构建。
金融5秒、实际Host、PAPER及自然时间验收均未据此通过，目标仍ACTIVE。

## Q044 隔离修复与非作者核验

逐字基线提交3b439f0；候选仅复用本轮已校验的fresh required库存，筛 plugin/src（排generated identity）与contracts_ts/src，再加13固定构建控制。exact集合、类型/重复/来源成员、link/变更、原哈希framing、产物身份、所有transport/owner和容量守门保留。旧43集合即使重新封签也拒绝。

真实完整V2 guard正例先2RED（0.36s，原plugin_inputs_invalid），最终17新项＋2原Main只读transport共 **19 passed in 5.72s，无跳过**。真实Windows junction负例已执行；早先1skip不冒最终运行。原transport确实回放20,046,677B/60,002sources，并拒绝43,578,176B/61shards。原历史codec/owner/guard/framing AST保持，不能据私有synthetic fixture授正式release权限。

候选codec SHA180c726a987d3b4deacb940b04113c3f1940bae6870cd47df3690a34b0e68691，新test SHA8e4dd38c183d309020999041bc016bb096897aaa0f874b114eabb85450533c21。新helper23行，test296行/函数max47；两owned Ruff0、新test实际strict1file0。整个旧codec strict仍78项，baseline/candidate的rule+message多重集相同，未抑制或声称whole0。

Root非作者独立核全部冻结source/原始证据SHA、模块AST（仅helper与expected赋值不同）、当前Main实际完整输入集合71和原framing重算a925…01ec64；C0/I0。收据见[scope-final](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q044-build-input-contract/scope-final.json)及[root review](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q044-build-input-contract/root-nonauthor-review.json)。证据按实际字节核验，未无理由重跑同19项。

本节时点仅D候选已验证，Main仍原b0b8…f8652d；未再正常构建、未取得有效normal receipt或Host/金融资格。后续与Q043合并采纳必须保全prior49c仍引用的旧codec/saved_report/API decoder/必要旧测试原件，并重新绑定真正当前Main源，不能重用Q042临时收据。

## Main采纳续接

Q044真实候选及报告已精确提交2f5579e，Main codec已按13文件[采纳回执](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/incremental-report-main-integration/receipt.json)变为180c726a…e68691；原b0b8codec逐字备份且作为prior49c原件create-only保全。Main产品服务未部署，Q042输出不是新Q043界面的构建认证。

新正常构建仅prepare：prepared6c28b44a…a91527/run c0911e19…9d01fa，Root/C final peer340d33b4…d4b1fd，546输出备份/22旧源/71native/13来源均具名。尚未执行guard-build或取得有效normal receipt。下一lawful验证已暴露新test裸support导入的Main包收集失败；该test若修订，当前prepared13-source身份随之成为历史，必须封新来源prepared，不能改写6c原件或假称已构建。

## 当前唯一正常构建实际启动

旧prepared6c28/run c091按当时13来源保历史；test导入修正后另封当前聚合58a305并新建final evidence。最新run7e2a5d321d4a1010ce76a4619993b3f7d2f60e4e89e8a8eb66a3c388280bfbdb / prepareda7d79c0c49beb9361a2ee4173e303cdf64da550c3acc04fa462d263e69aaee06，after-import final peer c862404c…c9b334c。除精确新source/adoption/pin与新证据目录，原producer/13source/71native/22prior旧源/546backup/8GiB/10GiB门原样；旧准备包没有运行或改写。

唯一实际新build已11:38:23.275123Z启动，session33973、pwsh PID3756/FILETIME134373155032768176；启动前free14603001856B≥10GiB，NODE_OPTIONS inherited absent、子值hash560d91cc…e0878c。输出只fresh Main/artifacts/incremental-delivery-20261024/guard-build。此节时点仍RUNNING，不冒normal receipt/Host/金融资格。A仅处理私有Windows命令表示的纯guard测试，不并发native/SQL/前端业务或修改Main源码。


## 正常构建真实终态：通过，未部署

唯一构建 session33973 已于2026-10-24T11:56:34.955537Z退出0，总1091.6803226s。原retention/assessment、UI与Host各一次均0；原producer的临时及最终receipt校验均完成，stdout真实返回VALIDATED_BUILD_RECEIPT。

[真实normal receipt](E:/project/study/codex/dsh/decision-hub/artifacts/incremental-delivery-20261024/guard-build/product-validated-builds.json) 为245727B，SHA `936c0aa80206cb42e9e379c143f1a5fcd549c7a36716fc1c3ce65eb38f965f7c`；[完整终态实证](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q044-normal-build-final/final-receipt.json) SHA `35496acbcb4cb2eb8d6c49681e74dd75ccb3ecb27c1e62bd6f0822c2b3ed92e9`，stage passed/error null。13source/71native/22prior/recipe/producer/package/两installed pointers不变，546备份实物完整。未额外重跑build或guard。

实际guard effective source_count60908/artifact_count48；raw1799sources和完整lib/dist565文件123874471B+2aux是不同范围。与546前件相比4名变、0缺、21新增；不可拿receipt48冒全产物数量。pnpm内部依赖协调无完整前件，仍不能声称零node_modules动作。正常receipt仍明确native_execution_qualified/eligible_for_deployment/whole_product=false；没有API/Host/Worker运行、正式DB迁移或付费模型提交。局部构建通过后继续真实同Run、官方Host和脱库报告验证，长期金融目标ACTIVE。
