import sys
import types
import unittest

# Provide lightweight stubs so importing bot.services.* does not pull optional deps during tests.
httpx_stub = types.ModuleType("httpx")


class _StubResponse:
    status_code = 200
    text = ""

    def json(self):  # pragma: no cover - simple stub
        return {}

    def close(self) -> None:  # pragma: no cover - simple stub
        return None


class _StubAsyncClient:
    def __init__(self, *args, **kwargs):  # pragma: no cover - stub
        pass

    async def aclose(self) -> None:  # pragma: no cover - stub
        return None


class _StubClient:
    def __init__(self, *args, **kwargs):  # pragma: no cover - stub
        pass

    def get(self, *args, **kwargs):  # pragma: no cover - stub
        raise RuntimeError("httpx stub: network disabled in tests")


class _StubHTTPTransport:
    def __init__(self, *args, **kwargs):  # pragma: no cover - stub
        pass


class _StubAsyncHTTPTransport:
    def __init__(self, *args, **kwargs):  # pragma: no cover - stub
        pass


class _StubException(Exception):
    pass


httpx_stub.Response = _StubResponse
httpx_stub.AsyncClient = _StubAsyncClient
httpx_stub.Client = _StubClient
httpx_stub.HTTPTransport = _StubHTTPTransport
httpx_stub.AsyncHTTPTransport = _StubAsyncHTTPTransport
httpx_stub.RequestError = _StubException
httpx_stub.ReadTimeout = _StubException
httpx_stub.ConnectError = _StubException
sys.modules.setdefault("httpx", httpx_stub)

yookassa_stub = types.ModuleType("yookassa")
yookassa_stub.Configuration = object()
yookassa_stub.Payment = object()
sys.modules.setdefault("yookassa", yookassa_stub)

from bot.services.wildberries import Product, score_item

try:
    from bot.telegram.flow_search import parse_excludes
except ModuleNotFoundError:  # pragma: no cover - optional dependency missing in CI
    parse_excludes = None


@unittest.skipIf(parse_excludes is None, "aiogram is not installed")
class ParseExcludesTest(unittest.TestCase):
    def test_parse_excludes_removes_short_tokens(self) -> None:
        raw = "б/у, ., -, восстановленный; X;  ,новый"
        result = parse_excludes(raw)  # type: ignore[arg-type]
        self.assertIn("б/у", result)
        self.assertIn("восстановленный", result)
        self.assertNotIn(".", result)
        self.assertNotIn("-", result)
        self.assertNotIn("x", result)


class ScoreItemTest(unittest.TestCase):
    def test_score_item_returns_expected_tuple(self) -> None:
        product = Product(
            id=1,
            name="Телефон",
            brand="Brand",
            price=30.0,
            wallet_price=30.0,
            best_buyout_price=60.0,
            discount=None,
            profit_rub=30.0,
            profit_percent=50.0,
            rating=4.5,
            reviews=120,
            features=["Цвет: черный"],
            stock=15,
            seller_name="Store",
            seller_rating=4.8,
            seller_orders=400,
            seller_registration="2021-01-01",
            url="https://example.com",
            image_url=None,
        )
        pct, gain, rating, reviews, stock = score_item(product)
        self.assertAlmostEqual(pct, 50.0, places=1)
        self.assertEqual(gain, 30)
        self.assertAlmostEqual(rating, 4.5)
        self.assertEqual(reviews, 120)
        self.assertEqual(stock, 15)


if __name__ == "__main__":
    unittest.main()
