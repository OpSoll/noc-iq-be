from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api import deps
from app.schemas.site_hierarchy import SiteHierarchyCreate, SiteHierarchyResponse
from app.services.site_hierarchy import SiteHierarchyService

router = APIRouter()
service = SiteHierarchyService()

@router.post("/", response_model=SiteHierarchyResponse)
def create_site_hierarchy(
    *,
    db: Session = Depends(deps.get_db),
    site_in: SiteHierarchyCreate,
):
    """
    Create a new site hierarchy entry.
    """
    if site_in.parent_id:
        parent = service.get_site_by_id(db, site_id=site_in.parent_id)
        if not parent:
            raise HTTPException(status_code=404, detail="Parent site not found")
            
    return service.create_site(db, site_in=site_in)
