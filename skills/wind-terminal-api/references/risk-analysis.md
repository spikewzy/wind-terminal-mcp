# Wind 价格序列的风险分析

`analyze_wind_risk` 读取已验证的 Wind WSD/WSES/WSI 回执或可重建的 `series_row_selection`，计算价格收益风险。它不向终端发起新查询，结果另存 `risk_analysis` 派生回执，保留每份输入的代码、字段、参数、获取时间和 SHA256。来源始终为 `wind_terminal_api`。

## 输入与完整流程

证券和基准分别使用一份单代码价格回执。至少需要四个正数、有限、非空价格，形成三个收益区间。缺值、零价和负价都需要先核查；工具不会删除、填充或把它们变成零收益。`periods_per_year` 必须显式提供，252只是本次日频演示约定。

已有回执时调用：

```json
{
  "receipt_id": "股票价格的原始或选中序列回执ID",
  "benchmark_receipt_id": "基准价格回执ID",
  "periods_per_year": 252,
  "annual_risk_free_rate": 0.02,
  "confidence": 0.95,
  "alignment": "strict"
}
```

不提供 `benchmark_receipt_id` 时只计算单条序列指标，`benchmark=null`。复权方式、币种、价格或全收益基准以及观察频率需要与研究问题一致，工具会保存参数但不自动认证这些口径。选中序列需传 `derived_receipt_id`；工具会重新核对原始指纹和选取规则。

Alice `get_risk_metrics` 可提交以下完整 `resolved_request`；`question` 仍保留原始问题。该结构可一次完成股票取数、基准取数和计算：

```json
{
  "method": "wsd",
  "arguments": {
    "codes": "600519.SH", "fields": "close",
    "beginTime": "2025-09-29", "endTime": "2026-09-29",
    "options": "Period=D;PriceAdj=F"
  },
  "evidence": "已验证的WSD价格字段和官方Period/PriceAdj参数；需按实际问题确认价格基准",
  "analysis": {
    "kind": "risk", "periods_per_year": 252,
    "annual_risk_free_rate": 0.02, "confidence": 0.95, "alignment": "strict",
    "benchmark_request": {
      "method": "wsd",
      "arguments": {
        "codes": "000300.SH", "fields": "close",
        "beginTime": "2025-09-29", "endTime": "2026-09-29",
        "options": "Period=D;PriceAdj=U"
      },
      "evidence": "沪深300价格指数；本例没有使用全收益指数"
    }
  }
}
```

可将 `benchmark_request` 替换为 `benchmark_receipt_id` 复用已存基准，两者不能同时提供。参数、基准请求的方法及字段在股票查询前校验。基准查询失败时，错误保留已经完成的股票回执，不改用其他数据源。

## 日期对齐

默认 `strict` 要求股票与基准的每个收益区间具有相同的起止时间。只相同结束日还不够，例如股票收益是周二至周四，基准是周三至周四，两者不能直接计算同期 Beta。

明确选择 `common_intervals` 后，先分别按各自原始观察点计算收益，再匹配起点和终点都相同的区间；返回双方未匹配区间，不先对价格取交集再计算跨日收益。至少需要三个匹配收益区间。股票自身的波动、回撤、VaR等仍使用完整股票样本，基准相关指标只使用匹配区间，两个样本范围分别记录。

日期与零点时间可规范为同一ISO表示；有明确时区偏移的时间统一到UTC。没有时区信息时原样处理，不猜交易所时区，不把无时区时间与带偏移时间自动配对。实际频率和跨市场交易时段仍需使用者确认。

## 指标口径

所有收益均为相邻观测价格的简单收益，比例为小数。这里描述的是给定价格序列，不是交易策略收益，也不声称等同于 Wind 或 Alice 的专有计算值。

| 输出 | 本地计算约定 |
| --- | --- |
| 年化波动率 | 收益的样本标准差，分母 n−1，再乘显式年化频率的平方根 |
| Sharpe | 年有效无风险利率先转每期，平均超额收益除以样本波动率后年化 |
| 最大回撤 | 相对运行峰值的最小负比例，另给峰值日、谷值日、首次恢复日和观察点距离；没有恢复则明确标记 |
| 下跌数量 | 负收益的观察区间数，不自动命名为自然日数；零收益单独统计 |
| Beta、相关性、R² | 匹配样本上的含截距单因子OLS斜率、Pearson相关性及其平方 |
| Jensen Alpha | 每期平均股票超额收益减 Beta × 每期平均基准超额收益；年化值使用每期Alpha × 年化频率，明确为算术年化 |
| 跟踪误差、信息比率 | 股票收益减基准收益的样本标准差及均值/标准差，按显式频率年化 |
| 历史 VaR | 负收益作为损失，对等权经验分布取置信水平分位数，采用逆CDF/最近秩约定 |
| 历史 ES | 平均最差的 1−置信水平 概率质量；边界观测只计所需的部分权重，避免样本较少或重复值时扩大尾部 |

VaR/ES的期限为一个输入观察区间，不按平方根规则外推至多日；若尾部仍是正收益，损失值可为负，不强行归零。零波动使Sharpe无法定义，零基准方差使Beta/Alpha无法定义，零主动收益波动使信息比率无法定义；这些字段为null，并附具体原因。

本地定义参考 [NumPy经验分位数说明](https://numpy.org/doc/stable/reference/generated/numpy.quantile.html)、[PerformanceAnalytics的CAPM Beta](https://search.r-project.org/CRAN/refmans/PerformanceAnalytics/html/CAPM.beta.html) 与 [Jensen Alpha](https://search.r-project.org/CRAN/refmans/PerformanceAnalytics/html/CAPM.jensenAlpha.html)，离散尾部处理参考 [Expected Shortfall and Beyond](https://arxiv.org/abs/cond-mat/0203558)。这些是公式参考，不是 Wind 字段认证；本工具的年化和样本政策以本页明确约定为准。

## 当前验证证据

- `verification/mcp-risk.json`：一次Alice显式风险计划通过真实MCP调用两条Wind日线，茅台和沪深300各242个价格、241个收益区间；随后回执读取、重新计算、95%与99%尾部、无基准及错误输入共10项检查通过。
- `verification/risk-independent-check.json`：从相同原始回执使用NumPy 2.3.5独立核对22个数值及完整收益、回撤序列。Beta/Alpha通过矩阵最小二乘复算，ES通过止损恒等式复算，未调用生产计算函数；最大绝对差小于7×10⁻¹⁴。
- `tests/test_risk.py`：17项回归覆盖手算样本、已知回归系数、离散尾部及重复值、零方差、错位日历、时区、未恢复回撤、缺失数据、派生序列指纹和基准失败时保留股票回执。

`probe_risk.py` 会发起两次新Wind查询；日常回归用 `verify_mcp.py --saved-analysis`。独立复算脚本 `verify_risk_independent.py` 需在具有NumPy的环境运行，仅读取已有回执，不增加服务运行依赖。
