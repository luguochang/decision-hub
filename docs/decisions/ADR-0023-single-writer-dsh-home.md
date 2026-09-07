# ADR-0023：同一 DSH_HOME 的单写者启动边界

日期：2026-09-04
状态：accepted

## 决策

通过 Decision Hub 的 `infra/dsh/run-web.sh` 启动官方 DSH Web 时，同一规范化
`DSH_WEB_HOME` 只允许一个存活的写进程：

1. 启动器在修改 profile、preset 或 Session 存储前，以原子 `mkdir` 获取 Home 级锁；
2. 锁记录 PID、进程启动标识和规范化 Home，存活且三者匹配时第二实例立即失败；
3. 进程退出后遗留的锁，或 PID 已复用但启动标识不匹配的锁，由下一次启动原子隔离后接管；
4. 不修改官方 DSH 上游、JSONL 格式或 Session API，也不靠 HTTP 重试掩盖存储所有权冲突；
5. 需要并行运行 replay、canary 或开发实例时，必须给每个实例独立 `DSH_WEB_HOME`。

## 背景

G2-AF-04 真实自动链复验期间，机器上两个官方 DSH Web 进程同时使用
`data/dsh-live`。旧实例曾出现 Hub 已接受 Session、但 DSH 创建阶段返回
`host_internal_error` 且随后无法 inspect 的不一致。锁定的 DSH JSONL backend 对单进程内
同一 Session 的操作有 coordinator 串行化和原子文件发布，但没有发现 Home 级跨进程 owner
门；两个 Web 进程还会共同修改 profile、workspace registry 和 Session 索引。因此必须在产品
启动边界拒绝这种运行拓扑。

## 候选与否决项

- 采用 Home 级原子目录锁：不增加运行依赖，macOS/Linux 都可用，并能在进程崩溃后恢复。
- 否决只检查端口：不同端口仍可能写同一 Home，不能保护存储。
- 否决只记录 PID：PID 可复用，会形成永久误锁；必须同时比较进程启动标识。
- 否决在业务请求上增加更多重试：重试不能修复两个进程同时写 profile/JSONL 的所有权错误。
- 否决修改 DSH 上游存储：会破坏锁定上游升级边界，并重复实现其内部 Session 协议。

## 后果

- 同一 live Home 的第二个产品入口会明确失败，并提示停止旧实例或使用独立 Home。
- 锁文件属于 gitignored 运行态；正常或异常退出后都不进入仓库，下一次启动会校验并回收 stale
  owner。锁目录出现未知文件时 fail-closed，不递归删除。
- 这只消除 Decision Hub 启动器造成的多写者风险；绕过启动器直接运行上游 CLI 不在保护范围。

## 迁移/回滚

- 不迁移、不删除现有 Session/JSONL。停止多余 DSH 进程后使用原 Home 重启即可。
- 回滚可移除启动器的锁调用和本 ADR，不改变 Hub Ledger、DSH Session 或 active pointer。

## 受影响契约与测试

- 不改变跨模块 schema；只增加启动运行约束。
- `tests/dsh_native/test_dsh_home_lock.py` 覆盖存活 owner 拒绝、dead owner 恢复和 PID 复用恢复。
- `tests/dsh_native/test_product_launcher.py` 保持 DSH 官方插件和 live profile 既有契约。
