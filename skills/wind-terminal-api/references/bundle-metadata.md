# Wind API 本地目录的读取与核验

开源发行说明：以下数量与证据描述开发环境的历史验证。完整快照不随开源仓库/ZIP 分发，请按 [本机导入说明](../../../docs/LOCAL_REFERENCES.md) 在自己的机器生成；未导入时搜索返回明确缺口。

2026-09-29 至 30 日从本机 Wind API.app 的六份非凭据资源中直接提取目录，目录解析未使用网络、解密或内存注入。原文件未修改。来源标为 `metadata_source_id=wind_api_bundle`；它是 Wind 终端 API 的元信息渠道，不是 Alice 数据，也不是一次数值查询。后续样本取数通过官方 WindPy 单独验证。

| 文件 | 已解析范围 | 限制 |
| --- | --- | --- |
| `Contents/Resources/etc/EquityQuery/lookuptables.xml` | 257 组参数查找表、2,683 个普通选项、28 个自定义选项 | 选项表不包含每个 WindPy 字段的参数绑定关系；宏表达式只作文本保存 |
| `Contents/Resources/etc/EquityQuery/excel_chs.dat` | 16,863 条指标名称及内部表达式、915 个目录分组 | 仅解析已核对文件指纹对应的明文记录结构；内部表达式不是已验证的 WindPy 字段名 |
| `Contents/Resources/etc/lang/M_ZH-CN_WDF.iml` 与 `M_EN-US_WDF.iml` | 每种语言 20,047 项标签；目录全部 17,778 个节点与中文标签逐项核对，并附上同 ID 的英文标签 | WRES 索引中的明文 UTF-8 标签；不是完整指标释义，不增加可取数指标数量 |
| `Contents/Resources/etc/base/SysGrp.xml` | 23,495 个唯一板块 ID、名称、英文名及目录层级 | 文件注释说明属性，原始属性完整保留；目录节点不等于成分名单或历史权限 |
| `Contents/Resources/etc/base/IndexSectors.dat` | 5,572 组指数代码与板块 ID 对应关系 | 5,148 组按 ID 对上板块树；其余 424 组保存在索引的 `index_sector_pairs` 中并标为未匹配，不补造名称或同名绑定 |

索引为项目 `references/wind-bundle-metadata.json`，当前格式版本 3。记录了原文件路径、SHA256、导入时间和每条二进制目录记录的偏移。`record_offset` 从本条名称的长度前缀起算；尾部 `language_id` 已与中文标签全量核对，另一尾部数值 `raw_tail_value` 仍不赋予 API 含义。旧版 `raw_prefix_words` 实际涉及前一条记录的尾部，已移除，避免错配。目录同名节点保留，不自动合并；缺少的根目录关系明确保留未知。

## 如何查询

- 已知证券字段和少量正式释义：`search_wind_fields(question="营业收入", method="wss")`。
- 扩大候选发现：`search_wind_bundle_metadata(question="营业收入", kind="indicator")`。可同时给类别关键词，例如“营业收入 银行”。结果中的 `windpy_field=null` 和 `windpy_mapping_verified=false` 表示尚不能直接据此取数。
- 支持英文标签，例如 `question="Stock Abbreviation"` 可找到“股票简称”。`labels` 保留语言文件的两个原始文本变体，不假定它们分别代表简称或全称。
- 查本地选项：`search_wind_bundle_metadata(question="报表类型", kind="parameter")`。查询 `90905` 可精确匹配这组本地表，返回 1/2/3/4 对应合并、母公司、调整后合并、调整后母公司。表编号不是接口参数名。
- 查板块：`search_wind_bundle_metadata(question="全部A股", kind="sector")`；也可按“Passive Index Funds”、板块 ID 或已关联的指数代码查询。类别路径用于区分同名分类，不把指数代码当成 sectorid。424 组缺少树节点的映射只保留在索引中，未加入有名称的板块搜索结果。
- `limit` 与 `offset` 分页；检查 `current_source_matches_snapshot` 和 `source_files`。指标结果同时核对 DAT 与两份语言文件，参数结果核对 XML，板块结果核对 SysGrp.xml 和 IndexSectors.dat。false 表示至少一份文件已变化；null 表示存在无法核对的文件且未发现明确变化。两者都不能称为已核对的当前版本。

需要重建时在项目环境执行 `import_bundle_metadata.py`。XML 按其结构解析；DAT 和语言文件只接受本次已验证 SHA256。版本改变时应重新核实格式，不能沿用偏移猜测。WSS/WSD/WSET 的其他资源仍为未识别二进制，当前没有解码、也没有仅因二进制就断言其加密方式。

## 板块名单核验

2026-09-30 通过真实 MCP / WindPy 查询日期 2026-09-29：

| 目录名称 | sectorid | 返回代码数量 | 原始回执 |
| --- | --- | --- | --- |
| 全部A股 | `a001010100000000` | 5,571；SZ 2,903、SH 2,320、BJ 348 | `18c4f556cda84e53af2c9787abbee453` |
| 被动指数型基金 | `2001010102000000` | 4,502；全部为 OF 后缀 | `319f562545fa4ceea38269fdd2aa8bf9` |

两份名单均非空、没有重复代码、代码格式有效，完整证据在 `verification/mcp-sector-universes.json`。基金目录路径为“基金 / 中国公募基金 / Wind开放式基金分类 / 股票型基金”，不代表全部基金，代码数量也不等于去重后的产品数量。日期是请求参数；仅凭这一日结果不认证所有历史成分或披露时点。

目录中“沪深300”存在不同板块 ID；IndexSectors.dat 的 000300.SH 对应 a001030201000000，而“市场类 / 常用指数成份”下的同名节点为 1000000090000000。两者不按名称自动合并，也不未经对照就声称成分完全一致。

## WAI 候选目录说明

`recognize_wind_entities` 的 `financial_candidate_context` 将四类财务实体（stockBondIndex、stockIndex、bondIndex、fundIndex）与本地目录按节点编号和名称同时匹配，保留类别路径、英文标签、未解析根节点及内部表达式。只规范字符宽度、首尾空格和大小写，不按相似名称猜测。原始 `raw_entity` 及全部备选项保持不变。

- `node_id_and_name_match`：目录编号和名称均匹配，可用于辨认类别，仍不等于可执行字段映射。
- `node_id_not_found` / `name_mismatch`：保留未匹配状态；主实体编号不自动当作备选目录编号，也不改用同名项。
- `metadata_unavailable`：索引缺失或来源文件无法验证时，原始识别结果仍可返回，但不附上已核实的目录对应关系。

2026-09-30 通过真实 MCP 查询“贵州茅台的营业收入和净利润”：45 条候选匹配目录，18 条未匹配，计数保留不同实体类型的重复候选，并非 45 个唯一字段。可区分一般企业、银行、证券和保险公司的利润表，也能区分净利润在利润表与现金流量表补充资料中的节点。证据为 `verification/mcp-financial-candidates.json`，原始 WindPy 回执 `621efcfaf1194b1abb75a7b6d52e1567`。

## 已做的参数差异试验

`verification/financial-parameter-probes.json` 保存了贵州茅台、营业收入、2025-12-31 的三次 WSS 查询：

- `rptType=1;unit=1` 与 `rptType=2;unit=1` 返回不同值，证明该样本中报表类型选项会影响结果；本地表提供了 1 和 2 的标签。字段到选项表的正式绑定仍需 CG 或对应文档确认。
- `rptType=1;unit=10000` 与 `unit=1` 返回相同金额，没有按 10,000 缩放。本地“常规数量”的数值不能直接当作 WindPy unit 的枚举。
- 未用这些试验认证其他币种、全部报表类型、单位、字段、市场或历史可得时间。原始数值、参数和回执全部保留；不额外乘除或补造数据单位。

这条路径能减少人工查询，但目录发现、接口字段映射、参数含义、真实数据权限仍是四个独立验证步骤。
