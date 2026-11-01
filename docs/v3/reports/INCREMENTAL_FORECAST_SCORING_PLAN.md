# 原预测结果评分与学习数据链 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this bounded slice with TDD.

**Goal:** 原BTC/ETH × 4h/24h/72h已发表预测，在真实到期后沿原采集、原件持久化、原PC和下轮经验入口获得可追溯评分事实。
**Architecture:** 旧forecast experience v1四个software输入保持逐字不动。新增明确v2 producer/reader和truth helper，原normal runtime负责新版本到期；原Worker输入与provider-use按显式版本接新事实，不重算旧行。原PC按result-view v1/v2分派，不换工作台。
**Tech Stack:** 现有Python/Decimal/SQLAlchemy、原HTTP capture/archive、TypeScript/Zod/React；不新增依赖。
**Spec:** Main AGENTS当前渐进授权、docs/v3/01_REQUIREMENTS.md §3.1/3.3/5.2；TASK_MASTER_LIST T701–T703。完整曲线、训练、四周真实样本和金融/PAPER资格不由此切片宣告完成。

## Global Constraints

- Main、正式DB、服务、真实HTTP/模型只读或不运行；实现/fixture全部D隔离。共享index由Root协调；最新已明确授权空index后精确提交本白名单，不并发stage，不采用Main。
- 已冻结TaskA/B/PC/retention源不修改；V6只经原Action publisher成功冻结target后进入到期链，报告complete本身不是预测发表。
- 旧application/perpetual_forecast_experience.py、ports/perpetual_forecast_experience.py、ports/perpetual_close.py、application/market_original_parse.py保持原字节及真实__file__，旧v1 method/hash/read不改变。
- 不修改任何旧行、截止、旧概率或原时间；旧v1样本不补算logloss。新v2存原概率和onehot、Brier、logloss及原件refs。
- p_actual=0的logloss显式infinite，未知为unavailable；不引入epsilon伪装有限分数。收益率不当PAPER PnL。
- 旧14自然日、四周、事件、PAPER与全部最终门保持。合成native、venue或私有SQL验证不是成熟真实样本。

## Review Focus

1. V6原Commit后原Action publisher必须形成独立冻结target/forecast；无发表不得采真值或填标签。
2. 未到期、错资产、重复/缺页、cursor断链、未确认分钟或未来原件均不得形成评分。
3. 4/24/72小时需要241/1441/4321完整边界分钟；新策略最多1/5/15页，不改旧close全局6页策略。
4. 旧v1 hash/reader完全兼容；新v2与学习selection/use显式版本，不以今日代码/时间重写历史。
5. source refs、Task/ProviderInput/causal-use区别保留；PC可读不冒实际经验改善或金融资格。

## Task 1 — 六组合到期真值与持久评分

**Files:** 新 application/perpetual_forecast_scoring.py、application/perpetual_forecast_truth.py、application/perpetual_forecast_truth_capture.py、ports/perpetual_forecast_scoring.py；修改 application/perpetual_forecast_runtime.py；测试 tests/runtime/test_perpetual_forecast_scoring.py、candidate_perpetual_forecast_support.py 及同前缀 focused tests。
**Interfaces:** 新experience.v2/role/sourceID；原publication target与snapshot/ref为唯一预测入口。derive/record/read保持原prepare_originals与同事务写后校验。v2记录每类prediction/onehot及asset/horizon，缺事件族/regime保持unknown。

Root已明确批准追加 apps/hub_worker/composition.py 一个原构造点参数：原runtime默认v1，真实normal composition显式选择v2。逐字Main基线与D前像另存 composition-baseline.json；不改旧四个software输入。新truth codec为okx-perp-forecast-truth.v1，独立15页上限，不扩旧codec的6页身份范围。

- [x] 封存精确Main基线，实际六组合/旧v1及数学、分页、normal等RED/GREEN留据。
- [x] publication鉴真、分页真值完整性与v2评分/record/read；旧v1 reader实际来源及原件保真。
- [x] 完整cursor、缺页/PIT/错资产/未到期、私有SQL幂等/冲突/自重封拒绝有实际证据。
- [x] 原V6 native提交后，同Run新一致copy经原Action publisher→due→reader→下一Task/Input完成分段闭环；三次新test与continuation exit1均保留，合法prior修复后另RO exit0，不冒单次native全GREEN。

## Task 2 — 原学习输入与PC读取

**Files:** 新 application/perpetual_forecast_scoring_view.py、application/perpetual_forecast_scoring_inputs.py；修改 application/perpetual_forecast_result_view.py、application/perpetual_experience_use.py、apps/hub_worker/research.py（仅经验prepare/freeze接线）；后续原PC api/perpetualForecastResult.ts、product/PerpetualAdvicePanel.tsx及各自focused tests由C独立负责，本文作者不改PC。
**Interfaces:** result-view.v2包含新评分及可靠性输入；v1保原形状。含新experience.v2的Task selection/provider-use显式v3，旧v1/v2分支不变；同owner/asset/horizon/冻结时点选择与ProviderInput实际接收、因果引用分开记录。

- [x] 原下一Task/selector/ProviderInput两实际原件：referenced与not_referenced分开；raw输入、prompt/task/ref全联结，合法snapshot重派与内存重封拒绝。
- [x] C保原PC查询/组件v1/v2，原builder八wire覆盖六组合/无限/未知；已独立peer、D提交c65914167dfcaf75cfd5753b0422f641bd1168c5，本文作者未触其两生产路径。
- [x] D定向回归、类型/Ruff与非作者peer有据，Scope最终逐pin封存后按Root授权精确本地提交。
- [ ] Root Main精确采用/采用后验证、新normal及真实成熟取证另行执行；ETH08运行期间Main保持冻结。

## Current audit

原Worker research.py:1700/1747调用原Action发布hook，publisher._read_committed不硬限定V5。report V6本身不写ForecastRecord；必须明确原Action发表成功才有target进入runtime。现runtime只挑4h，旧experience最多两capture/241分钟，result/PC也只接受4h。旧reader重算_derive包含实际software文件SHA，故本方案保四原输入文件不动，不假造旧来源。

后端候选白名单共11生产源（6新、5原接线）+10新focused测试/支持文件+本计划/最终报告。测试/支持文件为 test_perpetual_forecast_{scoring,truth,collection,scoring_store,scoring_runtime,scoring_learning,scoring_legacy,scoring_native}.py、candidate_perpetual_forecast_support.py、candidate_perpetual_scoring_provider_support.py。Scope最终逐文件列SHA，不包括temp原件和C的PC白名单。仅Root授权的23文件精确本地提交，不stage其他来源。

最终证据见INCREMENTAL_FORECAST_SCORING_REPAIR.md：七focused联合55PASS（snapshot修复前）；修后纯49PASS、真实两v3原件RO重派exit0/仅255条BEGIN/SELECT、实际自重封仅内存backup拒绝。Main配置新六源/十测试0；旧五源140→119、零新增，旧Ruff18I001/25E501保同数。Source101和Target198全部SHA前后同。不是整仓strict、单次native测试全GREEN、真实模型使用或四周成熟样本。
