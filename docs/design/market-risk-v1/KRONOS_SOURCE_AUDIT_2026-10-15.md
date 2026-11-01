# Kronos 全仓源码阅读范围与复用裁决

日期：2026-10-15。关联：[产品与架构实施总案](PRODUCT_VALUE_AND_PLUGGABLE_FORECAST_IMPLEMENTATION_BLUEPRINT_2026-10-15.md)。本文件为静态审计记录，不是运行测试、漏洞扫描或金融效果报告。

## 1 准确回答“整个代码读了吗”

前次研究读了论文、接口和关键推理代码，没有逐文件阅读整个仓库。本轮将官方仓库完整浅克隆到独立参考目录，并锁定提交：

- 仓库：[shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos)。
- 提交：`67b630e67f6a18c9e9be918d9b4337c960db1e9a`。
- 本地参考目录：`E:/project/study/codex/dsh/reference-research/Kronos-20261015`。
- `git ls-files` 清点：92 个受版本管理文件。
- 全部 `.py/.html/.sh/.yaml`：33 个、12,546 行；包括空的 Python init 文件。
- 本轮由主代理与两个研究代理分工，逐文件完整静态阅读上述 33 个文件；另读根 README/LICENSE/requirements、WebUI 说明与依赖、CSV 中英文说明及 gitignore。
- 数据 CSV、报告 JSON、PNG 等清点用途，不声称逐条验证其数据、图像或报告。没有阅读浅克隆之外的全部历史 commit，也没有声称审计所有上游依赖源码。
- 未执行其代码、安装依赖、下载权重、运行回归、训练、连接交易所或复现收益。参考克隆保持未修改；业务项目尚未接入 Kronos。

因此现在可以说“已完成该提交全部源码/配置的静态阅读”，不能说“全部功能已跑通”“论文已复现”“实盘能力已验证”。其他被比较的开源项目是官方资料与接口研究，不在这次全仓审计声明内。

## 2 全部源码/配置覆盖清单

以下文件均以该固定提交为准。行数按本地文本读取计数，均已全文阅读。

| 文件 | 行数 | 实际职责 |
|---|---:|---|
| `model/__init__.py` | 17 | 类导出和名称注册 |
| `model/kronos.py` | 662 | Tokenizer、Kronos、采样与单/批量预测 |
| `model/module.py` | 570 | BSQ、注意力、位置编码、embedding 与损失 |
| `finetune/config.py` | 131 | Qlib/A 股训练、测试配置 |
| `finetune/dataset.py` | 138 | pickle 窗口数据与随机采样 |
| `finetune/qlib_data_preprocess.py` | 130 | Qlib 读取、字段和切分 |
| `finetune/qlib_test.py` | 362 | 股票排名信号与 Qlib 回测 |
| `finetune/train_predictor.py` | 244 | DDP 预测器训练 |
| `finetune/train_tokenizer.py` | 281 | DDP tokenizer 训练 |
| `finetune/utils/__init__.py` | 0 | 空文件 |
| `finetune/utils/training_utils.py` | 118 | NCCL、种子及辅助函数 |
| `finetune_csv/config_loader.py` | 267 | YAML 配置与路径 |
| `finetune_csv/configs/config_ali09988_candle-5min.yaml` | 72 | 港股 5m 示例 |
| `finetune_csv/finetune_base_model.py` | 468 | CSV 数据集和预测器训练 |
| `finetune_csv/finetune_tokenizer.py` | 359 | CSV tokenizer 训练 |
| `finetune_csv/train_sequential.py` | 361 | 两阶段训练协调 |
| `examples/get_akshare_date_2024-2025_x.py` | 629 | 股票数据获取与清洗示例 |
| `examples/get_date_new.py` | 661 | 数据获取与后处理示例 |
| `examples/prediction_akshare_2024-2025.py` | 545 | 股票预测、图表及报告 |
| `examples/prediction_batch_example.py` | 72 | 多序列推理示例 |
| `examples/prediction_cn_markets_day.py` | 208 | 中国市场日频示例 |
| `examples/prediction_example.py` | 80 | 基本 OHLCVA 推理 |
| `examples/prediction_new.py` | 1333 | 下载、预测、规则增强与报告 |
| `examples/prediction_new_GUI.py` | 1625 | 本地 GUI、预测与大量后处理 |
| `examples/prediction_wo_vol_example.py` | 68 | 无成交量推理示例 |
| `examples/run_backtest_kronos.py` | 455 | 预测文件/真实价格的简化回测 |
| `examples/yuce/historical_backtest.py` | 384 | 随机游走“预测”的历史演示 |
| `tests/data/generate_regression_output.py` | 91 | 固定权重 revision 生成回归样本 |
| `tests/test_kronos_regression.py` | 140 | 预测数组与少量 MSE 回归 |
| `webui/app.py` | 708 | Flask、文件读取、模型全局状态、推理/图表 |
| `webui/run.py` | 89 | 启动和依赖安装提示 |
| `webui/start.sh` | 40 | Shell 依赖检查与启动 |
| `webui/templates/index.html` | 1238 | 参数/窗口选择、Plotly 与误差展示 |

合计：model/finetune/finetune_csv 源码配置 4,180 行；examples 6,060 行；webui/tests 2,306 行。额外说明文档不计入 12,546 行。

## 3 它是完整开源项目，但不是可直接替换的完整产品

仓库包含实际模型结构、推理、微调、测试、演示程序和 MIT 许可证，公开 mini/small/base 的外部权重入口。large 在 Model Zoo 中标未公开。源码里有随机初始化分支，但没有完整公开全球预训练数据采集、清洗、去重、训练清单和权重再现链；HF 权重与配置也不包含在本地 Git 源码中。[固定版本 README](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/README.md)

其核心问题是“怎样对金融 K 线建模”。我们的产品问题还包括“当时知道什么、多久前发布、什么条件行动、来源出错如何失效、是否提醒到用户、何时结算和是否值得使用”。这些能力不能从 Kronos 的一张预测图或股票回测中取得。

直接复用源码需保留 MIT 版权与许可证；独立记录权重卡及所选 revision 的许可。数据获取权限和再分发权限另核，不由仓库许可证自动授予。[固定版本 LICENSE](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/LICENSE)

## 4 推理内核的实际行为

最小运行模块为 `model/` 三文件；依赖主要为 PyTorch、NumPy、Pandas、einops、huggingface_hub、tqdm，权重序列化还涉及 safetensors。Qlib、Comet、Flask、Plotly、Matplotlib 不是核心推理的必要依赖。根 requirements 的 `torch>=2.0.0` 不是可复现部署锁文件。

调用链为：六通道数据与时间字段 → 按历史通道均值/标准差归一化与 clip → tokenizer 离散化 → 自回归 token 采样 → 解码 → 反归一化。输入 OHLC 必需，缺 volume 时 volume/amount 都填零，缺 amount 时使用 volume×OHLC 均价估算。真实和合成 amount 应在适配器标记，不能混成同样质量的数据。[输入处理](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/model/kronos.py#L519)

多样本在 `auto_regressive_inference` 中扩成 B×S，生成后恢复成 `[B,S,T,6]`，第 467 行求样本均值。公开 `predict()`/`predict_batch()` 返回均值序列；batch 是多个独立序列并行，不表示跨资产联合建模。[采样与平均](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/model/kronos.py#L394)

small/base 的推荐 max_context=512。实现逐步重算滑动窗口，没有 KV cache，最终解码受窗口限制；不应由“能滑动”推导任意长预测都有效。适配器应在归一化前明确裁剪输入，显式限制预测长度，不能依赖内部隐式截断。[滑动采样](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/model/kronos.py#L398)

内核没有本产品所需的 PIT、收盘状态、连续频率、OHLC 几何、正价格、非负量和无限值等完整校验；输出也没有硬性 OHLC 合法约束。这些是我们的 adapter 合同职责，不能默认模型自动满足。

## 5 对接入有直接影响的静态发现

下述为该提交源码上的发现。未执行复现；需要运行确认的影响均在进入相关流程前做最小实验。不能把某训练/演示分支的问题推断为线上推理内核整体无效。

| 发现 | 源码依据 | 对我们的裁决 |
|---|---|---|
| 通用 `model` 包名和 sys.path | [kronos.py:9](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/model/kronos.py#L9) | 隔离包名、相对导入、独立环境；业务进程不直接 import |
| 多路径被平均 | [kronos.py:467](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/model/kronos.py#L467) | 原路径接口单独适配，不从均值反推概率 |
| CSV 用含未来目标的整个窗口算归一化 | [finetune_base_model.py:118](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/finetune_csv/finetune_base_model.py#L118) | CSV 流程不能直接作为严格事前微调/评测；Qlib dataset 使用 past_x，不能一概而论 |
| 整段 token 验证的交叉注意力在 eval 时关闭 causal | [module.py:387](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/model/module.py#L387)、[验证入口](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/finetune/train_predictor.py#L135) | 早期 s2 query 可关注后续 hidden state，整段验证 loss 不能直接当事前预测成绩。在线逐步 decode 没有传入真实未来，是另一条路径 |
| 两个 CSV 独立 main 内 os 后导入形成局部变量 | [tokenizer:296](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/finetune_csv/finetune_tokenizer.py#L296)、[base:380](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/finetune_csv/finetune_base_model.py#L380) | 静态可见先使用未赋值局部 os 的路径；不承诺独立训练入口直接可跑，先做复现 |
| 非默认从零 tokenizer 配置的两阶段未加载前阶段结果 | [train_sequential.py:167](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/finetune_csv/train_sequential.py#L167) | 不直接复制串行微调流程；冻结并验证 checkpoint 交接 |
| CSV DDP forward 绕开 wrapper | [base:291](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/finetune_csv/finetune_base_model.py#L291) | 多卡同步另做最小复现，README 声明不当测试证明 |
| WebUI“latest”分支取文件前段预测，JSON 时间却从全文件末尾继续 | [app.py:468](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/webui/app.py#L468)、[571](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/webui/app.py#L571) | 在有更长历史文件时，结果标签与真实预测原点错位；不复用此 API 或展示流程 |
| WebUI 模型为全局可变状态、权重未锁 revision | [app.py:629](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/webui/app.py#L629)、[645](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/webui/app.py#L645) | 不作为并发生产模型服务；由自己的 bundle 和隔离 executor 管理 |
| WebUI 缺时间戳时合成 2024-01-01 小时序列 | [app.py:103](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/webui/app.py#L103) | 产品缺真实时间必须拒绝或显式标合成，不可用于事前认证 |
| 简化回测在无真实 future 时用预测价格作收益计算 | [run_backtest_kronos.py:172](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/examples/run_backtest_kronos.py#L172) | 属模型路径上的模拟，不能作为市场收益；不纳入正式评测器 |
| 同回测前填信号及空头现金流值得修复 | [run_backtest_kronos.py:154](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/examples/run_backtest_kronos.py#L154)、[204](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/examples/run_backtest_kronos.py#L204) | 0 平仓状态和开空现金流不适合直接评估策略；使用本项目有版本的纸面现金流 |
| historical_backtest 未调用 Kronos，使用随机游走 | [historical_backtest.py:73](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/examples/yuce/historical_backtest.py#L73) | 文件名和展示名不能作为模型实际参与的证据 |
| “宏观/行业增强”含大量写死强度与事实 | [prediction_new.py:497](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/examples/prediction_new.py#L497)、[555](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/examples/prediction_new.py#L555)、[729](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/examples/prediction_new.py#L729) | 不借用其增强评分或把整条路径乘高当作事件融合；需要认证事实和独立样本外验证 |
| 数据失败后可生成模拟历史，保存未区分 synthetic | [prediction_new.py:101](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/examples/prediction_new.py#L101)、[191](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/examples/prediction_new.py#L191) | 不复用下载/降级链；产品不以合成数据冒充真实行情 |
| GUI 随机修正价格/量及预设假期涨幅 | [GUI:1031](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/examples/prediction_new_GUI.py#L1031)、[1127](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/examples/prediction_new_GUI.py#L1127)、[1244](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/examples/prediction_new_GUI.py#L1244) | 不借用后处理作为校准，原预测与规则产物必须分层保存 |

WebUI 前端常规路径会传 `start_date`，故“latest”错位特指 API 无 start_date 分支，不能写成所有 UI 请求都存在同一错位。前端误差计算按数组位置对齐，不能替代按真实时间及目标身份的评测。

根 README 和 WebUI 文档部分说明与实际代码不完全一致；例如“amount 不使用”和“模型不可用将模拟预测”不能脱离真实分支理解。设计以锁定源码和实际运行回执为准。

## 6 训练和回测还有哪些环境假设

Qlib 目录绑定中国股票池、REG_CN、日频交易、pickle 与 `vol/amt` 字段；部分成交额为合成，股票 TopkDropout、次日开盘和费用设定不适用于 BTC/ETH 24×7 的操作评测。默认推理设备含 `cuda:1`，NCCL/CUDA 初始化与 Comet 依赖需要单独准备，根 requirements 未覆盖全部训练依赖。

Qlib 配置的原始日期区间有重叠，但不能仅凭字符串重叠就判定所有训练样本泄漏；应按预测原点、目标区间和实际 loader 处理验证 purge。其 dataset 按 past_x 归一化，区别于 CSV 流程的全窗口问题。

Qlib 数据集忽略传入 idx 而随机抽全池，DistributedSampler 不等于严格不重叠分片；多 worker 随机状态需验证。所谓梯度累积有的实现是拆一个 loader batch 为微批，不能据配置直接推断有效 batch 成倍增加，余数处理也要核验。

CSV 配置包含占位绝对路径；默认配置入口与实际示例名不同；读取了 `max_context` 不表示所有训练窗口已经受到该值约束。这些都支持把训练作为独立离线流程，不能让它成为首版产品可用性的前置依赖。

## 7 测试目前能证明什么

仓库回归测试锁定 small 模型 revision `901c26c1332695a2a8f243eb2f37243a37bea320` 和 tokenizer revision `0e0117387f39004a9016484a186a908917e22426`，CPU 固定种子；测试 512/256 上下文的输出与少量随机样本 MSE。MSE_SAMPLE_SIZE=4。[回归测试源码](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/tests/test_kronos_regression.py)

这是有用的模型行为兼容测试。它没有验证我们的数据来源、事前发布、4h/24h/72h 概率校准、条件操作、成本、仓位、提醒或实盘收益。本轮也没有执行这些回归，后续应在隔离环境先跑原测试，再加产品合同与前瞻评测。

## 8 最小复用边界及补丁计划

```text
Decision Hub 领域合同
        ↓ 认证快照 / 版本化 ForecastRequest
独立预测 adapter 与进程协议
        ↓
独立环境：明确包名的 model/ 三文件 + 锁定依赖/权重
        ↓ 原始路径 / 输入hash / 推理回执
Hub 校验、原件入账、校准、策略、Gate、发布与结果
```

第一阶段允许的补丁：包内相对导入与命名空间、显式 eval、与旧默认接口兼容的原路径出口。输入输出验证、参数冻结、种子集合和时点检查尽量放在我们的 adapter，避免修改模型算法。

优先重复调用 `sample_count=1` 得到完整路径并测性能；若需要批量原路径出口，必须同步处理后续切片、反归一化与维度，不能只删一行 mean。原均值接口的回归结果要保持，新增路径形状和平均一致性另验。一致性比较采用同一次采样路径及其均值；多次单路径与一次批量采样可能消耗不同 RNG 序列，不能仅凭相同种子要求逐值相等。

不在首次接入中改注意力算法、不复制 CSV 训练、不引入 Qlib 股票交易回测、不带入 UI 和下载器。不把静态发现顺手修成一大套 fork；每个算法改动都是新的模型候选，需要独立资格。

固定上游 commit、权重 revision/hash、依赖 lock、补丁 hash、设备与 dtype。发布后禁止跟随 latest 静默变化。新候选经过合同、输入、性能、冻结样本外和前瞻对照；失败就停用组件，领域账本与基线不迁移。

进入生产前需完成的最小验证：原回归可运行；非法/未来/断档输入被拒；历史 cutoff 相同时改变未来 suffix 不改输入和预测；原路径有效与均值兼容；超时/OOM/崩溃/重复响应可恢复；模型包停止后 API/通知仍工作；同一快照的基线比较、成本后操作评估和全机会分母完整。

这份审读支持“复用真实推理内核、独立验证产品价值”的方案，也解释了为什么既不应仅抄一个提示词，也不应把整个开源仓库直接嫁接进产品。
