from __future__ import annotations

from apps.decision_worker.main import run_approved_fixture
from packages.alpaca_execution_mcp import AlpacaPaperExecutionAdapter
from tests.integration.test_exit_runtime import _reduce_bundle


def test_alpaca_mleg_entry_request_preserves_exact_approved_economics() -> None:
    decision = run_approved_fixture()
    request = AlpacaPaperExecutionAdapter._request(decision.command.plan)
    payload = request.model_dump(mode="json", exclude_none=True)

    assert payload["order_class"] == "mleg"
    assert payload["limit_price"] == float(decision.command.plan.limit_debit)
    assert payload["client_order_id"] == decision.command.plan.client_order_id
    assert len(payload["client_order_id"]) <= 48
    assert [item["position_intent"] for item in payload["legs"]] == [
        "buy_to_open",
        "sell_to_open",
    ]


def test_alpaca_mleg_close_uses_signed_credit_and_close_intents() -> None:
    _, _, bundle = _reduce_bundle()
    request = AlpacaPaperExecutionAdapter._request(bundle.command.plan)
    payload = request.model_dump(mode="json", exclude_none=True)

    assert bundle.command.plan.price_effect == "CREDIT"
    assert payload["limit_price"] == -float(bundle.command.plan.limit_price)
    assert [item["position_intent"] for item in payload["legs"]] == [
        "sell_to_close",
        "buy_to_close",
    ]
