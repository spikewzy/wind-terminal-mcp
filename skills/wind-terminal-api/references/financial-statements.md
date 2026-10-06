# 财务报表样本核对

2026-09-30，已将贵州茅台 `600519.SH` 的九项 WSS 字段，共 34 个返回值，与公司《2025年年度报告》逐格核对。样本包括两个年度；归母净利润只核对合并报表，其余八项核对合并与母公司报表。原始 PDF 的第 56—63 页已经目视检查，包含表头、币种、单位和年度列。

## 验证范围

报告日分别为 `20251231`、`20241231`，显式指定 `rptType=1` 或 `2`，`unit=1`。最初四字段核对16格，新增五字段核对18格；八次保存的 WSS 原始回执中，34格均与年报人民币元数值一致。

| 样本参数或字段 | 年报对应内容 | 适用边界 |
| --- | --- | --- |
| `rptType=1` | 合并报表 | 此公司、此年度及这些字段的对应关系 |
| `rptType=2` | 母公司报表 | 未认证调整报表等其他枚举 |
| `oper_rev` | 营业收入 | 合并表另列营业总收入，不能混用 |
| `net_profit_is` | 净利润 | 合并表该项包含少数股东损益，不等于归母净利润 |
| `tot_assets` / `tot_liab` | 资产总计 / 负债合计 | 期末存量；营业收入和净利润是年度流量 |
| `np_belongto_parcomsh` | 归属于母公司股东的净利润 | 合并利润表按所有权归属划分的结果；不替代母公司单体报表净利润 |
| `opprofit` | 营业利润 | 不等于净利润或利润总额 |
| `tot_equity` | 所有者权益（或股东权益）合计 | 合并表含少数股东权益；不等于归母净资产 |
| `monetary_cap` | 货币资金 | 期末存量；不替代现金及现金等价物余额 |
| `fin_exp_is` | 财务费用 | 该样本为负，原样保留；不取绝对值或转成缺失 |
| `unit=1` 的这些返回值 | 与人民币元金额一致 | 不证明 `unit` 的完整缩放规则 |

2024 年参考值来自 **2025 年年报的比较列**，不能当作 2024 年当时可获得的原始披露版本。当前核对不提供历史发布时间或无前瞻偏差认证。

## 调用时如何使用

1. `search_wind_fields("净利润", method="wss")` 返回 `net_profit_is` 的 `statement_value_evidence`，包括报表行、单位、年度、PDF 页码和回执。`verified_statement_sample_count` 当前为 4。这与 `official_definition_observed` 的 Wind 界面释义分开；净利润字段仍没有完整官方释义。
2. `query_wind_data` 的相关 WSS 返回值及 `read_wind_receipt` 附带 `statement_reference_checks`。只有代码、报告日、报表类型、单位选项和字段落在参考范围时，才逐格比较。
3. `covered_cell_count` 与 `matched_cell_count` 分别表示有参考值、实际一致的数量。多代码或多字段可能仅部分覆盖；`all_covered_cells_match=true` 不表示其他单元格也已验证。分页前检查完整原始快照，因此页内值的数量可能小于核对数量。
4. 额外或重复选项、默认参数、日期宏、其他公司/报告期/单位选项不沿用认证。`unit=10000` 的既有试验未缩放返回值，仍为未覆盖；不能仅因数值相同就视作相同口径。
5. 值不一致时保留 Wind 原值，并给出差额；修订等原因需要另行核对。参考 PDF 缺失或指纹变化时标记证据不可用。字段检索还核验保存的 Wind 回执、请求及指纹；失效样本不会计入已核对数量。

`search_wind_fields("归母净利润", method="wss")` 已能返回 `np_belongto_parcomsh` 和两个合并样本。别名只有在至少一个年报与原始回执的匹配证据有效时才参与检索；`statement_report_types_with_samples` 区分已有证据覆盖的报表范围，不能把无样本当作 API 不可用。

中文问题的归母字段必须使用合并口径。没有另写报表类型时，计划以 `statement_scope_inference` 说明这一语义推定；明确指定母公司报表则拒绝，不改参数。普通净利润或营业收入没有这一唯一含义，缺报表口径时仍需明确。详见[中文财务问题](fundamental-questions.md)。

每股收益仍未接入自动映射：官方手册有 `eps_basic` 示例，但本次茅台、五粮液2025年报的 WSS 结果均为空；此前 WSD 季度样本也为空。茅台年报第63页明确披露基本每股收益65.66元/股，不能拿这项披露值填充 Wind 返回。原因尚未查明，不推广为所有证券或所有日期都不可用。

值的通道始终为 `data_source_id=wind_terminal_api`。外部核对材料单列 `metadata_source_id=issuer_disclosure`，保留公司、公开链接与文件指纹，不替换原始 `source`，也不改为 Alice MCP。

## 证据

- 项目 `references/financial-statement-evidence.json`：34个参考单元格及八次原始回执的指纹。v0.14的16格参考文件原字节保存在 `references/snapshots/financial-statement-evidence-v0.14.json`，用于追溯旧计划中的参考指纹。
- 项目 `references/issuer-filings/600519-2025-annual.pdf`：[巨潮资讯公开年报](https://static.cninfo.com.cn/finalpage/2026-04-17/1225114741.PDF)。
- 项目 `verification/mcp-financial-statements.json`：四次取数及逐格比较，16/16 一致。
- 项目 `verification/mcp-statement-expansion.json`：新增字段的18格比较，全部一致；重用首次发现性查询，再做三个日期/报表组合。`financial-field-expansion-discovery.json` 区分非官方原始用户代码中的候选拼写与实际 Wind/年报证明，未执行外部示例代码。
- 项目 `verification/mcp-attributable-financials.json`：11项完整流程检查，包含一条中文问题到12格财务值、归母口径推定、不同净利润行、负数费用、字段别名证据及错误口径拒绝；1次WAI、2次WSS。
- 项目 `verification/eps-basic-availability.json`：每股收益WSS空值及年报对照，未认证可用数值样本。
- 项目 `verification/mcp-analysis.json`：通过真实 MCP 协议读取已有回执和字段证据，并验证分页及未覆盖单位选项；没有重新取数。

`all_parameter_semantics_certified`、`unit_scaling_rule_certified` 和 `point_in_time_safe` 均保持 false。有限样本帮助确认常见口径，不是 Wind 全库字段认证。
