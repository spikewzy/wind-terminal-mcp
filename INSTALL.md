# Wind 终端 MCP 安装与接入

安装后的操作流程见 [使用说明](USER_GUIDE.md)，复杂研究案例见 [期货进阶演示](FUTURES_DEMOS.md)。

本包把已登录的本机 Wind 终端通过标准 MCP 提供给支持本地 stdio 的客户端。来源名为 **Wind 终端 API**，服务器名为 `wind_terminal_api`；与 Alice MCP 独立。

服务面向股票、债券、基金、指数、期货、期权、外汇、宏观及产业数据，不以农业为中心。`wind_capabilities.data_coverage` 列出当前验证范围与缺口；接口能够调用不等于账号有全库权限或历史已完整。

## 安装

首次使用推荐先看 [README 的一段安装 prompt 和三条命令](README.md#快速安装)。本页供需要指定 SDK、离线依赖或排错时查阅。

0.24.1 为桌面系统源码包，支持范围如下。Python 至少 3.10，推荐 3.12；必须安装与操作系统、CPU 架构和 Python 位数匹配的官方 WindPy 与本地库，并使用对应 Wind 桌面终端的登录和授权。安装包不包含或下载 Wind SDK。

| 系统 | 安装与运行策略 | 验证状态 |
| --- | --- | --- |
| macOS | 默认发现 Wind API.app | 2026-10-06 本机真实查询生成三份研究示例；其他 SDK/CPU 组合需实测 |
| Windows | 使用 `.venv\\Scripts\\python.exe`，支持官方 SDK 路径与 DLL 目录配置 | 适配代码和模拟测试通过；待 Windows 实机取数验收 |
| 中科方德 5.0 桌面版 | 必须识别到方德、5.0 和桌面版本信息 | 待对应系统、CPU 和 SDK 实测 |
| UOS 20 桌面版 | 必须识别到 UOS 20 和桌面版本信息 | 待对应系统、CPU 和 SDK 实测 |
| 银河麒麟 V10 SP1 桌面版 | 必须识别到 V10、SP1 和桌面版本信息 | 待对应系统、CPU 和 SDK 实测 |
| 其他 Linux、上述产品的其他版本、任何 Linux 服务器版 | 不支持；安装和原生调用前拒绝 | 不提供绕过开关 |

系统范围对照 [Wind 官方下载中心](https://wind.com.cn/download.htm)；官方提供终端安装包不等于本 MCP 已完成各 CPU/SDK 组合的实测。Linux 从 `/etc/os-release` 及厂商版本文件识别，不把“有图形桌面”当作服务器版变成桌面版。无法确认桌面版或麒麟 SP1 时会提示原因并停止；不根据“兼容 Debian/Ubuntu”放行。

解压 ZIP，进入解压目录，用 Python 3.12 执行：

```sh
python3.12 install.py --check
python3.12 install.py
```

`--check` 只检查系统、Python 和 SDK 文件，不安装或取数。安装程序创建包内 `.venv`、安装锁定依赖并生成当前机器的客户端配置，不改现有客户端设置。已有离线依赖时可用 `--wheelhouse /绝对路径/wheels`；依赖必须与目标系统、CPU 和 Python 匹配。部分信创 CPU 未必有预编译 Python 依赖，需要为该架构准备可用依赖或离线 wheel，不能拿 AMD64 包直接用于 ARM、龙芯或 MIPS。

Windows 使用以下命令。SDK 路径为示例，替换为官方 Wind 安装中实际包含 `WindPy.py` 的目录；若当前 Python 已由官方安装器配置好，安装器也会尝试从其模块路径发现该文件。

```powershell
py -3.12 install.py --check
py -3.12 install.py --wind-module-dir "D:\WindSDK\Python"
.venv\Scripts\python.exe verify_delivery.py
```

三个支持的信创桌面系统使用 `python3.12 install.py --wind-module-dir /实际路径/包含WindPy.py的目录`。无需照搬 Mac 的软链接或 `/Applications` 路径；不要从非官方包仓库安装同名 `WindPy` 包。

官方 SDK 需要额外动态库目录时，加 `--wind-library-dir /实际动态库目录`，Windows 可使用盘符路径。生成的配置会保存 `WIND_TERMINAL_MODULE_DIR`、可选 `WIND_TERMINAL_LIBRARY_DIR` 以及 UTF-8 进程设置。SDK 库搜索路径只传给查询工作进程，不修改系统级环境变量。

先运行不带 `--live` 的检查。打开对应平台的 Wind 应用并登录后，需要真实链路验收时再执行：

```sh
.venv/bin/python verify_delivery.py --live
```

省略 `--live` 只检查本地 SDK 文件、MCP 协议和已打包文档。检查结果写入本目录 `verification/delivery-*.json`；真实查询的原始回执保存在本目录 `runtime`。这里的成功表示通用 MCP 客户端验证通过，不代替每款厂商客户端的实测。

Windows 将下文 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。本 MCP 不修改 Windows 系统时区；原生日期仍需按 Wind SDK 和任务口径核对。

## 客户端配置

安装后查看 `client-configs/mcp.json`、`client-configs/codex.toml` 或 `client-configs/workbuddy.json`，将 `wind_terminal_api` 条目合并到目标客户端设置中，保留其他服务器。

| 客户端 | 配置位置与格式 |
| --- | --- |
| Codex | `~/.codex/config.toml`，使用生成的 TOML 条目 |
| Claude Code | 项目 `.mcp.json`，使用生成 JSON 中的 `mcpServers` 条目 |
| Cursor | 项目 `.cursor/mcp.json` 或 `~/.cursor/mcp.json`，使用生成 JSON |
| Gemini CLI | 项目 `.gemini/settings.json` 或用户设置中的 `mcpServers`，使用生成 JSON 的服务器条目 |
| WorkBuddy | 用户 `~/.workbuddy/mcp.json` 或项目 `.workbuddy/mcp.json`，使用生成的 `client-configs/workbuddy.json` |
| 其他本地 MCP harness | 复制 JSON 条目中的 `command`、`args`、`env`，按客户端要求填写 |

启动配置不要求客户端从安装目录运行。更改安装路径后须重新安装 Python 环境并重新生成配置，不能直接搬移虚拟环境。项目里的 Skills 属于可选说明，调用 MCP 本身不依赖安装 Skills。

### WorkBuddy 接入

已提供专用 JSON 和通用 MCP 协议验证。WorkBuddy 应用内的安装、登录和调用仍需在使用者环境验证。

打开 WorkBuddy 的「插件 → MCP 服务器 → 配置 MCP」，将 `client-configs/workbuddy.json` 中的 `wind_terminal_api` 条目合并到已有 `mcpServers`。跨项目使用时选择用户级；只在当前项目使用时选择项目级。保存后查看服务器连接状态。此配置使用本机 Wind 登录，不填写 Alice MCP 的 Key。路径与操作入口依据 [WorkBuddy 官方 MCP 文档](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide)。

[WorkBuddy官方连接器文档](https://open.workbuddy.cn/docs/connector)确认本地stdio支持`command`与`args`，配置有`command`时可以省略`type`。安装程序生成当前机器的绝对路径；移动安装目录后应重新生成配置。

已有安装可以生成当前路径的专用配置：

```sh
.venv/bin/python client_config.py --format workbuddy
```

通过该配置检查启动、工具发现及本地功能，无需连接Wind取数：

```sh
.venv/bin/python verify_delivery.py --config client-configs/workbuddy.json --output verification/workbuddy-config-local.json
```

这项检查使用通用 MCP 客户端；WorkBuddy 应用内调用仍记为未测试。可在应用内先调用 `wind_capabilities` 和 `wind_status(connect=false)`，确认服务器及来源标识；再以有限查询验证实际 Wind 链路。`--live` 会新增真实取数。

## EDB指标元信息

从终端导出的简体中文CSV可通过`parse_wind_edb_export(csv_text, evidence)`解析；本地文件可用`.venv/bin/python inspect_edb_export.py /绝对路径/export.csv --evidence "导出时间及选项"`读取。支持UTF-8和GB18030、列布局，要求名称和指标ID表头；解析不会取数或自动登记。

CSV显示单位及频率可能经过转换，先核对原始指标和必要的WindPy返回，再用`register_economic_indicators`登记。官方数值查询与元信息权限分别验证。流程、已核对样本和同名异码反例见[EDB元信息与官方导出](skills/wind-terminal-api/references/edb-metadata.md)。

完整EDB代码的本地检索采用精确匹配，未登记时返回空结果；名称检索仍展示不同来源的候选。目录未命中不表示Wind没有数据，明确代码也不要求先登记。

## 首次调用

让客户端先执行 `wind_capabilities`，查看官方读取接口参数，再执行 `wind_status(connect=true)` 验证 Wind 登录。通用入口为 `query_wind_data(method, arguments)`；常用方法包括 WSS 截面、WSD 历史、WSQ 快照、WSI 分钟、EDB 宏观和 WSET 专题。

不知道表名或参数时，先调用 `search_wind_query_recipes`，例如 `{"question":"仓单"}`、`{"question":"可交割券","method":"wset"}` 或 `{"question":"会员 排名"}`。它返回明确的 method/arguments 和验证边界。示例日期不是默认日期；新安装不含开发查询回执，样本会标记为未在本机重新验证，需经实际 Wind 查询核对。

国内期货已有样本可按品种组合关键词查找，如`{"question":"沪铝 仓单","method":"wsd"}`或`{"question":"30年期国债 净持仓","method":"wsd"}`。读取结果里的`product_discovery.products`确认具体代码与该代码的历史空值情况；一条示例可能包含多个品种，检索不会自动裁剪原批次。普通塑料和塑料月均价保留不同标识，空值样本也可以被检索到。

中文字段找不到时，可显式调用 `search_wind_fields(question="交易单位", method="wss", scope="candidates")` 查更大的候选目录。它包含2022年Windget源码中的6,865项字段，不是当前官方字段库；options、单位与权限仍需核实。默认observed范围保留本机证据，两类结果不自动混用。

示例工具参数：

```json
{
  "method": "wss",
  "arguments": {
    "codes": "600519.SH",
    "fields": "sec_name,close",
    "options": "tradeDate=20260928"
  }
}
```

这是一条明确历史日期的示例，不代表默认查询日。字段与选项可用 `search_wind_documentation`、`search_wind_fields`、`search_wind_bundle_metadata` 查找线索。通用取数不以已收录字段清单为白名单；数据范围取决于 Wind 接口和用户权限。

本安装包不复制开发环境的历史查询回执。字段示例的“已有成功样本”可能因此降级；依赖历史证据的可选 Alice 中文财报解析会明确报告证据缺失，通用 Wind 查询仍可使用。单位、报告期和历史版本要按具体需求核对。

新安装的本地EDB目录默认为空，原有安装的已登记目录保留。查目录外基本面指标时调用 `search_economic_indicator(question="中国:产量:粗钢:当月值", scope="terminal")`，审查 Wind 返回的候选和歧义，再以确认代码及明确起止日期调用 `query_economic_indicator_data`。候选识别不提供完整单位、频率、发布者和历史范围，也不是全库枚举；这些字段未知时不得补造。需要单位/频率/来源过滤时使用已保存元信息的 `scope="local"`。

同时检查`edb_text_coverage`及候选的`occurrences`：例如“碳酸锂库存”可能只识别出“库存”，品种限定词仍未匹配。重复代码保留每段出处；完整文字匹配也需要核对定义，不能自动选取。新版结果需客户端重新加载MCP服务；更新文件不会替换已经运行的旧进程。

EDB批量返回共享日期轴，不能把每个日期都算成各指标的一条有效观测。读取`edb_series_observations`查看逐代码区间内、区间外及空值数量；`OUT_OF_RANGE_DATA`时先用`read_wind_receipt`看原回执及`stored_query_validation`，避免重复下载。翻页后的诊断仍针对整张原始矩阵，不会因当前页没有越界日期就把失败批次变成成功。

## 排错

需要控制调用时，可在数据目录配置`query-budget.json`，设置本地请求次数、累计估算数据格和单次估算数据格上限。多个客户端使用同一目录和新版本时共享原子计数；详情见[本地查询预算](references/query-budget.md)。所有限额默认留空，不能当成Wind账户余额。用`wind_status(connect=false)`检查当前配置和历史观察；升级后先重新加载服务器。

- 找不到 `WindPy.py`：Mac 默认查找 `/Applications/Wind API.app`；各平台可通过安装器 `--wind-module-dir` 或客户端 `env.WIND_TERMINAL_MODULE_DIR` 指向官方文件所在目录。不会扫描整盘或下载 SDK。
- `UNSUPPORTED_PLATFORM`：查看 `wind_status(connect=false).platform_support.reason`。其他 Linux、Linux 服务器版、无法确认的桌面版本均不放行。
- `WIND_SDK_LOAD_ERROR`：检查 SDK、CPU/Python 位数和动态库目录；官方 Windows/Linux SDK 不能互换，Mac 的特定新闻接口限制也不会套用到它们。
- 登录或权限错误：检查 Wind API 的登录和授权。Alice MCP 的 Key、积分与本服务无关。
- 不能写入缓存：给当前安装目录写入权限，或通过 `WIND_TERMINAL_MCP_DATA_DIR` 指定可写数据目录。
- 客户端发现工具失败：确认配置引用的是本机 `.venv/bin/python`（Windows 为 `.venv\Scripts\python.exe`）及 `server.py` 的绝对路径，保留生成的 `env`，然后重新加载该服务器。
- 终端 SDK 返回空值或不支持方法：查看原始错误和回执，不替换成其他来源。本机特定版本新闻接口限制会明确报告。

本版完成 Windows 和指定信创桌面系统的代码适配；跨系统模拟测试不等于目标机实测。真实 Windows/信创取数还须在对应机器安装官方 SDK 后验证。远程 HTTP、其他 Linux 和 Linux 服务器系统均不在支持范围。软件目录及官方帮助的完整快照不随开源包分发，需按 [本机参考资料导入](docs/LOCAL_REFERENCES.md) 在自己的机器生成；这不限制通用 WindPy 查询，其他平台也不能将 Mac 目录当作当地客户端最新字段字典。

配置参考：[Claude Code MCP](https://code.claude.com/docs/en/mcp)、[Cursor MCP](https://cursor.com/docs/mcp)、[Gemini CLI MCP](https://geminicli.com/docs/tools/mcp-server/)。
