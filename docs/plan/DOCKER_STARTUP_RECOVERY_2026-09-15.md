# Docker Desktop 启动故障记录（2026-09-15）

## 结论

这次故障不是 Decision Hub 代码、WSL2 内核或项目 compose 配置导致的。Docker Desktop 4.80.0 的 backend 在初始化可选的 Inference manager 时，遇到残留的 Windows AF_UNIX socket 重解析点：

```text
starting services: initializing Inference manager:
listening on unix://C:/Users/90603/AppData/Local/Docker/run/dockerInference:
remove ...: The file cannot be accessed by the system.
```

错误码对应 Windows `ERROR_CANT_ACCESS_FILE (1920)`。backend 因此退出，`docker-desktop` WSL2 发行版没有启动，Docker API 管道不存在，项目之前只能使用宿主机回退模式。

这与 Docker Desktop 反馈中记录的同类故障一致：残留 `dockerInference` 或 secrets engine socket 会让 backend 在引擎启动前崩溃；处理方式是完全退出 Docker、停止 WSL、将运行时 socket 目录改名留档，而不是恢复出厂或删除数据盘。

## 已执行的修复

1. 确认 `docker context` 指向 `desktop-linux`，但 server 为空；`com.docker.service` 为 stopped，`docker-desktop` WSL2 发行版为 stopped。
2. 停止所有 Docker Desktop/backend 进程，并执行 `wsl --shutdown`。
3. 将可重建的运行时目录改名保留：
   - `%LOCALAPPDATA%\Docker\run` → `run.stale-20260915-211611`
   - `%LOCALAPPDATA%\docker-secrets-engine` → `docker-secrets-engine.stale-20260915-211611`
4. 将 `%APPDATA%\Docker\settings-store.json` 中的 `EnableDockerAI` 设置为 `false`，并保留原文件备份：
   - `settings-store.json.bak-20260915-211249`
5. 重新启动 Docker Desktop。Docker Desktop 自动重建了新的 `run` 和 `docker-secrets-engine` 目录。

没有执行以下高风险操作：恢复出厂、卸载 Docker、删除 `%LOCALAPPDATA%\Docker`、删除 `%PROGRAMDATA%\DockerDesktop`、注销 WSL 发行版、`docker system prune`。因此原有 Docker 数据盘、镜像、容器和卷没有被主动删除。

## 验证结果

```text
docker context show
desktop-linux

docker version --format 'client={{.Client.Version}} server={{.Server.Version}}'
client=29.6.1 server=29.6.1

docker info --format 'ServerVersion={{.ServerVersion}} StorageDriver={{.Driver}} OSType={{.OSType}}'
ServerVersion=29.6.1 StorageDriver=overlayfs OSType=linux
```

同时确认：

- `docker-desktop` WSL2 发行版为 `Running`；
- `\\.\pipe\dockerDesktopLinuxEngine` 存在；
- `docker ps` 可正常访问 daemon（当前没有运行中的容器）；
- 新 backend 日志没有再次出现 Inference manager 启动错误。

## 后续约束

- 当前已关闭 Docker AI/Model Runner；普通 Linux 容器不受影响。需要 Model Runner 时，先确认 Docker Desktop 已升级到修复该 socket 启动问题的版本，再恢复 `EnableDockerAI`，不要直接恢复旧的坏运行时目录。
- `run.stale-20260915-211611` 和 `docker-secrets-engine.stale-20260915-211611` 先保留，确认一段时间稳定后再由用户手动删除。它们是运行时 socket，不是镜像/卷数据。
- 下次出现同样错误，优先执行“完全退出 Docker → `wsl --shutdown` → 改名这两个运行时目录 → 重启”，禁止把“Reset to factory defaults”作为第一步。
- Decision Hub 的启动脚本仍可使用宿主机模式；Docker 恢复后，如需切换回容器编排，再单独做 compose 启动验证，不把 Docker 修复和业务功能改造混在一起。

