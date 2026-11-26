"""Point LM Studio's govparse MCP import path at this verified checkout.

Run from any directory with:
    python path/to/scripts/deploy_lmstudio.py

Use --config and --source to validate the update against a redirected config
without changing the real LM Studio configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            name = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def _backup_path(config: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return config.with_name(f"{config.name}.backup-{stamp}-{uuid.uuid4().hex[:8]}")


def _prepend_path(existing: str, source: Path) -> str:
    target = str(source.resolve())
    entries = [item for item in existing.split(os.pathsep) if item]
    key = os.path.normcase(os.path.normpath(target)).casefold()
    kept = [
        item
        for item in entries
        if os.path.normcase(os.path.normpath(item.strip('"'))).casefold() != key
    ]
    return os.pathsep.join([target, *kept])


def update_config(
    config: Path, source: Path, server_name: str = "govparse"
) -> tuple[bool, Path | None]:
    config = config.expanduser().resolve()
    source = source.expanduser().resolve()
    if not (source / "govparse" / "__init__.py").is_file():
        raise ValueError(f"not a govparse source checkout: {source}")
    if not config.is_file() or config.is_symlink():
        raise ValueError(f"configuration file is missing or is a symlink: {config}")

    original = config.read_bytes()
    try:
        settings = json.loads(original.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid LM Studio MCP JSON: {exc}") from exc
    servers = settings.get("mcpServers")
    server = servers.get(server_name) if isinstance(servers, dict) else None
    if not isinstance(server, dict):
        raise ValueError(f"MCP server {server_name!r} is not configured")
    command = server.get("command")
    if not isinstance(command, str) or not Path(command).is_file():
        raise ValueError(f"configured MCP executable is missing: {command!r}")
    env = server.setdefault("env", {})
    if not isinstance(env, dict):
        raise ValueError(f"{server_name}.env must be a JSON object")

    old_pythonpath = env.get("PYTHONPATH", "")
    if not isinstance(old_pythonpath, str):
        raise ValueError(f"{server_name}.env.PYTHONPATH must be a string")
    new_pythonpath = _prepend_path(old_pythonpath, source)
    if new_pythonpath == old_pythonpath:
        return False, None
    env["PYTHONPATH"] = new_pythonpath
    updated = (json.dumps(settings, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    # Protect against an update racing with a GUI/editor change. The backup is
    # byte-for-byte and atomically published beside the original config.
    if hashlib.sha256(config.read_bytes()).digest() != hashlib.sha256(original).digest():
        raise RuntimeError("LM Studio config changed during update; retry after reviewing it")
    backup = _backup_path(config)
    _atomic_write(backup, original)
    if config.read_bytes() != original:
        raise RuntimeError(f"LM Studio config changed before replacement; backup kept at {backup}")
    _atomic_write(config, updated)
    return True, backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path.home() / ".lmstudio" / "mcp.json",
        help="LM Studio MCP config (defaults to ~/.lmstudio/mcp.json)",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src",
        help="govparse source checkout (defaults to this verified worktree)",
    )
    parser.add_argument("--server-name", default="govparse")
    args = parser.parse_args()
    changed, backup = update_config(args.config, args.source, args.server_name)
    print(f"Config: {args.config.expanduser().resolve()}")
    print(f"PYTHONPATH source: {args.source.expanduser().resolve()}")
    if changed:
        print(f"Atomic backup: {backup}")
        print("LM Studio config updated. Restart LM Studio to reload the MCP server.")
    else:
        print("LM Studio already points to this checkout; no file changes made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
