"""Bounded statement vocabulary; each mapping still requires live sample evidence.

These are curated line correspondences, not the vendor's full field dictionary.
Keep source report restrictions next to aliases so recognition cannot strip them.
"""

STATEMENT_FIELDS = {
    "oper_rev": {"label": "营业收入", "aliases": ["营业收入", "oper_rev"], "report_types": ["1", "2"]},
    "net_profit_is": {"label": "净利润", "aliases": ["净利润", "net_profit_is"], "report_types": ["1", "2"],
                      "meaning_note": "Consolidated net profit includes minority interests; not attributable profit."},
    "tot_assets": {"label": "资产总计", "aliases": ["资产总计", "总资产", "tot_assets"], "report_types": ["1", "2"]},
    "tot_liab": {"label": "负债合计", "aliases": ["负债合计", "总负债", "tot_liab"], "report_types": ["1", "2"]},
    "np_belongto_parcomsh": {
        "label": "归属于母公司股东的净利润",
        "aliases": ["归属于母公司股东的净利润", "归属母公司股东的净利润", "归属于上市公司股东的净利润", "归母净利润", "np_belongto_parcomsh"],
        "report_types": ["1"],
        "required_report_type": "1",
        "meaning_note": "Attributable net profit is a consolidated ownership split, not parent-only statement net profit."},
    "opprofit": {"label": "营业利润", "aliases": ["营业利润", "opprofit"], "report_types": ["1", "2"]},
    "tot_equity": {"label": "所有者权益（或股东权益）合计",
                   "aliases": ["所有者权益（或股东权益）合计", "所有者权益(或股东权益)合计", "所有者权益合计", "股东权益合计", "tot_equity"],
                   "report_types": ["1", "2"],
                   "meaning_note": "Consolidated total equity includes minority interests; not parent-attributable equity."},
    "monetary_cap": {"label": "货币资金", "aliases": ["货币资金", "monetary_cap"], "report_types": ["1", "2"]},
    "fin_exp_is": {"label": "财务费用", "aliases": ["财务费用", "fin_exp_is"], "report_types": ["1", "2"],
                   "meaning_note": "Preserve the signed statement expense; negative amounts are not missing values."},
}
