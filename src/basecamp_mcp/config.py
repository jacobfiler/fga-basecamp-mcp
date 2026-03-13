"""Config file management for Basecamp MCP credentials."""

import json
import logging
import os
import platform
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _config_dir() -> Path:
    """Platform-appropriate config directory."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("APPDATA", Path.home())) / "basecamp-mcp"
    if system == "Darwin":
        return Path.home() / ".config" / "basecamp-mcp"
    # Linux / other
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "basecamp-mcp"
    )


CONFIG_DIR = _config_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict | None:
    """Load config from ~/.config/basecamp-mcp/config.json."""
    if not CONFIG_FILE.exists():
        return None
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to read config: {e}")
        return None


def save_config(config: dict) -> None:
    """Save config to ~/.config/basecamp-mcp/config.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":  # Unix-like systems
        CONFIG_DIR.chmod(0o700)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
    # Restrict permissions to owner only (no-op on Windows)
    if platform.system() != "Windows":
        CONFIG_FILE.chmod(0o600)


def _update_config(updates: dict) -> None:
    """Load config, merge updates, and save. No-op if config doesn't exist."""
    config = load_config()
    if not config:
        logger.error("No config file to update")
        return
    config.update(updates)
    save_config(config)


def update_tokens(access_token: str, refresh_token: str | None = None) -> None:
    """Update tokens in existing config (called after refresh)."""
    updates: dict = {
        "access_token": access_token,
        "token_updated_at": datetime.now().isoformat(),
    }
    if refresh_token is not None:
        updates["refresh_token"] = refresh_token
    _update_config(updates)


def update_doc_search(url: str, token: str | None = None) -> None:
    """Add or update document search API settings in existing config."""
    updates: dict = {"doc_search_url": url.rstrip("/")}
    if token is not None:
        updates["doc_search_token"] = token
    _update_config(updates)
