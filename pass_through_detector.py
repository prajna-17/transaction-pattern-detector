"""Deterministic detection of rapid pass-through transaction patterns."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable

from config_loader import Configuration
from detector import Transaction


@dataclass(frozen=True)
class RapidPassThroughEvidence:
	"""Store the exact incoming and outgoing transactions for one match."""

	account_id: str
	incoming_transaction: Transaction
	outgoing_transactions: tuple[Transaction, ...]
	total_outgoing_amount: Decimal
	outflow_ratio: Decimal
	window_start: datetime
	window_end: datetime


@dataclass(frozen=True)
class RapidPassThroughAccount:
	"""Store all rapid pass-through matches for one account."""

	account_id: str
	evidence: tuple[RapidPassThroughEvidence, ...]


def _validate_transactions(transactions: list[Transaction]) -> None:
	"""Reject duplicate IDs and mixed timezone-aware timestamp styles."""
	transaction_ids = [transaction.txn_id for transaction in transactions]
	duplicate_ids = sorted({
		transaction_id
		for transaction_id in transaction_ids
		if transaction_ids.count(transaction_id) > 1
	})
	if duplicate_ids:
		duplicates = ", ".join(duplicate_ids)
		raise ValueError(f"Duplicate transaction ID(s): {duplicates}.")

	awareness = {
		transaction.timestamp.tzinfo is not None
		and transaction.timestamp.utcoffset() is not None
		for transaction in transactions
	}
	if len(awareness) > 1:
		raise ValueError(
			"Transactions must use either all timezone-naive or all "
			"timezone-aware timestamps; mixed timestamps are not supported."
		)


def _window_duration(window_hours: Decimal) -> timedelta:
	"""Convert Decimal hours to an exact timedelta at microsecond precision."""
	total_microseconds = window_hours * Decimal("3600000000")
	if total_microseconds != total_microseconds.to_integral_value():
		raise ValueError(
			"Rapid pass-through window must be representable to microsecond precision."
		)
	return timedelta(microseconds=int(total_microseconds))


def _make_evidence(
	account_id: str,
	incoming: Transaction,
	outgoing: tuple[Transaction, ...],
	total_outgoing: Decimal,
) -> RapidPassThroughEvidence:
	"""Build typed evidence after a qualifying match has been found."""
	return RapidPassThroughEvidence(
		account_id=account_id,
		incoming_transaction=incoming,
		outgoing_transactions=outgoing,
		total_outgoing_amount=total_outgoing,
		outflow_ratio=total_outgoing / incoming.amount,
		window_start=incoming.timestamp,
		window_end=outgoing[-1].timestamp,
	)


def detect_rapid_pass_through(
	transactions: Iterable[Transaction], config: Configuration
) -> list[RapidPassThroughAccount]:
	"""Return accounts where incoming credits are followed by qualifying debits."""
	transaction_list = list(transactions)
	_validate_transactions(transaction_list)
	transactions_by_account: dict[str, list[Transaction]] = {}
	for transaction in transaction_list:
		transactions_by_account.setdefault(transaction.account_id, []).append(transaction)

	window_duration = _window_duration(config.rapid_pass_through_window_hours)
	required_ratio = config.rapid_pass_through_min_outflow_ratio
	flagged_accounts: list[RapidPassThroughAccount] = []

	for account_id, account_transactions in sorted(transactions_by_account.items()):
		credits = sorted(
			(
				transaction
				for transaction in account_transactions
				if transaction.direction == "credit" and transaction.amount > 0
			),
			key=lambda transaction: (transaction.timestamp, transaction.txn_id),
		)
		debits = sorted(
			(
				transaction
				for transaction in account_transactions
				if transaction.direction == "debit"
			),
			key=lambda transaction: (transaction.timestamp, transaction.txn_id),
		)

		consumed_debit_ids: set[str] = set()
		evidence: list[RapidPassThroughEvidence] = []
		for incoming in credits:
			latest_time = incoming.timestamp + window_duration
			outgoing: list[Transaction] = []
			total_outgoing = Decimal("0")
			required_amount = incoming.amount * required_ratio

			for debit in debits:
				if debit.txn_id in consumed_debit_ids:
					continue
				if debit.timestamp < incoming.timestamp:
					continue
				if debit.timestamp > latest_time:
					break

				outgoing.append(debit)
				total_outgoing += debit.amount
				if total_outgoing >= required_amount:
					outgoing_tuple = tuple(outgoing)
					evidence.append(
						_make_evidence(
							account_id,
							incoming,
							outgoing_tuple,
							total_outgoing,
						)
					)
					consumed_debit_ids.update(
						debit.txn_id for debit in outgoing_tuple
					)
					break

		if evidence:
			flagged_accounts.append(
				RapidPassThroughAccount(account_id, tuple(evidence))
			)

	return flagged_accounts