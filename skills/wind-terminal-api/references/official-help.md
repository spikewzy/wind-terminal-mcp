# 官方帮助入口与已核对规则

2026-10-04已从终端文档中心读取《Wind期货合约名称规则》，28页，封面日期2020-05-01，目录更新日期2023-11-16。按 `document="futures_rules"` 检索的是带原PDF页码的阅读摘要，来源 `wind_terminal_ui`，不是原PDF副本。主连/次主连、近月连续、连月和指数区分见[期货虚拟合约规则](futures-contract-rules.md)；历史规则、2026年当前适用性及价格调整尚不能合并认证。

2026-09-30 已补充不依赖窗口操作的读取路径：从官方帮助页公开加载的目录和正文导入 9 份资料，并通过 `search_wind_documentation` 检索。保留来源、章节、行号和本地副本指纹，详见 [官方文档检索](documentation-search.md)。检索不会实时重查网页，文档示例不自动成为本机调用验证。

2026-09-29 通过 Wind 金融终端的可见帮助页面读取，不是第三方示例。终端底部输入 `API`，选择 Wind API 插件，再打开“手册”。正文和菜单能通过 CUA 可访问性读取；选择后若正文仍是上一页，等完成独立工作后再核实标题和内容。

## 实际可见的文档

| 文档 | 终端内可见地址 | 用途 |
| --- | --- | --- |
| Client API | `http://114.80.154.45/ApiHelpCenter/web/webapp/manual/id-0bd5f99c-3a53-4cc4-a818-03fe3f0b34f8` | 函数分工、错误码、查询规模和流量说明 |
| Python 接口手册 | `http://114.80.154.45/ApiHelpCenter/web/webapp/manual/id-b89ae6bf-17db-40d6-8f7c-123702a30755` | WSD/WSS/WSI/WSQ 参数、返回轴和示例 |
| Mac API | `http://114.80.154.45/ApiHelpCenter/web/webapp/manual/id-93fbf2b7-636b-4f5c-b846-1c65ed28377d` | Mac 的独立 Wind API.app 安装与认证方式 |
| 代码生成器 | `http://114.80.154.45/ApiHelpCenter/web/webapp/manual/id-f4d230c0-9d1d-4728-b28a-e89fb43bc441` | 代码生成流程和指标选择界面 |
| 接口 FAQ | `http://114.80.154.45/ApiHelpCenter/web/webapp/manual/id-3218ff17-dbc3-4691-b473-18eabf697c77` | 财务序列报告期与交易日历的区别；2026-09-29 已从终端页面读取 |
| AI 接口手册 | `http://114.80.154.45/ApiHelpCenter/web/webapp/manual/id-2427d85e-c883-43d2-b49e-43b4e447d050` | 金融实体识别返回契约；本机 WAI fer 已通过实际 MCP 调用 |

这些地址记录当前终端所显示的文档位置，可能随版本和站点变化；优先从已登录终端导航，不猜测内部接口或抓取认证信息。公开产品介绍为 [Wind Client API](https://www.wind.com.cn/mobile/ClientApi/zh.html)。

## 对当前实现有影响的规则

- WSD 的 `Period` 支持日、周、月、季、半年、年，对应 `D/W/M/Q/S/Y`。Alice 的 `6mo` 可映射为 `S`；边界与返回列仍要验证。
- WSD 的前后复权为 `F/B`；文档还列定点复权 `T`，但没有由此证明 Alice 的 `afdate` 参数可以直接复制。
- WSI 文档列分钟数 1 至 60。本实现不能直接把 120 或 240 传入并声称已复刻其聚合规则。
- WSS 可以多标的多字段，但一次请求针对一个交易日或报告期；`rptDate`、`rptType`、币种等需要按具体指标确认。
- 财务序列的报告期是季末，不等于公告日。FAQ 的 WSD 财务示例使用 `Period=Q;Days=Alldays`：按交易日历取季度数据可能把周末季末移到前一交易日，造成缺值。页面给出的字段为 `eps_basic`；2026-09-30 已通过两次真实 MCP 查询验证日期差异：600519.SH 的 oper_rev/net_profit_is 在默认日历下缺失 2024 年前两个季度值，Days=Alldays 下四个季末都有值；eps_basic 在两组参数中均全空。详见 `verification/financial-calendar-probes.json`。没有据此认证全部字段、单季/累计口径或历史可得时间。
- 原始列、代码、日期轴各有含义；WSET 专题表不能按时间序列方式分页。
- 当前手册提到 WSD/WSS/EDB 单次单元格限制及技术指标更小的限制；实际额度仍以账户、版本和返回错误为准。采用小样本确认及受控分批，不能把本地目录数量当作授权额度。
- Mac 文档明确 Wind API.app 与 Wind 金融终端是独立应用。能够打开终端手册或 EDB 界面，不能替代 WindPy 认证与数据权限验证。

不要照抄通用 Python 手册的 Windows 注册表修复流程到 Mac。现有 WindPy 已正常取数，无需重装或删除 `~/.Wind`。

## 智能实体识别的核验边界

AI 接口手册列出 `edbIndex`（EDB 指标）和 `stockBondIndex`（股债属性、财务字段）两类实体。文档中的请求包含 `text` 和 `outType`；结构化返回包含实体名称、类型、ID、文本位置，并有外层和内层状态。该契约没有承诺返回指标单位、频率、完整参数说明、公布日期或全库分页搜索。

用户从本机官方代码生成器复制的命令为 `w.wai("fer", "Wind")`。2026-09-29 已通过独立 MCP 执行成功，原始回执为 `1f55480e1bcf45ac96c83a82e6d78c1c`。后续多段公开名称识别的回执为 `f60fe38585bd49ff9f2a58c750d99522`，证据在 `verification/wai-fer.json`。无需猜测函数名或内部 HTTP 地址。

实际 `Data[0][0]` 是 JSON 字符串。除了 SDK 的 `ErrorCode`，还须校验外层 `status`、内层 `succeed` 与状态码；当前实测状态码字段为 `status_code`，手册使用 `statusCode`。应用失败不能因为 `ErrorCode=0` 就认定成功。专用工具 `recognize_wind_entities` 已做这些检查，并保留原始回执。

- “中国:GDP:不变价:当季同比”返回 `M0039354`，“中国:CPI:当月同比”返回 `M0000612`；后者已另外通过 EDB 取回 2026 年 1—8 月 8 个观测，未因此补造元信息。
- “中国:沿海:库存量:豆粕”返回 `S6999828`，与终端已选 `V0103973` 同名。2026 年 9 月的 4 个日期和值相同，只能证明这一小段样本一致，不能证明全历史、更新频率或原始机构完全相同。工具显式提示同名异码，不自动合并目录。
- “贵州茅台的营业收入和净利润”返回证券代码，以及 `stockBondIndex` 数字 ID 和多个候选。这些 ID 不是已验证的 `oper_rev` 等 WindPy 字段名，仍需代码生成器或正式字段字典确认。
  其中营业收入数字 ID `84952` 已单独做过兼容性探测：WSS 返回 `-40522006 / invalid indicators`。失败回执 `439930dcc62b4a6f84204cf7528ecbec` 保存于 `verification/wai-financial-id-probe.json`，没有据此创建字段映射。

实体识别提供新的候选发现路径；它仍不等于完整字段说明、可分页的全库目录或 Alice 自然语言查询服务。识别零结果也不能证明 Wind 不包含该指标。

2026-09-29 复查时，Wind API 的 EDB 空向导可读取截图及窗口标题，但坐标点击仍返回 `noWindowsAvailable`；重选应用和窗口 Raise 后未恢复。该证据仅说明当前自动化操作失败，不说明是用户操作冲突、SDK 未登录或指标无权限。终端内官方帮助页面的可访问性导航正常。

## 保存的查询知识

2026-09-29 补充实测：`wsq(..., "rt_date,rt_time")` 对股票、ETF、指数返回 YYYYMMDD 数值日期；分钟行情默认日期据此选择，并保留每次快照回执。`wsi` 对请求字段 `amt` 返回 `amount`，三类证券样本均相同。该对应关系仅用于 WSI 验证，原始字段名不改写；不据此推断金额单位。证据为 `verification/latest-quote-date-probe.json`、`intraday-field-aliases.json` 与 `mcp-latest-quotes.json`。

MCP 资源 `windterminal://field-notes` 提供本次少量官方字段释义；`windterminal://query-recipes` 提供带回执的小样本查询。字段定义与 API 实测应一起使用：接口接受字段证明请求可执行，不证明其单位、发布日期和全部选项都已核对。
