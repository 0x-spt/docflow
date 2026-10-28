from datetime import UTC, datetime, timedelta

from test_documents import add_doc


async def test_stats_counts(api, auth, make_tenant):
    now = datetime.now(UTC)
    for st in ("ok", "ok", "ok", "needs_review", "failed", "queued"):
        await add_doc(api, auth, status=st)
    await add_doc(api, auth, status="ok", created_at=now - timedelta(days=10))
    await add_doc(api, await make_tenant("Чужой"), status="failed")

    r = await api.client.get("/v1/stats", headers=auth, params={"days": 5})
    assert r.status_code == 200
    assert r.json() == {
        "days": 5,
        "total": 6,
        "by_status": {"queued": 1, "processing": 0, "ok": 3, "needs_review": 1, "failed": 1},
        "auto_share": 0.6,
        "reviewed": 0,
    }
    assert (await api.client.get("/v1/stats", headers=auth)).json()["total"] == 7


async def test_stats_empty_auto_share_null(api, auth):
    body = (await api.client.get("/v1/stats", headers=auth)).json()
    assert body["total"] == 0 and body["auto_share"] is None


async def test_stats_bad_days_422(api, auth):
    assert (await api.client.get("/v1/stats", headers=auth, params={"days": 0})).status_code == 422
    assert (await api.client.get("/v1/stats", headers=auth, params={"days": 366})).status_code == 422
