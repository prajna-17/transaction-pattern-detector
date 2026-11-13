from datetime import datetime
from decimal import Decimal

import pytest

from config_loader import Configuration
from detector import Transaction
from pass_through_detector import detect_rapid_pass_through


def make_config(window_hours="72", ratio="0.8"):
	"""Return configuration values used by pass-through tests."""
	return Configuration(
		Decimal("50000"),
		Decimal("72"),
		3,
		Decimal(window_hours),
		Decimal(ratio),
	)


def transaction(txn_id, account_id, amount, timestamp, direction):
	"""Create a typed transaction for a detector test."""
	return Transaction(
		txn_id=txn_id,
		account_id=account_id,
		counterparty="CP-1",
		amount=Decimal(str(amount)),
		direction=direction,
		timestamp=datetime.fromisoformat(timestamp),
		channel="UPI",
	)


def detect(rows, window_hours="72", ratio="0.8"):
	"""Run the pass-through detector against test transactions."""
	return detect_rapid_pass_through(rows, make_config(window_hours, ratio))


def test_detects_one_qualifying_credit_debit_pair():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-1", "800", "2026-01-01T09:00:00", "debit"),
	]

	accounts = detect(rows)

	evidence = accounts[0].evidence[0]
	assert accounts[0].account_id == "ACC-1"
	assert evidence.incoming_transaction.txn_id == "C1"
	assert [item.txn_id for item in evidence.outgoing_transactions] == ["D1"]


def test_ignores_debit_before_credit():
	rows = [
		transaction("D1", "ACC-1", "800", "2026-01-01T07:00:00", "debit"),
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
	]

	assert detect(rows) == []


def test_accepts_all_naive_timestamps():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-1", "800", "2026-01-01T09:00:00", "debit"),
	]

	assert len(detect(rows)) == 1


def test_accepts_all_timezone_aware_timestamps():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00+00:00", "credit"),
		transaction("D1", "ACC-1", "800", "2026-01-01T09:00:00+00:00", "debit"),
	]

	assert len(detect(rows)) == 1


def test_rejects_mixed_timezone_awareness():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-1", "800", "2026-01-01T09:00:00+00:00", "debit"),
	]

	with pytest.raises(ValueError, match="mixed timestamps"):
		detect(rows)


def test_rejects_duplicate_transaction_ids():
	rows = [
		transaction("DUPLICATE", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("DUPLICATE", "ACC-1", "800", "2026-01-01T09:00:00", "debit"),
	]

	with pytest.raises(ValueError, match="Duplicate transaction ID"):
		detect(rows)


def test_does_not_match_different_accounts():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-2", "800", "2026-01-01T09:00:00", "debit"),
	]

	assert detect(rows) == []


def test_multiple_debits_can_satisfy_one_credit():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-1", "300", "2026-01-01T09:00:00", "debit"),
		transaction("D2", "ACC-1", "500", "2026-01-01T10:00:00", "debit"),
	]

	evidence = detect(rows)[0].evidence[0]

	assert [item.txn_id for item in evidence.outgoing_transactions] == ["D1", "D2"]
	assert evidence.total_outgoing_amount == Decimal("800")


def test_debit_cannot_be_reused():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("C2", "ACC-1", "1000", "2026-01-01T09:00:00", "credit"),
		transaction("D1", "ACC-1", "800", "2026-01-01T10:00:00", "debit"),
	]

	accounts = detect(rows)

	assert len(accounts[0].evidence) == 1
	assert accounts[0].evidence[0].incoming_transaction.txn_id == "C1"


def test_exact_window_boundary_is_included():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-1", "800", "2026-01-04T08:00:00", "debit"),
	]

	assert len(detect(rows)[0].evidence) == 1


def test_transaction_just_outside_window_is_excluded():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-1", "800", "2026-01-04T08:01:00", "debit"),
	]

	assert detect(rows) == []


def test_outside_window_debit_does_not_stop_later_eligible_debit():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D-OUT", "ACC-1", "800", "2026-01-04T08:01:00", "debit"),
		transaction("D-IN", "ACC-1", "800", "2026-01-02T08:00:00", "debit"),
	]

	evidence = detect(rows)[0].evidence[0]

	assert [item.txn_id for item in evidence.outgoing_transactions] == ["D-IN"]


def test_fractional_window_is_supported_without_float_conversion():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-1", "800", "2026-01-01T09:30:00", "debit"),
	]

	assert len(detect(rows, window_hours="1.5")[0].evidence) == 1


def test_exact_ratio_boundary_qualifies():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-1", "800", "2026-01-01T09:00:00", "debit"),
	]

	assert detect(rows)[0].evidence[0].outflow_ratio == Decimal("0.8")


def test_ratio_just_below_boundary_does_not_qualify():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-1", "799.99", "2026-01-01T09:00:00", "debit"),
	]

	assert detect(rows) == []


def test_zero_value_credit_is_ignored_safely():
	rows = [
		transaction("C0", "ACC-1", "0", "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-1", "800", "2026-01-01T09:00:00", "debit"),
	]

	assert detect(rows) == []


def test_zero_value_debit_does_not_change_match_amount():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D0", "ACC-1", "0", "2026-01-01T09:00:00", "debit"),
		transaction("D1", "ACC-1", "800", "2026-01-01T10:00:00", "debit"),
	]

	evidence = detect(rows)[0].evidence[0]

	assert evidence.total_outgoing_amount == Decimal("800")
	assert [item.txn_id for item in evidence.outgoing_transactions] == ["D0", "D1"]


def test_debit_larger_than_incoming_amount_qualifies():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-1", "1200", "2026-01-01T09:00:00", "debit"),
	]

	evidence = detect(rows)[0].evidence[0]

	assert evidence.total_outgoing_amount == Decimal("1200")
	assert evidence.outflow_ratio == Decimal("1.2")


def test_preserves_exact_evidence_fields():
	incoming = transaction("C-EXACT", "ACC-EXACT", "1234.56", "2026-01-01T08:00:00", "credit")
	outgoing = transaction("D-EXACT", "ACC-EXACT", "987.65", "2026-01-01T10:30:00", "debit")

	evidence = detect([incoming, outgoing])[0].evidence[0]

	assert evidence.account_id == "ACC-EXACT"
	assert evidence.incoming_transaction == incoming
	assert evidence.outgoing_transactions == (outgoing,)
	assert evidence.total_outgoing_amount == Decimal("987.65")
	assert evidence.window_start == datetime.fromisoformat("2026-01-01T08:00:00")
	assert evidence.window_end == datetime.fromisoformat("2026-01-01T10:30:00")
	assert evidence.incoming_transaction.direction == "credit"
	assert evidence.outgoing_transactions[0].direction == "debit"


def test_multiple_credits_with_distinct_debits_are_preserved():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-1", "800", "2026-01-01T09:00:00", "debit"),
		transaction("C2", "ACC-1", "2000", "2026-01-02T08:00:00", "credit"),
		transaction("D2", "ACC-1", "1600", "2026-01-02T09:00:00", "debit"),
	]

	evidence = detect(rows)[0].evidence

	assert [item.incoming_transaction.txn_id for item in evidence] == ["C1", "C2"]
	assert [item.outgoing_transactions[0].txn_id for item in evidence] == ["D1", "D2"]


def test_failed_candidate_does_not_consume_debits():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("C2", "ACC-1", "500", "2026-01-01T09:00:00", "credit"),
		transaction("D1", "ACC-1", "400", "2026-01-01T10:00:00", "debit"),
	]

	evidence = detect(rows)[0].evidence

	assert len(evidence) == 1
	assert evidence[0].incoming_transaction.txn_id == "C2"
	assert evidence[0].outgoing_transactions[0].txn_id == "D1"


def test_identical_timestamps_use_transaction_id_as_deterministic_tie_breaker():
	rows = [
		transaction("C2", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-1", "800", "2026-01-01T09:00:00", "debit"),
	]

	evidence = detect(rows)[0].evidence

	assert len(evidence) == 1
	assert evidence[0].incoming_transaction.txn_id == "C1"


def test_competing_credits_use_fifo_order():
	rows = [
		transaction("C2", "ACC-1", "1000", "2026-01-01T09:00:00", "credit"),
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("D1", "ACC-1", "800", "2026-01-01T10:00:00", "debit"),
	]

	evidence = detect(rows)[0].evidence

	assert len(evidence) == 1
	assert evidence[0].incoming_transaction.txn_id == "C1"


def test_no_debit_id_appears_more_than_once_in_evidence():
	rows = [
		transaction("C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"),
		transaction("C2", "ACC-1", "1000", "2026-01-01T09:00:00", "credit"),
		transaction("D1", "ACC-1", "800", "2026-01-01T10:00:00", "debit"),
		transaction("D2", "ACC-1", "800", "2026-01-01T11:00:00", "debit"),
	]

	evidence = detect(rows)[0].evidence
	debit_ids = [
		item.txn_id
		for match in evidence
		for item in match.outgoing_transactions
	]

	assert len(debit_ids) == len(set(debit_ids))