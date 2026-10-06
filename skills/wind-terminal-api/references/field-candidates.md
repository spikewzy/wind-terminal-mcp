# 中文字段发现

使用 `search_wind_fields`，不需要安装 Windget 或执行其代码。

1. 默认 `scope="observed"`：检索本机已保存的界面释义、查询示例和核对证据。逐项检查原始回执、空值和适用范围；示例不是默认参数。
2. 已观察库里缺少所需字段时，显式指定 `scope="candidates"`，例如 `{"question":"开始交易日","method":"wss","scope":"candidates"}`。结果只给候选字段、中文标签及其出处，不能自动视为有效查询。
3. 根据需求明确代码、日期、口径及必要选项；有正式示例或足够参数依据后，做小范围 WindPy 试取并读取回执。没有依据的选项保持未知。候选本身不触发原生查询、自动选码或替代字段。

候选目录来自公开的 [windget 0.0.7](https://pypi.org/project/windget/0.0.7/)：2022-06-23发布，MIT许可。按源码中的字面字段提取6,865个不同字段、13,375个函数映射。WSS/WSD各6,111项，WSI20项，WST60项，WSQ292项，WSEE399项，WSES382项。这些数量是源码映射数量，不能当作当前Wind可用指标或权限数量；2022年的目录可能过时，也不包含完整EDB代码库和WSET专题参数。

`labels` 为第三方源码的中文标签，不是官方定义；`source`、`unit`、`definition`、`options_schema` 保持未知。特别是“合约乘数”等通用标签可能涉及不同资产，不能把某个资产的有效字段推到另一资产。源码只转发额外参数，没有描述完整的必需选项、枚举或默认值。同名候选与同一字段的不同接口映射保留，不按排序自动选取。

候选项始终返回 `candidate_only=true`、`runtime_query_verified=false`。若后来有单次成功请求，其证据在 observed 范围或 `search_wind_query_recipes` 中另列，不将整个候选词典升级为已验证。新安装同样可检索候选，但不携带开发机回执。

`references/community-fields-manifest.json` 保存版本、包URL、包与7个源码文件的SHA256，以及生成目录指纹；每次检索检查目录指纹，不匹配则报错。`build_community_fields.py` 只接受该固定版本包，用语法树读取单条返回表达式，不导入或执行下载源码。许可原文保存在 `references/third-party/windget-LICENSE.md`。核对指纹只证明本地副本一致，不证明上游内容最新或参数正确。

软件内置目录仍通过 `search_wind_bundle_metadata` 检索；其内部表达式不自动转换为本词典或WindPy字段。官方文档、软件目录、第三方候选、真实查询证据有各自的来源，不互相冒充。
