"""Assemble the FastAPI application.

The API has no user accounts and is meant for the local machine, so any web page
open in the user's browser is a potential client. ``TrustedHostMiddleware``
stops DNS rebinding, where a hostile domain resolves to 127.0.0.1. No CORS
headers are ever sent, so other sites can neither read responses nor send
``DELETE`` requests, and FastAPI's strict JSON content type check rejects
cross-site form posts.
"""

from collections.abc import Sequence

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from git_detective import __version__
from git_detective.api.config import ApiSettings
from git_detective.api.errors import register_error_handlers
from git_detective.api.registry import RepositoryRegistry
from git_detective.api.routes import router
from git_detective.api.runner import AnalysisRunner
from git_detective.storage import AnalysisStore

LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def create_app(
    settings: ApiSettings, *, allowed_hosts: Sequence[str] = LOOPBACK_HOSTS
) -> FastAPI:
    """Build the application, creating or upgrading its database.

    Raises:
        StorageError: If the database cannot be initialized.
    """
    store = AnalysisStore(settings.database)
    store.initialize()
    registry = RepositoryRegistry(settings.repositories)

    app = FastAPI(
        title="Git Detective API",
        version=__version__,
        description="Analyze allowed local Git repositories and browse past reports.",
    )
    app.state.store = store
    app.state.registry = registry
    app.state.runner = AnalysisRunner(
        registry, store, max_concurrent=settings.max_concurrent_analyses
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    register_error_handlers(app)
    app.include_router(router)
    return app
