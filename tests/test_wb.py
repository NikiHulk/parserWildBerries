import sys
import types
import unittest

if "httpx" not in sys.modules:
    httpx_stub = types.ModuleType("httpx")

    class _StubResponse:
        status_code = 200
        text = ""

        def json(self):  # pragma: no cover - simple stub
            return {}

    class _StubClient:
        def __init__(self, *args, **kwargs):  # pragma: no cover - stub
            pass

        def get(self, *args, **kwargs):  # pragma: no cover - stub
            raise RuntimeError("httpx stub: disabled in tests")

        def close(self) -> None:  # pragma: no cover - stub
            return None

    class _StubAsyncClient:
        def __init__(self, *args, **kwargs):  # pragma: no cover - stub
            pass

        async def aclose(self) -> None:  # pragma: no cover - stub
            return None

    class _StubTransport:
        def __init__(self, *args, **kwargs):  # pragma: no cover - stub
            pass

    class _StubRequestError(Exception):
        pass

    httpx_stub.Response = _StubResponse
    httpx_stub.Client = _StubClient
    httpx_stub.AsyncClient = _StubAsyncClient
    httpx_stub.HTTPTransport = _StubTransport
    httpx_stub.AsyncHTTPTransport = _StubTransport
    httpx_stub.RequestError = _StubRequestError
    httpx_stub.ReadTimeout = _StubRequestError
    httpx_stub.ConnectError = _StubRequestError
    sys.modules.setdefault("httpx", httpx_stub)

if "yookassa" not in sys.modules:
    yookassa_stub = types.ModuleType("yookassa")
    yookassa_stub.Configuration = object()
    yookassa_stub.Payment = object()
    sys.modules.setdefault("yookassa", yookassa_stub)

from bot.services.wildberries import _passes_price_limits, extract_price_rub


class ExtractPriceRubTestCase(unittest.TestCase):
    def test_extracts_from_various_price_schemas(self) -> None:
        cases = [
            ({"sizes": [{"price": {"salePriceU": 15000}}]}, 150.0, "salePriceU"),
            ({"sizes": [{"price": {"priceU": 9900}}]}, 99.0, "priceU"),
            ({"sizes": [{"price": {"product": 12345}}]}, 123.45, "product"),
            (
                {"sizes": [{"price": {"extended": {"minPriceU": 8700}}}]},
                87.0,
                "extended.minPriceU",
            ),
            (
                {"sizes": [{"price": {"totalPrice": {"basicPriceU": 4321}}}]},
                43.21,
                "totalPrice.basicPriceU",
            ),
            ({"priceU": 22200}, 222.0, "root.priceU"),
        ]
        for payload, expected, label in cases:
            with self.subTest(label=label):
                value = extract_price_rub(payload)
                self.assertIsNotNone(value)
                self.assertAlmostEqual(value or 0.0, expected, places=2)
                # cached value should be stored after extraction
                self.assertIn("_price_rub", payload)

    def test_returns_none_when_price_absent(self) -> None:
        self.assertIsNone(extract_price_rub({"sizes": [{}]}))


class PassesPriceLimitsTestCase(unittest.TestCase):
    def test_respects_min_max_bounds(self) -> None:
        item = {"sizes": [{"price": {"priceU": 12500}}]}
        allowed, price = _passes_price_limits(item, 100.0, 150.0)
        self.assertTrue(allowed)
        self.assertAlmostEqual(price or 0.0, 125.0, places=2)

    def test_rejects_when_price_missing(self) -> None:
        allowed, price = _passes_price_limits({}, 10.0, 20.0)
        self.assertFalse(allowed)
        self.assertIsNone(price)

    def test_rejects_above_max(self) -> None:
        item = {"sizes": [{"price": {"product": 25000}}]}
        allowed, price = _passes_price_limits(item, None, 200.0)
        self.assertFalse(allowed)
        self.assertAlmostEqual(price or 0.0, 250.0, places=2)


if __name__ == "__main__":
    unittest.main()
