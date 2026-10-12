"""Local stdio MCP proxy. This process has no Wind SDK dependency."""
import argparse
import asyncio

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from wind_relay.common import RelayError, encode, load_contract, read_json, transport_error
from wind_relay.proxy import Proxy


def build_server(proxy):
    contract = load_contract()
    server = Server("Wind terminal relay", instructions=(
        "Authenticated relay to a physical WindPy desktop, independent of Alice MCP. "
        "When result_ready=false, no Wind data has been returned: call relay_job_status with the SAME job_id. "
        "Never resubmit a pending/uncertain job. Mock results are labeled relay_mock and synthetic_fixture=true. "
        "Native results retain data_source_id=wind_terminal_api and original source, dates, units, null/zero/empty states."))
    extra = [
        Tool(name="relay_job_status", description="Retrieve your existing job without making a new Wind request. Poll the same ID when result_ready=false.",
             inputSchema={"type": "object", "properties": {"job_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                           "wait_seconds": {"type": "number", "minimum": 0, "maximum": 5, "default": 2}}, "required": ["job_id"], "additionalProperties": False}),
        Tool(name="relay_cancel_job", description="Cancel your queued job. Already-started Wind work is not terminated or retried.",
             inputSchema={"type": "object", "properties": {"job_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"}},
                          "required": ["job_id"], "additionalProperties": False}),
        Tool(name="relay_status", description="Inspect relay worker mode, connectivity and your own pending count; does not log into Wind.",
             inputSchema={"type": "object", "properties": {}, "additionalProperties": False}),
    ]

    @server.list_tools()
    async def list_tools():
        return [Tool.model_validate(tool) for tool in contract["tools"]] + extra

    @server.call_tool()
    async def call_tool(name, arguments):
        try:
            if name == "relay_job_status":
                result = await proxy.get(arguments["job_id"], arguments.get("wait_seconds"))
            elif name == "relay_cancel_job":
                result = await proxy.cancel(arguments["job_id"])
            elif name == "relay_status":
                result = await proxy.connection.request("GET", "/v1/status")
            else:
                result = await proxy.call(name, arguments)
        except RelayError as exc:
            result = transport_error(exc.code, str(exc))
        return CallToolResult(content=[TextContent(type="text", text=encode(result).decode())],
                              structuredContent=result, isError=result.get("ok") is False)
    return server


async def run(config):
    proxy = Proxy(config)
    server = build_server(proxy)
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await proxy.connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    asyncio.run(run(read_json(args.config)))


if __name__ == "__main__":
    main()
