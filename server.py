#!/usr/bin/env python3
"""Azure DevOps MCP Server - Interact with Azure DevOps from AI assistants.

Uses Arcade secrets as fallback when environment variables are not set.
Configure secrets in Arcade Dashboard or .env file:
- AZURE_DEVOPS_ORG: Your Azure DevOps organization name
- AZURE_DEVOPS_PAT: Personal Access Token
"""

# Windows compatibility shim - must be imported BEFORE arcade_mcp_server
import os
import sys

if os.name == "nt":  # Windows
    import types
    fcntl_module = types.ModuleType("fcntl")
    fcntl_module.LOCK_SH = 1
    fcntl_module.LOCK_EX = 2
    fcntl_module.LOCK_UN = 8
    fcntl_module.LOCK_NB = 4
    fcntl_module.flock = lambda fd, op: None
    fcntl_module.fcntl = lambda fd, cmd, arg=0: 0
    fcntl_module.ioctl = lambda fd, req, arg=0, mut=True: 0
    sys.modules["fcntl"] = fcntl_module

from typing import Annotated, Any, Optional

from arcade_mcp_server import MCPApp, Context

from arcade_azure_devops_mcp.client import AzureDevOpsClient, AzureDevOpsClientError
from arcade_azure_devops_mcp.auth.manager import AuthManager


class AsyncMCPApp(MCPApp):
    """MCPApp subclass with run_async support for FastMCP compatibility."""

    async def run_async(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        reload: bool = False,
        transport: str = "stdio",
        **kwargs: Any,
    ) -> None:
        """Run the server asynchronously."""
        if len(self._catalog) == 0:
            import sys
            from loguru import logger
            logger.error("No tools added to the server. Use @app.tool decorator or app.add_tool().")
            sys.exit(1)

        # Apply configuration overrides (env vars)
        host, port, transport, reload = self._get_configuration_overrides(
            host, port, transport, reload
        )

        self._setup_logging(transport == "stdio")

        import os
        from loguru import logger
        from arcade_mcp_server.exceptions import ServerError
        
        if os.getenv("ARCADE_MCP_CHILD_PROCESS") == "1":
            reload = False

        logger.info(f"Starting {self._name} v{self.version} with {len(self._catalog)} tools")

        # Monkey-patch HTTPStreamableTransport to be permissive
        from arcade_mcp_server.transports.http_streamable import HTTPStreamableTransport
        from starlette.requests import Request
        from starlette.types import Send
        
        # Patch headers check
        def permissive_check_headers(self, request):
            return True, True
        HTTPStreamableTransport._check_accept_headers = permissive_check_headers

        # Patch content type check
        def permissive_check_content_type(self, request):
            return True
        HTTPStreamableTransport._check_content_type = permissive_check_content_type

        # Patch session validation to allow ALL requests to proceed without session ID
        async def permissive_validate_session(self, request: Request, send: Send) -> bool:
            return True
        HTTPStreamableTransport._validate_session = permissive_validate_session
        
        # Patch middleware to disable slash adding which confuses routing for new endpoints
        from arcade_mcp_server.fastapi.middleware import AddTrailingSlashToPathMiddleware
        async def noop_dispatch(self, request, call_next):
             return await call_next(request)
        AddTrailingSlashToPathMiddleware.dispatch = noop_dispatch

        # Patch HTTPSessionManager to handle stale session IDs gracefully
        from arcade_mcp_server.transports.http_session_manager import HTTPSessionManager
        from arcade_mcp_server.transports.http_streamable import MCP_SESSION_ID_HEADER
        from uuid import uuid4
        from anyio.abc import TaskStatus
        import anyio
        from arcade_mcp_server.session import ServerSession
        from starlette.types import Scope, Receive, Send

        async def permissive_handle_stateful_request(self, scope: Scope, receive: Receive, send: Send) -> None:
            """Process request in stateful mode - maintain session state.
            Patched to treat unknown session IDs as new sessions instead of 400s.
            """
            request = Request(scope, receive)
            request_mcp_session_id = request.headers.get(MCP_SESSION_ID_HEADER)

            # Existing session case
            if request_mcp_session_id and request_mcp_session_id in self._server_instances:
                transport = self._server_instances[request_mcp_session_id]
                logger.debug("Session already exists, handling request directly")
                await transport.handle_request(scope, receive, send)
                return

            # New session case (ID is None OR ID is unknown/stale)
            # Original code would return 400 for unknown ID. We proceed to create new.
            
            if request_mcp_session_id and request_mcp_session_id not in self._server_instances:
                logger.warning(f"Client sent unknown session ID {request_mcp_session_id}. Creating new session.")
                # We don't need to unset the header because we just proceed to create a new transport
                # and ignore the incoming ID for the purpose of lookup.
                # However, the Transport itself might check the header against its own ID?
                # HTTPStreamableTransport._validate_session checks if header matches self.mcp_session_id.
                # If we create a NEW transport with a NEW ID, but the request still has the OLD ID header,
                # _validate_session will fail (return 404 or 400).
                
                # So we MUST remove the header from the request/scope before passing it to the new transport.
                # Modifying 'scope' headers is tricky but possible.
                
                # Actually, simpler: We patched _validate_session in HTTPStreamableTransport to always return True!
                # So the mismatch won't matter there.
                pass

            logger.debug("Creating new transport")
            async with self._session_creation_lock:
                new_session_id = uuid4().hex
                http_transport = HTTPStreamableTransport(
                    mcp_session_id=new_session_id,
                    is_json_response_enabled=self.json_response,
                    event_store=self.event_store,
                )

                if http_transport.mcp_session_id is None:
                    raise RuntimeError("MCP session ID not set")
                self._server_instances[http_transport.mcp_session_id] = http_transport
                logger.info(f"Created new transport with session ID: {new_session_id}")

                # Define the server runner
                async def run_server(
                    *, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED
                ) -> None:
                    async with http_transport.connect() as streams:
                        read_stream, write_stream = streams
                        task_status.started()
                        try:
                            # Create a session for this connection
                            session = ServerSession(
                                server=self.server,
                                read_stream=read_stream,
                                write_stream=write_stream,
                                init_options={"transport_type": "http"},
                            )

                            # Set the session on the transport
                            http_transport.session = session

                            # Run the session (start + loop until closed)
                            await session.run()

                            # Brief yield to allow cleanup
                            await anyio.sleep(0)
                        except Exception as e:
                            logger.error(
                                f"Session {http_transport.mcp_session_id} crashed: {e}",
                                exc_info=True,
                            )
                        finally:
                            # Clean up on crash
                            if (
                                http_transport.mcp_session_id
                                and http_transport.mcp_session_id in self._server_instances
                                and not http_transport.is_terminated
                            ):
                                logger.info(
                                    f"Cleaning up crashed session {http_transport.mcp_session_id}"
                                )
                                del self._server_instances[http_transport.mcp_session_id]

                if self._task_group is None:
                    raise RuntimeError("Task group not initialized")
                await self._task_group.start(run_server)

                # Handle the HTTP request
                await http_transport.handle_request(scope, receive, send)

        HTTPSessionManager._handle_stateful_request = permissive_handle_stateful_request

        if transport in ["http", "streamable-http", "streamable"]:
            if reload:
                # Reloading is typically synchronous/blocking for the watcher
                self._run_with_reload(host, port)
            else:
                debug = self.log_level == "DEBUG"
                log_level = "debug" if debug else "info"
                
                from arcade_mcp_server.worker import create_arcade_mcp, serve_with_force_quit
                from arcade_mcp_server.usage import ServerTracker
                from starlette.responses import Response
                from starlette.types import Scope, Receive, Send

                app_instance = create_arcade_mcp(
                    catalog=self._catalog,
                    mcp_settings=self._mcp_settings,
                    debug=debug,
                    **self.server_kwargs,
                )

                # Define a proxy class
                class MCPASGIProxy:
                    def __init__(self, parent_app):
                        self._app = parent_app

                    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
                        session_manager = getattr(self._app.state, "session_manager", None)
                        if session_manager is None:
                            resp = Response("MCP server not initialized", status_code=503)
                            await resp(scope, receive, send)
                            return
                        await session_manager.handle_request(scope, receive, send)

                # Mount the proxy at /sse and /messages
                proxy = MCPASGIProxy(app_instance)
                
                # Use mount which matches path prefixes and is robust
                app_instance.mount("/sse", proxy)
                app_instance.mount("/messages", proxy)
                
                # Add root route for health checks
                @app_instance.get("/")
                async def root():
                    return {"status": "ok", "service": "azure-devops-mcp"}

                # print(f"App instance type: {type(app_instance)}")
                # for route in app_instance.routes:
                #     print(f"Registered route: {route.path}")

                tracker = ServerTracker()
                tracker.track_server_start(
                    transport="http",
                    host=host,
                    port=port,
                    tool_count=len(self._catalog),
                )

                await serve_with_force_quit(
                    app=app_instance,
                    host=host,
                    port=port,
                    log_level=log_level,
                )
                
        elif transport == "stdio":
            from arcade_mcp_server.__main__ import run_stdio_server
            from arcade_mcp_server.usage import ServerTracker

            tracker = ServerTracker()
            tracker.track_server_start(
                transport="stdio",
                host=None,
                port=None,
                tool_count=len(self._catalog),
            )
            await run_stdio_server(
                catalog=self._catalog,
                settings=self._mcp_settings,
                **self.server_kwargs,
            )
        else:
            raise ServerError(f"Invalid transport: {transport}")


# Create the MCP App
app = AsyncMCPApp(name="azure_devops", version="0.1.0", log_level="INFO")

# Secrets required for Azure DevOps authentication
AZURE_SECRETS = ["AZURE_DEVOPS_ORG", "AZURE_DEVOPS_PAT"]


def _get_client(context: Optional[Context] = None) -> AzureDevOpsClient:
    """Get an authenticated Azure DevOps client.
    
    Args:
        context: Optional Arcade context for secrets fallback
    """
    auth = AuthManager(context=context)
    return AzureDevOpsClient(auth)


# ==================== Core Tools ====================


@app.tool(requires_secrets=AZURE_SECRETS)
async def list_projects(
    context: Context,
    state_filter: Annotated[str, "Filter projects by state: wellFormed, createPending, deleted, all"] = "wellFormed",
    top: Annotated[Optional[int], "Maximum number of projects to return"] = None,
    skip: Annotated[Optional[int], "Number of projects to skip for pagination"] = None,
) -> Annotated[dict[str, Any], "List of projects in the organization"]:
    """List all projects in the Azure DevOps organization."""
    client = _get_client(context)
    try:
        return await client.list_projects(state_filter=state_filter, top=top, skip=skip)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to list projects: {e}") from e
    finally:
        await client.close()


@app.tool(requires_secrets=AZURE_SECRETS)
async def get_project(
    context: Context,
    project: Annotated[str, "Project name or ID"],
) -> Annotated[dict[str, Any], "Project details"]:
    """Get details of a specific Azure DevOps project."""
    client = _get_client(context)
    try:
        return await client.get_project(project)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to get project '{project}': {e}") from e
    finally:
        await client.close()


@app.tool(requires_secrets=AZURE_SECRETS)
async def list_teams(
    context: Context,
    project: Annotated[str, "Project name or ID"],
    top: Annotated[Optional[int], "Maximum number of teams to return"] = None,
    skip: Annotated[Optional[int], "Number of teams to skip for pagination"] = None,
) -> Annotated[dict[str, Any], "List of teams in the project"]:
    """List all teams in a project."""
    client = _get_client(context)
    try:
        return await client.list_teams(project=project, top=top, skip=skip)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to list teams: {e}") from e
    finally:
        await client.close()


@app.tool(requires_secrets=AZURE_SECRETS)
async def search_identities(
    context: Context,
    search_filter: Annotated[str, "Filter type: General, AccountName, DisplayName, MailAddress"],
    filter_value: Annotated[str, "Value to search for"],
) -> Annotated[dict[str, Any], "List of matching identities"]:
    """Search for identities (users/groups) in Azure DevOps."""
    client = _get_client(context)
    try:
        return await client.get_identities(search_filter=search_filter, filter_value=filter_value)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to search identities: {e}") from e
    finally:
        await client.close()


# ==================== Work Item Tools ====================


@app.tool(requires_secrets=AZURE_SECRETS)
async def get_work_item(
    context: Context,
    project: Annotated[str, "Project name or ID"],
    work_item_id: Annotated[int, "Work item ID"],
    expand: Annotated[Optional[str], "Expand options: None, Relations, Fields, Links, All"] = None,
) -> Annotated[dict[str, Any], "Work item details"]:
    """Get a work item by ID."""
    client = _get_client(context)
    try:
        return await client.get_work_item(project=project, work_item_id=work_item_id, expand=expand)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to get work item {work_item_id}: {e}") from e
    finally:
        await client.close()


@app.tool(requires_secrets=AZURE_SECRETS)
async def create_work_item(
    context: Context,
    project: Annotated[str, "Project name or ID"],
    work_item_type: Annotated[str, "Work item type (e.g., Task, Bug, User Story)"],
    title: Annotated[str, "Title of the work item"],
    description: Annotated[Optional[str], "Description/details of the work item"] = None,
    assigned_to: Annotated[Optional[str], "User to assign the work item to"] = None,
    area_path: Annotated[Optional[str], "Area path for the work item"] = None,
    iteration_path: Annotated[Optional[str], "Iteration path for the work item"] = None,
    state: Annotated[Optional[str], "Initial state of the work item"] = None,
    priority: Annotated[Optional[int], "Priority (1-4, where 1 is highest)"] = None,
) -> Annotated[dict[str, Any], "Created work item"]:
    """Create a new work item."""
    client = _get_client(context)
    try:
        document: list[dict[str, Any]] = [
            {"op": "add", "path": "/fields/System.Title", "value": title}
        ]
        if description:
            document.append({"op": "add", "path": "/fields/System.Description", "value": description})
        if assigned_to:
            document.append({"op": "add", "path": "/fields/System.AssignedTo", "value": assigned_to})
        if area_path:
            document.append({"op": "add", "path": "/fields/System.AreaPath", "value": area_path})
        if iteration_path:
            document.append({"op": "add", "path": "/fields/System.IterationPath", "value": iteration_path})
        if state:
            document.append({"op": "add", "path": "/fields/System.State", "value": state})
        if priority:
            document.append({"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority", "value": priority})

        return await client.create_work_item(project=project, work_item_type=work_item_type, document=document)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to create work item: {e}") from e
    finally:
        await client.close()


@app.tool(requires_secrets=AZURE_SECRETS)
async def update_work_item(
    context: Context,
    project: Annotated[str, "Project name or ID"],
    work_item_id: Annotated[int, "Work item ID to update"],
    title: Annotated[Optional[str], "New title"] = None,
    description: Annotated[Optional[str], "New description"] = None,
    assigned_to: Annotated[Optional[str], "New assignee"] = None,
    state: Annotated[Optional[str], "New state"] = None,
    priority: Annotated[Optional[int], "New priority"] = None,
) -> Annotated[dict[str, Any], "Updated work item"]:
    """Update an existing work item."""
    client = _get_client(context)
    try:
        document: list[dict[str, Any]] = []
        if title:
            document.append({"op": "add", "path": "/fields/System.Title", "value": title})
        if description:
            document.append({"op": "add", "path": "/fields/System.Description", "value": description})
        if assigned_to:
            document.append({"op": "add", "path": "/fields/System.AssignedTo", "value": assigned_to})
        if state:
            document.append({"op": "add", "path": "/fields/System.State", "value": state})
        if priority:
            document.append({"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority", "value": priority})

        if not document:
            raise RuntimeError("No fields provided to update")

        return await client.update_work_item(project=project, work_item_id=work_item_id, document=document)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to update work item {work_item_id}: {e}") from e
    finally:
        await client.close()


@app.tool(requires_secrets=AZURE_SECRETS)
async def run_work_item_query(
    context: Context,
    project: Annotated[str, "Project name or ID"],
    query: Annotated[str, "WIQL query string"],
    top: Annotated[Optional[int], "Maximum number of results"] = None,
) -> Annotated[dict[str, Any], "Query results"]:
    """Run a WIQL (Work Item Query Language) query."""
    client = _get_client(context)
    try:
        return await client.run_wiql_query(project=project, query=query, top=top)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to run query: {e}") from e
    finally:
        await client.close()


@app.tool(requires_secrets=AZURE_SECRETS)
async def my_work_items(
    context: Context,
    project: Annotated[str, "Project name or ID"],
    include_completed: Annotated[bool, "Include completed work items"] = False,
    top: Annotated[int, "Maximum number of work items to return"] = 50,
) -> Annotated[dict[str, Any], "Work items assigned to current user"]:
    """Get work items assigned to the current user."""
    client = _get_client(context)
    try:
        state_filter = ""
        if not include_completed:
            state_filter = "AND [System.State] <> 'Closed' AND [System.State] <> 'Done' AND [System.State] <> 'Removed'"

        query = f"""
        SELECT [System.Id], [System.Title], [System.State], [System.WorkItemType]
        FROM WorkItems
        WHERE [System.AssignedTo] = @Me {state_filter}
        ORDER BY [System.ChangedDate] DESC
        """
        return await client.run_wiql_query(project=project, query=query, top=top)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to get my work items: {e}") from e
    finally:
        await client.close()


@app.tool(requires_secrets=AZURE_SECRETS)
async def add_work_item_comment(
    context: Context,
    project: Annotated[str, "Project name or ID"],
    work_item_id: Annotated[int, "Work item ID"],
    text: Annotated[str, "Comment text (supports HTML)"],
) -> Annotated[dict[str, Any], "Created comment"]:
    """Add a comment to a work item."""
    client = _get_client(context)
    try:
        return await client.add_work_item_comment(project=project, work_item_id=work_item_id, text=text)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to add comment: {e}") from e
    finally:
        await client.close()


# ==================== Repository Tools ====================


@app.tool(requires_secrets=AZURE_SECRETS)
async def list_repositories(
    context: Context,
    project: Annotated[str, "Project name or ID"],
) -> Annotated[dict[str, Any], "List of repositories"]:
    """List all Git repositories in a project."""
    client = _get_client(context)
    try:
        return await client.list_repositories(project=project)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to list repositories: {e}") from e
    finally:
        await client.close()


@app.tool(requires_secrets=AZURE_SECRETS)
async def list_branches(
    context: Context,
    project: Annotated[str, "Project name or ID"],
    repository_id: Annotated[str, "Repository name or ID"],
    filter_contains: Annotated[Optional[str], "Filter branches containing this string"] = None,
    top: Annotated[Optional[int], "Maximum number of branches to return"] = None,
) -> Annotated[dict[str, Any], "List of branches"]:
    """List branches in a repository."""
    client = _get_client(context)
    try:
        return await client.list_branches(project=project, repository_id=repository_id, filter_contains=filter_contains, top=top)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to list branches: {e}") from e
    finally:
        await client.close()


@app.tool(requires_secrets=AZURE_SECRETS)
async def list_pull_requests(
    context: Context,
    project: Annotated[str, "Project name or ID"],
    repository_id: Annotated[str, "Repository name or ID"],
    status: Annotated[str, "Filter by status: Active, Abandoned, Completed, All"] = "Active",
    top: Annotated[int, "Maximum number of PRs to return"] = 50,
) -> Annotated[dict[str, Any], "List of pull requests"]:
    """List pull requests in a repository."""
    client = _get_client(context)
    try:
        return await client.list_pull_requests(project=project, repository_id=repository_id, status=status, top=top)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to list pull requests: {e}") from e
    finally:
        await client.close()


@app.tool(requires_secrets=AZURE_SECRETS)
async def create_pull_request(
    context: Context,
    project: Annotated[str, "Project name or ID"],
    repository_id: Annotated[str, "Repository name or ID"],
    source_ref_name: Annotated[str, "Source branch (e.g., 'refs/heads/feature')"],
    target_ref_name: Annotated[str, "Target branch (e.g., 'refs/heads/main')"],
    title: Annotated[str, "Pull request title"],
    description: Annotated[Optional[str], "Pull request description"] = None,
    is_draft: Annotated[bool, "Create as draft PR"] = False,
) -> Annotated[dict[str, Any], "Created pull request"]:
    """Create a new pull request."""
    client = _get_client(context)
    try:
        return await client.create_pull_request(
            project=project, repository_id=repository_id,
            source_ref_name=source_ref_name, target_ref_name=target_ref_name,
            title=title, description=description, is_draft=is_draft
        )
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to create pull request: {e}") from e
    finally:
        await client.close()


# ==================== Pipeline Tools ====================


@app.tool(requires_secrets=AZURE_SECRETS)
async def list_build_definitions(
    context: Context,
    project: Annotated[str, "Project name or ID"],
    name: Annotated[Optional[str], "Filter by definition name"] = None,
    top: Annotated[int, "Maximum number of definitions to return"] = 50,
) -> Annotated[dict[str, Any], "List of build definitions"]:
    """List build/pipeline definitions in a project."""
    client = _get_client(context)
    try:
        return await client.list_build_definitions(project=project, name=name, top=top)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to list build definitions: {e}") from e
    finally:
        await client.close()


@app.tool(requires_secrets=AZURE_SECRETS)
async def list_builds(
    context: Context,
    project: Annotated[str, "Project name or ID"],
    status: Annotated[Optional[str], "Filter by status: all, completed, inProgress, notStarted"] = None,
    result: Annotated[Optional[str], "Filter by result: canceled, failed, succeeded"] = None,
    top: Annotated[int, "Maximum number of builds to return"] = 50,
) -> Annotated[dict[str, Any], "List of builds"]:
    """List builds in a project."""
    client = _get_client(context)
    try:
        return await client.list_builds(project=project, status=status, result=result, top=top)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to list builds: {e}") from e
    finally:
        await client.close()


@app.tool(requires_secrets=AZURE_SECRETS)
async def queue_build(
    context: Context,
    project: Annotated[str, "Project name or ID"],
    definition_id: Annotated[int, "Build definition ID to queue"],
    source_branch: Annotated[Optional[str], "Branch to build (e.g., 'refs/heads/main')"] = None,
) -> Annotated[dict[str, Any], "Queued build"]:
    """Queue a new build."""
    client = _get_client(context)
    try:
        return await client.queue_build(project=project, definition_id=definition_id, source_branch=source_branch)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to queue build: {e}") from e
    finally:
        await client.close()


@app.tool(requires_secrets=AZURE_SECRETS)
async def run_pipeline(
    context: Context,
    project: Annotated[str, "Project name or ID"],
    pipeline_id: Annotated[int, "Pipeline ID"],
    branch: Annotated[Optional[str], "Branch to run on (e.g., 'main')"] = None,
) -> Annotated[dict[str, Any], "Started pipeline run"]:
    """Start a new pipeline run."""
    client = _get_client(context)
    try:
        return await client.run_pipeline(project=project, pipeline_id=pipeline_id, branch=branch)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to run pipeline: {e}") from e
    finally:
        await client.close()


# ==================== Wiki Tools ====================


@app.tool(requires_secrets=AZURE_SECRETS)
async def list_wikis(
    context: Context,
    project: Annotated[Optional[str], "Project name or ID (optional)"] = None,
) -> Annotated[dict[str, Any], "List of wikis"]:
    """List wikis in a project or organization."""
    client = _get_client(context)
    try:
        return await client.list_wikis(project=project)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to list wikis: {e}") from e
    finally:
        await client.close()


@app.tool(requires_secrets=AZURE_SECRETS)
async def get_wiki_page(
    context: Context,
    project: Annotated[str, "Project name or ID"],
    wiki_identifier: Annotated[str, "Wiki name or ID"],
    path: Annotated[str, "Page path (e.g., '/Home')"],
    include_content: Annotated[bool, "Include page content in response"] = True,
) -> Annotated[dict[str, Any], "Wiki page details and content"]:
    """Get a specific wiki page."""
    client = _get_client(context)
    try:
        return await client.get_wiki_page(project=project, wiki_identifier=wiki_identifier, path=path, include_content=include_content)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to get wiki page: {e}") from e
    finally:
        await client.close()


# ==================== Search Tools ====================


@app.tool(requires_secrets=AZURE_SECRETS)
async def search_code(
    context: Context,
    search_text: Annotated[str, "Text to search for in code"],
    project: Annotated[Optional[str], "Filter by project name"] = None,
    repository: Annotated[Optional[str], "Filter by repository name"] = None,
    top: Annotated[int, "Maximum number of results to return"] = 25,
) -> Annotated[dict[str, Any], "Code search results"]:
    """Search for code across repositories."""
    client = _get_client(context)
    try:
        return await client.search_code(search_text=search_text, project=project, repository=repository, top=top)
    except AzureDevOpsClientError as e:
        raise RuntimeError(f"Failed to search code: {e}") from e
    finally:
        await client.close()


# ==================== Entry Point ====================

if __name__ == "__main__":
    # Get transport from command line argument, default to "stdio"
    # - "stdio" (default): Standard I/O for Claude Desktop, CLI tools, etc.
    #   Supports requires_secrets out-of-the-box
    # - "http": HTTPS streaming for Cursor, VS Code, etc.
    #   Requires Arcade Deploy or env vars for secrets
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"

    # Run the server
    import asyncio
    asyncio.run(app.run_async(transport=transport, host="127.0.0.1", port=8000))
