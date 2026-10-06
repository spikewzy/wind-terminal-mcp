# 固定样本池的分批筛选

用于把 Alice `search_stocks` / `search_funds` 的已明确问题转换成 Wind 终端查询。先确认样本池、日期、字段、比较口径和排序；自然语言的解释仍由调用方完成，工具不猜指标或全部市场范围。

## 先取得样本池

用有官方依据的 WSET/WEQS 请求获取名单，保存 `receipt_id`。查询必须包含真实证券代码列；列名默认 `wind_code`，其他列名由 `code_field` 明确指定。工具接受通过本服务验证的原始回执，支持 1 至 10,000 个互不重复的代码。重复、缺列或未通过验证时停止，不自动去重或截断。

已验证的指数成分示例：

```json
{"method":"wset","arguments":{"tablename":"sectorconstituent","options":"date=2026-09-28;windcode=000300.SH;field=wind_code,sec_name"}}
```

这段示例确定的是指定日期的沪深 300 成分，不证明它包含全部 A 股。板块名称、全市场范围和历史成分的含义须单独确认；不要把当前名单用于过去的选股而忽略幸存者偏差。

需要更广的样本池时，先用 `search_wind_bundle_metadata(question, kind="sector")` 查本地板块目录，并核对类别路径。2026-09-30 经实际 MCP 验证的另外两份请求为：

```json
{"method":"wset","arguments":{"tablename":"sectorconstituent","options":"date=2026-09-29;sectorid=a001010100000000;field=wind_code"}}
```

这是目录中的“全部A股”，返回 5,571 个不同代码（深 2,903、沪 2,320、北 348），回执 `18c4f556cda84e53af2c9787abbee453`。

```json
{"method":"wset","arguments":{"tablename":"sectorconstituent","options":"date=2026-09-29;sectorid=2001010102000000;field=wind_code"}}
```

这是“Wind开放式基金分类 / 股票型基金 / 被动指数型基金”，返回 4,502 个不同 OF 代码，回执 `319f562545fa4ceea38269fdd2aa8bf9`。未按基金产品去重，不是全部基金。两份名单均在当前 10,000 代码处理上限内，但本轮只核验成分名单，没有对其全量查询 WSS 字段。历史可得性另行确认。

WSET 的日期要单独核对。`table_date_observations` 会保留整表 date/tradedate 列与请求日期的字面差异，不能以 raw.Times 替代行日期。若使用带日期列的指数权重名单作为样本池，先解决不匹配或未知日期含义；本轮沪深300权重请求已出现“请求9月29日，行日期9月1日”的情况。详见 [日期对照证据](documentation-search.md)。

## 分批取数与筛选

首次调用 `screen_wind_universe`，提供 `plan`：

```json
{
  "universe_receipt_id": "上一步返回的原始回执ID",
  "fields": "sec_name,close",
  "options": "tradeDate=20260928",
  "conditions": [{"field":"close","op":"gt","value":100}],
  "sort_by": "close",
  "descending": true,
  "limit": 10,
  "evidence": "记录实际字段、日期和选项的官方或已验证依据"
}
```

每批 50 个代码，每次调用默认处理 2 批，可用 `batches_per_call` 调成 1 至 3。字段与 options 全程固定，不自动调整报告期、币种或单位；取数采用新请求，不隐式命中缓存。

- `status=pending`：尚未取完，结果不包含排名。下一次只传 `continuation_receipt_id`，可继续指定 `batches_per_call`。
- `status=batch_failed`：查看 `error` 和原始失败回执；已完成批次有保存。停止自动重试，不把缺失证券跳过或换成 Alice 数据。外部问题解决后可显式继续。
- `status=complete`：全部名单成员都取完，跨批次统一筛选、排序后才应用 limit。`matched_count` 是截断前符合条件的数量。缺值通过 `excluded_unknown_codes` 明示。

继续调用会核对原始名单指纹、已完成批次的来源、参数、代码顺序和验证状态。不同日期或不同字段的快照不可混入。`input_receipt_ids` 保留名单和所有原始取数批次；最终结果作为本地派生回执保存。

`complete` 仅代表这份名单已全部处理。`full_market_coverage_certified=false`、`point_in_time_safe=false` 保留：样本池完整性、财务披露时点及历史原始版本不是分批查询能够证明的。

## Alice 契约入口

`call_alice_tool_on_windpy` 的 `search_stocks` / `search_funds` 可用相同流程。`resolved_request` 设置 `method="wss"`、`arguments={fields,options}`、`universe_receipt_id`、`evidence`，并提供 `analysis={kind:"screen",conditions,sort_by?,descending?,limit?}`。证券代码来自固定名单，arguments 不再包含 codes。返回 pending 后，使用 `screen_wind_universe` 和 continuation 回执继续。

2026-09-30 已通过真实 MCP 测试：使用 2026-09-28 沪深 300 名单，分 6 批查询同日名称与收盘价；两次继续调用后覆盖全部 300 个代码。测试条件为收盘价大于 100，返回前 10，筛选前共匹配 38 个。独立读取六份原始快照核对名单、匹配数量、全局排序和缺失值，7 项条件全部通过。证据：`verification/mcp-universe-screen.json`。这不是对任意市场、字段或历史时点的全范围认证。
