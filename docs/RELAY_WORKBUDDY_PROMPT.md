# 给信创电脑 WorkBuddy 的部署指令

先提供试验增量包、**只属于执行端的 `worker.json`**，并确认它内的云端网关地址已可访问。不要提供其他人的客户端凭据。下面这段可直接复制：

```text
请在这台 UnionTech OS Desktop 20 Pro 信创电脑上部署 WindPy 云端转发试验的执行端。

先读增量包中的 docs/RELAY_TRIAL.md，定位现在已稳定使用的 Wind Terminal MCP 0.24.2 安装目录和 venv Python，把增量文件放到该目录；保留已有 server.py、wind_bridge、WindPy SDK、登录状态和 MCP 配置。不要重装、不要另启一个真实 Wind MCP、不要杀现有进程、不要导入真实 WindPy或跑 verify_delivery.py。若工具契约与现有 server.py 不匹配，停止覆盖并说明版本差异。

将我提供的 worker.json 存到仅自己可读的 .relay/worker.json。不要显示 token，不要上传配置，也不要把整个 .relay 目录提交到仓库。

先用原有 venv 运行 tests/test_relay.py 和 relay_demo.py，确认离线模拟通过。再以前台或可停止的专属会话运行 relay_worker.py --config .relay/worker.json --mode mock，通过已有网关通信；不要切到 native。

最后告诉我实际安装路径、Python 路径、执行端会话、网关通信状态以及哪些环节还没验收。模拟来源必须是 relay_mock。缺少网关入口或网络权限时，指出具体缺项，不改动远程桌面、其他应用或现有服务器设置。
```

云端和每个人本地代理的配置步骤在 `RELAY_TRIAL.md`。这段指令只部署信创执行端的模拟阶段，尚不进行真实 WindPy 取数。
