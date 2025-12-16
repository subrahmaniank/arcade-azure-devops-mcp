"""Pydantic models for Azure DevOps API responses."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# Core Models
class TeamProjectReference(BaseModel):
    """Reference to a team project."""

    id: str
    name: str
    description: Optional[str] = None
    url: Optional[str] = None
    state: Optional[str] = None
    visibility: Optional[str] = None


class WebApiTeam(BaseModel):
    """Team information."""

    id: str
    name: str
    description: Optional[str] = None
    url: Optional[str] = None
    project_name: Optional[str] = Field(None, alias="projectName")
    project_id: Optional[str] = Field(None, alias="projectId")


class IdentityRef(BaseModel):
    """Identity reference."""

    id: str
    display_name: Optional[str] = Field(None, alias="displayName")
    unique_name: Optional[str] = Field(None, alias="uniqueName")
    url: Optional[str] = None
    image_url: Optional[str] = Field(None, alias="imageUrl")


# Work Item Models
class WorkItemReference(BaseModel):
    """Reference to a work item."""

    id: int
    url: Optional[str] = None


class WorkItem(BaseModel):
    """Work item details."""

    id: int
    rev: Optional[int] = None
    fields: dict[str, Any] = Field(default_factory=dict)
    relations: Optional[list[dict[str, Any]]] = None
    url: Optional[str] = None


class WorkItemComment(BaseModel):
    """Work item comment."""

    id: int
    text: str
    created_by: Optional[IdentityRef] = Field(None, alias="createdBy")
    created_date: Optional[datetime] = Field(None, alias="createdDate")


# Repository Models
class GitRepository(BaseModel):
    """Git repository information."""

    id: str
    name: str
    url: Optional[str] = None
    project: Optional[TeamProjectReference] = None
    default_branch: Optional[str] = Field(None, alias="defaultBranch")
    size: Optional[int] = None
    remote_url: Optional[str] = Field(None, alias="remoteUrl")
    ssh_url: Optional[str] = Field(None, alias="sshUrl")
    web_url: Optional[str] = Field(None, alias="webUrl")


class GitRef(BaseModel):
    """Git reference (branch/tag)."""

    name: str
    object_id: Optional[str] = Field(None, alias="objectId")
    creator: Optional[IdentityRef] = None
    url: Optional[str] = None


class GitCommitRef(BaseModel):
    """Git commit reference."""

    commit_id: str = Field(alias="commitId")
    author: Optional[dict[str, Any]] = None
    committer: Optional[dict[str, Any]] = None
    comment: Optional[str] = None
    url: Optional[str] = None


class GitPullRequest(BaseModel):
    """Pull request information."""

    pull_request_id: int = Field(alias="pullRequestId")
    repository: Optional[GitRepository] = None
    status: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    source_ref_name: Optional[str] = Field(None, alias="sourceRefName")
    target_ref_name: Optional[str] = Field(None, alias="targetRefName")
    created_by: Optional[IdentityRef] = Field(None, alias="createdBy")
    creation_date: Optional[datetime] = Field(None, alias="creationDate")
    merge_status: Optional[str] = Field(None, alias="mergeStatus")
    is_draft: Optional[bool] = Field(None, alias="isDraft")
    url: Optional[str] = None


class CommentThread(BaseModel):
    """Pull request comment thread."""

    id: int
    status: Optional[str] = None
    comments: Optional[list[dict[str, Any]]] = None
    thread_context: Optional[dict[str, Any]] = Field(None, alias="threadContext")
    is_deleted: Optional[bool] = Field(None, alias="isDeleted")


# Pipeline Models
class BuildDefinitionReference(BaseModel):
    """Build definition reference."""

    id: int
    name: str
    url: Optional[str] = None
    path: Optional[str] = None
    queue_status: Optional[str] = Field(None, alias="queueStatus")


class Build(BaseModel):
    """Build information."""

    id: int
    build_number: Optional[str] = Field(None, alias="buildNumber")
    status: Optional[str] = None
    result: Optional[str] = None
    queue_time: Optional[datetime] = Field(None, alias="queueTime")
    start_time: Optional[datetime] = Field(None, alias="startTime")
    finish_time: Optional[datetime] = Field(None, alias="finishTime")
    definition: Optional[BuildDefinitionReference] = None
    requested_by: Optional[IdentityRef] = Field(None, alias="requestedBy")
    source_branch: Optional[str] = Field(None, alias="sourceBranch")
    source_version: Optional[str] = Field(None, alias="sourceVersion")
    url: Optional[str] = None


class PipelineRun(BaseModel):
    """Pipeline run information."""

    id: int
    name: Optional[str] = None
    state: Optional[str] = None
    result: Optional[str] = None
    created_date: Optional[datetime] = Field(None, alias="createdDate")
    finished_date: Optional[datetime] = Field(None, alias="finishedDate")
    url: Optional[str] = None


# Wiki Models
class WikiV2(BaseModel):
    """Wiki information."""

    id: str
    name: str
    type: Optional[str] = None
    project_id: Optional[str] = Field(None, alias="projectId")
    repository_id: Optional[str] = Field(None, alias="repositoryId")
    mapped_path: Optional[str] = Field(None, alias="mappedPath")
    url: Optional[str] = None


class WikiPage(BaseModel):
    """Wiki page information."""

    id: Optional[int] = None
    path: str
    content: Optional[str] = None
    git_item_path: Optional[str] = Field(None, alias="gitItemPath")
    is_parent_page: Optional[bool] = Field(None, alias="isParentPage")
    order: Optional[int] = None
    sub_pages: Optional[list["WikiPage"]] = Field(None, alias="subPages")
    url: Optional[str] = None


# Test Plan Models
class TestPlan(BaseModel):
    """Test plan information."""

    id: int
    name: str
    description: Optional[str] = None
    state: Optional[str] = None
    iteration: Optional[str] = None
    area_path: Optional[str] = Field(None, alias="areaPath")
    start_date: Optional[datetime] = Field(None, alias="startDate")
    end_date: Optional[datetime] = Field(None, alias="endDate")


class TestSuite(BaseModel):
    """Test suite information."""

    id: int
    name: str
    suite_type: Optional[str] = Field(None, alias="suiteType")
    plan: Optional[dict[str, Any]] = None
    parent_suite: Optional[dict[str, Any]] = Field(None, alias="parentSuite")


class TestCase(BaseModel):
    """Test case information."""

    id: int
    name: Optional[str] = None
    work_item: Optional[WorkItemReference] = Field(None, alias="workItem")
    point_assignments: Optional[list[dict[str, Any]]] = Field(
        None, alias="pointAssignments"
    )


# Iteration Models
class TeamSettingsIteration(BaseModel):
    """Team iteration settings."""

    id: str
    name: str
    path: str
    attributes: Optional[dict[str, Any]] = None
    url: Optional[str] = None


# Search Models
class CodeSearchResult(BaseModel):
    """Code search result."""

    file_name: Optional[str] = Field(None, alias="fileName")
    path: Optional[str] = None
    repository: Optional[dict[str, Any]] = None
    project: Optional[dict[str, Any]] = None
    matches: Optional[dict[str, Any]] = None
    content_id: Optional[str] = Field(None, alias="contentId")


# API Response Wrappers
class ListResponse(BaseModel):
    """Generic list response wrapper."""

    count: int
    value: list[Any]

