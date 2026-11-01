# Q-040：补全 recipe 路径集合的真实严格类型

状态：DECIDED，2026-10-24。用户授权自主裁决；这是类型检查实证后的单行补全，不请求例行确认。

关闭旧配置所有 Unknown 抑制后，真实 Main recipe09c401 有3条诊断，新归档契约测试0；5ce基线也同3条，完整规则/消息没有新增。这不是完整项目strict通过。原件 `recipe-archive-all-unknown-diagnostic/scope-final.json`，SHA fb6a41dd7e77d8e908d151e41c2c34b611f6a90c8e5f151ae4143198edd27f83。

只允许 D `packages/kernel/decision_hub_kernel/application/research_graph_recipe.py` 的 `prepare_graph_recipe_manifest_v2` 内 `paths = []` 改为 `paths: list[tuple[str, str]] = []`。真实 DTO 本来要求该类型；不改表达式、控制流、运行时行为，不加 Any/cast/ignore 或配置抑制，不改旧断言。用此已有真实静态 RED 为依据，在全 Unknown 开启的同等 fresh strict 政策下核 candidate＋测试；必要 Ruff 和去掉此单行注解的运行 AST 等价验证即可，不重跑无行为变化的28业务测试。

当前 Main 构建中，Main 文件与旧证据保持冻结。本卡只写 D/正式v3文档镜像，暂不接 Main。先精确本地提交、报告和非作者复核；当前正常构建/官方Host验收仍绑定09c401，不能把它的回执用来认证后续新字节。未来 Main 接回必须核旧SHA/备份，随后按实际源码重新取得正常构建回执；不改写旧回执或历史原件。最终项目 strict/真实金融门仍按原要求，目标ACTIVE。

## 实施结果

c8d2aeb已提交并获Root非作者C0/I0。10:16:25Z备份后接Main8d79；实际Main两目标同fresh无抑制strict2files0/1.381s，源/配置前后同。未重跑运行逻辑不变的28测试，不声称整体项目0。Q039失败回执仍09c401；新normal资格待Q042真实构建，见 INCREMENTAL_RECIPE_STRICT_TYPE.md。
