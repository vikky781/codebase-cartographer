# Local MCP configuration

CodebaseCartographer exposes one local stdio server. Every supported host launches the same executable:

```json
{
  "mcpServers": {
    "codebase-cartographer": {
      "command": "cartographer-mcp",
      "args": []
    }
  }
}
```

Install the runtime first. `pipx` is recommended because it makes the launcher available on your user `PATH` without activating a project environment.

```bash
pipx install "git+https://github.com/vikky781/codebase-cartographer.git"
```

If a desktop host does not inherit that `PATH`, set `command` to the absolute executable path instead:

```json
{
  "mcpServers": {
    "codebase-cartographer": {
      "command": "C:\\Users\\you\\pipx\\venvs\\codebase-cartographer\\Scripts\\cartographer-mcp.exe",
      "args": []
    }
  }
}
```

On macOS and Linux, the corresponding path normally ends in `/bin/cartographer-mcp`. Do not point a host at an arbitrary repository script: use the installed console entry point so dependencies are resolved by the chosen Python environment.

## Client-specific locations

| Host | Wrapper/configuration |
| --- | --- |
| Codex | `plugins/codebase-cartographer/.mcp.json`, installed through `.agents/plugins/marketplace.json`. |
| Claude Code | `integrations/claude-code/.mcp.json`, installed as a Claude plugin or copied to a project `.mcp.json`. |
| Antigravity | `integrations/antigravity/mcp_config.json`, installed as a plugin or copied to `.agents/mcp_config.json`. |

All configurations use local stdio. There is no HTTP endpoint, access token, or server-side code upload in the CodebaseCartographer process.

## Codex marketplace

The repository marketplace is already tracked at `.agents/plugins/marketplace.json`; do not copy or
create another catalog. From a checkout, add it as a marketplace source:

```bash
codex plugin marketplace add ./
```

Or add the public repository directly:

```bash
codex plugin marketplace add vikky781/codebase-cartographer --ref main
```

Restart the ChatGPT/Codex desktop app, open **Plugins**, select **Codebase Cartographer Local**,
and install `codebase-cartographer`. The MCP server requires the `cartographer-mcp` executable to
be installed first; the marketplace bundle intentionally does not install Python dependencies for
the host.

## Troubleshooting

1. Run `cartographer-mcp` in a terminal. It should stay open waiting for stdio; stop it with `Ctrl+C`.
2. If the host reports command-not-found, use the absolute launcher path shown above.
3. Confirm that the host starts a fresh process after changing plugin configuration; desktop hosts commonly cache plugin bundles.
4. Run `python -m pytest tests/ -v --tb=short` from a checkout before reporting an issue.
