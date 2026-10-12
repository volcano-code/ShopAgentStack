"""Actual FastAPI route, real SQLite, explicit identity double; no paid models."""
import pytest
import httpx
from shop_agent_stack import app as module
from shop_agent_stack.business import BusinessError
from test_history_pages import seed


@pytest.mark.asyncio
async def test_http_paging_legacy_ownership_and_cache_control(tmp_path, monkeypatch):
    store, sid = seed(tmp_path, 30)
    monkeypatch.setattr(module, "store", store, raising=False)
    async def identity(token):
        if token not in ("owner", "other"):
            raise BusinessError("登录失效", 401)
        return {"memberId": 1 if token == "owner" else 2, "executionToken": "synthetic"}
    monkeypatch.setattr(module, "identity", identity)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://test") as client:
        response = await client.get(f"/sessions/{sid}/history", headers={"Authorization": "owner"})
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        page = response.json(); assert len(page['runs']) == 12 and page['remaining'] == 18
        old = await client.get(f"/sessions/{sid}", headers={"Authorization": "owner"})
        assert len(old.json()['runs']) == 30
        next_page = await client.get(f"/sessions/{sid}/history", params={"cursor":page['next_cursor']}, headers={"Authorization":"owner"})
        assert len(next_page.json()['runs']) == 12
        for token, code in [("other", 404), ("missing", 401)]:
            result = await client.get(f"/sessions/{sid}/history", headers={"Authorization":token})
            assert result.status_code == code and "合成内容" not in result.text
        for query in [{"limit":0}, {"limit":51}, {"lane":"private"}, {"cursor":"x"*513}]:
            result = await client.get(f"/sessions/{sid}/history", params=query, headers={"Authorization":"owner"})
            assert result.status_code == 422
        result = await client.get(f"/sessions/{sid}/history?cursor=invalid", headers={"Authorization":"owner"})
        assert result.status_code == 400
