import csv
from decimal import Decimal

import pytest

from config_loader import Configuration
from detector import TransactionInputError, detect_structuring, load_transactions


def make_config():
	"""Return the configuration used by the detector tests."""
	return Configuration(Decimal("50000"), Decimal("72"), 3)


def write_transactions(tmp_path, rows):
	"""Write transaction dictionaries to a temporary CSV file."""
	path = tmp_path / "transactions.csv"
	columns = [
		"txn_id",
		"account_id",
		"counterparty",
		"amount",
		"direction",
		"timestamp",
		"channel",
	]
	with path.open("w", newline="", encoding="utf-8") as transaction_file:
		writer = csv.DictWriter(transaction_file, fieldnames=columns)
		writer.writeheader()
		writer.writerows(rows)
	return path


def transaction(txn_id, account_id, amount, timestamp, direction="credit"):
	"""Create a readable transaction row for a test."""
	return {
		"txn_id": txn_id,
		"account_id": account_id,
		"counterparty": "CP-1",
		"amount": str(amount),
		"direction": direction,
		"timestamp": timestamp,
		"channel": "UPI",
	}


def detect_rows(tmp_path, rows):
	"""Load test rows and run the deterministic detector."""
	return detect_structuring(load_transactions(write_transactions(tmp_path, rows)), make_config())


def test_detects_valid_qualifying_account(tmp_path):
	rows = [
		transaction("T1", "ACC-1", 15000, "2026-01-01T08:00:00"),
		transaction("T2", "ACC-1", 18000, "2026-01-02T08:00:00"),
		transaction("T3", "ACC-1", 20000, "2026-01-03T08:00:00"),
	]

	flagged = detect_rows(tmp_path, rows)

	evidence = flagged[0].evidence_windows[0]
	assert flagged[0].account_id == "ACC-1"
	assert [item.txn_id for item in evidence.transactions] == ["T1", "T2", "T3"]
	assert evidence.transaction_count == 3
	assert evidence.combined_amount == Decimal("53000")
	assert evidence.window_start.isoformat() == "2026-01-01T08:00:00"
	assert evidence.window_end.isoformat() == "2026-01-03T08:00:00"


def test_does_not_flag_combined_amount_equal_to_threshold(tmp_path):
	rows = [
		transaction("T1", "ACC-1", 15000, "2026-01-01T08:00:00"),
		transaction("T2", "ACC-1", 16000, "2026-01-01T09:00:00"),
		transaction("T3", "ACC-1", 19000, "2026-01-01T10:00:00"),
	]

	assert detect_rows(tmp_path, rows) == []


def test_does_not_flag_transactions_outside_rolling_window(tmp_path):
	rows = [
		transaction("T1", "ACC-1", 20000, "2026-01-01T08:00:00"),
		transaction("T2", "ACC-1", 20000, "2026-01-04T08:01:00"),
		transaction("T3", "ACC-1", 20000, "2026-01-04T09:00:00"),
	]

	assert detect_rows(tmp_path, rows) == []


def test_ignores_debit_transactions(tmp_path):
	rows = [
		transaction("T1", "ACC-1", 20000, "2026-01-01T08:00:00", "debit"),
		transaction("T2", "ACC-1", 20000, "2026-01-01T09:00:00", "debit"),
		transaction("T3", "ACC-1", 20000, "2026-01-01T10:00:00", "debit"),
	]

	assert detect_rows(tmp_path, rows) == []


def test_does_not_flag_fewer_than_minimum_transactions(tmp_path):
	rows = [
		transaction("T1", "ACC-1", 30000, "2026-01-01T08:00:00"),
		transaction("T2", "ACC-1", 30000, "2026-01-01T09:00:00"),
	]

	assert detect_rows(tmp_path, rows) == []


def test_includes_transactions_exactly_on_window_boundary(tmp_path):
	rows = [
		transaction("T1", "ACC-1", 18000, "2026-01-01T08:00:00"),
		transaction("T2", "ACC-1", 18000, "2026-01-02T08:00:00"),
		transaction("T3", "ACC-1", 18000, "2026-01-04T08:00:00"),
	]

	flagged = detect_rows(tmp_path, rows)

	assert len(flagged) == 1
	assert flagged[0].evidence_windows[0].transaction_count == 3


def test_detects_multiple_accounts(tmp_path):
	rows = [
		transaction("A1", "ACC-1", 20000, "2026-01-01T08:00:00"),
		transaction("A2", "ACC-1", 20000, "2026-01-01T09:00:00"),
		transaction("A3", "ACC-1", 20000, "2026-01-01T10:00:00"),
		transaction("B1", "ACC-2", 20000, "2026-01-01T08:00:00"),
		transaction("B2", "ACC-2", 20000, "2026-01-01T09:00:00"),
		transaction("B3", "ACC-2", 20000, "2026-01-01T10:00:00"),
	]

	assert [account.account_id for account in detect_rows(tmp_path, rows)] == [
		"ACC-1",
		"ACC-2",
	]


def test_excludes_individual_transaction_equal_to_threshold(tmp_path):
	rows = [
		transaction("T1", "ACC-1", 50000, "2026-01-01T08:00:00"),
		transaction("T2", "ACC-1", 10000, "2026-01-01T09:00:00"),
		transaction("T3", "ACC-1", 10000, "2026-01-01T10:00:00"),
	]

	assert detect_rows(tmp_path, rows) == []


def test_keeps_multiple_distinct_minimal_windows(tmp_path):
	rows = [
		transaction("T1", "ACC-1", 20000, "2026-01-01T08:00:00"),
		transaction("T2", "ACC-1", 20000, "2026-01-01T09:00:00"),
		transaction("T3", "ACC-1", 20000, "2026-01-01T10:00:00"),
		transaction("T4", "ACC-1", 20000, "2026-01-01T11:00:00"),
	]

	flagged = detect_rows(tmp_path, rows)

	windows = flagged[0].evidence_windows
	assert len(windows) == 2
	assert [item.txn_id for item in windows[0].transactions] == ["T1", "T2", "T3"]
	assert [item.txn_id for item in windows[1].transactions] == ["T2", "T3", "T4"]


def test_rejects_csv_with_missing_required_column(tmp_path):
	path = tmp_path / "transactions.csv"
	path.write_text("txn_id,account_id\nT1,ACC-1\n", encoding="utf-8")

	with pytest.raises(TransactionInputError, match="missing required column"):
		load_transactions(path)
