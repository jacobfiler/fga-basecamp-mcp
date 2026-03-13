"""MCP server with Basecamp tools."""

import logging
import re
import sys

from mcp.server.fastmcp import FastMCP

from .client import BasecampClient, DocSearchClient
from .config import load_config

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "basecamp",
    instructions="Read and query Basecamp projects, messages, todos, and documents",
)

# Initialized lazily on first tool call
_client: BasecampClient | None = None
_UNSET = object()
_doc_client: DocSearchClient | None | object = _UNSET


def _ensure_initialized() -> None:
    """Load config once and initialize both clients."""
    global _client, _doc_client
    if _client is not None:
        return
    config = load_config()
    if not config:
        raise RuntimeError("Not configured. Run `basecamp-mcp auth` first.")
    _client = BasecampClient(config)
    if config.get("doc_search_url"):
        _doc_client = DocSearchClient(
            config["doc_search_url"], config.get("doc_search_token")
        )
        logger.info(f"Document search enabled: {config['doc_search_url']}")
    else:
        _doc_client = None


def _get_client() -> BasecampClient:
    _ensure_initialized()
    assert _client is not None
    return _client


def _get_doc_client() -> DocSearchClient | None:
    """Return the doc search client, or None if not configured."""
    _ensure_initialized()
    return _doc_client if isinstance(_doc_client, DocSearchClient) else None


def _doc_search_request(path: str, params: dict | None = None) -> dict:
    """Make a GET request to the document search API."""
    client = _get_doc_client()
    if not client:
        return {
            "error": "Document search not configured. Run `basecamp-mcp connect-docs`."
        }
    return client.get(path, params)


def _summarize_project(p: dict) -> dict:
    """Extract useful fields from a project dict."""
    return {
        "id": p["id"],
        "name": p["name"],
        "description": p.get("description", ""),
        "purpose": p.get("purpose", ""),
        "created_at": p.get("created_at", ""),
        "updated_at": p.get("updated_at", ""),
        "bookmark_url": p.get("app_url", ""),
    }


def _summarize_message(m: dict) -> dict:
    return {
        "id": m["id"],
        "subject": m.get("subject", ""),
        "created_at": m.get("created_at", ""),
        "updated_at": m.get("updated_at", ""),
        "creator": m.get("creator", {}).get("name", ""),
        "comments_count": m.get("comments_count", 0),
        "app_url": m.get("app_url", ""),
    }


def _summarize_todolist(t: dict) -> dict:
    desc = _strip_html(t.get("description", ""))
    return {
        "id": t["id"],
        "name": t.get("name", t.get("title", "")),
        "description": desc[:200] if desc else "",
        "completed": t.get("completed", False),
        "completed_ratio": t.get("completed_ratio", ""),
        "comments_count": t.get("comments_count", 0),
        "app_url": t.get("app_url", ""),
    }


def _summarize_todo(t: dict) -> dict:
    return {
        "id": t["id"],
        "title": t.get("content", t.get("title", "")),
        "completed": t.get("completed", False),
        "due_on": t.get("due_on"),
        "assignees": [a.get("name", "") for a in t.get("assignees", [])],
        "creator": t.get("creator", {}).get("name", ""),
        "comments_count": t.get("comments_count", 0),
        "app_url": t.get("app_url", ""),
    }


def _summarize_person(p: dict) -> dict:
    return {
        "id": p["id"],
        "name": p.get("name", ""),
        "email_address": p.get("email_address", ""),
        "title": p.get("title", ""),
        "admin": p.get("admin", False),
    }


def _summarize_document(d: dict) -> dict:
    return {
        "id": d["id"],
        "title": d.get("title", ""),
        "created_at": d.get("created_at", ""),
        "updated_at": d.get("updated_at", ""),
        "creator": d.get("creator", {}).get("name", ""),
        "app_url": d.get("app_url", ""),
    }


def _strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace."""

    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _summarize_comment(c: dict) -> dict:
    return {
        "id": c["id"],
        "content": _strip_html(c.get("content", "")),
        "creator": c.get("creator", {}).get("name", ""),
        "created_at": c.get("created_at", ""),
    }


def _resolve_dock_id(
    client: BasecampClient, project_id: int, tool_name: str, provided_id: int | None
) -> tuple[int | None, str | None]:
    """Resolve a dock tool ID, auto-discovering if not provided.

    Returns (resolved_id, error_message). If error_message is set, resolved_id is None.
    """
    if provided_id:
        return provided_id, None
    tool = client.get_dock_tool(project_id, tool_name)
    if not tool:
        return None, f"No {tool_name} found for this project."
    return tool["id"], None


# ── Tools ────────────────────────────────────────────────────────


@mcp.tool()
def list_projects() -> list[dict]:
    """List all Basecamp projects the user can access.

    Returns project names, IDs, and descriptions.
    """
    client = _get_client()
    projects = client.list_projects()
    return [_summarize_project(p) for p in projects]


@mcp.tool()
def get_project(project_id: int | None = None, name: str | None = None) -> dict | str:
    """Get a Basecamp project by ID or name (fuzzy match).

    Provide either project_id or name. Returns project details including
    available tools (message board, todos, docs).

    Args:
        project_id: Numeric project ID
        name: Project name to search for (case-insensitive, partial match)
    """
    client = _get_client()

    if project_id:
        project = client.get_project(project_id)
    elif name:
        project = client.find_project_by_name(name)
    else:
        return "Provide either project_id or name."

    if not project:
        return "Project not found."

    # Extract dock tools for easy reference
    tools = {}
    for tool in project.get("dock", []):
        if tool.get("enabled"):
            tools[tool["name"]] = tool["id"]

    result = _summarize_project(project)
    result["dock_tools"] = tools
    return result


@mcp.tool()
def list_messages(
    project_id: int,
    message_board_id: int | None = None,
    limit: int = 50,
) -> list[dict] | str:
    """List messages on a project's message board.

    If message_board_id is not provided, it will be auto-discovered from the project.

    Args:
        project_id: The project ID
        message_board_id: The message board ID (auto-discovered if omitted)
        limit: Maximum number of messages to return (default 50)
    """
    client = _get_client()
    message_board_id, err = _resolve_dock_id(
        client, project_id, "message_board", message_board_id
    )
    if err:
        return err

    messages = client.list_messages(project_id, message_board_id)
    return [_summarize_message(m) for m in messages[:limit]]


@mcp.tool()
def read_message(project_id: int, message_id: int) -> dict | str:
    """Read a message and all its comments.

    Args:
        project_id: The project ID
        message_id: The message ID
    """
    client = _get_client()

    message = client.get_message(project_id, message_id)
    if not message:
        return "Message not found."

    comments = client.get_comments(project_id, message_id)
    result = _summarize_message(message)
    result["content"] = _strip_html(message.get("content", ""))
    result["comments"] = [_summarize_comment(c) for c in comments]
    return result


@mcp.tool()
def list_todolists(project_id: int, todoset_id: int | None = None) -> list[dict] | str:
    """List todolists in a project.

    If todoset_id is not provided, it will be auto-discovered from the project.

    Args:
        project_id: The project ID
        todoset_id: The todoset ID (auto-discovered if omitted)
    """
    client = _get_client()
    todoset_id, err = _resolve_dock_id(client, project_id, "todoset", todoset_id)
    if err:
        return err

    todolists = client.list_todolists(project_id, todoset_id)
    return [_summarize_todolist(t) for t in todolists]


@mcp.tool()
def list_todos(
    project_id: int,
    todolist_id: int,
    completed: bool = False,
    limit: int = 50,
) -> list[dict]:
    """List todos in a todolist.

    Args:
        project_id: The project ID
        todolist_id: The todolist ID
        completed: If True, show completed todos. Default: False (pending only)
        limit: Maximum number of todos to return (default 50)
    """
    client = _get_client()
    todos = client.list_todos(project_id, todolist_id, completed=completed)
    return [_summarize_todo(t) for t in todos[:limit]]


@mcp.tool()
def read_todo(project_id: int, todo_id: int) -> dict | str:
    """Read a todo and its comments.

    Args:
        project_id: The project ID
        todo_id: The todo ID
    """
    client = _get_client()

    todo = client.get_todo(project_id, todo_id)
    if not todo:
        return "Todo not found."

    comments = client.get_comments(project_id, todo_id)
    result = _summarize_todo(todo)
    result["description"] = _strip_html(todo.get("description", ""))
    result["created_at"] = todo.get("created_at", "")
    result["comments"] = [_summarize_comment(c) for c in comments]
    return result


@mcp.tool()
def list_people() -> list[dict]:
    """List all people in the Basecamp account."""
    client = _get_client()
    people = client.list_people()
    return [_summarize_person(p) for p in people]


@mcp.tool()
def list_documents(project_id: int, vault_id: int | None = None) -> list[dict] | str:
    """List documents in a project's Docs & Files vault.

    If vault_id is not provided, it will be auto-discovered from the project.

    Args:
        project_id: The project ID
        vault_id: The vault ID (auto-discovered if omitted)
    """
    client = _get_client()
    vault_id, err = _resolve_dock_id(client, project_id, "vault", vault_id)
    if err:
        return err

    docs = client.list_documents(project_id, vault_id)
    return [_summarize_document(d) for d in docs]


@mcp.tool()
def read_document(project_id: int, document_id: int) -> dict | str:
    """Read a document's content.

    Args:
        project_id: The project ID
        document_id: The document ID
    """
    client = _get_client()

    doc = client.get_document(project_id, document_id)
    if not doc:
        return "Document not found."

    result = _summarize_document(doc)
    result["content"] = _strip_html(doc.get("content", ""))
    return result


def _summarize_upload(u: dict) -> dict:
    return {
        "id": u["id"],
        "title": u.get("title", ""),
        "filename": u.get("filename", ""),
        "content_type": u.get("content_type", ""),
        "byte_size": u.get("byte_size", 0),
        "created_at": u.get("created_at", ""),
        "creator": u.get("creator", {}).get("name", ""),
        "app_url": u.get("app_url", ""),
        "download_url": u.get("download_url", ""),
    }


def _summarize_vault_folder(v: dict) -> dict:
    return {
        "id": v["id"],
        "title": v.get("title", ""),
        "documents_count": v.get("documents_count", 0),
        "uploads_count": v.get("uploads_count", 0),
        "vaults_count": v.get("vaults_count", 0),
        "app_url": v.get("app_url", ""),
    }


@mcp.tool()
def browse_vault(project_id: int, vault_id: int | None = None) -> dict | str:
    """Browse a vault folder — shows sub-folders, documents, and uploaded files.

    Use this to explore the Docs & Files section of a project. If vault_id is not
    provided, shows the root vault. Use sub-folder IDs from the results to drill deeper.

    Args:
        project_id: The project ID
        vault_id: The vault/folder ID (auto-discovers root vault if omitted)
    """
    client = _get_client()
    vault_id, err = _resolve_dock_id(client, project_id, "vault", vault_id)
    if err:
        return err

    data = client.browse_vault(project_id, vault_id)
    return {
        "folders": [_summarize_vault_folder(v) for v in data["folders"]],
        "documents": [_summarize_document(d) for d in data["documents"]],
        "uploads": [_summarize_upload(u) for u in data["uploads"]],
    }


@mcp.tool()
def search_project(project_id: int, keywords: str) -> dict[str, list[dict]] | str:
    """Search across a project's messages, documents, uploads, and todos.

    Matches keywords against titles/subjects. Searches ALL uploads and documents
    in the project (every vault, every nesting level — no depth limit). Returns
    up to 30 results per type.

    This is the best tool for finding something when you don't know where it is.

    Args:
        project_id: The project ID to search
        keywords: Space-separated keywords to match (e.g. "style guide" or "brand colors")
    """
    client = _get_client()
    raw = client.search_project(project_id, keywords)

    if "error" in raw:
        return raw["error"]

    return {
        "messages": [_summarize_message(m) for m in raw["messages"]],
        "documents": [_summarize_document(d) for d in raw["documents"]],
        "uploads": [_summarize_upload(u) for u in raw["uploads"]],
        "todos": [_summarize_todo(t) for t in raw["todos"]],
    }


@mcp.tool()
def search_all_projects(keywords: str, max_results: int = 15) -> dict[str, list[dict]]:
    """Search across ALL projects for documents, messages, uploads, and todos.

    Use this when you don't know which project something is in. Searches every
    project the user has access to. Can be slow for accounts with many projects.

    Args:
        keywords: Space-separated keywords to match (e.g. "style guide")
        max_results: Maximum results per type (default 15)
    """
    client = _get_client()
    projects = client.list_projects()
    cap = min(max(max_results, 1), 30)

    combined: dict[str, list[dict]] = {
        "messages": [],
        "documents": [],
        "uploads": [],
        "todos": [],
    }

    for project in projects:
        # Stop early if all categories are full
        if all(len(combined[k]) >= cap for k in combined):
            break

        raw = client.search_project(project["id"], keywords)
        if "error" in raw:
            continue
        for key in combined:
            for item in raw[key]:
                item["_project_name"] = project["name"]
                item["_project_id"] = project["id"]
            combined[key].extend(raw[key])

    # Cap results
    for key in combined:
        combined[key] = combined[key][:cap]

    return combined


# ── Document Search Tools (optional) ─────────────────────────


@mcp.tool()
def search_document_content(
    query: str,
    project_id: int | None = None,
    limit: int = 10,
) -> dict | str:
    """Full-text search inside ingested documents (e.g. .docx files from Basecamp).

    This searches the actual content of documents, not just titles. Requires
    a connected document search API (set up via `basecamp-mcp connect-docs`).

    Use this when you need to find information *within* documents rather than
    just locating documents by name.

    Results include `recording_id` and/or `upload_id` plus `project_id` which
    can be used to link directly to the source in Basecamp:
    - Comment attachments: https://3.basecamp.com/{account_id}/buckets/{project_id}/recordings/{recording_id}
    - Vault uploads: https://3.basecamp.com/{account_id}/buckets/{project_id}/uploads/{upload_id}

    Always include these Basecamp links when presenting results to the user.

    Args:
        query: Natural language search query (e.g. "SAVE Act voting requirements")
        project_id: Optional Basecamp project ID to filter results
        limit: Maximum number of results (1-50, default 10)
    """
    params: dict = {"q": query, "limit": min(max(limit, 1), 50)}
    if project_id:
        params["project_id"] = project_id

    result = _doc_search_request("/api/documents/search", params)
    if "error" in result:
        return result["error"]

    return {
        "query": result.get("query", query),
        "count": result.get("count", 0),
        "results": result.get("results", []),
    }


@mcp.tool()
def document_stats() -> dict | str:
    """Get statistics about the document search index.

    Shows how many documents are indexed, storage breakdown by source type, etc.
    Requires a connected document search API.
    """
    result = _doc_search_request("/api/documents/stats")
    if "error" in result:
        return result["error"]
    return result


def main():
    """Entry point — handles subcommands and MCP server."""
    subcmd = sys.argv[1] if len(sys.argv) > 1 else None

    if subcmd == "auth":
        from .auth import run_auth_flow

        run_auth_flow()
    elif subcmd == "connect-docs":
        from .auth import run_connect_docs

        run_connect_docs()
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
