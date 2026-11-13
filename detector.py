"""Deterministic detection of structuring patterns in transaction data."""

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from config_loader import Configuration


class TransactionInputError(ValueError):
	"""Explain that the transaction CSV cannot be used."""


@dataclass(frozen=True)
class Transaction:
	"""Represent one validated transaction from the input CSV."""

	txn_id: str
	account_id: str
	counterparty: str
	amount: Decimal
	direction: str
	timestamp: datetime
	channel: str


@dataclass(frozen=True)
class EvidenceWindow:
	"""Store the exact transactions that caused one detection."""

	account_id: str
	transactions: tuple[Transaction, ...]
	transaction_count: int
	combined_amount: Decimal
	window_start: datetime
	window_end: datetime


@dataclass(frozen=True)
class FlaggedAccount:
	"""Store one account and all of its distinct evidence windows."""

	account_id: str
	evidence_windows: tuple[EvidenceWindow, ...]


CSV_COLUMNS = {
	"txn_id",
	"account_id",
	"counterparty",
	"amount",
	"direction",
	"timestamp",
	"channel",
}


def _parse_amount(value: str, row_number: int) -> Decimal:
	"""Convert an amount to Decimal and reject invalid or negative values."""
	try:
		amount = Decimal(value)
	except (InvalidOperation, ValueError) as error:
		raise TransactionInputError(
			f"Row {row_number}: amount must be a valid number."
		) from error

	if not amount.is_finite() or amount < 0:
		raise TransactionInputError(
			f"Row {row_number}: amount must be a non-negative number."
		)
	return amount


def _parse_transaction(row: dict[str, str], row_number: int) -> Transaction:
	"""Validate one CSV row and convert it into a Transaction object."""
	missing_values = [
		column
		for column in CSV_COLUMNS
		if not isinstance(row.get(column), str) or not row[column].strip()
	]
	if missing_values:
		missing = ", ".join(sorted(missing_values))
		raise TransactionInputError(
			f"Row {row_number}: missing value(s) for {missing}."
		)

	direction = row["direction"].strip().lower()
	if direction not in {"credit", "debit"}:
		raise TransactionInputError(
			f"Row {row_number}: direction must be 'credit' or 'debit'."
		)

	try:
		timestamp = datetime.fromisoformat(row["timestamp"].strip())
	except ValueError as error:
		raise TransactionInputError(
			f"Row {row_number}: timestamp must be valid ISO format."
		) from error

	return Transaction(
		txn_id=row["txn_id"].strip(),
		account_id=row["account_id"].strip(),
		counterparty=row["counterparty"].strip(),
		amount=_parse_amount(row["amount"].strip(), row_number),
		direction=direction,
		timestamp=timestamp,
		channel=row["channel"].strip(),
	)


def load_transactions(path: str | Path = "data/transactions.csv") -> list[Transaction]:
	"""Read and validate all transactions from a CSV file."""
	transaction_path = Path(path)
	try:
		with transaction_path.open(newline="", encoding="utf-8") as transaction_file:
			reader = csv.DictReader(transaction_file)
			if reader.fieldnames is None:
				raise TransactionInputError("Transaction CSV is missing its header row.")
			missing_columns = sorted(CSV_COLUMNS - set(reader.fieldnames))
			if missing_columns:
				missing = ", ".join(missing_columns)
				raise TransactionInputError(
					f"Transaction CSV is missing required column(s): {missing}."
				)

			transactions = []
			seen_ids = set()
			for row_number, row in enumerate(reader, start=2):
				transaction = _parse_transaction(row, row_number)
				if transaction.txn_id in seen_ids:
					raise TransactionInputError(
						f"Row {row_number}: duplicate transaction ID '{transaction.txn_id}'."
					)
				seen_ids.add(transaction.txn_id)
				transactions.append(transaction)
			return transactions
	except FileNotFoundError as error:
		raise TransactionInputError(
			f"Transaction CSV was not found: {transaction_path}"
		) from error
	except csv.Error as error:
		raise TransactionInputError(
			f"Transaction CSV could not be parsed: {transaction_path}"
		) from error
	except OSError as error:
		raise TransactionInputError(
			f"Transaction CSV could not be read: {transaction_path}"
		) from error


def _make_evidence_window(transactions: tuple[Transaction, ...]) -> EvidenceWindow:
	"""Build the evidence details for one already-qualified transaction window."""
	return EvidenceWindow(
		account_id=transactions[0].account_id,
		transactions=transactions,
		transaction_count=len(transactions),
		combined_amount=sum(
			(transaction.amount for transaction in transactions), Decimal("0")
		),
		window_start=transactions[0].timestamp,
		window_end=transactions[-1].timestamp,
	)


def _find_evidence_windows(
	transactions: list[Transaction], config: Configuration
) -> list[EvidenceWindow]:
	"""Find distinct minimal qualifying windows for one account."""
	window_duration = timedelta(hours=float(config.rolling_window_hours))
	candidates: list[tuple[Transaction, ...]] = []

	for start in range(len(transactions)):
		for end in range(start + config.min_transaction_count - 1, len(transactions)):
			window = tuple(transactions[start : end + 1])
			if window[-1].timestamp - window[0].timestamp > window_duration:
				break
			combined_amount = sum(
				(transaction.amount for transaction in window), Decimal("0")
			)
			if combined_amount > config.reporting_threshold:
				candidates.append(window)

	unique_candidates = list(dict.fromkeys(candidates))
	minimal_candidates = [
		candidate
		for candidate in unique_candidates
		if not any(
			set(other).issubset(candidate) and len(other) < len(candidate)
			for other in unique_candidates
		)
	]
	return [_make_evidence_window(window) for window in minimal_candidates]


def detect_structuring(
	transactions: Iterable[Transaction], config: Configuration
) -> list[FlaggedAccount]:
	"""Return accounts with qualifying credit transaction evidence."""
	transactions_by_account: dict[str, list[Transaction]] = {}
	for transaction in transactions:
		if (
			transaction.direction == "credit"
			and transaction.amount < config.reporting_threshold
		):
			transactions_by_account.setdefault(transaction.account_id, []).append(
				transaction
			)

	flagged_accounts = []
	for account_id, account_transactions in sorted(transactions_by_account.items()):
		ordered_transactions = sorted(
			account_transactions,
			key=lambda transaction: (transaction.timestamp, transaction.txn_id),
		)
		evidence_windows = _find_evidence_windows(ordered_transactions, config)
		if evidence_windows:
			flagged_accounts.append(
				FlaggedAccount(account_id, tuple(evidence_windows))
			)
	return flagged_accounts
