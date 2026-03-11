"""GROBID API router — health check and convenience Docker start."""

import subprocess

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from litexplorer.api.deps import get_db
from litexplorer.external.grobid import GrobidClient
from litexplorer.models.settings import Setting

router = APIRouter(prefix="/api/grobid", tags=["grobid"])


def _get_grobid_url(db: Session) -> str:
    row = db.execute(select(Setting).where(Setting.key == "grobid_url")).scalar_one_or_none()
    return (row.value or "").strip() if row else ""


class GrobidStatusResponse(BaseModel):
    available: bool
    url: str


class GrobidStartResponse(BaseModel):
    success: bool
    message: str


@router.get("/status", response_model=GrobidStatusResponse)
def grobid_status(db: Session = Depends(get_db)):
    """Check whether the configured GROBID instance is reachable."""
    url = _get_grobid_url(db)
    if not url:
        return GrobidStatusResponse(available=False, url="")
    try:
        client = GrobidClient(base_url=url)
        alive = client.check_health()
        client.close()
        return GrobidStatusResponse(available=alive, url=url)
    except Exception:
        return GrobidStatusResponse(available=False, url=url)


@router.post("/start", response_model=GrobidStartResponse)
def grobid_start():
    """Attempt to start a stopped Docker container named 'grobid'."""
    try:
        result = subprocess.run(
            ["docker", "start", "grobid"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return GrobidStartResponse(success=True, message="Container 'grobid' started.")
        stderr = result.stderr.strip()
        return GrobidStartResponse(success=False, message=stderr or "docker start failed.")
    except FileNotFoundError:
        return GrobidStartResponse(success=False, message="Docker is not installed or not on PATH.")
    except subprocess.TimeoutExpired:
        return GrobidStartResponse(success=False, message="docker start timed out after 10 seconds.")
    except Exception as exc:
        return GrobidStartResponse(success=False, message=str(exc))
