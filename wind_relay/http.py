from __future__ import annotations

import httpx

from .common import RelayError, validate_base_url


class Connection:
    def __init__(self, config, *, transport=None):
        self.base = validate_base_url(config["url"], config.get("allow_private_http", False))
        token = config["token"]
        if not isinstance(token, str) or len(token) < 32:
            raise RelayError("INVALID_TOKEN", "Use the generated client or worker token")
        self.client = httpx.AsyncClient(transport=transport, timeout=8, follow_redirects=False,
                                        trust_env=False, headers={"Authorization": "Bearer " + token})

    async def request(self, method, path, payload=None):
        try:
            response = await self.client.request(method, self.base + path, json=payload)
        except httpx.HTTPError as exc:
            raise RelayError("RELAY_UNREACHABLE", "Relay connection failed: " + type(exc).__name__, 503) from None
        try:
            value = response.json()
        except ValueError:
            raise RelayError("RELAY_PROTOCOL_ERROR", "Relay did not return JSON", 502) from None
        if not isinstance(value, dict):
            raise RelayError("RELAY_PROTOCOL_ERROR", "Relay response must be a JSON object", 502)
        if response.status_code >= 300:
            raise RelayError(value.get("code", "RELAY_HTTP_ERROR"), value.get("message", "Relay request rejected"), response.status_code)
        return value

    async def close(self):
        await self.client.aclose()
