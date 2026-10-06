# Third-party notices and source boundaries

The MIT license at the repository root covers the original MCP implementation and project-authored documentation. It does not grant rights to Wind software, account entitlements, upstream data, or third-party works.

## Wind

WindPy, Wind Terminal / Wind API and their native libraries must be obtained from Wind under the user's own authorization. This repository does not distribute their binaries, credentials, complete help manuals or complete application metadata snapshots. Optional import scripts build the documentation and software-metadata indexes on the user's machine; see [local reference setup](docs/LOCAL_REFERENCES.md).

Project-authored reference records describe historical tests, parameters and evidence boundaries. Their existence does not certify current entitlements, full market coverage or point-in-time history. Research outputs in `docs/examples/` retain the upstream Wind / USDA / Mysteel source attribution and observation dates; opening the code does not relicense the underlying providers' data or make the scenario conclusions factual guarantees.

A public issuer filing used by an optional financial-evidence reference remains an issuer document, with its original attribution in `references/financial-statement-evidence.json`; it is outside the project's MIT grant.

## windget 0.0.7

`references/community-fields.json` contains candidate field mappings extracted from the public windget wheel without executing it. The distribution's MIT notice is reproduced in [windget-LICENSE.md](references/third-party/windget-LICENSE.md), with provenance in `references/community-fields-manifest.json`. These 2022 candidates are not an official current Wind field dictionary.

## Rebar report viewer

The standalone rebar HTML includes a generated Data app viewer and third-party browser components. It is included as a finished research example, not as the MCP server implementation. Its distributed runtime's complete third-party license notices are copied alongside the example in [rebar-third-party-notices.txt](docs/examples/rebar-third-party-notices.txt) and embedded as non-executable text in the HTML itself. Those components retain their respective licenses and copyright holders.

## Python dependencies

Dependencies are installed from `requirements.lock.txt` rather than vendored. They retain their own licenses, including the Python MCP SDK and pywin32 on Windows.

