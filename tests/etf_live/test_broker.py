from decimal import Decimal

import httpx
import pytest

from packages.etf_live.broker import AlpacaLiveBroker, BrokerError


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        value = self.responses.get((method, url))
        if isinstance(value, Exception):
            raise value
        return httpx.Response(200, json=value, request=httpx.Request(method, url))


def test_account_requires_exact_identity_and_multiplier_one():
    origin = "https://api.alpaca.markets"
    transport = FakeTransport({("GET", f"{origin}/v2/account"): {"id": "acct", "status": "ACTIVE", "cash": "1000", "buying_power": "1000", "non_marginable_buying_power": "1000", "equity": "1000", "multiplier": "1", "trading_blocked": False, "account_blocked": False, "trade_suspended_by_user": False}})
    broker = AlpacaLiveBroker(api_key="k", api_secret="s", account_id="acct", transport=transport)
    assert broker.account().cash == Decimal("1000")
    bad = AlpacaLiveBroker(api_key="k", api_secret="s", account_id="other", transport=transport)
    with pytest.raises(BrokerError, match="ACCOUNT_ID_MISMATCH"):
        bad.account()


def test_origin_is_allowlisted():
    with pytest.raises(ValueError, match="ORIGIN_NOT_ALLOWLISTED"):
        AlpacaLiveBroker(api_key="k", api_secret="s", account_id="acct", trading_origin="http://example.test")


def test_submission_unknown_is_not_retried():
    origin = "https://api.alpaca.markets"
    transport = FakeTransport({("POST", f"{origin}/v2/orders"): httpx.ConnectTimeout("timeout")})
    broker = AlpacaLiveBroker(api_key="k", api_secret="s", account_id="acct", transport=transport)
    with pytest.raises(BrokerError):
        broker.submit_order({"symbol": "TQQQ"})
