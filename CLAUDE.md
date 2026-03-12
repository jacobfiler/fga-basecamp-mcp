# basecamp-mcp

Open-source MCP server for Basecamp. Lets Claude (Desktop & Code) read Basecamp projects, messages, todos, and documents using any user's own OAuth credentials.

## Project Structure

```
src/basecamp_mcp/
├── __init__.py     # Version
├── server.py       # MCP server + tool definitions (entry point)
├── client.py       # Basecamp API client (httpx, token refresh)
├── auth.py         # OAuth flow (browser + local callback server)
└── config.py       # Config file read/write (~/.config/basecamp-mcp/)
```

## Key Patterns

- **Config:** `~/.config/basecamp-mcp/config.json` — stores OAuth tokens, account ID, user info. Permissions set to 0o600.
- **Token refresh:** On 401, auto-refresh via `POST https://launchpad.37signals.com/authorization/token` with `type=refresh`. New tokens persisted to config file.
- **Dock parsing:** Projects have a `dock` array with tools (message_board, todoset, vault). Tools auto-discover IDs when user omits them.
- **Pagination:** Basecamp returns 50 items per page. `_paginate()` follows pages up to `max_pages`.

## Entry Points

- `basecamp-mcp` — runs MCP server (stdio transport)
- `basecamp-mcp auth` — interactive OAuth setup (optionally prompts for doc search at the end)
- `basecamp-mcp connect-docs` — connect a document search API (standalone, for existing installs)

## Tools

**Basecamp API (always available):**
list_projects, get_project, list_messages, read_message, list_todolists, list_todos, read_todo, list_people, list_documents, read_document, browse_vault, search_project, search_all_projects

**Document Search (optional — requires `doc_search_url` in config):**
search_document_content, document_stats

Document search tools call an external API (e.g. socialbot) that indexes .docx content into PostgreSQL full-text search. The tools always register but return a helpful error if unconfigured.

## Development

```bash
pip install -e .
basecamp-mcp auth   # one-time setup
basecamp-mcp        # run server
```

## Basecamp API Notes

- Base URL: `https://3.basecampapi.com/{account_id}`
- Auth: Bearer token
- User-Agent header required
- Rate limit: 50 req / 10 seconds
- Access tokens expire every 2 weeks, refresh tokens last 10 years
