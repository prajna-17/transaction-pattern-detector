import json
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from urllib.error import HTTPError

from detector import EvidenceWindow, FlaggedAccount, Transaction
from pass_through_detector import RapidPassThroughAccount, RapidPassThroughEvidence
from explainer import (
    _rapid_pass_through_payload,
    _request_body,
    explain_flagged_accounts,
    explain_rapid_pass_through_accounts,
)


class FakeResponse:
    """Provide the small response interface used by urllib context managers."""

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        return False

    def read(self):
        return self.body


def make_flagged_account():
    """Create detector evidence for an already-flagged test account."""
    transactions = tuple(
        Transaction(
            txn_id=txn_id,
            account_id="ACC-1",
            counterparty="CP-1",
            amount=Decimal(amount),
            direction="credit",
            timestamp=datetime.fromisoformat(timestamp),
            channel="UPI",
        )
        for txn_id, amount, timestamp in [
            ("T1", "20000", "2026-01-01T08:00:00"),
            ("T2", "20000", "2026-01-01T09:00:00"),
            ("T3", "20000", "2026-01-01T10:00:00"),
        ]
    )
    window = EvidenceWindow(
        account_id="ACC-1",
        transactions=transactions,
        transaction_count=3,
        combined_amount=Decimal("60000"),
        window_start=transactions[0].timestamp,
        window_end=transactions[-1].timestamp,
    )
    return FlaggedAccount("ACC-1", (window,))


def make_pass_through_account():
    incoming = Transaction(
        "C1", "ACC-PT", "CP-1", Decimal("1000"), "credit",
        datetime.fromisoformat("2026-01-01T08:00:00"), "UPI"
    )
    outgoing = Transaction(
        "D1", "ACC-PT", "CP-1", Decimal("800"), "debit",
        datetime.fromisoformat("2026-01-01T09:00:00"), "UPI"
    )
    evidence = RapidPassThroughEvidence(
        account_id="ACC-PT",
        incoming_transaction=incoming,
        outgoing_transactions=(outgoing,),
        total_outgoing_amount=Decimal("800"),
        outflow_ratio=Decimal("0.8"),
        window_start=incoming.timestamp,
        window_end=outgoing.timestamp,
    )
    return RapidPassThroughAccount("ACC-PT", (evidence,))


def test_generates_explanation_from_evidence_only(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(
            json.dumps(
                {
                    "choices": [{
                        "message": {
                            "content": (
                                "Account ACC-1 has 3 transactions totaling 60000 from "
                                "2026-01-01T08:00:00 to 2026-01-01T10:00:00: T1 "
                                "20000 at 2026-01-01T08:00:00, T2 20000 at "
                                "2026-01-01T09:00:00, and T3 20000 at "
                                "2026-01-01T10:00:00. The deterministic detector "
                                "identified the configured structuring pattern."
                            )
                        }
                    }]
                }
            ).encode()
        )

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr("explainer.urlopen", fake_urlopen)

    results = explain_flagged_accounts([make_flagged_account()])

    assert "Account ACC-1 has 3 transactions totaling 60000" in results[0].explanation
    assert results[0].error is None
    assert captured["request"].get_header("User-agent") == "TransactionPatternDetector/1.0"
    assert captured["request"].get_header("Content-type") == "application/json"
    assert captured["request"].has_header("Authorization")
    body = json.loads(captured["request"].data)
    user_message = body["messages"][1]["content"]
    assert "T1" in user_message
    assert "transactions.csv" not in user_message
    assert "counterparty" not in user_message


def test_prompt_enforces_exact_evidence_only_explanation():
    body = json.loads(_request_body({"account_id": "ACC-1"}, "test-model"))
    system_message = body["messages"][0]["content"]

    assert "sole authority" in system_message
    assert "Preserve transaction IDs, counts, amounts, and timestamps exactly" in system_message
    assert "Do not recalculate, estimate, or infer any time duration" in system_message
    assert "never describe a window as a number of hours or days" in system_message
    assert "Do not add currency symbols or assume a currency" in system_message
    assert "Do not claim suspicious intent, fraud, or money laundering" in system_message


def test_pass_through_explanation_preserves_evidence_and_formats_values(monkeypatch):
    account = make_pass_through_account()
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        "explainer.urlopen",
        lambda request, timeout: FakeResponse(
            json.dumps({
                "choices": [{"message": {"content": (
                    "Account ACC-PT received credit C1 for 1000 at "
                    "2026-01-01T08:00:00, followed by debit D1 for 800 at "
                    "2026-01-01T09:00:00. The supplied outflow ratio is 0.8000 "
                    "over 1.0000 elapsed hours. The deterministic detector "
                    "identified a qualifying credit-followed-by-debit pattern."
                )}}]
            }).encode()
        ),
    )

    result = explain_rapid_pass_through_accounts([account])[0]

    assert result.error is None
    assert "C1" in result.explanation
    assert "D1" in result.explanation
    assert "0.8000" in result.explanation
    assert "1.0000" in result.explanation
    payload = _rapid_pass_through_payload(account)
    assert payload["evidence"][0]["outflow_ratio"] == "0.8000"
    assert payload["evidence"][0]["elapsed_hours"] == "1.0000"


def test_rejects_unsupported_fund_tracing_claim(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        "explainer.urlopen",
        lambda request, timeout: FakeResponse(
            json.dumps({"choices": [{"message": {"content": (
                "Account ACC-PT received C1 and D1 was directly funded by it."
            )}}]}).encode()
        ),
    )

    result = explain_rapid_pass_through_accounts([make_pass_through_account()])[0]

    assert result.explanation is None
    assert result.error == "LLM response contained an unsupported conclusion or rule."
    assert result.flagged_account.account_id == "ACC-PT"


def test_reports_missing_api_key_without_calling_llm(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    result = explain_flagged_accounts([make_flagged_account()])[0]

    assert result.explanation is None
    assert "GROQ_API_KEY" in result.error
    assert result.flagged_account.account_id == "ACC-1"


def test_reports_api_failure_and_keeps_deterministic_evidence(monkeypatch):
    def failing_urlopen(request, timeout):
        raise OSError("network unavailable")

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr("explainer.urlopen", failing_urlopen)

    result = explain_flagged_accounts([make_flagged_account()])[0]

    assert result.explanation is None
    assert result.error == "LLM API request failed."
    assert result.flagged_account.evidence_windows[0].combined_amount == Decimal("60000")


def test_reports_structured_http_error_without_exposing_api_key(monkeypatch):
    api_key = "test-secret-key"
    error_body = json.dumps(
        {
            "error": {
                "message": f"Invalid key {api_key}",
                "code": "invalid_api_key",
            }
        }
    ).encode()

    def failing_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            {},
            BytesIO(error_body),
        )

    monkeypatch.setenv("GROQ_API_KEY", api_key)
    monkeypatch.setattr("explainer.urlopen", failing_urlopen)

    result = explain_flagged_accounts([make_flagged_account()])[0]

    assert result.error == (
        "LLM API returned HTTP 401 (Content-Type: unknown) "
        "(invalid_api_key): Invalid key [REDACTED]"
    )
    assert api_key not in result.error
    assert result.flagged_account.account_id == "ACC-1"


def test_reports_plain_text_http_error_with_redacted_excerpt(monkeypatch):
    api_key = "test-secret-key"
    body = (
        f"Authorization: Bearer {api_key}; access denied by gateway."
    ).encode()

    def failing_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {"Content-Type": "text/plain"},
            BytesIO(body),
        )

    monkeypatch.setenv("GROQ_API_KEY", api_key)
    monkeypatch.setattr("explainer.urlopen", failing_urlopen)

    result = explain_flagged_accounts([make_flagged_account()])[0]

    assert "HTTP 403 (Content-Type: text/plain)" in result.error
    assert "Response excerpt: Authorization: [REDACTED]" in result.error
    assert api_key not in result.error


def test_reports_html_http_error_with_bounded_excerpt(monkeypatch):
    body = ("<html><body>" + "x" * 600 + "</body></html>").encode()

    def failing_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {"Content-Type": "text/html"},
            BytesIO(body),
        )

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr("explainer.urlopen", failing_urlopen)

    result = explain_flagged_accounts([make_flagged_account()])[0]

    excerpt = result.error.split("Response excerpt: ", 1)[1]
    assert "HTTP 403 (Content-Type: text/html)" in result.error
    assert len(excerpt) == 500
    assert excerpt.endswith("...")


def test_reports_empty_http_error_body(monkeypatch):
    def failing_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {"Content-Type": "application/json"},
            BytesIO(b""),
        )

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr("explainer.urlopen", failing_urlopen)

    result = explain_flagged_accounts([make_flagged_account()])[0]

    assert result.error == (
        "LLM API returned HTTP 403 (Content-Type: application/json). "
        "Response body was empty."
    )


def test_handles_empty_input_and_empty_evidence(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    empty_account = FlaggedAccount("ACC-EMPTY", ())

    assert explain_flagged_accounts([]) == []
    result = explain_flagged_accounts([empty_account])[0]
    assert result.explanation is None
    assert result.error == "No deterministic evidence was supplied for this account."


def test_llm_response_cannot_change_detection_decision(monkeypatch):
    def fake_urlopen(request, timeout):
        return FakeResponse(
            b'{"choices":[{"message":{"content":"This is not suspicious."}}]}'
        )

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr("explainer.urlopen", fake_urlopen)
    flagged_account = make_flagged_account()

    result = explain_flagged_accounts([flagged_account])[0]

    assert result.explanation is None
    assert result.error == "LLM response contained an unsupported conclusion or rule."
    assert result.flagged_account == flagged_account
    assert result.flagged_account.evidence_windows[0].transaction_count == 3


def test_reports_malformed_llm_response(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr("explainer.urlopen", lambda request, timeout: FakeResponse(b"{}"))

    result = explain_flagged_accounts([make_flagged_account()])[0]

    assert result.explanation is None
    assert result.error == "LLM response did not contain valid message content."