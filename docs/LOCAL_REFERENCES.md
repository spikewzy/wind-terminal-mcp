# 可选：在本机生成参考资料

核心查询、字段范例、EDB、回执与分析工具安装后即可使用。以下两项用于增强文档/软件目录检索，完整快照不随公开仓库分发。未导入时，相关搜索返回 `LOCAL_REFERENCE_NOT_INSTALLED`，并明确标注 `reference_available=false`；这不表示 Wind 取数不可用。

## 官方公开帮助

在安装目录运行：

```sh
.venv/bin/python import_official_help.py --download
```

这会从代码中列出的 Wind 公开帮助端点下载 9 份文档，不使用账号凭据，不执行文档内的代码。每份文档限制 5 MB，逐份保存校验信息，再在本机建立 `references/official-help/`。端点不可达时保留错误，无自动换源。

也可先自行保存官方响应，再运行：

```sh
.venv/bin/python import_official_help.py /实际目录/已保存的官方帮助响应
```

检索入口为 `search_wind_documentation`。本机文档快照不保证当前远端内容相同；函数、字段和参数仍以安装的 SDK 与有限查询为准。开发环境另外观察过的终端 PDF 摘要不随公开包导入，也不伪装为公开帮助。

## 已安装 Wind API 的软件目录

当前解析器针对已检验的 macOS Wind API 文件版本；在拥有这些官方文件的机器运行：

```sh
.venv/bin/python import_bundle_metadata.py
```

默认读取 `/Applications/Wind API.app/Contents/Resources/etc`。不同安装位置可设置 `WIND_TERMINAL_BUNDLE_DIR`；改变路径不代表其他版本或 Windows/信创格式已兼容。解析器只接受已检验的文件指纹，遇到版本变化会明确拒绝，须重新核查格式。

本机生成 `references/wind-bundle-metadata.json`，供 `search_wind_bundle_metadata` 及候选上下文使用。原始文件留在 Wind 应用中；目录编号、内部表达式和板块 ID 均不会自动转成 WindPy 字段或宏执行。

两个生成目录均已加入 `.gitignore`，发行 ZIP 也明确排除。导入操作不取行情，不证明账号数据权限。

