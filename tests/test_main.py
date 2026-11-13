import csv
import io
import json
from io import BytesIO
from urllib.error import HTTPError

from main import configure_console_encoding, run_application


class FakeResponse:
	"""Provide the response interface used by the explainer's HTTP client."""

	def __init__(self, body):
		self.body = body

	def __enter__(self):
		return self

	def __exit__(self, exception_type, exception_value, traceback):
		return False

	def read(self):
		return self.body


def write_inputs(tmp_path, rows):
	"""Create temporary configuration and transaction input files."""
	config_path = tmp_path / "config.json"
	config_path.write_text(
		json.dumps(
			{
				"reporting_threshold": 50000,
				"rolling_window_hours": 72,
				"min_transaction_count": 3,
				"rapid_pass_through_window_hours": 72,
				"rapid_pass_through_min_outflow_ratio": 0.8,
			}
		),
		encoding="utf-8",
	)
	transaction_path = tmp_path / "transactions.csv"
	columns = [
		"txn_id",
		"account_id",
		"counterparty",
		"amount",
		"direction",
		"timestamp",
		"channel",
	]
	with transaction_path.open("w", newline="", encoding="utf-8") as transaction_file:
		writer = csv.DictWriter(transaction_file, fieldnames=columns)
		writer.writeheader()
		writer.writerows(rows)
	return config_path, transaction_path


def transaction(txn_id, account_id, amount, timestamp, direction="credit"):
	"""Create one CSV row for an integration test."""
	return {
		"txn_id": txn_id,
		"account_id": account_id,
		"counterparty": "CP-1",
		"amount": str(amount),
		"direction": direction,
		"timestamp": timestamp,
		"channel": "UPI",
	}


def qualifying_rows():
	"""Return three credits that form one deterministic flagged account."""
	return [
		transaction("T1", "ACC-1", 20000, "2026-01-01T08:00:00"),
		transaction("T2", "ACC-1", 20000, "2026-01-01T09:00:00"),
		transaction("T3", "ACC-1", 20000, "2026-01-01T10:00:00"),
	]


def test_configures_console_for_unicode_output():
	class FakeOutput:
		def __init__(self):
			self.settings = None

		def reconfigure(self, **settings):
			self.settings = settings

	output = FakeOutput()

	configure_console_encoding(output)

	assert output.settings == {"encoding": "utf-8", "errors": "replace"}


def test_runs_complete_flow_and_prints_llm_explanation(tmp_path, monkeypatch):
	config_path, transaction_path = write_inputs(tmp_path, qualifying_rows())
	monkeypatch.setenv("GROQ_API_KEY", "test-key")
	monkeypatch.setattr(
		"explainer.urlopen",
		lambda request, timeout: FakeResponse(
			b'{"choices":[{"message":{"content":"Account ACC-1 has 3 transactions totaling 60000 from 2026-01-01T08:00:00 to 2026-01-01T10:00:00: T1 20000 at 2026-01-01T08:00:00, T2 20000 at 2026-01-01T09:00:00, and T3 20000 at 2026-01-01T10:00:00. The deterministic detector identified the configured structuring pattern."}}]}'
		),
	)
	output = io.StringIO()

	status = run_application(config_path, transaction_path, output)

	text = output.getvalue()
	assert status == 0
	assert "Account: ACC-1" in text
	assert "T1: 20000" in text
	assert "Combined amount: 60000" in text
	assert "Pattern: Structuring" in text
	assert "Severity score: 80.00" in text
	assert "AI explanation: Account ACC-1 has 3 transactions totaling 60000" in text


def test_prints_message_for_empty_detection_result(tmp_path, monkeypatch):
	rows = [transaction("T1", "ACC-1", 20000, "2026-01-01T08:00:00", "debit")]
	config_path, transaction_path = write_inputs(tmp_path, rows)
	monkeypatch.delenv("GROQ_API_KEY", raising=False)
	output = io.StringIO()

	status = run_application(config_path, transaction_path, output)

	assert status == 0
	assert output.getvalue() == "No flagged accounts found.\n"


def test_llm_failure_does_not_hide_deterministic_results(tmp_path, monkeypatch):
	config_path, transaction_path = write_inputs(tmp_path, qualifying_rows())
	monkeypatch.setenv("GROQ_API_KEY", "test-key")

	def failing_urlopen(request, timeout):
		raise OSError("network unavailable")

	monkeypatch.setattr("explainer.urlopen", failing_urlopen)
	output = io.StringIO()

	status = run_application(config_path, transaction_path, output)

	text = output.getvalue()
	assert status == 0
	assert "Account: ACC-1" in text
	assert "T1: 20000" in text
	assert "Combined amount: 60000" in text
	assert "Severity score: 80.00" in text
	assert "AI explanation unavailable: LLM API request failed." in text


def test_pass_through_only_finding_is_printed_with_score(tmp_path, monkeypatch):
	rows = [
		transaction("C1", "ACC-PT", 1000, "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-PT", 800, "2026-01-01T09:00:00", "debit"),
	]
	config_path, transaction_path = write_inputs(tmp_path, rows)
	monkeypatch.delenv("GROQ_API_KEY", raising=False)
	output = io.StringIO()

	status = run_application(config_path, transaction_path, output)

	text = output.getvalue()
	assert status == 0
	assert "Pattern: Rapid Pass-Through" in text
	assert "Account: ACC-PT" in text
	assert "Severity score: 70.92" in text
	assert "Incoming: C1: 1000" in text
	assert "D1: 800" in text
	assert "Total outgoing amount: 800" in text
	assert "Outflow ratio: 0.8000" in text


def test_both_pattern_types_are_printed(tmp_path, monkeypatch):
	rows = qualifying_rows() + [
		transaction("PC1", "ACC-PT", 1000, "2026-01-02T08:00:00", "credit"),
		transaction("PD1", "ACC-PT", 800, "2026-01-02T09:00:00", "debit"),
	]
	config_path, transaction_path = write_inputs(tmp_path, rows)
	monkeypatch.setenv("GROQ_API_KEY", "test-key")
	responses = iter(
		[
			b'{"choices":[{"message":{"content":"Account ACC-1 has 3 transactions totaling 60000 from 2026-01-01T08:00:00 to 2026-01-01T10:00:00: T1 20000 at 2026-01-01T08:00:00, T2 20000 at 2026-01-01T09:00:00, and T3 20000 at 2026-01-01T10:00:00. The deterministic detector identified the configured structuring pattern."}}]}',
			b'{"choices":[{"message":{"content":"Account ACC-PT received credit PC1 for 1000 at 2026-01-02T08:00:00, followed by debit PD1 for 800 at 2026-01-02T09:00:00. The supplied outflow ratio is 0.8000 over 1.0000 elapsed hours. The deterministic detector identified a qualifying credit-followed-by-debit pattern."}}]}',
		]
	)
	monkeypatch.setattr(
		"explainer.urlopen",
		lambda request, timeout: FakeResponse(next(responses)),
	)
	output = io.StringIO()

	status = run_application(config_path, transaction_path, output)

	text = output.getvalue()
	assert status == 0
	assert "Pattern: Structuring" in text
	assert "Pattern: Rapid Pass-Through" in text
	assert "AI explanation: Account ACC-1 has 3 transactions totaling 60000" in text
	assert "AI explanation: Account ACC-PT received credit PC1 for 1000" in text
	assert "Severity score:" in text


def test_both_llm_failures_preserve_both_deterministic_findings(tmp_path, monkeypatch):
	rows = qualifying_rows() + [
		transaction("PC1", "ACC-PT", 1000, "2026-01-02T08:00:00", "credit"),
		transaction("PD1", "ACC-PT", 800, "2026-01-02T09:00:00", "debit"),
	]
	config_path, transaction_path = write_inputs(tmp_path, rows)
	monkeypatch.setenv("GROQ_API_KEY", "test-key")

	def failing_urlopen(request, timeout):
		raise OSError("network unavailable")

	monkeypatch.setattr("explainer.urlopen", failing_urlopen)
	output = io.StringIO()

	status = run_application(config_path, transaction_path, output)

	text = output.getvalue()
	assert status == 0
	assert "Account: ACC-1" in text
	assert "Account: ACC-PT" in text
	assert "Pattern: Structuring" in text
	assert "Pattern: Rapid Pass-Through" in text
	assert text.count("Severity score:") == 2
	assert text.count("AI explanation unavailable: LLM API request failed.") == 2


def test_detailed_http_error_reaches_cli_output(tmp_path, monkeypatch):
	config_path, transaction_path = write_inputs(tmp_path, qualifying_rows())
	monkeypatch.setenv("GROQ_API_KEY", "test-key")

	def failing_urlopen(request, timeout):
		body = json.dumps(
			{
				"error": {
					"message": "Request rejected by Groq",
					"code": "forbidden",
				}
			}
		).encode()
		raise HTTPError(request.full_url, 403, "Forbidden", {}, BytesIO(body))

	monkeypatch.setattr("explainer.urlopen", failing_urlopen)
	output = io.StringIO()

	status = run_application(config_path, transaction_path, output)

	text = output.getvalue()
	assert status == 0
	assert "Account: ACC-1" in text
	assert "AI explanation unavailable: LLM API returned HTTP 403" in text
	assert "Content-Type: unknown" in text
	assert "(forbidden): Request rejected by Groq" in text


def test_reports_missing_input_file(tmp_path):
	output = io.StringIO()

	status = run_application(tmp_path / "missing.json", tmp_path / "missing.csv", output)

	assert status == 1
	assert "Configuration file was not found" in output.getvalue()