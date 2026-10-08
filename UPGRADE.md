# 已有安装升级到 0.24.2

本页适用于已经配置好 `wind_terminal_api` 的用户；首次使用见 [安装说明](INSTALL.md)。升级更新 MCP 源码，保留本机 Wind、官方 SDK、现有 Python 环境和研究数据。

0.24.2 将慢调用移出 MCP 消息处理线程，并调整原生调用的排队与取消行为。工具名称、参数和来源标识保持兼容。这能修正已确认的调度问题；尚未证明所有 WorkBuddy 断连都由该问题引起，也不代表已完成各信创 CPU、SDK 和 WorkBuddy 版本的实机验收。

## 让 agent 完成升级

在另一台已经安装本 MCP 的机器上，可把下面这段交给有本地文件权限的 agent：

```text
请将本机 Wind Terminal MCP 升级到 GitHub spikewzy/wind-terminal-mcp 的 0.24.2 或后续版本。先读取新版 UPGRADE.md 和 INSTALL.md，定位 WorkBuddy 实际使用的 wind_terminal_api 配置及原安装目录；不要新建第二个安装或 MCP 条目。

先备份本地代码改动、原客户端条目、runtime（或 WIND_TERMINAL_MCP_DATA_DIR 指定的目录）及 query-budget.json；保持 SDK 路径和现有 env。由 WorkBuddy 停用该连接，确认没有进行中的查询后，在原目录更新完整源码。若旧 server.py 或 wind_bridge/mcp_dispatch.py 有临时补丁，先备份再用新版完整替换，不能将旧补丁重新叠加。

升级阶段不连接 MCP、不运行 server.py、不导入真实 WindPy、不跑 verify_delivery.py、不杀进程。先执行版本、依赖和模拟调度检查。安装路径及依赖未变时保留 .venv；只有依赖缺失或锁定文件变化才补装依赖。不要重装 Wind 或 SDK。

完成后给出安装路径、实际版本、检查结果和需要我在 WorkBuddy 中完成的重载步骤。仅保留一个 wind_terminal_api 条目，不修改其他 MCP。真实取数验收另行执行，不能用离线通过代替。
```

## 手动升级

1. 在 WorkBuddy 中找到实际生效的 `wind_terminal_api` 条目，记录 `command`、`args` 和 `env`。确认原安装目录，避免更新了另一个副本。用户级和项目级配置也要核对，避免重复启用同一个服务。
2. 等待当前查询结束，在客户端中停用这一条连接。备份本地改动、客户端条目和数据目录。默认数据目录为安装目录下的 `runtime/`；设置过 `WIND_TERMINAL_MCP_DATA_DIR` 时，以该路径为准。预算策略 `query-budget.json` 位于数据目录内。备份留在本机，不提交到公开仓库。
3. 在原安装目录更新完整源码，保留 `.venv/`、`runtime/`、`client-configs/` 及其他本机设置。升级不删除回执、登记的指标或预算记录。
4. 核对版本与依赖，完成下文的离线检查，再在 WorkBuddy 中启用一次该连接。

**Git 安装且没有本地源码改动时**，在原安装目录检查后更新：

```sh
git status --short
git pull --ff-only
```

如有本地源码改动，先备份并处理差异；不要使用强制重置清除本地文件。已加过临时 `server.py` / `wind_bridge/mcp_dispatch.py` 补丁的安装，必须将这两个文件作为一组更新成新版，保留补丁备份仅供对照。

**ZIP 安装时**，从 [GitHub Releases](https://github.com/spikewzy/wind-terminal-mcp/releases) 下载 0.24.2 或后续源码包，先解压到临时目录，再将包内源码覆盖到原安装目录。不要直接让客户端指向临时解压目录，也不要把旧 `.venv` 搬到新路径。新版文件必须完整覆盖，不能只复制新的调度模块而保留旧 `server.py`。

### 环境与配置

本次仅更新源码时，若 `requirements.lock.txt` 未变且依赖检查通过，无需重跑安装器。

若锁定依赖变化或有缺失，在原目录使用现有环境补装：

```sh
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip check
```

离线机器用与系统、CPU、Python 匹配的 wheelhouse：

```sh
.venv/bin/python -m pip install --no-index --find-links /实际路径/wheels -r requirements.lock.txt
```

Windows 将 `.venv/bin/python` 换为 `.venv\Scripts\python.exe`。

`install.py` 会复用原目录的 `.venv` 并重新生成 `client-configs/`，不会自行修改 WorkBuddy 的有效配置。升级时继续保留原客户端的 `env`，尤其是 `WIND_TERMINAL_MODULE_DIR`、可选 `WIND_TERMINAL_LIBRARY_DIR` 和 `WIND_TERMINAL_MCP_DATA_DIR`；不要用新生成的配置盲目覆盖本机自定义设置。

有效启动配置应直接引用本机 `.venv` Python 和 `server.py` 的绝对路径。只要路径和设置正确，源码更新不需要改变这两个路径。只有换了目录、系统或 Python，才需要在新位置重建环境并生成配置；这属于迁移，不是本次更新的必需步骤。

### 离线检查

在原安装目录执行以下检查；它们不启动 MCP 服务，也不加载真实 WindPy：

```sh
.venv/bin/python -c "from wind_bridge import VERSION; print(VERSION)"
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s tests -p test_mcp_dispatch.py
```

版本应为 `0.24.2` 或所下载的后续版本。模拟调度测试仅使用假任务，检查并发、取消和工具参数兼容性。`verify_delivery.py` 即使不加 `--live` 也会启动独立 MCP 服务；已有连接升级期间不要用它验证，以免另启实例。

## WorkBuddy 升级后验收

1. 完成源码更新后，在 WorkBuddy 中启用原来的 `wind_terminal_api` 连接，让客户端重新启动这一条服务。若界面没有单独重载入口，可停用后再启用一次；仍不能重载时再退出并重新打开 WorkBuddy。仅保存相同配置或新建聊天不保证重启服务。
2. 先调用 `wind_capabilities`，确认返回的版本；再调用 `wind_status(connect=false)` 检查本地状态。两项均不取 Wind 数据。若显示旧版本，先排查安装路径或旧服务尚未重载，不要继续取数。
3. 需要真实链路验收时，由使用者明确安排一个代码、一个字段和有限日期的查询，核对日期、`data_source_id` 和 `receipt_id`。随后再逐步恢复研究任务。不要同时启动另一个 MCP 或绕过服务直接运行 WindPy。

取消已经开始的原生调用不会强行终止 Wind SDK；该调用允许完成并保存回执。取消仍在排队的请求应避免再开始原生调用。重试前先查已有回执，防止重复消耗额度。

## 如果仍然断开

保留最先出现的原始错误、请求起止时间、WorkBuddy 版本、操作系统及 CPU、MCP 版本，并检查对应时间的客户端和服务端日志。日志需脱敏后再分享。

`Connection closed` 只说明连接已经关闭，不能单凭它判断是客户端超时、服务退出或系统问题；`Not connected` 往往是后续状态。进程仍然存在也不证明原 stdio 管道可恢复。停止自动重试，先按上述步骤重载原连接，再用本地状态工具检查。重装相同源码不能修正调度缺陷；若新版仍断开，应按原始证据继续排查。

WorkBuddy 原生调用体验和信创 SDK 支持，需要在实际目标机器验证；另一台 Mac 上的成功或离线模拟通过不能替代这一项。
