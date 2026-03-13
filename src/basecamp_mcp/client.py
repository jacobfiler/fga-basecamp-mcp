"""Basecamp API client with automatic token refresh."""

import logging

import httpx

from . import USER_AGENT
from .config import update_tokens

logger = logging.getLogger(__name__)
TOKEN_URL = "https://launchpad.37signals.com/authorization/token"


class BasecampClient:
    def __init__(self, config: dict):
        self.account_id = config["account_id"]
        self.base_url = f"https://3.basecampapi.com/{self.account_id}"
        self._access_token = config["access_token"]
        self._refresh_token = config.get("refresh_token", "")
        self._client_id = config.get("client_id", "")
        self._client_secret = config.get("client_secret", "")
        self._http: httpx.Client | None = None
        self._project_cache: dict[int, dict] = {}

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    def _client(self) -> httpx.Client:
        if self._http is None or self._http.is_closed:
            self._http = httpx.Client(
                headers=self._headers,
                timeout=30.0,
                transport=httpx.HTTPTransport(retries=2),
            )
        return self._http

    def _refresh_access_token(self) -> bool:
        """Refresh the access token using the refresh token."""
        if not all([self._refresh_token, self._client_id, self._client_secret]):
            logger.error("Cannot refresh: missing credentials")
            return False

        logger.info("Refreshing Basecamp access token...")
        try:
            response = httpx.post(
                TOKEN_URL,
                params={
                    "type": "refresh",
                    "refresh_token": self._refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            self._access_token = data["access_token"]
            new_refresh = data.get("refresh_token")
            if new_refresh:
                self._refresh_token = new_refresh

            # Persist new tokens to config file
            update_tokens(self._access_token, new_refresh)

            # Recreate HTTP client with new headers
            if self._http:
                try:
                    self._http.close()
                except Exception:
                    pass
                self._http = None

            logger.info("Token refreshed successfully")
            return True
        except httpx.HTTPError as e:
            logger.error("Token refresh failed: %s", type(e).__name__)
            return False

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Make a request with automatic 401 retry after token refresh."""
        client = self._client()
        response = client.request(method, url, **kwargs)

        if response.status_code == 401 and self._refresh_access_token():
            client = self._client()
            response = client.request(method, url, **kwargs)

        return response

    def _get(self, path: str, **kwargs) -> dict | None:
        """GET a Basecamp API endpoint. Returns parsed JSON or None on error."""
        url = f"{self.base_url}{path}"
        try:
            response = self._request("GET", url, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"GET {path} failed: {e}")
            return None

    def _paginate(
        self,
        path: str,
        max_pages: int = 10,
        params: dict | None = None,
        min_page_size: int = 50,
    ) -> list[dict]:
        """Paginate through a Basecamp list endpoint.

        Args:
            min_page_size: If a page returns fewer items than this, assume it's
                the last page.  Set to 1 for endpoints with small page sizes
                (e.g. the recordings index) so we paginate until truly empty.
        """
        all_items: list[dict] = []
        url = f"{self.base_url}{path}"
        base_params = dict(params) if params else {}

        for page in range(1, max_pages + 1):
            try:
                response = self._request(
                    "GET", url, params={**base_params, "page": page}
                )
                response.raise_for_status()
                items = response.json()
                if not items:
                    break
                all_items.extend(items)
                if len(items) < min_page_size:
                    break
            except httpx.HTTPError as e:
                logger.error(f"Pagination failed at page {page}: {e}")
                break

        return all_items

    # ── Projects ──────────────────────────────────────────────────

    def list_projects(self) -> list[dict]:
        return self._paginate("/projects.json")

    def get_project(self, project_id: int) -> dict | None:
        if project_id in self._project_cache:
            return self._project_cache[project_id]
        result = self._get(f"/projects/{project_id}.json")
        if result:
            self._project_cache[project_id] = result
        return result

    def find_project_by_name(self, name: str) -> dict | None:
        """Find a project by name (case-insensitive, partial match)."""
        projects = self.list_projects()
        name_lower = name.lower()

        # Exact match first
        for p in projects:
            if p["name"].lower() == name_lower:
                return p

        # Partial match
        for p in projects:
            if name_lower in p["name"].lower():
                return p

        return None

    def get_dock_tool(self, project_id: int, tool_name: str) -> dict | None:
        """Get a dock tool entry (message_board, todoset, vault, etc.)."""
        project = self.get_project(project_id)
        if not project:
            return None
        for tool in project.get("dock", []):
            if tool.get("name") == tool_name and tool.get("enabled"):
                return tool
        return None

    # ── Messages ──────────────────────────────────────────────────

    def list_messages(self, project_id: int, message_board_id: int) -> list[dict]:
        return self._paginate(
            f"/buckets/{project_id}/message_boards/{message_board_id}/messages.json"
        )

    def get_message(self, project_id: int, message_id: int) -> dict | None:
        return self._get(f"/buckets/{project_id}/messages/{message_id}.json")

    def get_comments(self, project_id: int, recording_id: int) -> list[dict]:
        return self._paginate(
            f"/buckets/{project_id}/recordings/{recording_id}/comments.json",
            max_pages=20,
        )

    # ── Todos ─────────────────────────────────────────────────────

    def list_todolists(self, project_id: int, todoset_id: int) -> list[dict]:
        return self._paginate(
            f"/buckets/{project_id}/todosets/{todoset_id}/todolists.json"
        )

    def list_todos(
        self,
        project_id: int,
        todolist_id: int,
        completed: bool = False,
    ) -> list[dict]:
        return self._paginate(
            f"/buckets/{project_id}/todolists/{todolist_id}/todos.json",
            params={"completed": str(completed).lower()},
        )

    def get_todo(self, project_id: int, todo_id: int) -> dict | None:
        return self._get(f"/buckets/{project_id}/todos/{todo_id}.json")

    # ── People ────────────────────────────────────────────────────

    def list_people(self) -> list[dict]:
        return self._paginate("/people.json")

    # ── Vault (Docs & Files) ────────────────────────────────────────

    def list_documents(self, project_id: int, vault_id: int) -> list[dict]:
        return self._paginate(f"/buckets/{project_id}/vaults/{vault_id}/documents.json")

    def list_uploads(self, project_id: int, vault_id: int) -> list[dict]:
        return self._paginate(f"/buckets/{project_id}/vaults/{vault_id}/uploads.json")

    def list_sub_vaults(self, project_id: int, vault_id: int) -> list[dict]:
        return self._paginate(f"/buckets/{project_id}/vaults/{vault_id}/vaults.json")

    def get_document(self, project_id: int, document_id: int) -> dict | None:
        return self._get(f"/buckets/{project_id}/documents/{document_id}.json")

    def browse_vault(self, project_id: int, vault_id: int) -> dict:
        """Get everything in a vault folder: sub-folders, documents, and uploads."""
        return {
            "folders": self.list_sub_vaults(project_id, vault_id),
            "documents": self.list_documents(project_id, vault_id),
            "uploads": self.list_uploads(project_id, vault_id),
        }

    def crawl_vault_ids(
        self, project_id: int, root_vault_id: int, max_depth: int = 3
    ) -> list[int]:
        """BFS crawl vault sub-folders up to max_depth. Returns all vault IDs."""
        all_ids = [root_vault_id]
        frontier = [root_vault_id]
        for _ in range(max_depth - 1):
            next_frontier = []
            for vid in frontier:
                for sv in self.list_sub_vaults(project_id, vid):
                    all_ids.append(sv["id"])
                    next_frontier.append(sv["id"])
            frontier = next_frontier
            if not frontier:
                break
        return all_ids

    def list_all_recordings(self, project_id: int, recording_type: str) -> list[dict]:
        """List ALL recordings of a given type in a project.

        Uses /projects/recordings.json?type=&bucket= which returns
        items from every vault (including orphaned/parent-less vaults that
        crawl_vault_ids misses). No depth limit.
        """
        return self._paginate(
            "/projects/recordings.json",
            max_pages=200,
            params={"type": recording_type, "bucket": project_id},
            min_page_size=1,
        )

    def list_all_uploads(self, project_id: int) -> list[dict]:
        return self.list_all_recordings(project_id, "Upload")

    def list_all_documents(self, project_id: int) -> list[dict]:
        return self.list_all_recordings(project_id, "Document")

    def _get_all_dock_ids(self, project: dict, tool_name: str) -> list[int]:
        """Get all enabled dock tool IDs for a given tool name.

        Some projects have multiple enabled tools of the same type (e.g. multiple
        todosets). This returns all of them, not just the first/last.
        """
        return [
            tool["id"]
            for tool in project.get("dock", [])
            if tool.get("enabled") and tool.get("name") == tool_name
        ]

    def search_project(
        self, project_id: int, keywords: str, project: dict | None = None
    ) -> dict[str, list[dict]]:
        """Search across a project's messages, documents, uploads, and todos by keyword-matching titles.

        Uses the recordings index API for uploads/documents to search ALL vault
        levels (including orphaned vaults). Returns matching items grouped by type.
        """
        if project is None:
            project = self.get_project(project_id)
        if not project:
            return {"error": "Project not found"}

        kw_list = keywords.lower().split()
        results: dict[str, list[dict]] = {
            "messages": [],
            "documents": [],
            "uploads": [],
            "todos": [],
        }

        def matches(title: str) -> bool:
            title_lower = title.lower()
            return any(kw in title_lower for kw in kw_list)

        # Search messages (across all message boards)
        for mb_id in self._get_all_dock_ids(project, "message_board"):
            for m in self.list_messages(project_id, mb_id):
                if matches(m.get("subject", "")):
                    results["messages"].append(m)

        # Search ALL documents and uploads via recordings API — no depth limit,
        # finds files in orphaned vaults too
        for d in self.list_all_documents(project_id):
            if matches(d.get("title", "")):
                results["documents"].append(d)
        for u in self.list_all_uploads(project_id):
            if matches(u.get("title", "") + " " + u.get("filename", "")):
                results["uploads"].append(u)

        # Search todos (across all todosets)
        for todoset_id in self._get_all_dock_ids(project, "todoset"):
            for tl in self.list_todolists(project_id, todoset_id):
                if matches(tl.get("name", tl.get("title", ""))):
                    results["todos"].append(tl)
                for t in self.list_todos(project_id, tl["id"]):
                    if matches(t.get("content", "")):
                        results["todos"].append(t)

        # Cap results
        for key in results:
            results[key] = results[key][:30]

        return results

    def close(self) -> None:
        if self._http:
            try:
                self._http.close()
            except Exception:
                pass


class DocSearchClient:
    """Lightweight client for an external document search API."""

    def __init__(self, url: str, token: str | None = None):
        self.base_url = url.rstrip("/")
        self._token = token
        self._http: httpx.Client | None = None

    def _client(self) -> httpx.Client:
        if self._http is None or self._http.is_closed:
            headers = {"User-Agent": USER_AGENT}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            self._http = httpx.Client(
                headers=headers,
                timeout=30.0,
                transport=httpx.HTTPTransport(retries=2),
            )
        return self._http

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET an endpoint. Returns parsed JSON or {"error": ...} on failure."""
        try:
            response = self._client().get(f"{self.base_url}{path}", params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Document search request failed: {e}")
            return {"error": f"Document search request failed: {e}"}

    def close(self) -> None:
        if self._http:
            try:
                self._http.close()
            except Exception:
                pass
