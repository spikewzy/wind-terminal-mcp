# 官方接口文档检索

开源发行说明：以下数量与证据描述开发环境的历史验证。完整快照不随开源仓库/ZIP 分发，请按 [本机导入说明](../../../docs/LOCAL_REFERENCES.md) 在自己的机器生成；未导入时搜索返回明确缺口。

2026-09-30 从 Wind 终端展示的官方帮助网页读取公开目录和文档正文，已将 9 份资料导入本地，共 122 段可检索正文。元信息来源为 `wind_api_official_help`；这是 Wind 终端 API 的说明渠道，独立于 Alice MCP。

2026-10-04另加入1份终端PDF阅读摘要，5段，合计10份检索资料、127段。新增摘要来源为 `wind_terminal_ui`，不是原PDF或公开接口响应；原9份资料保留不变。

## 入口

`search_wind_documentation(question, document?, limit=5, offset=0)` 按关键词查询本地副本。例如：

```json
{"question":"Days Alldays","document":"python"}
```

```json
{"question":"indexhistory","document":"python_sector"}
```

| document | 内容 |
| --- | --- |
| python | Python 接口手册 |
| api_faq | 通用接口 FAQ |
| python_faq | Python 接口 FAQ |
| client_api | Client API 使用说明 |
| mac_api | Mac API 使用说明 |
| code_generator | 代码生成器说明 |
| ai | AI 接口手册 |
| python_basic | Python 基础函数实例 |
| python_sector | Python 获取板块数据案例 |
| futures_rules | 《Wind期货合约名称规则》的终端PDF阅读摘要，含原页码和2020年规则变更边界 |

结果包含 `heading_path`、`line_start/line_end`、`page_url`、`fetched_at`；长章节按 `part/parts` 分段，使用 `next_offset` 继续。原文在项目 `references/official-help/`，原始公开响应在其 `raw/` 子目录。代码单元只保存为文本，原 Notebook 的输出不当作本机查询证据。

`futures_rules` 的 `content_kind=paraphrased_terminal_pdf_observation`、`metadata_source_id=wind_terminal_ui`；`pdf_pages`指向原PDF，`line_start/line_end`指向本地摘要。该项 `raw/` 文件保存人工观察记录而非原PDF，指纹只验证这份记录；`page_url`是终端文档中心入口，原PDF直链未知。2020-05-01的规则日期、2023-11-16的目录更新时间和2026-10-04的读取日分别保存，不认证2026年的规则无变更。详见[合约口径说明](futures-contract-rules.md)。

`local_raw_copy_matches_import` 只核对本地原始文件是否与导入指纹一致；`remote_freshness_checked=false` 表示本次搜索没有重查网页。返回旧文档不等于当前版本、字段、参数或账户权限已通过验证。查询零结果也不证明 Wind 没有该功能。

## 来源与更新

正文地址来自官方帮助页实际加载的公开脚本和菜单。使用了不带 Cookie 或令牌的只读 GET，没有调用管理功能、行情服务或账户接口。发现链、页面地址、SHA256 和获取时间在 `verification/official-help-retrieval.json`；MCP 搜索过程只访问已保存文件。

`import_official_help.py <目录>` 从已下载的九份命名响应重建索引。也可显式使用 `--download` 下载九份公开帮助；不运行 Notebook，不修改 Wind 软件。导入前检查返回码、父文档 ID 和 Notebook 结构；全部文件有效后才写入。每份请求的来源地址可从索引或检索结果的 documents 读取。

如果目标目录已有 `raw/futures_rules.json`，重建时一并保留其独立来源、摘要标记和页码，不将它按公开Notebook正文处理；不会重新读取终端或下载PDF。

通用手册中包含 Windows 安装、交易、组合上传及其他平台的示例，不能据此在 Mac 上执行或扩大 MCP 的接口范围。当前 MCP 仍仅允许明确列出的 WindPy 读取方法。

## 指数权重与日期的实际核验

官方 Python 基础案例给出了 IndexConstituent 表及 date、wind_code、i_weight 列。通过本机 MCP 做了两次日期对照：

| 请求日期 | 返回 date 列 | 代码数 | 权重值合计 | 原始回执 |
| --- | --- | --- | --- | --- |
| 2026-09-29 | 2026-09-01 | 300 | 99.9997 | `2ddd397fc7474a2b9086ad5b2c9930a9` |
| 2026-08-31 | 2026-08-31 | 300 | 100.0 | `1dda2be098a4434a9d10a3b46a07d826` |

两份表的 raw.Times 都是查询当天 2026-09-30，不能当作各行数据日期。9月29日请求不能作为同日权重使用；8月31日仅确认了这个历史样本的字面日期一致，没有证明日期的完整业务定义、公布时间或原始历史版本。

官方板块案例另有 indexhistory 调整历史请求。本机在 45 秒限制内未完成，回执为 `c86522a1412c4c3d8c0881aeda6a36ac`，错误 WIND_TIMEOUT。没有将其归因于账户权限，也没有认证成分调整事件的字段含义。`probe_index_history.py` 默认只查询两份权重对照；历史接口需显式加 `--include-history`，不自动重复已超时请求。

WSET 查询及 `read_wind_receipt` 新增 `table_date_observations`：对整表而非当前页比较日期，保留不匹配、空值、未解析值及 raw.Times。这是字面核对；`requested_date_coverage_certified` 和 `date_column_semantics_certified` 仍为 false。证据在 `verification/mcp-index-history.json`。
