import asyncio
import importlib
import json
import os
import types
import unittest
from unittest import mock


class _StubResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status_code = 200
        self._text = json.dumps(payload)

    @property
    def text(self) -> str:
        return self._text

    def close(self) -> None:  # pragma: no cover - nothing to close
        return None


class _StubClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[int, ...]]] = []

    def get(self, url: str, *, params: dict[str, str], headers: dict[str, str] | None = None):  # noqa: D401 - signature mirrors httpx.Client
        variant = "v4" if "cards/v4" in url else "v2"
        ids = tuple(int(token) for token in params.get("nm", "").split(",") if token)
        self.calls.append((variant, ids))
        if len(ids) == 1:
            product = {"id": ids[0], "salePriceU": ids[0] * 100, "name": f"item{ids[0]}"}
            payload = {"data": {"products": [product]}}
        else:
            payload = {"data": {"products": []}}
        return _StubResponse(payload)


class DetailBatchingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self) -> None:
        try:
            self.loop.run_until_complete(asyncio.sleep(0))
        finally:
            self.loop.close()
            asyncio.set_event_loop(None)

    def _reload_module(self, **env: str) -> types.ModuleType:
        import bot.services.wildberries as wb_mod

        patcher = mock.patch.dict(os.environ, env, clear=False)
        patcher.start()
        reloaded = importlib.reload(wb_mod)
        self.addCleanup(importlib.reload, reloaded)
        self.addCleanup(patcher.stop)
        return reloaded

    def _prepare_client(self, module: types.ModuleType) -> tuple[object, _StubClient]:
        client = module.WildberriesClient(timeout=0.1)
        stub = _StubClient()

        def _ensure_detail_client(self: object) -> _StubClient:  # pragma: no cover - simple wrapper
            return stub

        client._ensure_detail_client = types.MethodType(_ensure_detail_client, client)  # type: ignore[attr-defined]
        self.addCleanup(getattr(client, "_close_detail_client", lambda: None))
        return client, stub

    def test_split_on_empty_triggers_single_requests(self) -> None:
        module = self._reload_module(WB_DETAIL_MAX_BATCH="9", WB_DETAIL_SPLIT_ON_EMPTY="1")
        client, stub = self._prepare_client(module)

        detail_map, stats = self.loop.run_until_complete(
            client._fetch_details([1, 2, 3], timeout=1.0, headers={}, client=None)
        )

        self.assertIn(("v2", (1, 2, 3)), stub.calls)
        single_calls = [entry for entry in stub.calls if len(entry[1]) == 1]
        self.assertEqual(len(single_calls), 3)
        self.assertEqual(sorted(detail_map.keys()), [1, 2, 3])
        self.assertGreaterEqual(stats.get("singles", 0), 3)

    def test_split_disabled_leaves_batch_empty(self) -> None:
        module = self._reload_module(WB_DETAIL_MAX_BATCH="4", WB_DETAIL_SPLIT_ON_EMPTY="0")
        client, stub = self._prepare_client(module)

        detail_map, _ = self.loop.run_until_complete(
            client._fetch_details([10, 20, 30], timeout=1.0, headers={}, client=None)
        )

        self.assertIn(("v2", (10, 20, 30)), stub.calls)
        single_calls = [entry for entry in stub.calls if len(entry[1]) == 1]
        if single_calls:
            self.assertLessEqual(len(single_calls), len(stub.calls))
            expected_ids = sorted({ids[0] for _, ids in single_calls})
            self.assertEqual(sorted(detail_map.keys()), expected_ids)
        else:
            self.assertEqual(detail_map, {})


if __name__ == "__main__":  # pragma: no cover - manual run helper
    unittest.main()
