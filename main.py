"""Command-line entry point for transaction pattern detection."""

import argparse
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sys
from typing import TextIO

from config_loader import ConfigurationError, load_config
from detector import TransactionInputError, detect_structuring, load_transactions
from explainer import (
	ExplanationResult,
	RapidPassThroughExplanationResult,
	explain_flagged_accounts,
	explain_rapid_pass_through_accounts,
)
from pass_through_detector import (
	RapidPassThroughAccount,
	detect_rapid_pass_through,
)
from severity import score_rapid_pass_through_evidence, score_structuring_evidence


def configure_console_encoding(output: TextIO | None = None) -> None:
	"""Configure terminal output for Unicode text when the stream supports it."""
	output_stream = output or sys.stdout
	reconfigure = getattr(output_stream, "reconfigure", None)
	if callable(reconfigure):
		try:
			reconfigure(encoding="utf-8", errors="replace")
		except (TypeError, ValueError):
			pass


def print_results(
	structuring_results: list[ExplanationResult],
	pass_through_results: list[RapidPassThroughExplanationResult],
	config,
	output: TextIO,
) -> None:
	"""Print both deterministic finding types and optional explanations."""
	if not structuring_results and not pass_through_results:
		print("No flagged accounts found.", file=output)
		return

	finding_count = sum(
		len(result.flagged_account.evidence_windows)
		for result in structuring_results
	) + sum(len(result.flagged_account.evidence) for result in pass_through_results)
	print(f"Flagged findings: {finding_count}", file=output)

	for result in structuring_results:
		flagged_account = result.flagged_account
		for window_number, window in enumerate(
			flagged_account.evidence_windows, start=1
		):
			print("\nPattern: Structuring", file=output)
			print(f"Account: {flagged_account.account_id}", file=output)
			print(
				f"Severity score: {score_structuring_evidence(window, config)}",
				file=output,
			)
			print(f"  Evidence window {window_number}:", file=output)
			print(f"    Transaction count: {window.transaction_count}", file=output)
			print(f"    Combined amount: {window.combined_amount}", file=output)
			print(f"    Window start: {window.window_start.isoformat()}", file=output)
			print(f"    Window end: {window.window_end.isoformat()}", file=output)
			print("    Transactions:", file=output)
			for transaction in window.transactions:
				print(
					f"      {transaction.txn_id}: {transaction.amount} "
					f"at {transaction.timestamp.isoformat()}",
					file=output,
				)

		if result.explanation:
			print(f"  AI explanation: {result.explanation}", file=output)
		elif result.error:
			print(f"  AI explanation unavailable: {result.error}", file=output)

	for result in pass_through_results:
		flagged_account = result.flagged_account
		for evidence_number, evidence in enumerate(flagged_account.evidence, start=1):
			print("\nPattern: Rapid Pass-Through", file=output)
			print(f"Account: {flagged_account.account_id}", file=output)
			print(
				f"Severity score: {score_rapid_pass_through_evidence(evidence, config)}",
				file=output,
			)
			print(f"  Evidence match {evidence_number}:", file=output)
			incoming = evidence.incoming_transaction
			print(
				f"    Incoming: {incoming.txn_id}: {incoming.amount} "
				f"at {incoming.timestamp.isoformat()}",
				file=output,
			)
			print("    Outgoing transactions:", file=output)
			for transaction in evidence.outgoing_transactions:
				print(
					f"      {transaction.txn_id}: {transaction.amount} "
					f"at {transaction.timestamp.isoformat()}",
					file=output,
				)
			print(
				f"    Total outgoing amount: {evidence.total_outgoing_amount}",
				file=output,
			)
			display_ratio = evidence.outflow_ratio.quantize(
				Decimal("0.0000"), rounding=ROUND_HALF_UP
			)
			print(f"    Outflow ratio: {display_ratio}", file=output)
			print(f"    Window start: {evidence.window_start.isoformat()}", file=output)
			print(f"    Window end: {evidence.window_end.isoformat()}", file=output)

		if result.explanation:
			print(f"  AI explanation: {result.explanation}", file=output)
		elif result.error:
			print(f"  AI explanation unavailable: {result.error}", file=output)


def run_application(
	config_path: str | Path = "data/config.json",
	transaction_path: str | Path = "data/transactions.csv",
	output: TextIO | None = None,
) -> int:
	"""Run both detectors, score evidence, explain, and print results."""
	output_stream = output or sys.stdout
	try:
		config = load_config(config_path)
		transactions = load_transactions(transaction_path)
		structuring_accounts = detect_structuring(transactions, config)
		pass_through_accounts = detect_rapid_pass_through(transactions, config)
		structuring_results = explain_flagged_accounts(structuring_accounts)
		pass_through_results = explain_rapid_pass_through_accounts(
			pass_through_accounts
		)
	except (ConfigurationError, TransactionInputError, ValueError) as error:
		print(f"Error: {error}", file=output_stream)
		return 1

	print_results(structuring_results, pass_through_results, config, output_stream)
	return 0


def main(arguments: list[str] | None = None) -> int:
	"""Parse command-line paths and start the application."""
	configure_console_encoding()
	parser = argparse.ArgumentParser(
		description="Detect structuring patterns in transaction data."
	)
	parser.add_argument(
		"--config",
		default="data/config.json",
		help="Path to the JSON configuration file.",
	)
	parser.add_argument(
		"--transactions",
		default="data/transactions.csv",
		help="Path to the transaction CSV file.",
	)
	args = parser.parse_args(arguments)
	return run_application(args.config, args.transactions)


if __name__ == "__main__":
	raise SystemExit(main())
