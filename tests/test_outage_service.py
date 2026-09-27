from app.services.outage_service import OutageService

def test_list_outages():
    service = OutageService()
    res = service.list_outages("us-east-1")
    assert len(res) == 1
    assert res[0]["region"] == "us-east-1"
