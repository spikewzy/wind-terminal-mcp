# Wind 终端 MCP 使用说明

Wind 终端 MCP 将本机官方 WindPy 接入支持标准 MCP 的 AI 客户端。你可以用中文提出研究任务，让客户端依次查找字段和参数、制定查询计划、取得 Wind 数据，再读取回执、计算指标并核对结果。

数据源标识为 `wind_terminal_api`，名称为 **Wind 终端 API**。服务面向股票、债券、基金、指数、期货、期权、外汇、宏观及产业数据。本文对应 **0.24.1**，更新日期为 **2026 年 10 月 6 日**。

第一次使用按下面的快速上手操作。想看完整研究流程，直接阅读 [期货进阶演示](FUTURES_DEMOS.md)：其中包含真实历史样本、可复制提示词、工具参数、计算结果和对应证据。

## 能力与适用场景

服务提供 26 个 MCP 工具，通用取数入口封装 19 个官方读取方法。字段不局限于本地示例清单；使用有依据的 Wind 字段和参数，实际可取范围由官方 SDK、账号权限及返回结果决定。

| 研究需求 | 可以怎样使用 |
| --- | --- |
| 不知道字段或专题表参数 | 搜索中文字段、官方文档、软件目录及已有查询示例，取得明确的 `method` 和 `arguments` |
| 多品种、多字段、长区间取数 | 先比较 WSD/WSS 分组方案，再逐批执行；保留每批代码、字段、日期和回执 |
| 连续合约研究 | 查询历史实际合约映射，区分主连、次主连、近月及当季连续，核查换月前后的变化 |
| 期货基本面研究 | 联查仓单、品种持仓、会员排名、产业 EDB、股指估值与基差、国债 CTD 和资金利率 |
| 可复核的分析 | 由已保存回执计算均线、收益、回撤、波动率、历史 VaR/ES 或 EDB 公式；计算结果另存派生回执 |
| 使用多个 AI 客户端 | 同一 stdio 服务可接入 Codex、Claude Code、Cursor、Gemini CLI、WorkBuddy 等支持本地 MCP 的客户端 |

任务理解和多步骤组织由客户端模型完成；MCP 提供数据接口、发现线索、校验与分析工具。中文任务不是一个可以绕过字段含义、参数和权限确认的万能查询接口。

官方帮助与终端软件目录的完整快照不随开源包分发；相关搜索在未导入时返回明确缺口。本机导入方法见 [可选参考资料](docs/LOCAL_REFERENCES.md)，核心取数与分析工具不依赖这些快照。

## 快速上手

### 准备运行环境

需要已安装并获得授权的 Wind 桌面终端、对应平台的官方 WindPy 和动态库，以及 Python 3.10 或以上版本，推荐 3.12。开源代码和安装包不携带 Wind SDK、账号凭据或开发者的原始查询数据。

| 桌面系统 | 当前支持范围 |
| --- | --- |
| macOS | 保留支持，本机已有真实取数记录 |
| Windows | 已完成代码适配和模拟测试，实际 SDK 登录及取数需在目标机验证 |
| 中科方德 5.0 桌面版 | 已完成适配，需匹配目标 CPU 和官方 SDK 并在目标机验证 |
| UOS 20 桌面版 | 同上 |
| 银河麒麟 V10 SP1 桌面版 | 同上 |

其他 Linux 发行版、其他版本及 Linux 服务器版不在支持范围内。系统策略匹配与实际 Wind 连接成功是两项检查；详细 SDK 路径、动态库、离线依赖和安装步骤见 [安装说明](INSTALL.md)。

在源码或解压后的服务目录执行：

```sh
python3.12 install.py --check
python3.12 install.py
```

Windows 可用 `py -3.12`，并按实际位置指定官方 SDK：

```powershell
py -3.12 install.py --check
py -3.12 install.py --wind-module-dir "D:\WindSDK\Python"
```

安装器创建独立环境并生成当前机器的配置，不会自动改写客户端现有设置。将 `client-configs/mcp.json`、`client-configs/codex.toml` 或 `client-configs/workbuddy.json` 中的 `wind_terminal_api` 条目合并到相应客户端，保留其他服务器。

配置中的 Python 和 `server.py` 使用绝对路径。移动安装目录后应重新创建环境并生成配置，然后重新加载 MCP 服务。

### 检查服务和终端登录

在客户端复制下面的提示词：

```text
使用 Wind 终端 API。
先调用 wind_capabilities 和 wind_status(connect=false)，告诉我服务版本、
数据源标识、平台检查结果和当前 SDK 的接口限制。
然后调用 wind_status(connect=true) 检查 Wind 登录。
区分 MCP 已连接、Wind 已登录和具体数据已获授权这三种状态。
```

`wind_capabilities` 和 `wind_status(connect=false)` 不向 Wind 取数；`connect=true` 发起一次连接检查。登录成功后，仍需通过具体查询确认数据权限。`connected=null` 或 `connection_checked=false` 表示本次没有检查连接。

### 完成第一次期货查询

下面使用固定历史合约和区间，避免把连续代码选择与首次接入混在一起。执行会发起一次 Wind 历史查询。

```text
使用 Wind 终端 API 查询 M2701.DCE 在 2026-09-21 至 2026-09-28 的
close、settle、oi，options 使用 PriceAdj=U。
先找到已有示例确认参数，再调用 query_wind_data。
展示实际返回日期、原始字段、空值数量和 receipt_id。
随后用这个 receipt_id 计算 3 个观测的均线和价格回撤。
这是历史演示，结果日期不要写成今天。
```

`query_wind_data` 的工具参数：

```json
{
  "method": "wsd",
  "arguments": {
    "codes": "M2701.DCE",
    "fields": "close,settle,oi",
    "beginTime": "2026-09-21",
    "endTime": "2026-09-28",
    "options": "PriceAdj=U"
  },
  "max_age_seconds": 0
}
```

这个区间的已保存样本有 5 个返回日期。`receipt_id` 是你本机当次查询生成的标识；把它传给 `read_wind_receipt` 或分析工具即可继续处理，后续本地分析不再下载价格。完整结果见 [期货价格与风险计算](FUTURES_DEMOS.md#演示七-期货价格与风险计算)。

## 一项研究任务怎样执行

1. **明确范围。** 写清来源、品种或市场、指标、起止日期、频率和希望输出的比较。指定 Wind 终端 API 时使用 `wind_terminal_api`，指定 Alice MCP 时使用独立来源 `alice_mcp`。
2. **发现参数。** 优先搜索已有查询示例和字段证据；需要时查询官方帮助、软件目录或终端代码生成器。候选名称和内部编号不能直接当作可执行字段。
3. **规划取数。** 多代码、多字段先调用 `plan_wind_data_queries`，查看请求总数、估算数据格和全部分页。规划不会自动执行。
4. **小范围验证。** 核对 `ok`、错误信息、返回代码、字段和日期；接口返回成功仍可能全空、全零或日期越界。
5. **按计划执行。** 逐批保存回执；失败时保留已完成项，先查明原因。额度错误后停止自动重试和新增批量取数。
6. **分析和复核。** 以保存回执为输入，把原生值、客户端计算和 MCP 派生结果分别注明；跨回执关联必须使用代码和日期，不按数组位置猜测对应关系。

可以在复杂提示词结尾加一句：

```text
输出明确参数、数据表、计算公式和回执依据；
单位或发布日期未知时保留未知，空值与原始零分别统计，
推论与原始返回分开表达。
```

## 常用工具

| 阶段 | 工具 | 用途 |
| --- | --- | --- |
| 状态 | `wind_capabilities`、`wind_status` | 版本、读取签名、平台、SDK 限制、本地预算和历史额度错误 |
| 参数发现 | `search_wind_query_recipes` | 查明确的字段、专题表和 options，包含逐品种历史空值统计 |
| 字段发现 | `search_wind_fields` | 默认检索已有证据；显式 `scope=candidates` 检索第三方候选 |
| 文档和目录 | `search_wind_documentation`、`search_wind_bundle_metadata` | 查函数说明、原始出处、目录标签和参数候选 |
| EDB 发现 | `search_economic_indicator`、`recognize_wind_entities` | 查本地元信息，或调用官方 WAI 发现目录外候选 |
| 元信息 | `describe_economic_indicators`、`inspect_economic_catalog` | 查看单位、频率、上游来源及元信息出处 |
| 查询计划 | `plan_wind_data_queries` | 在本地规划 WSD/WSS 代码与字段分组 |
| 通用取数 | `query_wind_data` | WSD、WSS、WSQ、WSI、EDB、WSET 等官方读取接口 |
| EDB 取数 | `query_economic_indicator_data` | 用确认代码和日期读取基本面，检查逐代码观测状态 |
| 原始证据 | `read_wind_receipt` | 分页读取原始 Codes、Fields、Times、Data 及校验记录 |
| 序列分析 | `analyze_wind_series`、`analyze_wind_risk` | 均线、动量、收益、回撤、波动率、Sharpe、VaR/ES 和指定基准比较 |
| 截面分析 | `screen_wind_snapshot`、`aggregate_wind_snapshot` | 对已取得的明确样本池筛选、排序或汇总 |
| 名单筛选 | `screen_wind_universe` | 固定 WSET/WEQS 名单分批取数，继续到全部完成后统一排序 |
| 基本面计算 | `combine_economic_series`、`compare_saved_edb_versions` | 显式公式派生、比较本服务已经保存的下载版本 |
| 补充入口 | `parse_windpy_query`、`parse_wind_edb_export`、`register_economic_indicators` | 解析代码生成器查询或官方 CSV 表头，登记有证据的元信息 |

完整工具参数以客户端发现的 schema 和 `wind_capabilities` 为准。`plan_wind_fundamentals` 与 `call_alice_tool_on_windpy` 是额外便利层；其有限映射不会限制通用取数字段。

`search_economic_indicator(scope=local)` 只查本地目录；新安装默认没有研究种子库。目录外可用 `scope=terminal` 调用 WAI，但它返回的是候选，并不自动确认单位、频率、来源或选择代码。已确认的 EDB 代码可以直接取数，无需先登记。

## 怎样读取结果

| 返回信息 | 解释 |
| --- | --- |
| `ok`、`code`、`wind_error_code` | 本地处理结果与上游错误；不能只看 `ErrorCode=0` |
| `data_source_id`、`data_source_name` | 数据访问通道，当前为 Wind 终端 API |
| `source`、`metadata_provenance` | 原始上游来源及元信息出处；未知时保留空值 |
| `receipt_id`、`fetched_at` | 原始查询回执及抓取时间，抓取时间不等于观测日期或发布日期 |
| `raw.Codes`、`raw.Fields`、`raw.Times`、`raw.Data` | 官方原始数据轴和数值；不同方法的数组含义需要分别核对 |
| `derived_receipt_id` | MCP 分析生成的独立结果，可追溯输入回执和参数 |
| `from_cache` | 是否复用了明确允许的缓存 |
| `edb_series_observations` | EDB 整张矩阵逐代码的区间内、区间外、空值及原始零统计 |
| `table_date_observations` | WSET 请求日期、表内日期和原始时间轴的比较 |

WSD 多代码单字段时，Data 的各列对应代码；单代码多字段时，各列对应字段。WSS 通常以字段为列、代码为行。WSET 的 Codes 可能只是行编号，实际证券代码在表内 `wind_code` 列。读取回执时结合 `page.axis` 和 `raw_dimensions` 调整 offset，不能凭一个页面推断全表；检索和规划工具则按 `next_offset` 继续分页。

EDB 的 Times 是共享日期轴，每个日期不一定对每条指标都有数值。WSS 的原始 Times 也不能自动当成 options 中的历史交易日；报告中同时保留请求日期和原始时间。

## 缓存和调用预算

`query_wind_data` 和 EDB 查询默认 `max_age_seconds=0`，会取新数据。明确设置正数后，只有完全相同请求且在容忍时间内才可命中缓存。读取回执、查询计划和本地分析不消耗原生查询次数。

`wind_status(connect=false)` 返回本地预算位置。可以在该数据目录的 `query-budget.json` 设置本地限额，例如：

```json
{
  "schema_version": 1,
  "window_seconds": 604800,
  "max_native_calls": 10,
  "max_estimated_cells": null,
  "max_estimated_cells_per_request": null
}
```

这是示例任务预算，不是 Wind 账号额度。连接检查、WAI 识别、失败和超时也占原生尝试次数；未设置或为 `null` 的限额不拦截，`0` 明确禁止。多个客户端需要使用同一个数据目录和支持预算的新版本才能共享计数。启用数据格限额时，WSET、WAI 等未知返回规模的方法可能被 `LOCAL_BUDGET_UNESTIMATED` 拦截，详见 [本地预算](references/query-budget.md)。

请求规划减少往返次数，不减少代码乘字段乘日期的数据规模，也不授予额外权限。`quota_observation` 是历史错误观察，没有实时账号余额或准确恢复时间。

## 不通过聊天也可以调用

在服务目录用同一实现的 CLI 调用工具：

```sh
.venv/bin/python server.py --call wind_capabilities --params '{}'
.venv/bin/python server.py --call search_wind_query_recipes --params '{"question":"主连 映射","method":"wsd"}'
```

Windows 将 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。复杂 JSON 可由 MCP 客户端直接传入，避免手工拼接命令行引号。[演示参数文件](examples/futures-demo-requests.json) 提供按名称组织的 `tool` 和 `params`；文件本身不会执行任何查询。

## 常见问题

| 情况 | 下一步 |
| --- | --- |
| 客户端看不到工具 | 核对生成配置中的本机绝对路径，重新加载 MCP；单独检查客户端连接状态 |
| 找不到官方 SDK 或加载失败 | 核对 `--wind-module-dir`、动态库目录、CPU 架构及 Python 位数，按安装说明处理 |
| 查询字段没有命中本地清单 | 继续查官方文档、代码生成器或更广候选；本地清单不是字段白名单 |
| EDB 名称识别出多个代码 | 核对地区、品种、来源、频率和实际匹配的文字，选确认代码后再取数 |
| 全空或全零 | 保留原返回，检查日期、代码类型、options、适用性及授权，不自动换码或填值 |
| `OUT_OF_RANGE_DATA` | 先读已有失败回执和逐代码日期诊断；不把越界旧值标成请求期数据 |
| `error_category=quota_exceeded` | 保留完成回执，停止自动重试和新增批量下载，确认 Wind 可用情况后再继续 |
| `LOCAL_BUDGET_EXCEEDED` | 检查自己设置的本地限额；它与 Wind 上游超限分别记录 |

## 进一步阅读

- [期货进阶演示](FUTURES_DEMOS.md)：合约换月、批量规划、持仓与仓单、基差与估值、CTD 联查、基本面口径和风险计算。
- [期货调用与覆盖](skills/wind-terminal-api/references/futures.md)：已验证参数、样本范围和逐品种状态。
- [金融期货说明](skills/wind-terminal-api/references/financial-futures.md)：股指、国债、资金利率及历史验证。
- [EDB 元信息](skills/wind-terminal-api/references/edb-metadata.md)：同名异码、混频、单位、统计期和官方导出。

原生序列和本地指标描述输入数据，不自动构成期货策略收益：持仓、保证金、合约乘数、换月、交易成本及逐期信息可得时间需要另行建模。EDB 观测日不是发布日期，已经保存的下载版本也不能恢复未保存的历史发布版本。
