# 已完成normal的有界真实prior链

状态：D独立候选，Root已授权。当前Main、66bad冻结源码/快照、当前7b7d Query包和47376 normal控制包不改。

## 已证实问题

真实H1 normal已由47376同handle exit0并有限postflight errors=[]产生：receipt SHA `4511e6c6c71e639a379f5562dcf6c52665ab4b957bae3ebc568c2a3510b4de4f`。其retention SHA `5dad351c04bedc812a3152799b2aa5155740db14285160ffe3199e105661f569`（14,875,577字节），prior为6dd，正式installed仍49c。ae9当前脚本830行要求候选prior的retention直接指向runtime49c，因此以H1作下一prior会被single-hop拒绝；每次仍用6dd只会保旧7b7d，不能保证捕获H1真实Run实现。

这不是允许任意prior。只沿真实receipt的retention哈希绑定逐跳回溯，必须最终精确落到当前正式locator及receipt；不构造虚拟installed pointer，不改旧maps或终态。

## 隔离路径

`temp/research-completion-bounded-retention/candidate/infra/dsh/prepare-product-retention.py`：从物理Main ae9逐字保存后最小接入。新的同目录`maintenance_prior_chain.py`承载链校验，避免旧1300行脚本继续堆逻辑。

`temp/research-completion-bounded-retention/tests/`：纯文件拓扑及必要真实H1只读auth；旧66bad测试夹具只读复用。所有candidate、日志、baseline和scope留在本私有目录。本报告是唯一新增公开报告。

## 最小协议

1. 先保存实际pointer和runtime receipt。completed显式opt-in仍拒同installed；default/legacy原路径保留。
2. 每个completed节点验证原normal schema/root/path/SHA、标准build来源及终态、UI/Host计数、无process/migration/model/pointer副作用、原有带时区时间顺序、validated receipt的hash/count/plugin。
3. 每跳retention必须原ref哈希匹配，passed且不是preparation-only，无原来源替代；实际prior ref给出下一节点。completed的producer时代仍180/b47，实际runtime终点仍b0b/b47；不从不可信receipt自行推导受信时代。
4. 每跳验证canonical registry/source membership、原archive validator、前节点registry逐行保留、所有原transport。Metadata与producer字节组成去重witness，前后逐字复核；registry资源再经原validator复核。
5. 用visited规范路径检测cycle/hash冲突，有限hop和去重metadata字节预算仅约束维护证据遍历。暂定32hop/512MiB；不改研究、模型、金融、PAPER或质量门。
6. 报告区分completed链与真正installed，诚实记录hop数；旧已存single-hop normal不重解释、不重写。

## TDD与交付

先以真实Main ae9行为复现two-hop正例RED，再补cycle、外来root、hash、超hop/字节预算、未完成节点、非零副作用/权限数字、producer漂移、缺transport、丢registry及source membership负例。所有合成fixture明确不是实际build/研究/金融证据。

纯文件GREEN后才用H1真实终态原件auth，不能运行producer或guard、不能改Main。新的历史capturing验证必须独立冻结并由Root按实际维护切片安排；此处auth通过本身不授权normal、归档、运行或部署。

## 本切片最终证据

唯一真实H1认证handle2628已经exit0，`actual-h1-auth-result.json`记录H1(4511)→6dd→实际installed49c(2bc1)，hop_count=2；59份去重metadata共74,601,726字节。真实Main ae9旧分支先产生`retention_maintenance_retention_binding_invalid` RED，再由候选使用实际物理Main原180 codec/b47 archive validator逐跳通过；pointer/normal前后不变。没有producer、normal guard、Query、SQL、网络、模型或业务执行。

该次实际执行源码逐字保存在`execution-source/h1-auth/`。终态后只把`Path(chain.__file__)`改为`Path(str(chain.__file__))`及空字典lambda标明`dict[str, Any]`，并换行；helper算法、认证driver字节均未变。最终shim没有重复长认证，不声称最终script SHA直接跑过真实auth；最终纯文件39tests/1.76s全部通过，包含boolean/number权限区分、legacy auth和relocation/reference的AST保真。新helper/tests/auth四文件按Main现有strict配置0，Ruff0；旧script Main基线strict61→61/Ruff57→57，rule+message新增均0。

默认/legacy原auth分支和relocation/reference原AST保持；只有显式completed选择有界链。每跳仍使用原`_registry_from_prior`，后验再次验证全部原资源；32hop/512MiB仅是metadata遍历技术边界，不代表总磁盘读取量或性能承诺，不放宽原archive内预算。新report诚实区分hop_count/completed_receipts和真正runtime installed，不授执行权限。

未来仅在Root另行批准联合采用时取私有candidate两生产文件，现D根infra的66bad快照与Main ae9均未覆盖。原standard build已有CompletedMaintenancePrior opt-in可直接forward，source collector已有infra/dsh整树采集，会收新helper；无需再改CLI或installed locator。

此结果只是链认证候选：尚未捕获`51718c5030e7dbda210b84f75cae6c3401c7b362a524a7a4244da8d8e7de5ae0`实际H1实现，未生成后继normal。未来原producer必须以H1真实receipt及其真实source/artifact preimages为prior输入，不能改用6dd重捕7b冒充后继研究归档。Root85993当前Main真实7b archive Query通过是独立结果，不与这里auth或未来capture混算。
