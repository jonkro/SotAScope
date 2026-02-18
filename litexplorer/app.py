"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from litexplorer.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="LitExplorer", version="0.1.0", lifespan=lifespan)

# Import and include routers after app creation to avoid circular imports.
from litexplorer.api.works import authors_router, router as works_router  # noqa: E402
from litexplorer.api.venues import router as venues_router  # noqa: E402
from litexplorer.api.fields import router as fields_router  # noqa: E402
from litexplorer.api.venue_tiers import router as venue_tiers_router  # noqa: E402
from litexplorer.api.projects import router as projects_router  # noqa: E402

app.include_router(works_router)
app.include_router(authors_router)
app.include_router(venues_router)
app.include_router(fields_router)
app.include_router(venue_tiers_router)
app.include_router(projects_router)
