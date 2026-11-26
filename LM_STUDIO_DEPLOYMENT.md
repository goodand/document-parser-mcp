# Update LM Studio to this checkout

This script changes only `mcpServers.govparse.env.PYTHONPATH`. It places this
worktree's `src` directory first and preserves any existing PYTHONPATH entries
after it. Before replacing the config, it writes a byte-for-byte backup beside
the original and checks that the config has not changed concurrently.

In PowerShell, run:

```powershell
python "C:\Users\INTEC\Documents\Codex\2026-11-23\new-chat\work\publish-selected-diffs\scripts\deploy_lmstudio.py"
```

Then restart LM Studio so it reloads the MCP server. The default source path is
the `src` directory beside this document's checkout. To validate against a
copied config first, pass `--config` with that copy and `--source` with the
checkout's `src` directory.

This deploys the code in the local worktree; it does not update GitHub. Keep the
worktree at its current path while LM Studio uses it.
