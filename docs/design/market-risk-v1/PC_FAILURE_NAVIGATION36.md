# PC失败入口36：具体整改与验收

## 真实问题

35新ETH已知Run失败后，首页“查看研究执行记录”仍按请求状态跳到进度页，再到失败编辑页，再到原记录，需要三次点击才能看到原生失败及保存材料。[真实失败截图](../../../artifacts/mr-pc35/eth-failed35_01.png)和[操作回执](../../../artifacts/market-risk-implementation-20261007/B2/release35/pc35_limited01.json)保留。

## 实施

DEV `apps/decision-desk/src/product/RiskResearch.tsx` 仅改已有导航条件：API明确返回Run `failed` 或 `cancelled` 时，直接进入同一Run的只读结果/执行记录页；已完成仍进入结果页，排队、运行或状态未知仍进入进度页。已有“查看原因与恢复”、原件与显式重试保留。没有新POST、自动重试、报告资格或错误原因推断。

## 验收与状态

既有控制6P：两个保存的真实13失败DTO直接打开原记录；未知/运行中观测仍走进度；取消记录直接只读；正常排队保持。其余既有测试未执行，控制不等于真实PC已通过。修改当前仅DEV，ROOT35保持。合并核心修复后一次正式发布，再实际点击新ETH首页记录按钮，核对同一Run、失败说明、材料和恢复边界，无额外提交。

[受控源码和6控回执](../../../artifacts/market-risk-implementation-20261007/B2/release35/navigation36-seal01.json) · [当前任务](CURRENT_FOCUS.md)。
