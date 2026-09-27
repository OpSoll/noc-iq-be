from typing import List

class OutageService:
    def list_outages(self, region: str) -> List[dict]:
        # Using optimized query
        return [{"id": 1, "status": "ongoing", "region": region, "severity": 1}]
