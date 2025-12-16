"""Azure DevOps REST API client."""

from typing import Any, Optional
from urllib.parse import quote

import httpx

from .auth.manager import AuthManager


class AzureDevOpsClientError(Exception):
    """Error from Azure DevOps API."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class AzureDevOpsClient:
    """Async HTTP client for Azure DevOps REST API v7.1."""

    BASE_URL = "https://dev.azure.com/{organization}"
    VSSPS_URL = "https://vssps.dev.azure.com/{organization}"
    VSRM_URL = "https://vsrm.dev.azure.com/{organization}"
    SEARCH_URL = "https://almsearch.dev.azure.com/{organization}"
    API_VERSION = "7.1"

    def __init__(self, auth_manager: AuthManager):
        """Initialize the client.

        Args:
            auth_manager: AuthManager instance for authentication.
        """
        self.auth = auth_manager
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def base_url(self) -> str:
        """Get the base URL for the organization."""
        return self.BASE_URL.format(organization=self.auth.organization)

    @property
    def vssps_url(self) -> str:
        """Get the VSSPS URL for identity operations."""
        return self.VSSPS_URL.format(organization=self.auth.organization)

    @property
    def search_url(self) -> str:
        """Get the search URL."""
        return self.SEARCH_URL.format(organization=self.auth.organization)

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        url: str,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> Any:
        """Make an authenticated request to Azure DevOps API.

        Args:
            method: HTTP method.
            url: Full URL for the request.
            params: Query parameters.
            json: JSON body for POST/PUT/PATCH requests.
            headers: Additional headers.

        Returns:
            Parsed JSON response.

        Raises:
            AzureDevOpsClientError: If the request fails.
        """
        client = await self._get_client()
        auth_headers = await self.auth.get_headers_async()

        request_headers = {
            **auth_headers,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if headers:
            request_headers.update(headers)

        # Add API version to params
        params = params or {}
        if "api-version" not in params:
            params["api-version"] = self.API_VERSION

        try:
            response = await client.request(
                method=method,
                url=url,
                params=params,
                json=json,
                headers=request_headers,
            )
            response.raise_for_status()

            if response.status_code == 204:
                return None

            return response.json()

        except httpx.HTTPStatusError as e:
            error_msg = f"Azure DevOps API error: {e.response.status_code}"
            try:
                error_body = e.response.json()
                if "message" in error_body:
                    error_msg = f"{error_msg} - {error_body['message']}"
            except Exception:
                error_msg = f"{error_msg} - {e.response.text}"
            raise AzureDevOpsClientError(error_msg, e.response.status_code) from e

        except httpx.RequestError as e:
            raise AzureDevOpsClientError(f"Request failed: {str(e)}") from e

    # ==================== Core API ====================

    async def list_projects(
        self,
        state_filter: str = "wellFormed",
        top: Optional[int] = None,
        skip: Optional[int] = None,
    ) -> dict[str, Any]:
        """List all projects in the organization."""
        params: dict[str, Any] = {"stateFilter": state_filter}
        if top:
            params["$top"] = top
        if skip:
            params["$skip"] = skip

        url = f"{self.base_url}/_apis/projects"
        return await self._request("GET", url, params=params)

    async def get_project(self, project: str) -> dict[str, Any]:
        """Get a specific project by name or ID."""
        url = f"{self.base_url}/_apis/projects/{quote(project)}"
        return await self._request("GET", url)

    async def list_teams(
        self,
        project: str,
        top: Optional[int] = None,
        skip: Optional[int] = None,
    ) -> dict[str, Any]:
        """List teams in a project."""
        params: dict[str, Any] = {}
        if top:
            params["$top"] = top
        if skip:
            params["$skip"] = skip

        url = f"{self.base_url}/_apis/projects/{quote(project)}/teams"
        return await self._request("GET", url, params=params)

    async def get_identities(
        self,
        search_filter: str,
        filter_value: str,
    ) -> dict[str, Any]:
        """Search for identities."""
        params = {
            "searchFilter": search_filter,
            "filterValue": filter_value,
        }
        url = f"{self.vssps_url}/_apis/identities"
        return await self._request("GET", url, params=params)

    # ==================== Work Items API ====================

    async def get_work_item(
        self,
        project: str,
        work_item_id: int,
        expand: Optional[str] = None,
        fields: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Get a work item by ID."""
        params: dict[str, Any] = {}
        if expand:
            params["$expand"] = expand
        if fields:
            params["fields"] = ",".join(fields)

        url = f"{self.base_url}/{quote(project)}/_apis/wit/workitems/{work_item_id}"
        return await self._request("GET", url, params=params)

    async def get_work_items_batch(
        self,
        project: str,
        ids: list[int],
        fields: Optional[list[str]] = None,
        expand: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get multiple work items by IDs."""
        body: dict[str, Any] = {"ids": ids}
        if fields:
            body["fields"] = fields
        if expand:
            body["$expand"] = expand

        url = f"{self.base_url}/{quote(project)}/_apis/wit/workitemsbatch"
        return await self._request("POST", url, json=body)

    async def create_work_item(
        self,
        project: str,
        work_item_type: str,
        document: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create a new work item."""
        url = f"{self.base_url}/{quote(project)}/_apis/wit/workitems/${quote(work_item_type)}"
        return await self._request(
            "POST",
            url,
            json=document,
            headers={"Content-Type": "application/json-patch+json"},
        )

    async def update_work_item(
        self,
        project: str,
        work_item_id: int,
        document: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Update an existing work item."""
        url = f"{self.base_url}/{quote(project)}/_apis/wit/workitems/{work_item_id}"
        return await self._request(
            "PATCH",
            url,
            json=document,
            headers={"Content-Type": "application/json-patch+json"},
        )

    async def list_work_item_comments(
        self,
        project: str,
        work_item_id: int,
        top: Optional[int] = None,
    ) -> dict[str, Any]:
        """List comments on a work item."""
        params: dict[str, Any] = {}
        if top:
            params["$top"] = top

        url = f"{self.base_url}/{quote(project)}/_apis/wit/workitems/{work_item_id}/comments"
        return await self._request("GET", url, params=params)

    async def add_work_item_comment(
        self,
        project: str,
        work_item_id: int,
        text: str,
    ) -> dict[str, Any]:
        """Add a comment to a work item."""
        url = f"{self.base_url}/{quote(project)}/_apis/wit/workitems/{work_item_id}/comments"
        return await self._request("POST", url, json={"text": text})

    async def run_wiql_query(
        self,
        project: str,
        query: str,
        top: Optional[int] = None,
    ) -> dict[str, Any]:
        """Run a WIQL query."""
        params: dict[str, Any] = {}
        if top:
            params["$top"] = top

        url = f"{self.base_url}/{quote(project)}/_apis/wit/wiql"
        return await self._request("POST", url, json={"query": query}, params=params)

    async def get_query(
        self,
        project: str,
        query_id: str,
        depth: int = 0,
        expand: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get a saved query."""
        params: dict[str, Any] = {"$depth": depth}
        if expand:
            params["$expand"] = expand

        url = f"{self.base_url}/{quote(project)}/_apis/wit/queries/{quote(query_id)}"
        return await self._request("GET", url, params=params)

    async def list_backlogs(
        self,
        project: str,
        team: str,
    ) -> dict[str, Any]:
        """List backlogs for a team."""
        url = f"{self.base_url}/{quote(project)}/{quote(team)}/_apis/work/backlogs"
        return await self._request("GET", url)

    async def get_backlog_work_items(
        self,
        project: str,
        team: str,
        backlog_id: str,
    ) -> dict[str, Any]:
        """Get work items in a backlog."""
        url = f"{self.base_url}/{quote(project)}/{quote(team)}/_apis/work/backlogs/{quote(backlog_id)}/workItems"
        return await self._request("GET", url)

    # ==================== Git Repositories API ====================

    async def list_repositories(
        self,
        project: str,
    ) -> dict[str, Any]:
        """List repositories in a project."""
        url = f"{self.base_url}/{quote(project)}/_apis/git/repositories"
        return await self._request("GET", url)

    async def get_repository(
        self,
        project: str,
        repository_id: str,
    ) -> dict[str, Any]:
        """Get a specific repository."""
        url = f"{self.base_url}/{quote(project)}/_apis/git/repositories/{quote(repository_id)}"
        return await self._request("GET", url)

    async def list_branches(
        self,
        project: str,
        repository_id: str,
        filter_contains: Optional[str] = None,
        top: Optional[int] = None,
    ) -> dict[str, Any]:
        """List branches in a repository."""
        params: dict[str, Any] = {}
        if filter_contains:
            params["filter"] = filter_contains
        if top:
            params["$top"] = top

        url = f"{self.base_url}/{quote(project)}/_apis/git/repositories/{quote(repository_id)}/refs"
        return await self._request("GET", url, params=params)

    async def create_branch(
        self,
        project: str,
        repository_id: str,
        name: str,
        source_ref: str,
    ) -> dict[str, Any]:
        """Create a new branch."""
        # First get the source commit
        refs_url = f"{self.base_url}/{quote(project)}/_apis/git/repositories/{quote(repository_id)}/refs"
        refs_params = {"filter": f"heads/{source_ref}"}
        source_refs = await self._request("GET", refs_url, params=refs_params)

        if not source_refs.get("value"):
            raise AzureDevOpsClientError(f"Source branch '{source_ref}' not found")

        source_object_id = source_refs["value"][0]["objectId"]

        # Create the new branch
        body = [
            {
                "name": f"refs/heads/{name}",
                "oldObjectId": "0000000000000000000000000000000000000000",
                "newObjectId": source_object_id,
            }
        ]
        return await self._request("POST", refs_url, json=body)

    async def list_commits(
        self,
        project: str,
        repository_id: str,
        branch: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        author: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> dict[str, Any]:
        """List commits in a repository."""
        params: dict[str, Any] = {}
        if branch:
            params["searchCriteria.itemVersion.version"] = branch
        if top:
            params["$top"] = top
        if skip:
            params["$skip"] = skip
        if author:
            params["searchCriteria.author"] = author
        if from_date:
            params["searchCriteria.fromDate"] = from_date
        if to_date:
            params["searchCriteria.toDate"] = to_date

        url = f"{self.base_url}/{quote(project)}/_apis/git/repositories/{quote(repository_id)}/commits"
        return await self._request("GET", url, params=params)

    async def get_commit(
        self,
        project: str,
        repository_id: str,
        commit_id: str,
    ) -> dict[str, Any]:
        """Get a specific commit."""
        url = f"{self.base_url}/{quote(project)}/_apis/git/repositories/{quote(repository_id)}/commits/{quote(commit_id)}"
        return await self._request("GET", url)

    # ==================== Pull Requests API ====================

    async def list_pull_requests(
        self,
        project: str,
        repository_id: str,
        status: str = "Active",
        top: Optional[int] = None,
        skip: Optional[int] = None,
        creator_id: Optional[str] = None,
        reviewer_id: Optional[str] = None,
        source_ref_name: Optional[str] = None,
        target_ref_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """List pull requests in a repository."""
        params: dict[str, Any] = {
            "searchCriteria.status": status,
        }
        if top:
            params["$top"] = top
        if skip:
            params["$skip"] = skip
        if creator_id:
            params["searchCriteria.creatorId"] = creator_id
        if reviewer_id:
            params["searchCriteria.reviewerId"] = reviewer_id
        if source_ref_name:
            params["searchCriteria.sourceRefName"] = source_ref_name
        if target_ref_name:
            params["searchCriteria.targetRefName"] = target_ref_name

        url = f"{self.base_url}/{quote(project)}/_apis/git/repositories/{quote(repository_id)}/pullrequests"
        return await self._request("GET", url, params=params)

    async def get_pull_request(
        self,
        project: str,
        repository_id: str,
        pull_request_id: int,
    ) -> dict[str, Any]:
        """Get a specific pull request."""
        url = f"{self.base_url}/{quote(project)}/_apis/git/repositories/{quote(repository_id)}/pullrequests/{pull_request_id}"
        return await self._request("GET", url)

    async def create_pull_request(
        self,
        project: str,
        repository_id: str,
        source_ref_name: str,
        target_ref_name: str,
        title: str,
        description: Optional[str] = None,
        is_draft: bool = False,
    ) -> dict[str, Any]:
        """Create a new pull request."""
        body = {
            "sourceRefName": source_ref_name,
            "targetRefName": target_ref_name,
            "title": title,
            "isDraft": is_draft,
        }
        if description:
            body["description"] = description

        url = f"{self.base_url}/{quote(project)}/_apis/git/repositories/{quote(repository_id)}/pullrequests"
        return await self._request("POST", url, json=body)

    async def update_pull_request(
        self,
        project: str,
        repository_id: str,
        pull_request_id: int,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a pull request."""
        url = f"{self.base_url}/{quote(project)}/_apis/git/repositories/{quote(repository_id)}/pullrequests/{pull_request_id}"
        return await self._request("PATCH", url, json=updates)

    async def list_pull_request_threads(
        self,
        project: str,
        repository_id: str,
        pull_request_id: int,
    ) -> dict[str, Any]:
        """List comment threads on a pull request."""
        url = f"{self.base_url}/{quote(project)}/_apis/git/repositories/{quote(repository_id)}/pullrequests/{pull_request_id}/threads"
        return await self._request("GET", url)

    async def create_pull_request_thread(
        self,
        project: str,
        repository_id: str,
        pull_request_id: int,
        content: str,
        status: str = "active",
        file_path: Optional[str] = None,
        line_number: Optional[int] = None,
    ) -> dict[str, Any]:
        """Create a comment thread on a pull request."""
        body: dict[str, Any] = {
            "comments": [{"parentCommentId": 0, "content": content, "commentType": 1}],
            "status": status,
        }

        if file_path:
            body["threadContext"] = {
                "filePath": file_path,
            }
            if line_number:
                body["threadContext"]["rightFileStart"] = {
                    "line": line_number,
                    "offset": 1,
                }
                body["threadContext"]["rightFileEnd"] = {
                    "line": line_number,
                    "offset": 1,
                }

        url = f"{self.base_url}/{quote(project)}/_apis/git/repositories/{quote(repository_id)}/pullrequests/{pull_request_id}/threads"
        return await self._request("POST", url, json=body)

    async def reply_to_thread(
        self,
        project: str,
        repository_id: str,
        pull_request_id: int,
        thread_id: int,
        content: str,
    ) -> dict[str, Any]:
        """Reply to a comment thread."""
        body = {"content": content, "commentType": 1}

        url = f"{self.base_url}/{quote(project)}/_apis/git/repositories/{quote(repository_id)}/pullrequests/{pull_request_id}/threads/{thread_id}/comments"
        return await self._request("POST", url, json=body)

    # ==================== Pipelines API ====================

    async def list_build_definitions(
        self,
        project: str,
        name: Optional[str] = None,
        path: Optional[str] = None,
        top: Optional[int] = None,
    ) -> dict[str, Any]:
        """List build definitions."""
        params: dict[str, Any] = {}
        if name:
            params["name"] = name
        if path:
            params["path"] = path
        if top:
            params["$top"] = top

        url = f"{self.base_url}/{quote(project)}/_apis/build/definitions"
        return await self._request("GET", url, params=params)

    async def get_build_definition(
        self,
        project: str,
        definition_id: int,
    ) -> dict[str, Any]:
        """Get a build definition."""
        url = f"{self.base_url}/{quote(project)}/_apis/build/definitions/{definition_id}"
        return await self._request("GET", url)

    async def list_builds(
        self,
        project: str,
        definitions: Optional[list[int]] = None,
        branch_name: Optional[str] = None,
        status: Optional[str] = None,
        result: Optional[str] = None,
        top: Optional[int] = None,
        requested_for: Optional[str] = None,
    ) -> dict[str, Any]:
        """List builds."""
        params: dict[str, Any] = {}
        if definitions:
            params["definitions"] = ",".join(str(d) for d in definitions)
        if branch_name:
            params["branchName"] = branch_name
        if status:
            params["statusFilter"] = status
        if result:
            params["resultFilter"] = result
        if top:
            params["$top"] = top
        if requested_for:
            params["requestedFor"] = requested_for

        url = f"{self.base_url}/{quote(project)}/_apis/build/builds"
        return await self._request("GET", url, params=params)

    async def get_build(
        self,
        project: str,
        build_id: int,
    ) -> dict[str, Any]:
        """Get a specific build."""
        url = f"{self.base_url}/{quote(project)}/_apis/build/builds/{build_id}"
        return await self._request("GET", url)

    async def queue_build(
        self,
        project: str,
        definition_id: int,
        source_branch: Optional[str] = None,
        parameters: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Queue a new build."""
        body: dict[str, Any] = {
            "definition": {"id": definition_id},
        }
        if source_branch:
            body["sourceBranch"] = source_branch
        if parameters:
            import json

            body["parameters"] = json.dumps(parameters)

        url = f"{self.base_url}/{quote(project)}/_apis/build/builds"
        return await self._request("POST", url, json=body)

    async def get_build_logs(
        self,
        project: str,
        build_id: int,
    ) -> dict[str, Any]:
        """Get build logs list."""
        url = f"{self.base_url}/{quote(project)}/_apis/build/builds/{build_id}/logs"
        return await self._request("GET", url)

    async def get_build_log(
        self,
        project: str,
        build_id: int,
        log_id: int,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> str:
        """Get a specific build log content."""
        params: dict[str, Any] = {}
        if start_line:
            params["startLine"] = start_line
        if end_line:
            params["endLine"] = end_line

        url = f"{self.base_url}/{quote(project)}/_apis/build/builds/{build_id}/logs/{log_id}"
        client = await self._get_client()
        auth_headers = await self.auth.get_headers_async()

        response = await client.get(
            url,
            params={**params, "api-version": self.API_VERSION},
            headers=auth_headers,
        )
        response.raise_for_status()
        return response.text

    async def list_pipeline_runs(
        self,
        project: str,
        pipeline_id: int,
    ) -> dict[str, Any]:
        """List pipeline runs."""
        url = f"{self.base_url}/{quote(project)}/_apis/pipelines/{pipeline_id}/runs"
        return await self._request("GET", url)

    async def run_pipeline(
        self,
        project: str,
        pipeline_id: int,
        branch: Optional[str] = None,
        variables: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Run a pipeline."""
        body: dict[str, Any] = {}
        if branch:
            body["resources"] = {
                "repositories": {"self": {"refName": f"refs/heads/{branch}"}}
            }
        if variables:
            body["variables"] = {k: {"value": v} for k, v in variables.items()}

        url = f"{self.base_url}/{quote(project)}/_apis/pipelines/{pipeline_id}/runs"
        return await self._request("POST", url, json=body)

    # ==================== Wiki API ====================

    async def list_wikis(
        self,
        project: Optional[str] = None,
    ) -> dict[str, Any]:
        """List wikis."""
        if project:
            url = f"{self.base_url}/{quote(project)}/_apis/wiki/wikis"
        else:
            url = f"{self.base_url}/_apis/wiki/wikis"
        return await self._request("GET", url)

    async def get_wiki(
        self,
        project: str,
        wiki_identifier: str,
    ) -> dict[str, Any]:
        """Get a specific wiki."""
        url = f"{self.base_url}/{quote(project)}/_apis/wiki/wikis/{quote(wiki_identifier)}"
        return await self._request("GET", url)

    async def list_wiki_pages(
        self,
        project: str,
        wiki_identifier: str,
        path: Optional[str] = None,
        recursion_level: str = "oneLevel",
    ) -> dict[str, Any]:
        """List wiki pages."""
        params: dict[str, Any] = {
            "recursionLevel": recursion_level,
        }
        if path:
            params["path"] = path

        url = f"{self.base_url}/{quote(project)}/_apis/wiki/wikis/{quote(wiki_identifier)}/pages"
        return await self._request("GET", url, params=params)

    async def get_wiki_page(
        self,
        project: str,
        wiki_identifier: str,
        path: str,
        include_content: bool = True,
    ) -> dict[str, Any]:
        """Get a wiki page."""
        params: dict[str, Any] = {
            "path": path,
            "includeContent": include_content,
        }

        url = f"{self.base_url}/{quote(project)}/_apis/wiki/wikis/{quote(wiki_identifier)}/pages"
        return await self._request("GET", url, params=params)

    async def create_or_update_wiki_page(
        self,
        project: str,
        wiki_identifier: str,
        path: str,
        content: str,
        version: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create or update a wiki page."""
        params: dict[str, Any] = {"path": path}
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if version:
            headers["If-Match"] = version

        url = f"{self.base_url}/{quote(project)}/_apis/wiki/wikis/{quote(wiki_identifier)}/pages"
        return await self._request(
            "PUT",
            url,
            params=params,
            json={"content": content},
            headers=headers,
        )

    # ==================== Test Plans API ====================

    async def list_test_plans(
        self,
        project: str,
        filter_active: bool = True,
    ) -> dict[str, Any]:
        """List test plans."""
        params: dict[str, Any] = {}
        if filter_active:
            params["filterActivePlans"] = "true"

        url = f"{self.base_url}/{quote(project)}/_apis/testplan/plans"
        return await self._request("GET", url, params=params)

    async def get_test_plan(
        self,
        project: str,
        plan_id: int,
    ) -> dict[str, Any]:
        """Get a test plan."""
        url = f"{self.base_url}/{quote(project)}/_apis/testplan/plans/{plan_id}"
        return await self._request("GET", url)

    async def create_test_plan(
        self,
        project: str,
        name: str,
        area_path: Optional[str] = None,
        iteration: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a test plan."""
        body: dict[str, Any] = {"name": name}
        if area_path:
            body["areaPath"] = area_path
        if iteration:
            body["iteration"] = iteration
        if start_date:
            body["startDate"] = start_date
        if end_date:
            body["endDate"] = end_date

        url = f"{self.base_url}/{quote(project)}/_apis/testplan/plans"
        return await self._request("POST", url, json=body)

    async def list_test_suites(
        self,
        project: str,
        plan_id: int,
    ) -> dict[str, Any]:
        """List test suites in a plan."""
        url = f"{self.base_url}/{quote(project)}/_apis/testplan/Plans/{plan_id}/suites"
        return await self._request("GET", url)

    async def create_test_suite(
        self,
        project: str,
        plan_id: int,
        name: str,
        parent_suite_id: int,
    ) -> dict[str, Any]:
        """Create a test suite."""
        body = {"name": name, "parentSuite": {"id": parent_suite_id}}
        url = f"{self.base_url}/{quote(project)}/_apis/testplan/Plans/{plan_id}/suites"
        return await self._request("POST", url, json=body)

    async def list_test_cases(
        self,
        project: str,
        plan_id: int,
        suite_id: int,
    ) -> dict[str, Any]:
        """List test cases in a suite."""
        url = f"{self.base_url}/{quote(project)}/_apis/testplan/Plans/{plan_id}/Suites/{suite_id}/TestCase"
        return await self._request("GET", url)

    async def add_test_cases_to_suite(
        self,
        project: str,
        plan_id: int,
        suite_id: int,
        test_case_ids: list[int],
    ) -> dict[str, Any]:
        """Add test cases to a suite."""
        body = [{"workItem": {"id": tc_id}} for tc_id in test_case_ids]
        url = f"{self.base_url}/{quote(project)}/_apis/testplan/Plans/{plan_id}/Suites/{suite_id}/TestCase"
        return await self._request("POST", url, json=body)

    async def get_test_results(
        self,
        project: str,
        run_id: int,
    ) -> dict[str, Any]:
        """Get test results for a run."""
        url = f"{self.base_url}/{quote(project)}/_apis/test/Runs/{run_id}/results"
        return await self._request("GET", url)

    # ==================== Search API ====================

    async def search_code(
        self,
        search_text: str,
        project: Optional[str] = None,
        repository: Optional[str] = None,
        path: Optional[str] = None,
        branch: Optional[str] = None,
        top: int = 25,
        skip: int = 0,
    ) -> dict[str, Any]:
        """Search code across repositories."""
        filters: dict[str, list[str]] = {}
        if project:
            filters["Project"] = [project]
        if repository:
            filters["Repository"] = [repository]
        if path:
            filters["Path"] = [path]
        if branch:
            filters["Branch"] = [branch]

        body = {
            "searchText": search_text,
            "$top": top,
            "$skip": skip,
            "filters": filters,
        }

        url = f"{self.search_url}/_apis/search/codesearchresults"
        return await self._request("POST", url, json=body)

    # ==================== Iterations API ====================

    async def list_iterations(
        self,
        project: str,
        team: str,
        timeframe: Optional[str] = None,
    ) -> dict[str, Any]:
        """List iterations for a team."""
        params: dict[str, Any] = {}
        if timeframe:
            params["$timeframe"] = timeframe

        url = f"{self.base_url}/{quote(project)}/{quote(team)}/_apis/work/teamsettings/iterations"
        return await self._request("GET", url, params=params)

    async def get_iteration(
        self,
        project: str,
        team: str,
        iteration_id: str,
    ) -> dict[str, Any]:
        """Get a specific iteration."""
        url = f"{self.base_url}/{quote(project)}/{quote(team)}/_apis/work/teamsettings/iterations/{quote(iteration_id)}"
        return await self._request("GET", url)

