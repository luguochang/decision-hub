# 报告准备：SourceSnapshot v2 实施与验收

## 当前结论

2026-10-11，58候选已选择性安装，正在标准构建。组合控制251项通过；真实新BTC/ETH保存和独立确认尚未验收。原BTC一次恢复后准备超过120秒，已取消，Saved/ACK均为0；不会将其改写为成功。

## 问题与边界

原准备流程在同一次原生只读事务内，反复核验源码全字节和完整目录。已有release scope只复用安装资料，未覆盖这些源校验。旧私有读取器不会自动接受新的作用域；继续增超时或扩共享模块无法形成稳定接口。

新版端口声明不可变文件身份集：相对路径、字节数、SHA和集合SHA。数据不是权限。正常读取与冻结重放分别建立由服务端签发的原生只读作用域，使用同一文件身份DTO；没有跨请求缓存结果或许可。

```mermaid
flowchart LR
  A[当前安装资格与原source issuer] --> B[正常原生只读事务]
  B --> C[完整入口核验并持有不可改写文件句柄]
  C --> D[原权限及原件读取]
  D --> E[完整出口核验]
  E --> F[文件身份DTO及冻结材料]
  F --> G[独立冻结事务与本地作用域]
  G --> H[同DTO及完整原件重放比较]
  H --> I[原保存协议及独立ACK]
```

## 具体代码

| 文件 | 职责 |
|---|---|
| ports/research_source_snapshot.py | 版本化DTO和租约端口；禁止application/ORM依赖 |
| persistence/research_source_snapshot.py | 原生连接、事务、线程、任务、issuer绑定；Windows不可改写句柄及入口/出口完整校验 |
| application/research_graph_binding.py | 仅显式v2作用域复用；无作用域仍执行原完整读取 |
| application/research_publication_frozen_reader.py | 显式v2读取入口；原read_registered_original函数保留 |
| application/research_publication_runtime48.py | 当前安装三份源向量资格、正常与冻结各自接线；旧ABI在昂贵准备前拒绝 |

读取过程中检查原生只读身份、活跃句柄、实时schema/selector/runtime及实现绑定；入口和无缓存出口核验完整目录、文件字节、句柄及当前路径身份。新增未选文件在出口拒绝；原文件写入和删除由持有句柄立即阻止。作用域未正常退出的结果不能进入保存与ACK。

源文件2MiB、总源32MiB、材料192MiB及发布计算120秒保持。原65源、3配置、授权、未知提交围栏、48机会及28日协议保持。原Root57实际history host作为依赖保留；没有安装DEV的旧host。

## 已验证与待验证

| 验收 | 结果 |
|---|---|
| 正常/冻结独立作用域与DTO、身份变化及退出拒绝 | 35快速控制通过，独审无实质阻断 |
| 16000目录项、2048真实Windows文件、60次复用 | 规模控制通过，总16.219秒；这不是实际报告性能 |
| 新旧接口、取消/恢复、续租、保存围栏组合 | 251/251通过，无错误或跳过 |
| 正常标准58构建/启动及当前资格回读 | 正在执行 |
| 新BTC/ETH真实DSH、完整准备、Saved及ACK | 待实际验收 |
| 页面/下载/刷新/重启同字节及原性能门 | 待实际验收 |
| 自然复查、自动触发及PC核心矩阵 | 待实际验收 |

旧BTC的Research Run仍为degraded；PublicationJob为cancelled。这是两个不同状态。现有“更新公开证据”可创建关联的新Run，重新冻结当前安装；不继承旧执行输出或资料许可。按钮受理不代表报告已交付。

证据：DEV artifacts/core-goal-20261011/source-snapshot-abi02（候选SHA569f513b…、独审226fc3f9…）、composite-controls58_01（8bbcea3e…）；Root promotion58_01及后续mr-release-b2b4-58。实际状态见current-core-execution.json。
