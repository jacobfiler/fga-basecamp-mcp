"""OAuth flow for Basecamp — browser-based authorization with local callback server."""

import http.server
import sys
import urllib.parse
import webbrowser

import httpx

from . import USER_AGENT
from .config import load_config, save_config, update_doc_search

REDIRECT_URI = "http://localhost:8000/callback"
AUTH_BASE = "https://launchpad.37signals.com"


def run_auth_flow() -> None:
    """Interactive OAuth flow: prompt for credentials, open browser, catch callback."""
    print("=" * 60)
    print("Basecamp MCP — OAuth Setup")
    print("=" * 60)
    print()
    print("You need a Basecamp OAuth app to continue.")
    print("If you already have one, skip to entering your Client ID below.")
    print()
    print("To register a new app:")
    print("  1. Go to https://launchpad.37signals.com/integrations")
    print("     (Log in with your Basecamp account)")
    print('  2. Click "Register another application"')
    print("  3. Fill in the form:")
    print("     - Name: Basecamp MCP (or anything you like)")
    print("     - Company: Your company name")
    print("     - Website: https://github.com/jacobfiler/basecamp-mcp")
    print(f"     - Redirect URI: {REDIRECT_URI}  <-- must be exact")
    print('  4. Click "Register this app"')
    print("  5. Copy the Client ID and Client Secret shown on the next page")
    print()

    client_id = input("Client ID: ").strip()
    if not client_id:
        print("Client ID is required.")
        sys.exit(1)

    client_secret = input("Client Secret: ").strip()
    if not client_secret:
        print("Client Secret is required.")
        sys.exit(1)

    auth_url = (
        f"{AUTH_BASE}/authorization/new"
        f"?type=web_server"
        f"&client_id={client_id}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
    )

    # State to capture from callback
    result: dict = {}

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)

            if "code" not in params:
                error = params.get("error", ["unknown"])[0]
                result["error"] = error
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"Authorization failed: {error}".encode())
                return

            code = params["code"][0]
            print("\nGot authorization code, exchanging for tokens...")

            # Exchange code for tokens
            try:
                response = httpx.post(
                    f"{AUTH_BASE}/authorization/token",
                    params={
                        "type": "web_server",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "redirect_uri": REDIRECT_URI,
                        "code": code,
                    },
                    timeout=30.0,
                )
                response.raise_for_status()
                result["tokens"] = response.json()
            except httpx.HTTPError as e:
                result["error"] = str(e)
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"Token exchange failed. Check terminal.")
                return

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<h1>Authorized!</h1>"
                b"<p>You can close this window. Check your terminal.</p>"
            )

        def log_message(self, format, *args):
            pass

    print("\nOpening browser for authorization...")
    print("(If it doesn't open, visit this URL manually:)")
    print(f"  {auth_url}")
    print()

    webbrowser.open(auth_url)

    server = http.server.HTTPServer(("localhost", 8000), CallbackHandler)
    server.handle_request()

    if "error" in result:
        print(f"\nAuthorization failed: {result['error']}")
        sys.exit(1)

    tokens = result["tokens"]
    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token", "")

    # Discover account(s) and user identity
    print("Fetching account info...")
    try:
        response = httpx.get(
            "https://launchpad.37signals.com/authorization.json",
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": USER_AGENT,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        auth_data = response.json()
    except httpx.HTTPError as e:
        print(f"\nFailed to fetch account info: {e}")
        sys.exit(1)

    identity = auth_data.get("identity", {})
    user_name = (
        f"{identity.get('first_name', '')} {identity.get('last_name', '')}".strip()
    )
    user_email = identity.get("email_address", "")

    # Filter to Basecamp 3 accounts only
    accounts = [a for a in auth_data.get("accounts", []) if a.get("product") == "bc3"]

    if not accounts:
        print("\nNo Basecamp 3 accounts found for this user.")
        sys.exit(1)

    if len(accounts) == 1:
        account = accounts[0]
    else:
        print(f"\nFound {len(accounts)} Basecamp accounts:")
        for i, a in enumerate(accounts, 1):
            print(f"  {i}. {a['name']} (ID: {a['id']})")
        while True:
            choice = input(f"\nSelect account [1-{len(accounts)}]: ").strip()
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(accounts):
                    account = accounts[idx]
                    break
            except ValueError:
                pass
            print("Invalid selection.")

    config = {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": str(account["id"]),
        "account_name": account["name"],
        "user_name": user_name,
        "user_email": user_email,
    }

    save_config(config)

    # Auto-configure Claude Desktop
    _configure_claude_desktop()

    print()
    print("=" * 60)
    print("Setup complete!")
    print("=" * 60)
    print(f"  Account: {account['name']} (ID: {account['id']})")
    print(f"  User:    {user_name} ({user_email})")
    print()
    print("Basecamp tools are now available in Claude Desktop.")
    print("Restart Claude Desktop if it's currently running.")
    print()

    # Offer document search setup
    _offer_doc_search_setup()


def run_connect_docs() -> None:
    """Standalone command to connect a document search API."""
    config = load_config()
    if not config:
        print("Run `basecamp-mcp auth` first to set up Basecamp access.")
        sys.exit(1)

    print("=" * 60)
    print("Basecamp MCP — Document Search Setup")
    print("=" * 60)
    print()
    _prompt_doc_search()


def _offer_doc_search_setup() -> None:
    """Ask at end of auth flow whether to connect document search."""
    print("-" * 60)
    print("Optional: Connect a document search API")
    print("-" * 60)
    print()
    print("If your organization has a document ingestion service")
    print("(e.g. one that indexes .docx files from Basecamp),")
    print("you can connect it now to enable full-text document search.")
    print()

    answer = input("Connect a document search API? (y/N): ").strip().lower()
    if answer not in ("y", "yes"):
        print("Skipped. You can add this later with `basecamp-mcp connect-docs`.")
        return

    print()
    _prompt_doc_search()


def _prompt_doc_search() -> None:
    """Prompt for document search URL + token, validate, and save."""
    print("Enter the URL of your document search API.")
    print("This should be the base URL (e.g. https://your-app.ondigitalocean.app)")
    print()

    url = input("Document search API URL: ").strip()
    if not url:
        print("URL is required.")
        sys.exit(1)

    url = url.rstrip("/")

    print()
    print("If the API requires authentication, enter the Bearer token.")
    print("Leave blank if no authentication is needed.")
    print()

    token = input("API token (optional): ").strip() or None

    # Validate the connection
    print("\nTesting connection...")
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = httpx.get(
            f"{url}/api/documents/stats",
            headers=headers,
            timeout=15.0,
        )
        response.raise_for_status()
        stats = response.json()
    except httpx.ConnectError:
        print(f"\nCould not connect to {url}")
        print("Check the URL and make sure the service is running.")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"\nConnection failed: HTTP {e.response.status_code}")
        if e.response.status_code == 401:
            print("Authentication failed — check your API token.")
        sys.exit(1)
    except httpx.HTTPError as e:
        print(f"\nConnection failed: {e}")
        sys.exit(1)

    doc_count = stats.get("total_documents", stats.get("count", "?"))
    update_doc_search(url, token)

    print()
    print(f"Connected! {doc_count} documents indexed.")
    print("Document search tools are now available in Claude.")
    print()


def _configure_claude_desktop() -> None:
    """Auto-add basecamp MCP server to Claude Desktop config."""
    import json
    import platform
    import shutil
    from pathlib import Path

    system = platform.system()
    if system == "Darwin":
        config_path = (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    elif system == "Windows":
        import os

        appdata = Path(os.environ.get("APPDATA", ""))
        config_path = appdata / "Claude" / "claude_desktop_config.json"
    else:
        config_path = Path.home() / ".config" / "Claude" / "claude_desktop_config.json"

    # Find the executable — prefer uvx, fall back to direct command
    uvx_path = shutil.which("uvx")
    direct_path = shutil.which("basecamp-mcp")

    if uvx_path:
        basecamp_server = {
            "command": uvx_path,
            "args": ["basecamp-mcp"],
        }
    elif direct_path:
        basecamp_server = {
            "command": direct_path,
            "args": [],
        }
    else:
        print(
            "\nCould not find basecamp-mcp in PATH — skipping Claude Desktop auto-config."
        )
        return

    # Read existing config or start fresh
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {}

    if "mcpServers" not in existing:
        existing["mcpServers"] = {}

    existing["mcpServers"]["basecamp"] = basecamp_server
    config_path.write_text(json.dumps(existing, indent=2) + "\n")
    print(f"\nClaude Desktop config updated: {config_path}")
