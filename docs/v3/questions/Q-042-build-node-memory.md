# Q-042：恢复此前已验证的前端编译内存参数

状态：DECIDED，2026-10-24。按用户自主判断授权执行，编译资源配置与金融时限/证据容量分别处理。

## 实证

Q039正常build已越过retention与assessment，UI原`tsc -b && vite build`在约4GiB V8堆耗尽，exit134，Host未开始。旧D默认4GiB的tsc也曾同样OOM；此前“完整UI构建通过”的真实命令是显式 `node --max-old-space-size=8192 .../tsc -b`，随后同flag执行Vite，两者exit0，证据在 ui-baseline-verification。Main/D的UI配置、TypeScript5.9.3/Vite6.4.3、约4MB界面源及约16MB生成contracts相同，未见本次意外纳入归档树证据。不能把旧8GiB通过简称原默认命令通过。本次遗漏已验证内存参数，不以删类型或放宽检查处理。

Root实测机器总33389952KiB、空闲14810732KiB，约32GiB/14GiB；启动前再核资源。失败原件 final-receipt03513dcb…8d742 保留；没有validated receipt，不作Host成功。

## 精确执行范围

不改产品代码、tsconfig、package/lock、生成类型或任何金融/归档边界。Root已按Q040采纳Main recipe8d79a199…452487，实际Main recipe＋新test在无suppression fresh strict下2files0、1.381s，当前源码身份必须重新取得正常回执。

沿Q039原build-only/producer/pointer/71native inputs/18旧来源/525旧输出备份的核验，使用全新 `Main/artifacts/incremental-delivery-20261024/resource-build/` 和新D证据/执行器。只给构建子进程的继承 `NODE_OPTIONS` 追加 `--max-old-space-size=8192`，保留已有其他选项，不修改全局环境或服务。编译前空闲物理内存不足10GiB则停止，不自动改值或重跑。沿用真实49c prior path/SHA与旧来源，不覆写失败normal-build/validated-build。

原`build-product.ps1`只执行一次完整流程；不另跑重复私有UI编译（旧同输入8GiB成功已提供依据）。不显式install/更新依赖，不Spot/capture/DB、不run-product或部署；pnpm可能自动协调node_modules的未知边界如实记录，不以offline证明无动作。

仍保全部原TS检查、产品2MiB回执边界、normal来源/retention/产物guard、金融5秒/45秒及长期指标；仅调工程编译进程堆上限。成功必须实际终态＋正常回执与资源校验，失败保原件先归因，不自动再次build或继续上调内存。Q041只能待真实成功回执、17产物/新pins冻结与review后执行。目标ACTIVE。
