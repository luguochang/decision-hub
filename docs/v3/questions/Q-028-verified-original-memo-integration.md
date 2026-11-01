# Q-028：已核验原模块接回本地原工作副本

- 状态：DECIDED，2026-10-24。依据用户已授权自主判断和原代码渐进修复，由根执行者裁决，不再等待额外确认。
- 问题：D隔离目录只保全了当前前端/native/contracts及具名后端模块，其余Python是旧基点。给Python强塞候选模块或改写`__file__`会歪曲原件身份；直接用D运行也不能证明当前原系统行为。
- 决策：完成RED/GREEN、owned strict/Ruff与非作者只读复核后，只将已核验的`research_graph_recipe.py`接回Main本地源文件。D本地提交`f23ef9d`包含该文件和26项回归。原文件必须先逐字备份，写前再次核对旧SHA及8047/3097无监听；任何条件不符即停止本次写入，不自动覆盖新改动。
- 写入范围：`E:/project/study/codex/dsh/decision-hub/packages/kernel/decision_hub_kernel/application/research_graph_recipe.py`，从SHA `721e2d8d79560a6f43b554710dedee01569c0ddf5bc92363b85ea708b275bc87`到`5ce02ead1bf1531077383c45b3da33f9e6789368e7ad0175a1330323ad0f9d16`，保留CRLF。这是本地源码集成，不是服务发布，不修改Main其他已有脏文件，也不在Main批量提交。
- 保留：原界面、权限、原件鉴真、完整目录库存、CAS、原业务clock及5秒断言；正式数据、数据库和历史原件不变。不启动8047/3097，不改原profile，不调用付费模型。
- 计时：另用新建的timing-control包运行一次既有sameRun用例。该包明确不提供临时全读集hash和目录枚举观测，只保留实际文件打开/写入/DB/子进程/网络限制；产品自身所有校验不变。仅重新钉住确实改变的Main单文件SHA，其余48个pin必须相同。不能宣称严格诊断包在原观测配置下通过。
- 验证边界：26项测试、两文件strict0及Ruff0已核验；非作者收据`D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manifest-memo-peer-final.json`。实际同Run/5秒/Host/真实PAPER与长期指标仍以新运行原件为准；未通过时保留失败，不放宽门槛。
- 实际写入、端口快照、前后字节和pin更新在`D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manifest-memo-main-integration/`记录；本裁决本身不声称写入或业务验证已完成。
