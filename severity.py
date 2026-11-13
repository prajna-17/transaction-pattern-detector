"""Deterministic prioritization scores for detector evidence."""

from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from config_loader import Configuration
from detector import EvidenceWindow
from pass_through_detector import RapidPassThroughEvidence


ZERO_SCORE = Decimal("0.00")
ONE = Decimal("1")
HUNDRED = Decimal("100")
TWO_PLACES = Decimal("0.01")


def _round_score(score: Decimal) -> Decimal:
	"""Clamp a score to 0-100 and round it to two decimal places."""
	clamped_score = max(Decimal("0"), min(HUNDRED, score))
	return clamped_score.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _decimal_hours(duration: timedelta) -> Decimal:
	"""Convert a timedelta to hours without using floating-point arithmetic."""
	seconds = Decimal(duration.days * 86400 + duration.seconds)
	microseconds = Decimal(duration.microseconds) / Decimal("1000000")
	return (seconds + microseconds) / Decimal("3600")


def _decimal_timedelta(hours: Decimal) -> timedelta:
	"""Convert Decimal hours to a microsecond-precise timedelta."""
	total_microseconds = hours * Decimal("3600000000")
	if total_microseconds != total_microseconds.to_integral_value():
		raise ValueError("Configured window is not representable to microsecond precision.")
	return timedelta(microseconds=int(total_microseconds))


def _structuring_is_consistent(
	evidence: EvidenceWindow, config: Configuration
) -> bool:
	"""Check that structuring evidence still satisfies detector conditions."""
	transactions = evidence.transactions
	if (
		config.reporting_threshold <= 0
		or config.rolling_window_hours <= 0
		or config.min_transaction_count <= 0
	):
		return False
	if not transactions or evidence.transaction_count != len(transactions):
		return False
	if evidence.account_id != transactions[0].account_id:
		return False
	if evidence.transaction_count < config.min_transaction_count:
		return False
	if len({transaction.txn_id for transaction in transactions}) != len(transactions):
		return False
	if list(transactions) != sorted(
		transactions, key=lambda transaction: (transaction.timestamp, transaction.txn_id)
	):
		return False
	if any(
		transaction.account_id != evidence.account_id
		or transaction.direction != "credit"
		or transaction.amount < 0
		or not transaction.amount.is_finite()
		or transaction.amount >= config.reporting_threshold
		for transaction in transactions
	):
		return False
	if not evidence.combined_amount.is_finite():
		return False
	if sum(
		(transaction.amount for transaction in transactions), Decimal("0")
	) != evidence.combined_amount:
		return False
	if evidence.combined_amount <= config.reporting_threshold:
		return False
	try:
		actual_start = min(transaction.timestamp for transaction in transactions)
		actual_end = max(transaction.timestamp for transaction in transactions)
		if evidence.window_start != actual_start or evidence.window_end != actual_end:
			return False
		if any(
			transaction.timestamp < evidence.window_start
			or transaction.timestamp > evidence.window_end
			for transaction in transactions
		):
			return False
		if evidence.window_end - evidence.window_start > _decimal_timedelta(
			config.rolling_window_hours
		):
			return False
		if evidence.window_start != transactions[0].timestamp:
			return False
		if evidence.window_end != transactions[-1].timestamp:
			return False
	except (TypeError, ValueError):
		return False
	return True


def score_structuring_evidence(
	evidence: EvidenceWindow | None, config: Configuration | None
) -> Decimal:
	"""Return a deterministic 0-100 prioritization score for structuring evidence."""
	if evidence is None or config is None:
		return ZERO_SCORE
	try:
		if not _structuring_is_consistent(evidence, config):
			return ZERO_SCORE
		transactions = evidence.transactions
		count_factor = (
			min(Decimal(evidence.transaction_count) / Decimal("6"), ONE) * Decimal("30")
		)
		amount_factor = (
			min(evidence.combined_amount / config.reporting_threshold, Decimal("2"))
			* Decimal("35")
		)
		average_proximity = sum(
			(transaction.amount / config.reporting_threshold for transaction in transactions),
			Decimal("0"),
		) / Decimal(len(transactions))
		proximity_factor = average_proximity * Decimal("20")
		return _round_score(
			count_factor + amount_factor + proximity_factor + Decimal("15")
		)
	except (AttributeError, InvalidOperation, TypeError, ValueError, ZeroDivisionError):
		return ZERO_SCORE


def _pass_through_is_consistent(
	evidence: RapidPassThroughEvidence, config: Configuration
) -> bool:
	"""Check that pass-through evidence still satisfies detector conditions."""
	incoming = evidence.incoming_transaction
	outgoing = evidence.outgoing_transactions
	if (
		config.rapid_pass_through_window_hours <= 0
		or config.rapid_pass_through_min_outflow_ratio <= 0
		or config.rapid_pass_through_min_outflow_ratio > 1
	):
		return False
	if not outgoing or incoming.direction != "credit" or incoming.amount <= 0:
		return False
	if not incoming.amount.is_finite():
		return False
	if evidence.account_id != incoming.account_id:
		return False
	all_transactions = (incoming,) + outgoing
	if len({transaction.txn_id for transaction in all_transactions}) != len(all_transactions):
		return False
	if list(outgoing) != sorted(
		outgoing, key=lambda transaction: (transaction.timestamp, transaction.txn_id)
	):
		return False
	if any(
		transaction.account_id != evidence.account_id
		or transaction.direction != "debit"
		or transaction.amount < 0
		or not transaction.amount.is_finite()
		for transaction in outgoing
	):
		return False
	try:
		if any(
			transaction.timestamp < incoming.timestamp
			for transaction in outgoing
		):
			return False
		window_duration = _decimal_timedelta(
			config.rapid_pass_through_window_hours
		)
		latest_allowed = incoming.timestamp + window_duration
		if any(
			transaction.timestamp > latest_allowed
			for transaction in outgoing
		):
			return False
		if evidence.window_start != incoming.timestamp:
			return False
		actual_start = min(transaction.timestamp for transaction in all_transactions)
		actual_end = max(transaction.timestamp for transaction in all_transactions)
		if evidence.window_start != actual_start or evidence.window_end != actual_end:
			return False
		if any(
			transaction.timestamp < evidence.window_start
			or transaction.timestamp > evidence.window_end
			for transaction in all_transactions
		):
			return False
		if evidence.window_end - evidence.window_start > window_duration:
			return False
		total_outgoing = sum(
			(transaction.amount for transaction in outgoing), Decimal("0")
		)
		if not evidence.total_outgoing_amount.is_finite():
			return False
		if total_outgoing != evidence.total_outgoing_amount:
			return False
		calculated_ratio = total_outgoing / incoming.amount
		if not evidence.outflow_ratio.is_finite():
			return False
		if calculated_ratio != evidence.outflow_ratio:
			return False
		return calculated_ratio >= config.rapid_pass_through_min_outflow_ratio
	except (AttributeError, InvalidOperation, TypeError, ValueError, ZeroDivisionError):
		return False


def score_rapid_pass_through_evidence(
	evidence: RapidPassThroughEvidence | None, config: Configuration | None
) -> Decimal:
	"""Return a deterministic 0-100 prioritization score for pass-through evidence."""
	if evidence is None or config is None:
		return ZERO_SCORE
	try:
		if not _pass_through_is_consistent(evidence, config):
			return ZERO_SCORE
		incoming = evidence.incoming_transaction
		elapsed_hours = _decimal_hours(
			evidence.window_end - evidence.window_start
		)
		speed_factor = max(
			Decimal("0"),
			ONE - elapsed_hours / config.rapid_pass_through_window_hours,
		) * Decimal("30")
		ratio_factor = (
			min(evidence.outflow_ratio, Decimal("1.5")) / Decimal("1.5")
			* Decimal("40")
		)
		count_factor = (
			min(Decimal(len(evidence.outgoing_transactions)) / Decimal("3"), ONE)
			* Decimal("15")
		)
		return _round_score(
			ratio_factor + speed_factor + count_factor + Decimal("15")
		)
	except (AttributeError, InvalidOperation, TypeError, ValueError, ZeroDivisionError):
		return ZERO_SCORE