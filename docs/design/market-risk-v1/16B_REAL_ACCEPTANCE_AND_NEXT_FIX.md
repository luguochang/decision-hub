# 16b 实际验收与批次17核心修复方案

更新时间：2026-10-08T12:24:46.645898+00:00。这是已定位缺口的实现/验收单；33原需求仍以[清单](IMPLEMENTATION_CHECKLIST.md)为准。正式16b运行中，候选17未晋升。每步保存before、实际源码SHA、测试命令/失败/结果、正式回执及PC证据，失败原件不覆盖。

## F17-01 输入回传与源码纯计算

证据：[BTC阶段归因](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh16b/btc-provider-phase-attribution01/ANALYSIS01.md)。source_capture5.778s、original_auth6.546s、只读source2.444s在writer前累计14.768s；该op写锁wait0.000503s；15.002710s断开。上一op确有8.914s写锁等待，两者不混为同一原因。原deadline固定。

候选 `application/research_graph_recipe.py` 按每次真实读取SHA复用纯AST import names，缓存1024路径/4MiB；全部inventory/四道认证/写许可继续新查。有限控制已P，原before/proposed/profile在[封签](../../../artifacts/market-risk-implementation-20261007/B2/source-guard-profile16b/source-seal01.json)。实际cold重建差异不是live成功证明。正式结束门：真实callback按原15s完成、源码篡改/未知helper/写前后变化仍拒绝；不靠延长deadline。

## F17-02 审核进入综合判断与动态正文

证据：[ETH同Run原件](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh16b/execution01/receipt.json)。reviewer succeeded后synthesis succeeded，但projected reviews=[]；亲读exact nativewrapper发现review存在、rootgate最终unreviewed/review_attempt_used。不得把Worker最终独立后审冒作root已消费。

做法：从已知output原件纯比对target draft/candidate/SHA/context/input refs与canonicalV2 schema，修实际Hostgate或mapping丢失条件；保护正文追溯initial4和dynamic12，8个动态body缺件需接回真实发送/结果原件，不能以DTO证明正文已送入。修复范围、原wrapperSHA、每个不匹配字段先落artifact。正控准确review进入root再影响four effects；负控stale/foreign run/candidate/ref/body hash/缺root仍拒绝。正式结束门：exact root ReviewV2和真实上下文完整，四种影响分别落库，正文/publication/quality/context一致。四种影响不等于四名审稿人。

## F17-03 O04真实首页性能

原门p95<0.5s。正式actual100 100份200及fresh严格投影P，延迟F，p95=1.329321s；[原件](../../../artifacts/market-risk-implementation-20261007/B2/formal-o04-16b/actual01/receipt.json)。各资产一次物理ROprofile同源/changes0，700/705原件、712/717SQL；主要是每capture一次ORM和重复full JSON解析。

做法：MarketPathStore同会话批量读capture元数据；每个原件仍新鲜读取并SHA核验，按本次已验证digest+row_index复用有界纯解析行。不缓存完整摘要、时钟、metadata/授权/健康/quality，不截断原件或默认未知为有效。记录cache内存上限/淘汰。正控同fixture/冻结actual两资产完整DTO与基准相同；负控恢复mtime篡改/缺原件/错URL时间/冲突修订仍拒绝。新正式版本部署后才允许再一次100请求，无warmup/重试，fresh完整投影和延迟均须P。

## F17-04 真实用量清点

selected dispatch finish可读usage不等全Run。Core四源实现独立root/child session及dispatch清点、每input/finish/auth原件、冻结census/public DTO；public手写Zod与中文coverage已有候选。当前Host只有root resultEvents，child独立分母不足则coverage incomplete/totals null，费用始终未知；不自证成功、不以限制当消耗或模型理解。有限原件控制须覆盖complete可信分母、partial/child缺、tamper、old sidecar兼容。真正整门仍须Host producer补独立child生命周期清点及实际新Run贯通，不能把局部DTO兼容称G07完成。

## F17-05 自动复查与PC真实语义

30m已到期真实父计划P，但日nonmanual计数12/限12、随后窗口过期无child。保持其终态；review admission不按manual伪造、不提高配额冒充测试。诊断失败调用占用、来源/优先级及每日12与资产daily/三个horizon的容量关系，确定有限可维护策略后实现，验一个自然计划→同父child→publication/caseconsumer并发幂等。

PC候选已修 source显示OKX、回传超时中文、native done但Run未保存的进度标题、complete report停止说明。旧报告完整JSON及资格不改。标准编译后核两个真实状态，补截图。depth/range/directionaltrade及泛化缺口说明保留问题记录，后续按已知语义映射。首页未读质量/反方不能措辞为无反证，应引导打开真实报告。

## 合批发布及收口

1. 五条核心门由负责agent独立完成，Root审源码/证据，封签合批；禁止微改一次构建。
2. 标准停止/保留16b实际recipe/source版本，晋升、标准构建一次、标准Ready一次。原guard与2MiB不变。
3. 真实BTC/ETH各一次新请求，实际终态后各report/quality/context一次；同Run核PC原件下载、质量及完整正文；自动计划仍须真实child。
4. Q05真实20材料/两方法/模型理解、U01三非开发用户、真实自然告警/全分母/固定28日/成熟标签及黄金来源权利按原协议独立保留。当前全部未满足，whole_product_accepted/financial_effect_qualified保持false。

## 已进一步定位的根因（2026-10-08）

- 审核确实已执行：V2第4个问题的 blocking_scope 包含未允许的 monitor，canonical schema拒绝；不能自动删掉或映射该内容。修复为同一审稿child一次有界schema反馈/修正，保留首无效原件，第二次仍无效则明确失败。
- 8份动态完整证据以及审核在官方上下文prune后不在最终GenerateOptions中。动态accepted DTO约18,945B；旧review wrapper约379,594B，其中actual_prompt约299,134B、合法receipt约7.4KB。补受管保全块，重送完整accepted DTO和真实合法receipt并绑定原wrapper/hash，不重塞无关旧prompt，不以ID或摘要替正文。实际options必须留原件，Reader反核同Run原件/DBaccepted身份。
- 第二现货两次请求都是ETH-USDT、spot_price/spot_volume，Gateway在provider前合法拒绝：冻结crypto_spot_confirmation只允许spot_market_facts、fallback为空。已安装Gate/Huobi公共spot路线却挂在market.crypto_derivatives。候选修正显式合法第二spot fallback及非空spot-only字段约束，保scope/绑定/来源独立门；不放开衍生品冒现货。
