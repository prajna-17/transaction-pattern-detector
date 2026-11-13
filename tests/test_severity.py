from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

from config_loader import Configuration
from detector import EvidenceWindow, Transaction
from pass_through_detector import RapidPassThroughEvidence
from severity import score_rapid_pass_through_evidence, score_structuring_evidence


def make_config():
	"""Return the approved detector configuration for scoring tests."""
	return Configuration(
		Decimal("50000"),
		Decimal("72"),
		3,
		Decimal("72"),
		Decimal("0.8"),
	)


def transaction(txn_id, account_id, amount, timestamp, direction):
	"""Create a typed transaction for scoring tests."""
	return Transaction(
		txn_id=txn_id,
		account_id=account_id,
		counterparty="CP-1",
		amount=Decimal(str(amount)),
		direction=direction,
		timestamp=datetime.fromisoformat(timestamp),
		channel="UPI",
	)


def structuring_evidence(amounts=("10000", "15000", "30000")):
	"""Build valid structuring evidence with supplied credit amounts."""
	transactions = tuple(
		transaction(
			f"C{index}",
			"ACC-1",
			amount,
			f"2026-01-01T0{index}:00:00",
			"credit",
		)
		for index, amount in enumerate(amounts, start=1)
	)
	return EvidenceWindow(
		account_id="ACC-1",
		transactions=transactions,
		transaction_count=len(transactions),
		combined_amount=sum((item.amount for item in transactions), Decimal("0")),
		window_start=transactions[0].timestamp,
		window_end=transactions[-1].timestamp,
	)


def pass_through_evidence(
	outgoing_amounts=("800",), elapsed_hours=2, outgoing_timestamps=None
):
	"""Build valid pass-through evidence with a configurable speed."""
	incoming = transaction(
		"C1", "ACC-1", "1000", "2026-01-01T08:00:00", "credit"
	)
	if outgoing_timestamps is None:
		outgoing_timestamps = [
			(
				datetime.fromisoformat("2026-01-01T08:00:00")
				+ timedelta(hours=elapsed_hours)
			).isoformat()
		] * len(outgoing_amounts)
	outgoing = tuple(
		transaction(
			f"D{index}",
			"ACC-1",
			amount,
			outgoing_timestamps[index - 1],
			"debit",
		)
		for index, amount in enumerate(outgoing_amounts, start=1)
	)
	total = sum((item.amount for item in outgoing), Decimal("0"))
	return RapidPassThroughEvidence(
		account_id="ACC-1",
		incoming_transaction=incoming,
		outgoing_transactions=outgoing,
		total_outgoing_amount=total,
		outflow_ratio=total / incoming.amount,
		window_start=incoming.timestamp,
		window_end=outgoing[-1].timestamp,
	)


def test_structuring_score_uses_all_formula_components():
	evidence = structuring_evidence()

	assert score_structuring_evidence(evidence, make_config()) == Decimal("75.83")


def test_rapid_pass_through_score_uses_all_formula_components():
	evidence = pass_through_evidence()

	assert score_rapid_pass_through_evidence(evidence, make_config()) == Decimal("70.50")


def test_invalid_evidence_scores_zero():
	assert score_structuring_evidence(None, make_config()) == Decimal("0.00")
	assert score_rapid_pass_through_evidence(None, make_config()) == Decimal("0.00")
	assert score_structuring_evidence(
		replace(structuring_evidence(), combined_amount=Decimal("1")),
		make_config(),
	) == Decimal("0.00")


def test_rapid_rejects_reverse_chronological_outgoing_evidence():
	evidence = pass_through_evidence(
		("400", "400"),
		outgoing_timestamps=("2026-01-01T10:00:00", "2026-01-01T09:00:00"),
	)

	assert score_rapid_pass_through_evidence(evidence, make_config()) == Decimal("0.00")


def test_rapid_rejects_outgoing_transaction_beyond_window():
	evidence = pass_through_evidence(
		outgoing_timestamps=("2026-01-04T08:01:00",)
	)

	assert score_rapid_pass_through_evidence(evidence, make_config()) == Decimal("0.00")


def test_rapid_rejects_incorrect_window_boundaries():
	evidence = pass_through_evidence()

	assert score_rapid_pass_through_evidence(
		replace(evidence, window_start=datetime.fromisoformat("2026-01-01T07:00:00")),
		make_config(),
	) == Decimal("0.00")
	assert score_rapid_pass_through_evidence(
		replace(evidence, window_end=datetime.fromisoformat("2026-01-01T11:00:00")),
		make_config(),
	) == Decimal("0.00")


def test_rapid_rejects_incorrect_total_ratio_account_or_ids():
	evidence = pass_through_evidence(("800", "100"))
	second = replace(evidence.outgoing_transactions[1], account_id="ACC-2")

	assert score_rapid_pass_through_evidence(
		replace(evidence, total_outgoing_amount=Decimal("1")), make_config()
	) == Decimal("0.00")
	assert score_rapid_pass_through_evidence(
		replace(evidence, outflow_ratio=Decimal("0.1")), make_config()
	) == Decimal("0.00")
	assert score_rapid_pass_through_evidence(
		replace(evidence, outgoing_transactions=(evidence.outgoing_transactions[0], second)),
		make_config(),
	) == Decimal("0.00")


def test_rapid_rejects_invalid_direction_and_duplicate_id():
	evidence = pass_through_evidence(("400", "400"))
	wrong_direction = replace(evidence.outgoing_transactions[0], direction="credit")
	duplicate_id = replace(
		evidence.outgoing_transactions[1],
		txn_id=evidence.outgoing_transactions[0].txn_id,
	)

	assert score_rapid_pass_through_evidence(
		replace(evidence, outgoing_transactions=(wrong_direction, evidence.outgoing_transactions[1])),
		make_config(),
	) == Decimal("0.00")
	assert score_rapid_pass_through_evidence(
		replace(evidence, outgoing_transactions=(evidence.outgoing_transactions[0], duplicate_id)),
		make_config(),
	) == Decimal("0.00")


def test_structuring_rejects_transactions_outside_window_or_wrong_total():
	evidence = structuring_evidence()
	outside = replace(
		evidence.transactions[-1],
		timestamp=datetime.fromisoformat("2026-01-05T00:00:00"),
	)
	outside_evidence = replace(
		evidence,
		transactions=(evidence.transactions[0], evidence.transactions[1], outside),
		window_end=outside.timestamp,
	)

	assert score_structuring_evidence(outside_evidence, make_config()) == Decimal("0.00")
	assert score_structuring_evidence(
		replace(evidence, combined_amount=Decimal("1")), make_config()
	) == Decimal("0.00")


def test_structuring_rejects_wrong_direction_and_duplicate_id():
	evidence = structuring_evidence()
	wrong_direction = replace(evidence.transactions[0], direction="debit")
	duplicate_id = replace(
		evidence.transactions[1], txn_id=evidence.transactions[0].txn_id
	)

	assert score_structuring_evidence(
		replace(
			evidence,
			transactions=(wrong_direction,) + evidence.transactions[1:],
		),
		make_config(),
	) == Decimal("0.00")
	assert score_structuring_evidence(
		replace(
			evidence,
			transactions=(evidence.transactions[0], duplicate_id, evidence.transactions[2]),
		),
		make_config(),
	) == Decimal("0.00")
	assert score_rapid_pass_through_evidence(
		replace(pass_through_evidence(), outflow_ratio=Decimal("0.1")),
		make_config(),
	) == Decimal("0.00")


def test_scores_stay_between_zero_and_one_hundred():
	structuring = structuring_evidence(("49999",) * 6)
	pass_through = pass_through_evidence(("600", "600", "600"), elapsed_hours=0)

	structuring_score = score_structuring_evidence(structuring, make_config())
	pass_through_score = score_rapid_pass_through_evidence(pass_through, make_config())

	assert Decimal("0") <= structuring_score <= Decimal("100")
	assert Decimal("0") <= pass_through_score <= Decimal("100")
	assert structuring_score == Decimal("100.00")
	assert pass_through_score == Decimal("100.00")


def test_scores_are_rounded_to_two_decimal_places():
	score = score_structuring_evidence(
		structuring_evidence(("12345.67", "23456.78", "34567.89")),
		make_config(),
	)

	assert score == Decimal("88.64")
	assert score.as_tuple().exponent == -2


def test_faster_pass_through_has_higher_speed_score():
	fast = score_rapid_pass_through_evidence(
		pass_through_evidence(elapsed_hours=1), make_config()
	)
	slow = score_rapid_pass_through_evidence(
		pass_through_evidence(elapsed_hours=24), make_config()
	)

	assert fast > slow


def test_higher_outflow_ratio_has_higher_score():
	lower = score_rapid_pass_through_evidence(
		pass_through_evidence(("800",), elapsed_hours=2), make_config()
	)
	higher = score_rapid_pass_through_evidence(
		pass_through_evidence(("1200",), elapsed_hours=2), make_config()
	)

	assert higher > lower


def test_repeated_identical_inputs_have_identical_scores():
	evidence = structuring_evidence()

	assert score_structuring_evidence(evidence, make_config()) == score_structuring_evidence(
		evidence, make_config()
	)