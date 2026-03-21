"""Filesystem browsing API — navigate directories for PDF storage path selection."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sotascope.config import settings

router = APIRouter(prefix="/api/filesystem", tags=["filesystem"])


class BrowseResponse(BaseModel):
    current_path: str
    parent_path: str | None
    directories: list[str]


class MkdirRequest(BaseModel):
    path: str


class MkdirResponse(BaseModel):
    path: str


def _get_default_pdf_path() -> str:
    """Return the configured default PDF directory as a string."""
    return str(settings.pdf_dir)


@router.get("/browse", response_model=BrowseResponse)
def browse_directory(path: str | None = None):
    """List subdirectories at a given path.

    If path is empty or omitted, defaults to the current PDF storage root.
    Hidden directories (starting with '.') are excluded.
    """
    if not path:
        path = _get_default_pdf_path()

    target = Path(path).resolve()

    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Path does not exist or is not a directory: {target}")

    parent = str(target.parent) if target.parent != target else None

    directories = sorted(
        entry.name
        for entry in target.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    )

    return BrowseResponse(
        current_path=str(target),
        parent_path=parent,
        directories=directories,
    )


@router.post("/mkdir", response_model=MkdirResponse)
def make_directory(body: MkdirRequest):
    """Create a directory (including parents if needed)."""
    target = Path(body.path).resolve()
    target.mkdir(parents=True, exist_ok=True)
    return MkdirResponse(path=str(target))
