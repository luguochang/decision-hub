# Q-030：纯词法来源路径的等价实现

- 状态：DECIDED，2026-10-24，依据用户自主裁决和原代码渐进修复授权。
- 实证：低观测writer profile中，`_source_resolution`27310次累计9.2177053秒；其中反复构造相同祖先的`Path.relative_to`是可定位的纯词法成本。时间互含、有profile开销，不承诺收益或5秒通过。
- 范围：D的`research_graph_recipe.py`中`_source_resolution`和一个纯词法辅助函数；`_memo_helpers`仅增加新辅助函数与相关路径原方法的身份绑定；新增`tests/research/test_graph_recipe_source_path.py`。不修改`_module_file`解析、任何候选集合、每次`resolve(strict=True)`、原文/schema/runtime核验、权限/SQL/Run/CAS或既有断言，不增加文件/解析缓存。
- 决策：以原`path.relative_to(root).as_posix()`为独立oracle，证明组件级词法运算对本机原生Path的路径、大小写、相同路径、前缀冲突、相对/绝对、点/父段及异常行为等价；外来类型或不适合的路径保留原方法行为。保Windows的lower语义，不用Unicode casefold猜测等价。完整26项memo回归必须在变更后重跑，另核新增高优先候选及真实别名重定向依然失效。
- 集成条件：先RED/GREEN、相关Ruff/strict及非作者复核。Main当前recipe必须仍为已核验`5ce02ead1bf1531077383c45b3da33f9e6789368e7ad0175a1330323ad0f9d16`，逐字备份并确认8047/3097无监听后才允许本地接回，不部署服务或更改正式数据。业务验证新封包/新attempt，不覆盖任何旧运行收据。

- 结果续记：64项GREEN（含原26）/Ruff0/strict0/非作者C0/I0，本地提交3b07b97；is_relative_to动态替换的真实反例已修正。但与Q029组合实测27.2320451秒仍未达5秒，不支持采用此性能切片。已从Main逐字恢复5ce基线，D代码、单测及失败原件保留，不对各改动单独归因。下步转Q031业务读快照聚合，不重跑旧封包。
