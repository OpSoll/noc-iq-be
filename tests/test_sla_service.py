from app.services.sla_service import SLAService

def test_recalculate_sla():
    service = SLAService()
    assert service.recalculate_sla(1) == True
