---
name: wind-terminal-api
description: 使用本机 WindPy MCP 查询 Wind 终端数据，检索 EDB、软件目录及官方接口文档，核查口径和历史版本，或迁移 Alice 查询到 WindPy。来源标识 wind_terminal_api，独立于 Alice MCP；用于用户指定 WindPy 或任务已配置 Wind 终端 API 的数据工作。
---

# Wind 终端 API

数据服务为本机官方 Wind 桌面应用 / WindPy，MCP 服务器名 `wind_terminal_api`。Alice MCP 是另一来源。沿用用户指定来源，不因 Alice 的配额或权限错误切到本服务，也不把本服务失败静默转向 Alice。

0.24.1的平台范围为macOS、Windows、中科方德5.0桌面版、UOS20桌面版、银河麒麟V10 SP1桌面版。其他Linux和任何Linux服务器版不支持，未知桌面版本也不放行。先看`wind_capabilities.platform_policy`和`wind_status(connect=false).platform_support`；这些只核对平台策略，不能证明SDK、账号或所有CPU组合已实测。Windows/信创适配已加入，真实目标机验证仍待进行。每台机器使用对应平台的官方WindPy/动态库，不复制Mac SDK；具体安装与`--wind-module-dir`参数见项目`INSTALL.md`。Mac本地目录快照在其他平台上可能缺少原文件，只作带来源的候选，不冒充当地最新字段字典。

开源包不附完整官方帮助或终端目录快照。未导入时，文档/目录搜索返回 `LOCAL_REFERENCE_NOT_INSTALLED`；按项目 `docs/LOCAL_REFERENCES.md` 在本机导入后再使用。下文的大目录与文档段落数量描述开发环境的历史验证，不代表新安装自带这些内容。

## 当前入口

- MCP：优先发现 `wind_terminal_api` 服务器的工具；先读 `wind_capabilities` 了解实际映射和未完成项。
- 服务实现位于用户安装的 Wind 终端 MCP 目录；实际 Python 与服务绝对路径以该目录生成的 `client-configs/mcp.json` 为准，不依赖开发机器的项目名称或目录。
- MCP 工具未在当前会话加载时，可用该配置中的 `command` 和 `args[0]` 调用同一服务 CLI，追加 `--call <tool> --params '<JSON>'`。包含空格的路径须作为独立命令参数传入。
- `wind_status(connect=true)` 只验证终端连接，不代表每个指标都有权限。

## 基本面数据

本服务面向股票、债券、基金、指数、期货、期权、外汇、宏观和产业数据，没有农业优先级。当前用户强调国内全部商品及金融期货，日线为主、优先补齐基本面历史；这不是限制其他资产的白名单。`wind_capabilities.data_coverage` 分开记录接口路由、观察样本与尚未完成的覆盖验收，不把成功取到少量样本称为全覆盖。

国内期货的目录、全品种日线样本、基本面历史范围与权限反例见 [期货调用与覆盖](references/futures.md)。仓单可按“仓单 六日”检索82项商品产品记录的有限区间验证：78项有值、4项全空，原始零和空值分别保留；不同品种单位未知时不能合计，短区间不升级为完整历史。主连、次主连、近月连续、固定月份连月及品种指数先按 [官方合约规则摘要](references/futures-contract-rules.md) 区分；保留2020年前后的规则边界，不自动创建主力选择或复权算法。主连/次主连/近月或当季的`trade_hiscode`查询已有沪铜和国债两品种、两日共12个映射样本；可搜索“主连 映射”，不把`00`连续替换成主连，也不据样本推定全历史或价格调整。按实际目录发现代码；月均价与TAS代码保留，不能用仅含“字母+年月”的假设丢弃。某条指标权限失败或历史停止不代表整类数据不可用。股指估值、基差、四类国债CTD、SHIBOR及收益率曲线反例见[金融期货基本面](references/financial-futures.md)。

元信息不足时按 [EDB元信息与官方导出](references/edb-metadata.md) 从原始指标界面核对。`parse_wind_edb_export` 可解析官方格式CSV表头；导出可能经过变频或单位转换，显示信息保留在 `export_metadata`，不自动认作原始WindPy口径，也不自动导入数值或目录。已检查六项跨行业指标的现行名称、单位、频率与来源；包括停更的163家高炉样本，不能根据WAI旧名称续接其他样本。`FIELD_INFO=T` 本账户返回无权限，普通EDB数值权限另行判断。

新安装默认不导入农业研究种子库；原有用户目录和回执保留。`search_economic_indicator(scope="local")` 只检索用户已有元信息。目录之外使用 `scope="terminal"`，通过官方 WAI fer 发现候选，结果不受本地目录数量限制；此路径不支持 unit/freq/source 过滤，也不是全库枚举。保留同名异码、原始候选和未知单位，不自动选码、登记或取值。已确认的 EDB 代码可直接查询，不需要先注册进本地目录。

涉及产业供需、混频、单位、统计期或版本差异时，读取 [references/fundamentals.md](references/fundamentals.md)。其中农业示例仅用于说明口径，不限定服务范围。

1. 用 `inspect_economic_catalog` 查看目录构成，再用 `search_economic_indicator` 查代码、名称或关键词；可通过 filters 精确限定 freq/unit/source。阅读地区、频率、单位、上游来源、统计口径。它查询本地导入目录，不是 Wind 全库检索。零结果不能证明 Wind 无数据。
2. 有多个口径时，用任务上下文选定并说明；无法消除实质歧义才询问用户。不要把同类名称直接视作同一指标。API 查询使用确认代码，不静默选最高匹配分。
3. 用 `query_economic_indicator_data(question=<代码>, beginDate, endDate)` 取数。`observation` 选取查询结束日及以前的最后 N 个观测日期，与日期范围互斥；它不是按公布时间选择“最新发布 N 期”，可能排除已发布但以未来期末标记的观测。默认不使用旧缓存，不主动前填缺失。
4. 按 `ok`、`code`、`wind_error_code` 及真实日期判断；`ErrorCode=0` 仍可能日期越界。失败批次不继续扩大，也不换数据源。
5. 输出保留 `data_source_id`、`data_source_name`、`source`、元信息来源、原始回执 ID。目录中的 Alice 元信息仅是候选资料；不能宣称由 WindPy 重新验证了单位、来源或口径。

批量EDB的`Times`是共享日期轴，不能当作每条指标都具有相同数量的观测。查看`edb_series_observations.series`的逐代码区间内/外非空、空值和零值数量；`populated_out_of_range_codes`指明真正含越界非空值的指标。某条全空、某条只返回旧值和其他条有区间内值要分开说明，不据此推断频率、权限或停更。`OUT_OF_RANGE_DATA`仍是失败；先用`read_wind_receipt`读取已有证据及`stored_query_validation`，诊断不会自动删行、填值、重试或批准失败回执用于分析。详见[EDB元信息](references/edb-metadata.md)。

目录缺失或只有名称时，可先用 `recognize_wind_entities(text)` 调用已验证的官方 `w.wai("fer", text)` 寻找候选；它不依赖本地目录，但并非全库目录搜索。核对 `edb_candidates`、原始备选实体与同名异码警告；识别结果没有完整单位、频率或来源。`stockBondIndex` 返回的数字 ID 不能当作 WSS 字段名。识别不会自动登记、选择或取数，零结果也不证明 Wind 无此指标。

检查`edb_text_coverage`和每个候选的`occurrences`，按段读取`matched_text`及`unmatched_text`。“中国:库存:碳酸锂”曾只匹配“库存”，同一代码也出现在聚丙烯问题里；剩余地区、品种或统计口径不能据上下文自动补到该代码。`full_paragraph`只表示文字覆盖，不确认定义或排除备选代码。缺失/错误位置与分段数不一致保持未验证；商品实体`param`中的代码不能提升为用户所问指标。详见[EDB元信息](references/edb-metadata.md)。

财务备选项中的 `financial_candidate_context` 可提供本地报表类别：仅在目录节点编号和名称同时匹配、相关文件指纹均未变时补充。利用类别区分一般企业/银行及利润表/现金流量表，保留原始候选；匹配不代表 WindPy 字段、参数或选取已经确认。未匹配与索引不可用都不会自动回退到同名指标。

通过 Wind 终端 EDB 或 Wind API 代码生成器确认候选口径后，再以 `register_economic_indicators` 录入元信息及证据。已验证的桌面检索路径见 [references/terminal-ui.md](references/terminal-ui.md)，其元信息单列 `wind_terminal_ui`，不能冒充 API 返回。`parse_windpy_query` 可将单条生成代码转换为 MCP 参数，解析不会执行 Python，也不证明字段或权限有效。官方公开 SDK 说明可以辅助选函数；不得编造指标 ID。不要尝试读取凭据或解密应用的受保护字典。

## 其他领域与 Alice 迁移

`query_wind_data(method, arguments)` 支持官方 SDK 的显式读取接口。签名取自 `wind_capabilities`，字段、专题表名和 options 需有 Wind 代码生成器/文档/已验证调用依据。不要把 Alice 的中文字段或复权数字枚举直接传给 WindPy。

先用 `search_wind_query_recipes(question, method?)` 按中文任务、字段、表名或选项查实际试取请求，包含 WSET 表参数及期货仓单、会员排名、连续映射、合约资料、可交割券。返回完整 method/arguments、证据和限制；示例日期及选项不是默认值。新安装保留示例但没有开发机原始回执，`runtime_query_verified` 此时为 false；需在本机试取核验当前权限。该工具不执行查询，也不是全库参数字典。当前89条示例中包含空值及零值反例；先读限制，不能把请求成功直接当作数据有效。

期货可组合品种关键词检索，例如“沪铝 仓单”“尿素 仓单”“30年期国债 净持仓”。90项已核对产品关联到40条既有示例，`product_discovery.products`给出原请求中的代码及逐代码历史非空、缺失和原始零数量；它不是本次新取的数据。检索仍返回完整原批次，不自动选择产品或裁剪参数；先核对实际代码、日期及限定词，尤其区分普通品种、月均价及TAS。某代码的全空样本不能被同批其他代码的成功状态覆盖。新安装的原始回执验证仍以`runtime_query_verified`为准。

遇到 `error_category="quota_exceeded"` 时保留已完成结果，停止自动重试和新增批量取数，待Wind可用额度确认后再继续；不拆分以绕过额度或静默切换来源。`wind_status(connect=false).quota_observation` 仅是当前本地配置的近期失败回执记录，不是实时余额；额度范围和恢复时间未报告时保持未知。

批量WSD/WSS先用 `plan_wind_data_queries(method, arguments, limit?, offset?)` 在本地规划代码/字段分组，遍历全部分页后才得到完整请求集合。规划不取数、不自动执行、不改变日期和options；较少调用不代表较少数据用量或额度已恢复。保留回执和缺失值，遇超限停止。财报日历、前填起点及32项Q&A的适用边界见[批量查询规划](references/query-planning.md)。

也可阅读 `windterminal://query-recipes` 和 `windterminal://field-notes`，或项目中的 `references/verified-query-recipes.json` 与 `verification/wind-api-ui-fields.json`。它们分别保存已试取的请求及少量官方字段释义。官方帮助入口和已确认口径见 [references/official-help.md](references/official-help.md)。

查函数、日期参数和正式示例时，使用 `search_wind_documentation(question, document?, limit?, offset?)`。它检索9份官方文档正文及1份终端PDF阅读摘要，共127段，保留来源入口、章节、本地行号与获取时间；摘要另带原PDF页码和独立来源标记。`document` 可选 python、api_faq、python_faq、client_api、mac_api、code_generator、ai、python_basic、python_sector、futures_rules。后者是人工摘要，未保存原PDF，目录更新时间不代表当前规则认证。它不联网，也不执行代码；本地副本指纹一致不代表远端刚刚更新过。旧日期、Windows 安装流程和 AI HTTP 示例须结合本机 WindPy 单独核验。详见 [官方文档检索](references/documentation-search.md)。

证券字段可先用 `search_wind_fields(question, method)` 查询这些本地知识。它返回字段释义、上游 source、原始示例及回执核验状态。当前只收录 61 个字段，其中 3 个有官方界面释义；期货中文标签来自软件目录及逐项试取关联，不等于完整官方释义；零结果不代表 Wind 没有此字段。`successful_query_available` 表示原请求通过验证；`successful_sample_available` 还要求该字段至少有一个非空值，逐例查看 `value_availability`。这些状态不能推定报表类型、币种、单位和历史权限；示例日期与 options 不是默认值。检索不会发起 Wind 查询，也不会把无效字段或 WAI 数字 ID 自动替换成其他指标。

财报字段另有 `statement_value_evidence`：茅台两个年度的九项字段，共34个值与正式年报核对一致；归母字段仅合并，其他八项含母公司。可按“净利润”“归母净利润”“资产总计”等已核对行名或受证据支持的别名查到证据，PDF和原始回执指纹失效时降级。相关 WSS 查询及回执读取附 `statement_reference_checks`，整表检查后再分页，不覆盖原值。该合并样本的 `net_profit_is` 包含少数股东损益，不能当作归母；2024值为2025年报比较列。其他主体、日期、选项和单位缩放仍未认证。详见[财务报表核对](references/financial-statements.md)。

缺少字段时，可显式用 `search_wind_fields(question, method, scope="candidates")` 检索6,865个第三方候选字段及7种接口映射。目录来自2022年的windget 0.0.7，不是官方当前字段库；候选始终未验证，单位、定义和options规则未知。默认observed范围不自动切换，也不自动取数。按出处核对后做小范围查询，保留空值、无效字段和回执。详见[中文字段发现](references/field-candidates.md)。

更广的候选用 `search_wind_bundle_metadata(question, kind="indicator"|"parameter"|"sector")` 检索。已从软件本地明文记录提取 16,863 条指标、257 组参数表和 23,495 个板块节点，并关联软件的中英文标签，元信息来源 `wind_api_bundle`。内部表达式、目录节点号和选项值不能自动当作 WindPy 字段、WAI 主实体 ID 或接口参数；同名节点不自动选取。检查全部相关文件指纹是否仍匹配，详细流程见 [本地目录](references/bundle-metadata.md)。已有实测显示，直接把本地数量选项 10000 传给 `oper_rev` 的 `unit` 并未缩放返回值，不能据目录值补造金额单位。

`kind="sector"` 可按名称、英文名、类别、板块 ID 或已关联指数代码检索。先核对类别路径；同名板块的 ID 不自动合并，指数代码与 sectorid 分别使用。目录不是成分名单；确定板块后再用 `query_wind_data` 的 WSET sectorconstituent 查询指定日期。已验证“全部A股”和“被动指数型基金”的请求与回执见查询示例及 [固定样本池筛选](references/universe-screening.md)。基金代码数量不等于去重后产品数量，当前名单也不能证明历史覆盖。

`call_alice_tool_on_windpy(server_type, tool_name, params)` 直接迁移已映射的 EDB、部分行情和 K 线参数。返回 `QUERY_PLAN_REQUIRED` 的领域需先确认原始问题、Wind 字段及参数，再提交 `resolved_request={method, arguments, evidence}`。这是显式适配，不代表远端 Alice 的自然语言解析或分析已被复制。不要用取到原始价格作为风险统计、技术指标或公告全文已经完成的证据。

股票财务 `get_stock_fundamentals` 新增有限范围的中文问题自动取数：营业收入、净利润、资产总计、负债合计、归母净利润、营业利润、所有者权益合计、货币资金、财务费用，要求明确报告期、人民币元单位及报表口径。归母含义唯一要求合并口径时可省略报表类型，计划会保留推定理由；与明确母公司报表冲突则拒绝。先阅读[中文财务问题](references/fundamental-questions.md)。`plan_wind_fundamentals` 可只识别并查看计划；直接兼容调用会保存计划后执行WSS。WAI只用于证券类别与候选，不把财务数字ID当作字段；同名市场歧义、未知文字和交错的多公司日期/字段均会阻止取值。报表字段映射须有指纹核对通过的现存年报样本；全流程保留原始值、缺失值、请求报告期和独立派生回执。不能将净利润当归母，不能将合并权益合计当归母净资产，不能把样本单位认证扩展到所有公司；其他财务问题仍需显式计划。

股票、场内基金、指数的 `*_price_indicators`、`*_kline`、`*_quote` 接受名称或完整代码。名称通过 WAI fer 一次按行识别：须覆盖整个名称，主结果及所有备选项在对应证券类型内只能有一个完整代码；不按置信度选择。快照最多50个输入，序列每次一个。同名多市场或未识别项使整批停止，返回候选；明确代码跳过识别。`security_resolution` 保存原始识别和独立选择回执，行情失败或序列截取也保留证据。实测“中国平安”有A股、港股、ADR三项，不能自动选A股。详见[证券名称与市场歧义](references/security-names.md)。

股票、场内基金、指数的 `*_quote` 可省略日期：先读取该证券快照的 `RT_DATE`，再取这一日的一分钟序列。只给 begin 时延伸至 RT_DATE；只给 end 时拒绝。返回 `date_selection` 保留快照回执，RT_DATE 不是全市场日历或已收盘证明。日期无效时显式提供起止日期，不直接使用当天。当前 WSI 对 `amt` 返回 `amount`，工具通过 `field_aliases` 说明已验证对应关系；原始 Fields 保持不变。均价、换手率等未实现的输出不可自行补称已兼容。

日K的 `issusp="0"` 已按同一 WSD 返回的 `trade_status` 实现：`交易`保留，`停牌一天`剔除，先过滤再执行 count；空值及其他状态明确报错。股票和ETF有成功样本，沪深300状态为空，不能自动视作可交易。其他周期的停牌政策、120/240分钟聚合及 afdate 尚未完成。任何过滤或 count 截取都会另存 `derived_receipt_id`；后续分析选中序列须传这个 ID，`receipt_id` 仍指向完整原始数据。详见 [K线迁移](references/kline-migration.md)。

当使用新闻、公告、筛选、WAI 的其他功能、特殊字段或聚合分析时，先检查迁移矩阵和当前验证记录；尚未验证的路径需要真实小样本试取及内容核对。WAI fer 已验证的是实体识别，不能据此推定翻译、全文检索或其他 AI 服务可用。

当前财务文档兼容调用明确返回 `DOCUMENT_SEARCH_NOT_MIGRATED`；不能忽略 `top_k` 或把原始新闻列表当成 Alice 的相关片段检索。

调用新闻接口前检查 `wind_capabilities.native_runtime`。已核对的 Mac ARM64 26.1.7 SDK 中，`wnd/wnq/wnc` 的底层导出函数直接返回 NULL；当本机模块、库指纹及架构匹配时，MCP 会在连接前返回 `WIND_SDK_METHOD_UNAVAILABLE`，不得反复改变参数重试或归因于账户权限。其他 SDK 版本、架构和 WSET 专题表不能据此推断；公告的替代接口仍需正式参数和实际内容验证。证据为 `verification/native-news-availability.json`。

批量筛选股票或基金时，读取 [固定样本池筛选](references/universe-screening.md)。新增 `screen_wind_universe` 基于已验证的 WSET/WEQS 名单，分批 WSS 取数并保存继续回执；取完整份名单后统一排序。pending 时继续，batch_failed 时先核查错误，不把部分结果称为完整筛选。Alice 的 search_stocks/search_funds 已可通过 resolved_request 的 universe_receipt_id 接入该流程。

WSET 的 `table_date_observations` 分别记录 options 中的 date、整表 date/tradedate 列和 raw.Times，分页回执也保留整表比较。它只做字面日期核对，不推定发布日期或生效日；缺少日期、使用宏或重复 date 参数时不猜。实测请求沪深300的 2026-09-29 权重返回 2026-09-01 行日期，不能改标为9月29日；2026-08-31对照返回同日行日期，仍不证明完整历史或当时可得版本。indexhistory 本机样本超时，保留未验证状态，不因官方文档有示例而认定可用。

本地分析工具：`analyze_wind_series` 对已验证价格回执或选中序列计算均线、动量、回撤、波动率和 Sharpe，需显式设置年化频率；分析选中序列时会核对原始指纹并重建选择结果。过滤后观测可能不连续，按观测计算的收益不直接代表按日持仓回测。`screen_wind_snapshot` 只在已取得的样本池筛选；`aggregate_wind_snapshot` 汇总已确认同单位的截面字段；`combine_economic_series` 按显式公式组合 EDB 序列。派生结果有独立回执，不覆盖原始值。兼容查询的 resolved_request 可附带 `analysis={kind:"series"|"screen"|"aggregate", ...参数}`，具体结构见 MCP 工具说明及项目代码。

风险分析使用 `analyze_wind_risk`，具体流程与计算约定见[风险分析](references/risk-analysis.md)。它补充历史VaR/ES、回撤起止和恢复日期、下跌观察数；指定基准回执后计算Beta、相关性、Jensen Alpha、跟踪误差和信息比率。默认要求每个收益区间的起止时间均相同；显式 `common_intervals` 才取共同区间，不先合并价格造成跨期收益。至少四个正数非空价格；年化频率显式提供，零方差返回未定义原因。Alice显式计划可用 `analysis.kind="risk"`，并提供 `benchmark_receipt_id` 或带 method/arguments/evidence 的 `benchmark_request`，后者串行取两条价格再分析；基准失败保留已完成股票回执。结果不认证复权、币种、无前瞻偏差或与上游专有指标数值相同。

## 回测与历史版本

- 统计期、发布日期、查询日期分开记录。EDB 返回日期不得当作发布日期。
- 默认历史值可能含后续修订；仅有时间序列不能声称 point-in-time 安全。若无法取得当时版本，明确记录限制，并保留前瞻偏差风险。
- `compare_saved_edb_versions` 仅比较 MCP 开始保存后的下载快照，不能恢复过去未保存的发布版本。
- 日/周/月混频在研究层按实际可获得时间对齐；不按期末日期提前前填，未知发布日期不能自动假设为期末。
- 原始值与单位原样保存；例如“万吨”已包含量级，不再乘一次 `magnitude`。换算另列派生字段和公式。

## 失败与审计

原始回执、缓存及观测版本在本项目 `runtime/` 下，独立 SQLite 文件为 `wind_terminal_api.sqlite`。`read_wind_receipt` 分页读取完整原始查询证据。查询超时后原生工作进程会终止，不盲目重试整批。

覆盖状态见项目 `MIGRATION.md` 及 `verification/`。已通过的连接、协议或部分行情检查不能证明所有 35 个 Alice 工具具有完整语义兼容。
