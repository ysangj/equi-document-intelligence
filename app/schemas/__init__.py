"""API request/response models: the executable form of docs/progress.md -> API Contract.

T6-T8 route handlers use these as `response_model` / request bodies / query models
(`Annotated[ReturnsQuery, Query()]`). tests/test_api_contract.py checks that every
JSON example in the contract validates against the model named in its
`<!-- schema: Name -->` tag, and that it round-trips unchanged.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from app.schemas.ask import AskRequest, AskResponse, Citation
from app.schemas.auth import (
    AuthStatusResponse,
    DisconnectResponse,
    DriveFoldersQuery,
    DriveFoldersResponse,
    WatchRequest,
    WatchResponse,
)
from app.schemas.common import ErrorResponse, ListResponse, Page
from app.schemas.data import (
    AccountItem,
    FundFactItem,
    FundFactsQuery,
    FundItem,
    HoldingItem,
    HoldingsQuery,
    ReturnItem,
    ReturnsQuery,
    SourceQuery,
    SourceResponse,
    StatementItem,
    StatementsQuery,
)
from app.schemas.ingestion import (
    DocumentItem,
    DocumentsQuery,
    ProgressResponse,
    ReprocessResponse,
    SyncRequestResponse,
)
from app.schemas.review import (
    ReviewActionRequest,
    ReviewActionResponse,
    ReviewItem,
    ReviewListResponse,
    ReviewQuery,
)
from app.schemas.search import SearchQuery, SearchResponse

ReturnsPage = Page[ReturnItem]
FundFactsPage = Page[FundFactItem]
StatementsPage = Page[StatementItem]
HoldingsPage = Page[HoldingItem]
DocumentsPage = Page[DocumentItem]
FundsList = ListResponse[FundItem]
AccountsList = ListResponse[AccountItem]


def _example_models() -> dict[str, Any]:
    from app.main import ConfigResponse, HealthResponse  # T1 models live in app/main.py

    return {
        "ErrorResponse": ErrorResponse,
        "HealthResponse": HealthResponse,
        "ConfigResponse": ConfigResponse,
        "AuthStatusResponse": AuthStatusResponse,
        "DisconnectResponse": DisconnectResponse,
        "DriveFoldersResponse": DriveFoldersResponse,
        "WatchRequest": WatchRequest,
        "WatchResponse": WatchResponse,
        "SyncRequestResponse": SyncRequestResponse,
        "ProgressResponse": ProgressResponse,
        "DocumentsPage": DocumentsPage,
        "ReprocessResponse": ReprocessResponse,
        "ReturnsPage": ReturnsPage,
        "FundFactsPage": FundFactsPage,
        "StatementsPage": StatementsPage,
        "HoldingsPage": HoldingsPage,
        "FundsList": FundsList,
        "AccountsList": AccountsList,
        "SourceResponse": SourceResponse,
        "SearchResponse": SearchResponse,
        "AskRequest": AskRequest,
        "AskResponse": AskResponse,
        "Citation": TypeAdapter(Citation),
        "ReviewListResponse": ReviewListResponse,
        "ReviewItem": ReviewItem,
        "ReviewActionRequest": TypeAdapter(ReviewActionRequest),
        "ReviewActionResponse": ReviewActionResponse,
    }


def example_models() -> dict[str, TypeAdapter]:
    """Name used in `<!-- schema: Name -->` tags -> TypeAdapter."""
    return {k: v if isinstance(v, TypeAdapter) else TypeAdapter(v) for k, v in _example_models().items()}


__all__ = [
    "AccountItem", "AccountsList", "AskRequest", "AskResponse", "AuthStatusResponse", "Citation",
    "DisconnectResponse", "DocumentItem", "DocumentsPage", "DocumentsQuery", "DriveFoldersQuery",
    "DriveFoldersResponse", "ErrorResponse", "FundFactItem", "FundFactsPage", "FundFactsQuery", "FundItem",
    "FundsList", "HoldingItem", "HoldingsPage", "HoldingsQuery", "ProgressResponse", "ReprocessResponse",
    "ReturnItem", "ReturnsPage", "ReturnsQuery", "ReviewActionRequest", "ReviewActionResponse", "ReviewItem",
    "ReviewListResponse", "ReviewQuery", "SearchQuery", "SearchResponse", "SourceQuery", "SourceResponse",
    "StatementItem", "StatementsPage", "StatementsQuery", "SyncRequestResponse", "WatchRequest",
    "WatchResponse", "example_models",
]
