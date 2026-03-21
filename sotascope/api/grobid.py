"""GROBID API router — health check and convenience Docker start."""

import subprocess

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sotascope.api.deps import get_db
from sotascope.external.grobid import GrobidClient
from sotascope.models.settings import Setting

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
    """Start GROBID: try 'docker start grobid' first; fall back to 'docker run' if the container doesn't exist."""
    try:
        start_result = subprocess.run(
            ["docker", "start", "grobid"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if start_result.returncode == 0:
            return GrobidStartResponse(success=True, message="Container 'grobid' started.")

        # docker start failed — container may not exist yet; try docker run
        run_result = subprocess.run(
            [
                "docker", "run", "-d",
                "--name", "grobid",
                "-p", "8070:8070",
                "grobid/grobid:0.8.2-crf",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if run_result.returncode == 0:
            return GrobidStartResponse(success=True, message="Container 'grobid' created and started.")
        stderr = run_result.stderr.strip()
        return GrobidStartResponse(success=False, message=stderr or "docker run failed.")
    except FileNotFoundError:
        return GrobidStartResponse(success=False, message="Docker is not installed or not on PATH.")
    except subprocess.TimeoutExpired:
        return GrobidStartResponse(success=False, message="docker start/run timed out.")
    except Exception as exc:
        return GrobidStartResponse(success=False, message=str(exc))
