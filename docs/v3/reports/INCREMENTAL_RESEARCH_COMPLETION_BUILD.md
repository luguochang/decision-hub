# 完整研究正常构建与当前历史回读实证

2026-10-26接续。33文件已精确采用Main、三组必要回归70/77/97 PASS之后，Root唯一正常构建及当前Main历史回读已取得实际结果；尚未部署，也未新增真实研究或PAPER。

## 正常构建

Root exec47376持同一进程句柄：outer22160/FILETIME134374342429091824，supervisor11764；UTC2026-10-25T20:37:22.7785193Z至20:59:32.1308297Z，1329.352451秒、exit0。原builder21100/FILETIME134374342464127151，UI/Host各一次且exit0。原assessment、retention和临时/最终完整来源校验通过；migrations/process starts/stops/pointer writes/model submissions均0。

- 原standard producer：Main `infra/dsh/build-product.ps1`，显式CompletedMaintenancePrior，真实完成的6dd为source prior，实际installed49c保持不变。
- 新normal：`E:/project/study/codex/dsh/decision-hub/artifacts/incremental-delivery-20261026/research-completion-build/product-validated-builds.json`，SHA `4511e6c6c71e639a379f5562dcf6c52665ab4b957bae3ebc568c2a3510b4de4f`。
- 实际native fingerprint：`d4b1c07c74af4f4d442240800f5c42752138c8da6af529cca80dadffe66de699`。
- 有限postflight另已实际exit0/errors=[]；`final-receipt.json` SHA `57519b0cc1ef59224e7344e2583322948ee52edc4698d41705cc3f9db49a4fbd`，目录 `D:/DecisionHub-v3-development/evidence/legacy-sdk-review/research-completion-normal-build`。

71 controls、71 native输入、61份当前采用文件、542份真实prior输入、588份当前产物preimage均有逐SHA后验。原收据raw sources1807、完整guard有效sources61398、artifact map48、实际dist/lib607文件及auxiliary是不同范围。构建耗时不用于研究P50/P95统计。

原producer在本次真正standard流程中捕获7b7d实现，registry为 `486b535821f733996287e6fed0b42b3a7a0852328d2b973bd250415142edef05`、176注册；原175项保留。未手写注册或normal，没有将6dd假称installed。

## 当前Main经真实归档回读旧07

Root另用原normal启动完整guard实际通过后，在忠实restart01三文件的新副本上调用真正Main bootstrap及原SavedReportQuery；非空WAL被完整保留，没有使用immutable忽略WAL。Source原数据库只作字节复制，没有SQLite打开；查询仅新私有副本。

首次exec79199实际exit1：guard_returncode=0，Query已经返回，私有观察器在比较归档模块位置时把合法Windows `\\?\E:`与普通`E:`表示误判为不同目录。原FAIL、stdout/stderr及private SHM读锁/索引变化均保留，没有复位或重复制。

纯Windows路径实际RED后，新增r2观察器5项纯测试PASS；保留原包、真实guard和失败原件，只修本地扩展路径身份比较，并增核每个加载文件等于原index/payload字节。Root审读冻结scope `10ea9ee94218166e20915c04f5717523e5c36a38887fc150bc7d176472124c83` 后，唯一exec85993实际exit0，没有重跑build、fullguard或复制数据库。

实际结果：

- 当前Main manifest为 `51718c5030e7dbda210b84f75cae6c3401c7b362a524a7a4244da8d8e7de5ae0`，与旧07的7b7d不同。原Query前未加载旧private reader，后通过真实registry加载7b7d。
- 255个实际带物理文件的归档模块，其文件SHA均等于原验证payload和index；没有假造`__file__`或注入registry binding。
- 17个共同原字段、source_json/artifact_json实际UTF8字节全部等于原200响应，仅read_at反映新的实际读取时间。
- 1142条SQL仅BEGIN/SELECT，两个session total_changes均0；Source三文件、Target DB/WAL与normal SHA前后同。金融与执行两个false标记保持原值。

证据在 `D:/DH-report-current-archive01/evidence`：`query-result-r2.json` SHA `6c86fcb79bad8c428eb3d19b383c2bbd17816420ae3a60009ee2e2d486b09211`；`observer-r2-terminal.json` SHA `7e549c79e54e8ebec431a434ce038d9b68bb4d2b3ed7f5fb0ada1e6efc978570`。

这闭合了本次旧07当前版本归档读取缺口，不将旧07升级为合格研究，也不证明所有历史实现、可移植灾备、PAPER恢复或新模型内容已通过。后续真实ETH4h执行包正在封存；所有最终业务、质量及自然时间门继续保留，目标ACTIVE。
