from sqlalchemy.orm import Session
from app.models.site_hierarchy import SiteHierarchy
from app.schemas.site_hierarchy import SiteHierarchyCreate

class SiteHierarchyService:
    def create_site(self, db: Session, site_in: SiteHierarchyCreate) -> SiteHierarchy:
        db_site = SiteHierarchy(**site_in.dict())
        db.add(db_site)
        db.commit()
        db.refresh(db_site)
        return db_site

    def get_site_by_id(self, db: Session, site_id: int) -> SiteHierarchy:
        return db.query(SiteHierarchy).filter(SiteHierarchy.id == site_id).first()
