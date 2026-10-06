# K 线参数迁移与停牌处理

本页对应 Alice 的 get_stock_kline、get_fund_kline、get_index_kline，调用通道始终为 `wind_terminal_api`。这里只记录已经实现及核验的映射，完整均价、换手率、120/240分钟聚合和定点复权仍未完成。

## 日 K 停牌过滤

通过 `call_alice_tool_on_windpy` 使用 `period="1d"`、`issusp="0"` 时，会在同一 WSD 请求中取行情和 `trade_status`，先按状态过滤，再执行 `count`：

```json
{
  "server_type": "stock_data",
  "tool_name": "get_stock_kline",
  "params": {
    "windcode": "601989.SH",
    "begin_date": "2024-08-26",
    "end_date": "2024-09-06",
    "period": "1d",
    "aftype": "2",
    "issusp": "0",
    "count": -3
  }
}
```

当前只确认了两个日状态：`交易`保留，`停牌一天`剔除。空值、其他文字和部分时段停牌标签会返回 `UNVERIFIED_TRADING_STATUS`，同时保留原始回执；不会先截取 count 再忽略区间内的未知状态。`issusp="1"` 保留原始数据，也不为过滤额外查询状态列。

Wind 的官方接口 FAQ 第16节给出了 WSD/WSS 的 `trade_status` 字段。[中国重工停牌进展公告](https://www.sse.com.cn/disclosure/listedinfo/announcement/c/new/2024-09-10/601989_20240910_JFBC.pdf)明确其股票自2024-09-03起停牌。真实 WSD 样本中，9月3日至6日均为`停牌一天`，同时仍有价格和零成交量。因此不能用“价格非空”证明可交易，也不能把所有零成交量记录自动当作停牌。

股票与ETF样本可以得到已确认状态；沪深300指数两个交易日的状态均为空。指数请求 `issusp="0"` 因此明确失败，不能把“指数没有公司停牌”这种推断代替数据字段。原始样本与来源在 `verification/kline-suspension-probe.json`、`kline-status-domains.json`，实际 MCP 调用在 `mcp-kline-selection.json`。

## 条数、原始回执与后续分析

`count>0` 取符合条件的前 N 条，`count<0` 取最后 N 条，`count=0` 取全部。整个区间都是已确认停牌日时，结果是空序列，不补零、不前填。选中的记录可能包含其他字段的空值；停牌判断只取决于交易状态，后续分析另行检查价格缺失。

发生过滤或 count 截取时：

- `raw` 是选中记录的视图，`raw_is_selected_view=true`。
- `receipt_id` / `raw_input_receipt_id` 指向完整原始 Wind 回执，原始值与行数不变。
- `derived_receipt_id` 指向另外保存的选择结果，包含原始回执指纹、规则、Alice 参数、剔除日期与剩余数量。
- `row_selection` 明确操作顺序，便于核对“先过滤、再截取”。

后续调用 `analyze_wind_series` 分析选中序列时，传 `derived_receipt_id`。分析器会重读并核对原始回执指纹，按已记录规则重建所选行，再验证结果一致；原始或派生回执变化时明确报错。传 `receipt_id` 则分析原始完整序列，两者不能混用。

过滤后样本间隔可能不连续。均线、收益和波动率按选中观测计算，年化频率由调用者显式指定；不能把它们直接当作按日持仓的策略回测结果。

## 仍需核验的周期

周、月、季、半年、年 K 线的停牌处理不能按周期末那一天的状态决定。例如周五停牌，不表示该周其他交易日没有成交。分钟线还涉及日内停复牌与聚合边界。这些周期的 `issusp="0"` 在查询前返回 `UNVERIFIED_MAPPING`。

官方 WSI 文档列出的 BarSize 范围为1至60分钟，暂未把120/240直接传入；本地聚合的交易时段、午休、跨日边界与 Alice 输出规则仍需核验。`afdate` 也不能仅凭手册提及 PriceAdj=T 就直接映射。
