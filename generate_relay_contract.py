"""Export tool schemas offline. No MCP transport or real WindPy is started."""
import asyncio
import hashlib
import importlib.abc
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent


class ForbidWindPy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "WindPy" or fullname.startswith("WindPy."):
            raise RuntimeError("Offline schema export must not import real WindPy")


async def export():
    import server
    from wind_bridge import VERSION
    tools = await server.mcp.list_tools()
    payload = {"protocol_version": 1, "mcp_version": VERSION,
               "server_sha256": hashlib.sha256((ROOT / "server.py").read_bytes()).hexdigest(),
               "tools": [tool.model_dump(mode="json", exclude_none=True) for tool in tools]}
    destination = ROOT / "references" / "relay-tool-contract.json"
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tool_count": len(tools), "path": str(destination), "windpy_imported": "WindPy" in sys.modules}))


if __name__ == "__main__":
    sys.meta_path.insert(0, ForbidWindPy())
    with tempfile.TemporaryDirectory(prefix="wind-relay-schema-") as runtime:
        os.environ["WIND_TERMINAL_MCP_DATA_DIR"] = runtime
        asyncio.run(export())
