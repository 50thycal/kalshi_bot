"""Read-only catalog adapter for future consumers; returns advisory data only."""

import httpx


class CatalogClient:
    def __init__(self, base_url, token, transport=None):
        self.http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": "Bearer " + token},
            timeout=10,
            transport=transport,
        )

    def close(self):
        self.http.close()

    def get(self, path, **params):
        response = self.http.get(path, params={k: v for k, v in params.items() if v is not None})
        response.raise_for_status()
        return response.json()

    def assessments(self, strategy, series=None, offset=0):
        return self.get("/v1/assessments", strategy=strategy, series=series, offset=offset)

    def select(
        self,
        strategy,
        settlement_type=None,
        min_edge=None,
        min_confidence=None,
        evidence_source=None,
        offset=0,
    ):
        return self.get(
            "/v1/select",
            strategy=strategy,
            settlement_type=settlement_type,
            min_edge=min_edge,
            min_confidence=min_confidence,
            evidence_source=evidence_source,
            qualified="true",
            offset=offset,
        )
