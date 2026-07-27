from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from app.api import deps
from app.schemas.changelog import ChangelogEntryCreate, ChangelogEntryResponse
from app.services.changelog import ChangelogService

router = APIRouter()
service = ChangelogService()

@router.get("/", response_model=List[ChangelogEntryResponse])
def get_changelog(
    limit: int = 10,
    db: Session = Depends(deps.get_db),
):
    """
    Retrieve API changelog for client compatibility checks.
    """
    return service.get_latest_changes(db, limit=limit)

@router.post("/", response_model=ChangelogEntryResponse)
def add_changelog_entry(
    *,
    db: Session = Depends(deps.get_db),
    entry_in: ChangelogEntryCreate,
):
    """
    Add a new API changelog entry (Admin only).
    """
    return service.add_entry(db, entry_in=entry_in)
