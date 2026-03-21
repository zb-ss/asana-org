"""Asana API client module for Asana Org Bridge.

Provides real API operations for:
- Fetching My Tasks
- Updating task status, dates
- Moving tasks between sections/projects
- Adding comments

Uses requests library with retry logic and rate limit handling.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import requests  # type: ignore[import-untyped]

from asana_org_bridge.logging_config import get_logger

logger = get_logger(__name__)

# Asana API constants
ASANA_API_BASE_URL = "https://app.asana.com/api/1.0"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 3
RATE_LIMIT_STATUS_CODE = 429
DEFAULT_RETRY_AFTER_SECONDS = 60
MAX_RETRY_AFTER_SECONDS = 300


@dataclass
class AsanaTask:
    """Represents an Asana task."""

    gid: str
    name: str
    completed: bool
    permalink_url: str
    modified_at: datetime
    due_on: str | None = None
    due_at: str | None = None
    start_on: str | None = None
    notes: str | None = None
    memberships: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.memberships is None:
            self.memberships = []


@dataclass
class AsanaResult:
    """Result of an Asana API operation."""

    success: bool
    data: Any = None
    error: str | None = None
    status_code: int = 0


class AsanaAPIError(Exception):
    """Exception for Asana API errors."""

    def __init__(self, message: str, status_code: int = 0, code: str = ""):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class RateLimitError(AsanaAPIError):
    """Exception for rate limiting."""

    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message, status_code=RATE_LIMIT_STATUS_CODE)
        self.retry_after = retry_after


class AsanaClient:
    """Client for Asana REST API."""

    # Fields to request for task operations
    # Memberships require explicit dot-path sub-fields to return nested objects
    TASK_FIELDS = (
        "gid,name,completed,permalink_url,modified_at,"
        "due_on,due_at,start_on,notes,"
        "memberships.project.gid,memberships.project.name,"
        "memberships.section.gid,memberships.section.name"
    )

    STORY_FIELDS = (
        "created_by,created_by.name,text,created_at,"
        "type,resource_subtype"
    )

    def __init__(
        self,
        pat: str,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_RETRIES,
    ):
        """Initialize Asana API client.

        Args:
            pat: Personal Access Token
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts for transient errors
        """
        self._pat = pat
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {pat}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an HTTP request to Asana API with retry logic.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint path (without base URL)
            params: Query parameters
            json_data: JSON body for POST/PUT

        Returns:
            Parsed JSON response

        Raises:
            RateLimitError: When rate limited
            AsanaAPIError: For other API errors
        """
        url = f"{ASANA_API_BASE_URL}{endpoint}"

        for attempt in range(self._max_retries):
            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_data,
                    timeout=self._timeout,
                )

                # Handle rate limiting
                if response.status_code == RATE_LIMIT_STATUS_CODE:
                    retry_after = self._parse_retry_after(
                        response.headers.get("Retry-After")
                    )
                    if attempt < self._max_retries - 1:
                        logger.warning(
                            "rate_limited",
                            retry_after=retry_after,
                            attempt=attempt + 1,
                        )
                        time.sleep(retry_after)
                        continue
                    raise RateLimitError(
                        f"Rate limited after {self._max_retries} attempts",
                        retry_after=retry_after,
                    )

                # Handle other errors
                if response.status_code >= 400:
                    raise self._build_api_error(response)

                # Parse successful response
                return cast(dict[str, Any], response.json())

            except requests.RequestException as e:
                if attempt < self._max_retries - 1:
                    logger.warning(
                        "request_failed_retrying",
                        error=str(e),
                        attempt=attempt + 1,
                    )
                    time.sleep(2**attempt)  # Exponential backoff
                    continue
                raise AsanaAPIError(
                    f"Request failed after {attempt + 1} attempts: {e}"
                ) from e

        raise AsanaAPIError("Max retries exceeded")

    @staticmethod
    def _parse_retry_after(raw_value: str | None) -> int:
        """Parse Retry-After safely with bounded fallback."""
        if not raw_value:
            return DEFAULT_RETRY_AFTER_SECONDS

        try:
            parsed = int(float(raw_value.strip()))
        except (TypeError, ValueError):
            return DEFAULT_RETRY_AFTER_SECONDS

        return max(1, min(parsed, MAX_RETRY_AFTER_SECONDS))

    @staticmethod
    def _build_api_error(response: requests.Response) -> AsanaAPIError:
        """Build a structured AsanaAPIError from any HTTP error response."""
        fallback_message = f"HTTP {response.status_code} error"
        default_code = "HTTP_ERROR"

        try:
            parsed = response.json()
        except ValueError:
            text = (response.text or "").strip()
            if len(text) > 256:
                text = f"{text[:256]}..."
            message = (
                f"{fallback_message}: non-JSON error response"
                if not text
                else f"{fallback_message}: {text}"
            )
            return AsanaAPIError(
                message=message, status_code=response.status_code, code=default_code
            )

        errors = parsed.get("errors") if isinstance(parsed, dict) else None
        if isinstance(errors, list) and errors:
            first_error = errors[0]
            if isinstance(first_error, dict):
                message = str(first_error.get("message") or fallback_message)
                code = str(first_error.get("code") or default_code)
                return AsanaAPIError(
                    message=message, status_code=response.status_code, code=code
                )

        return AsanaAPIError(
            message=fallback_message,
            status_code=response.status_code,
            code=default_code,
        )

    def get_my_tasks(
        self,
        workspace_gid: str | None = None,
        project_gid: str | None = None,
        limit: int = 100,
        completed_since: str | None = None,
        modified_since: str | None = None,
    ) -> list[AsanaTask]:
        """Fetch My Tasks from Asana.

        Args:
            workspace_gid: Optional workspace GID to filter by
            project_gid: Optional project GID to filter by
            limit: Maximum number of tasks to fetch
            completed_since: ISO date; only return tasks completed after this
                date, or incomplete tasks. Use 'now' for incomplete only.
            modified_since: ISO date; only return tasks modified after this date

        Returns:
            List of AsanaTask objects

        Raises:
            AsanaAPIError: If the API call fails
        """
        params = {
            "opt_fields": self.TASK_FIELDS,
            "limit": min(limit, 100),  # Asana max is 100
        }

        if completed_since:
            params["completed_since"] = completed_since
        if modified_since:
            params["modified_since"] = modified_since

        # Build assignee filter
        if project_gid:
            # Get tasks from specific project
            endpoint = f"/projects/{project_gid}/tasks"
        else:
            # Get My Tasks (requires workspace)
            endpoint = "/tasks"
            if workspace_gid:
                params["workspace"] = workspace_gid
            params["assignee"] = "me"

        tasks: list[AsanaTask] = []
        while len(tasks) < limit:
            response = self._request("GET", endpoint, params=params)

            data = response.get("data", [])
            if not data:
                break

            for task_data in data:
                task = self._parse_task(task_data)
                tasks.append(task)

            # Check for pagination
            next_page = response.get("next_page")
            if not next_page or len(tasks) >= limit:
                break

            # Update params for next page - preserve original params and set offset
            # Asana returns next_page as {"offset": "..."} or {"offset": "...", "path": "..."}
            offset_value = (
                next_page.get("offset") if isinstance(next_page, dict) else None
            )
            if offset_value:
                params["offset"] = offset_value
            else:
                # No offset means no more pages
                break

        return tasks[:limit]

    def _parse_task(self, data: dict[str, Any]) -> AsanaTask:
        """Parse task data into AsanaTask object.

        Args:
            data: Raw task data from API

        Returns:
            AsanaTask object
        """
        modified_at_str = data.get("modified_at", "")
        modified_at = datetime.fromisoformat(modified_at_str.replace("Z", "+00:00"))

        return AsanaTask(
            gid=data.get("gid", ""),
            name=data.get("name", ""),
            completed=data.get("completed", False),
            permalink_url=data.get("permalink_url", ""),
            modified_at=modified_at,
            due_on=data.get("due_on"),
            due_at=data.get("due_at"),
            start_on=data.get("start_on"),
            notes=data.get("notes"),
            memberships=data.get("memberships", []),
        )

    def get_task(
        self,
        task_gid: str,
        opt_fields: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a single task by GID.

        Args:
            task_gid: Task GID to fetch
            opt_fields: Optional comma-separated fields to request

        Returns:
            Raw task data dictionary from the API

        Raises:
            AsanaAPIError: If the API call fails
        """
        if opt_fields is None:
            opt_fields = self.TASK_FIELDS

        response = self._request(
            "GET",
            f"/tasks/{task_gid}",
            params={"opt_fields": opt_fields},
        )
        return cast(dict[str, Any], response.get("data", {}))

    def update_task(
        self,
        task_gid: str,
        completed: bool | None = None,
        due_on: str | None = None,
        start_on: str | None = None,
        name: str | None = None,
    ) -> AsanaResult:
        """Update a task's fields.

        Args:
            task_gid: Task GID to update
            completed: New completion status
            due_on: New due date (YYYY-MM-DD)
            start_on: New start date (YYYY-MM-DD)
            name: New task name

        Returns:
            AsanaResult with success status

        Raises:
            AsanaAPIError: If the API call fails
        """
        data: dict[str, Any] = {}
        if completed is not None:
            data["completed"] = completed
        if due_on is not None:
            data["due_on"] = due_on
        if start_on is not None:
            data["start_on"] = start_on
        if name is not None:
            data["name"] = name

        if not data:
            return AsanaResult(success=True, data={}, status_code=200)

        try:
            response = self._request(
                "PUT",
                f"/tasks/{task_gid}",
                json_data={"data": data},
            )
            return AsanaResult(
                success=True,
                data=response.get("data"),
                status_code=200,
            )
        except AsanaAPIError as e:
            return AsanaResult(
                success=False,
                error=str(e),
                status_code=e.status_code,
            )

    def add_comment(
        self,
        task_gid: str,
        text: str,
    ) -> AsanaResult:
        """Add a comment to a task (story).

        Args:
            task_gid: Task GID to comment on
            text: Comment text

        Returns:
            AsanaResult with comment GID if successful

        Raises:
            AsanaAPIError: If the API call fails
        """
        try:
            response = self._request(
                "POST",
                f"/tasks/{task_gid}/stories",
                json_data={
                    "data": {
                        "text": text,
                    }
                },
            )
            story_data = response.get("data", {})
            return AsanaResult(
                success=True,
                data={
                    "story_gid": story_data.get("gid"),
                    "task_gid": task_gid,
                },
                status_code=201,
            )
        except AsanaAPIError as e:
            return AsanaResult(
                success=False,
                error=str(e),
                status_code=e.status_code,
            )

    def move_task_to_section(
        self,
        task_gid: str,
        section_gid: str,
    ) -> AsanaResult:
        """Move a task to a different section.

        Args:
            task_gid: Task GID to move
            section_gid: Target section GID

        Returns:
            AsanaResult with success status

        Raises:
            AsanaAPIError: If the API call fails
        """
        try:
            # Add task to section (Asana API pattern)
            response = self._request(
                "POST",
                f"/sections/{section_gid}/addTask",
                json_data={"data": {"task": task_gid}},
            )
            return AsanaResult(
                success=True,
                data=response.get("data"),
                status_code=200,
            )
        except AsanaAPIError as e:
            return AsanaResult(
                success=False,
                error=str(e),
                status_code=e.status_code,
            )

    def get_workspaces(self) -> list[dict[str, Any]]:
        """Get available workspaces.

        Returns:
            List of workspace dictionaries

        Raises:
            AsanaAPIError: If the API call fails
        """
        response = self._request("GET", "/workspaces")
        return cast(list[dict[str, Any]], response.get("data", []))

    def get_projects(self, workspace_gid: str) -> list[dict[str, Any]]:
        """Get projects in a workspace.

        Args:
            workspace_gid: Workspace GID

        Returns:
            List of project dictionaries

        Raises:
            AsanaAPIError: If the API call fails
        """
        response = self._request(
            "GET",
            f"/workspaces/{workspace_gid}/projects",
            params={"opt_fields": "gid,name,workspace"},
        )
        return cast(list[dict[str, Any]], response.get("data", []))

    def get_sections(self, project_gid: str) -> list[dict[str, Any]]:
        """Get sections in a project.

        Args:
            project_gid: Project GID

        Returns:
            List of section dictionaries

        Raises:
            AsanaAPIError: If the API call fails
        """
        response = self._request(
            "GET",
            f"/projects/{project_gid}/sections",
            params={"opt_fields": "gid,name,project"},
        )
        return cast(list[dict[str, Any]], response.get("data", []))

    def get_user_task_list(self, workspace_gid: str) -> dict[str, Any]:
        """Get the user's My Tasks (user_task_list) for a workspace.

        Args:
            workspace_gid: Workspace GID

        Returns:
            User task list dict with gid and name

        Raises:
            AsanaAPIError: If the API call fails
        """
        response = self._request(
            "GET",
            "/users/me/user_task_list",
            params={
                "workspace": workspace_gid,
                "opt_fields": "gid,name",
            },
        )
        return cast(dict[str, Any], response.get("data", {}))

    def get_tasks_for_section(
        self,
        section_gid: str,
        limit: int = 100,
        completed_since: str | None = None,
    ) -> list[AsanaTask]:
        """Get tasks in a specific section.

        Args:
            section_gid: Section GID
            limit: Maximum number of tasks to fetch
            completed_since: ISO date or 'now' for incomplete only

        Returns:
            List of AsanaTask objects

        Raises:
            AsanaAPIError: If the API call fails
        """
        params: dict[str, Any] = {
            "opt_fields": self.TASK_FIELDS,
            "limit": min(limit, 100),
        }
        if completed_since:
            params["completed_since"] = completed_since

        tasks: list[AsanaTask] = []
        response = self._request(
            "GET",
            f"/sections/{section_gid}/tasks",
            params=params,
        )
        data = response.get("data", [])
        for task_data in data:
            tasks.append(self._parse_task(task_data))
        return tasks

    def get_stories(
        self,
        task_gid: str,
        opt_fields: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch comment stories for a task.

        Only returns comment-type stories, filtering out system events.

        Args:
            task_gid: Task GID to fetch stories for
            opt_fields: Optional fields to request (defaults to comment-relevant fields)

        Returns:
            List of comment story dicts with gid, created_by, text, created_at

        Raises:
            AsanaAPIError: If the API call fails
        """
        if opt_fields is None:
            opt_fields = self.STORY_FIELDS

        response = self._request(
            "GET",
            f"/tasks/{task_gid}/stories",
            params={"opt_fields": opt_fields},
        )

        stories: list[dict[str, Any]] = []
        for story in response.get("data", []):
            is_comment = (
                story.get("resource_subtype") == "comment_added"
                or story.get("type") == "comment"
            )
            if not is_comment:
                continue

            stories.append({
                "gid": story.get("gid", ""),
                "created_by": story.get("created_by", {}),
                "text": story.get("text", ""),
                "created_at": story.get("created_at", ""),
            })

        return stories

    def get_task(
        self,
        task_gid: str,
        opt_fields: str | None = None,
    ) -> AsanaTask:
        """Fetch a single task by GID.

        Args:
            task_gid: Task GID to fetch
            opt_fields: Optional comma-separated fields to request

        Returns:
            AsanaTask object

        Raises:
            AsanaAPIError: If the API call fails
        """
        params: dict[str, Any] = {
            "opt_fields": opt_fields or self.TASK_FIELDS,
        }
        response = self._request("GET", f"/tasks/{task_gid}", params=params)
        return self._parse_task(response.get("data", {}))

    def close(self) -> None:
        """Close the client session."""
        self._session.close()


def create_asana_client(pat: str) -> AsanaClient:
    """Factory function to create Asana client.

    Args:
        pat: Personal Access Token

    Returns:
        Configured AsanaClient instance
    """
    return AsanaClient(pat=pat)
