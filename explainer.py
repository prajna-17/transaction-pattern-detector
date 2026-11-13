"""Generate analyst-friendly explanations from deterministic evidence."""

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from decimal import Decimal, ROUND_HALF_UP

from dotenv import load_dotenv

from detector import FlaggedAccount
from pass_through_detector import RapidPassThroughAccount


GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-20b"
REQUEST_TIMEOUT_SECONDS = 30

load_dotenv()


class ExplainerError(RuntimeError):
	"""Explain why an LLM explanation could not be generated."""


@dataclass(frozen=True)
class ExplanationResult:
	"""Keep deterministic evidence available with an optional explanation."""

	flagged_account: FlaggedAccount
	explanation: str | None
	error: str | None


@dataclass(frozen=True)
class RapidPassThroughExplanationResult:
	"""Keep pass-through evidence available with an optional explanation."""

	flagged_account: RapidPassThroughAccount
	explanation: str | None
	error: str | None


def _evidence_payload(flagged_account: FlaggedAccount) -> dict[str, Any]:
	"""Convert detector evidence into the small JSON payload sent to the LLM."""
	return {
		"account_id": flagged_account.account_id,
		"evidence_windows": [
			{
				"transaction_count": window.transaction_count,
				"combined_amount": str(window.combined_amount),
				"window_start": window.window_start.isoformat(),
				"window_end": window.window_end.isoformat(),
				"transactions": [
					{
						"txn_id": transaction.txn_id,
						"amount": str(transaction.amount),
						"timestamp": transaction.timestamp.isoformat(),
					}
					for transaction in window.transactions
				],
			}
			for window in flagged_account.evidence_windows
		],
	}


def _request_body(payload: dict[str, Any], model: str) -> bytes:
	"""Build a strict prompt that asks the LLM only to narrate supplied facts."""
	system_message = (
		"You are an analyst writing one concise, professional paragraph. "
		"The deterministic detector is the sole authority for the flagged account and "
		"evidence. Use only the supplied evidence and do not make or change the "
		"detection decision. Identify the account, exact transaction count, combined "
		"amount, evidence window, and every transaction ID. Preserve all IDs, amounts, "
		"and timestamps exactly. Say that the deterministic detector identified the "
		"configured structuring pattern. Preserve transaction IDs, counts, amounts, "
		"and timestamps exactly as supplied. Do not recalculate, estimate, or infer any "
		"time duration; never describe a window as a number of hours or days unless "
		"that exact duration is explicitly supplied. Do not call an observed amount a threshold, invent reporting "
		"or regulatory rules. Do not add currency symbols or assume a currency. Do not claim suspicious intent, "
		"fraud, or money laundering."
	)
	user_message = (
		"Write an analyst-friendly explanation for this already-flagged account. "
		"The JSON below is the complete evidence supplied by the deterministic "
		"detector:\n" + json.dumps(payload, indent=2)
	)
	return json.dumps(
		{
			"model": model,
			"temperature": 0,
			"messages": [
				{"role": "system", "content": system_message},
				{"role": "user", "content": user_message},
			],
		}
	).encode("utf-8")


def _extract_content(response_body: bytes) -> str:
	"""Extract a non-empty explanation from a Groq chat completion response."""
	try:
		response = json.loads(response_body.decode("utf-8"))
		content = response["choices"][0]["message"]["content"]
	except (KeyError, IndexError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as error:
		raise ExplainerError("LLM response did not contain valid message content.") from error

	if not isinstance(content, str) or not content.strip():
		raise ExplainerError("LLM response contained an empty explanation.")
	return content.strip()


def _validate_explanation(content: str, required_values: Iterable[str]) -> str:
	"""Reject explanations that omit evidence or make unsupported claims."""
	lower_content = content.casefold()
	for forbidden in (
		"threshold",
		"reporting requirement",
		"regulatory",
		"fraud",
		"money laundering",
		"suspicious",
		"suspicious intent",
		"suspicious motive",
		"directly funded",
		"funds were traced",
		"traced between",
		"destination",
	):
		if forbidden in lower_content:
			raise ExplainerError(
				"LLM response contained an unsupported conclusion or rule."
			)
	if "\n" in content.strip():
		raise ExplainerError("LLM response was not a single concise paragraph.")
	missing_values = [value for value in required_values if value not in content]
	if missing_values:
		raise ExplainerError("LLM response omitted supplied evidence details.")
	return content


def _safe_error_detail(value: Any, api_key: str) -> str | None:
	"""Return one error value without exposing the API key."""
	if not isinstance(value, (str, int, float)) or isinstance(value, bool):
		return None
	text = str(value).strip()
	if not text:
		return None
	return text.replace(api_key, "[REDACTED]")


def _redact_sensitive_text(text: str, api_key: str) -> str:
	"""Remove API keys and common authorization credentials from text."""
	redacted = text.replace(api_key, "[REDACTED]")
	redacted = re.sub(
		r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", redacted
	)
	redacted = re.sub(
		r"(?i)\bBasic\s+[^\s,;]+", "Basic [REDACTED]", redacted
	)
	return re.sub(
		r"(?i)(Authorization\s*[:=]\s*)[^\s,;]+",
		r"\1[REDACTED]",
		redacted,
	)


def _response_excerpt(response_body: Any, api_key: str) -> str | None:
	"""Return a redacted response excerpt no longer than 500 characters."""
	if isinstance(response_body, bytes):
		text = response_body.decode("utf-8", errors="replace")
	elif isinstance(response_body, str):
		text = response_body
	else:
		return None

	text = _redact_sensitive_text(text, api_key).strip()
	if not text:
		return None
	if len(text) > 500:
		return text[:497] + "..."
	return text


def _http_error_message(error: HTTPError, api_key: str) -> str:
	"""Safely describe a structured or unstructured HTTP error."""
	content_type = "unknown"
	if error.headers is not None:
		content_type = error.headers.get("Content-Type", "unknown")
	response_body: Any = b""
	try:
		response_body = error.read()
		if isinstance(response_body, bytes):
			response_body = response_body.decode("utf-8-sig")
		response = json.loads(response_body)
	except (OSError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
		response = {}

	prefix = (
		f"LLM API returned HTTP {error.code} "
		f"(Content-Type: {content_type})"
	)
	error_data = response.get("error") if isinstance(response, dict) else None
	if not isinstance(error_data, dict):
		error_data = response if isinstance(response, dict) else {}
	if not error_data:
		excerpt = _response_excerpt(response_body, api_key)
		if excerpt:
			return f"{prefix}. Response excerpt: {excerpt}"
		return f"{prefix}. Response body was empty."

	code = _safe_error_detail(error_data.get("code"), api_key)
	message = _safe_error_detail(error_data.get("message"), api_key)
	if code and message:
		return f"{prefix} ({code}): {message}"
	if message:
		return f"{prefix}: {message}"
	if code:
		return f"{prefix} ({code})."

	excerpt = _response_excerpt(response_body, api_key)
	if excerpt:
		return f"{prefix}. Response excerpt: {excerpt}"
	return f"{prefix}. Response body was empty."


def _request_explanation(
	payload: dict[str, Any], api_key: str, model: str
) -> str:
	"""Send one evidence-only request to Groq and return its explanation."""
	request = Request(
		GROQ_ENDPOINT,
		data=_request_body(payload, model),
		headers={
			"Authorization": f"Bearer {api_key}",
			"Content-Type": "application/json",
			"User-Agent": "TransactionPatternDetector/1.0",
		},
		method="POST",
	)

	try:
		with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
			content = _extract_content(response.read())
			required_values = [payload["account_id"]]
			for window in payload["evidence_windows"]:
				required_values.extend(
					[
						str(window["transaction_count"]),
						window["combined_amount"],
						window["window_start"],
						window["window_end"],
					]
				)
				required_values.extend(
					value
					for transaction in window["transactions"]
					for value in (
						transaction["txn_id"],
						transaction["amount"],
						transaction["timestamp"],
					)
				)
			return _validate_explanation(content, required_values)
	except HTTPError as error:
		raise ExplainerError(_http_error_message(error, api_key)) from error
	except (URLError, TimeoutError, OSError) as error:
		raise ExplainerError("LLM API request failed.") from error


def explain_flagged_accounts(
	flagged_accounts: Iterable[FlaggedAccount],
) -> list[ExplanationResult]:
	"""Explain each flagged account without changing detector results."""
	accounts = list(flagged_accounts)
	if not accounts:
		return []

	api_key = os.getenv("GROQ_API_KEY")
	model = os.getenv("GROQ_MODEL", DEFAULT_MODEL)
	results = []
	for flagged_account in accounts:
		if not flagged_account.evidence_windows:
			results.append(
				ExplanationResult(
					flagged_account,
					None,
					"No deterministic evidence was supplied for this account.",
				)
			)
			continue
		if not api_key:
			results.append(
				ExplanationResult(
					flagged_account,
					None,
					"GROQ_API_KEY environment variable is not set.",
				)
			)
			continue

		try:
			explanation = _request_explanation(
				_evidence_payload(flagged_account), api_key, model
			)
		except ExplainerError as error:
			results.append(ExplanationResult(flagged_account, None, str(error)))
		else:
			results.append(ExplanationResult(flagged_account, explanation, None))
	return results


def _rapid_pass_through_payload(
	flagged_account: RapidPassThroughAccount,
) -> dict[str, Any]:
	"""Convert pass-through evidence into a small evidence-only JSON payload."""
	def elapsed_hours(evidence: Any) -> str:
		duration = evidence.window_end - evidence.window_start
		seconds = Decimal(duration.days * 86400 + duration.seconds)
		microseconds = Decimal(duration.microseconds) / Decimal("1000000")
		hours = (seconds + microseconds) / Decimal("3600")
		return str(hours.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

	def ratio_for_display(evidence: Any) -> str:
		return str(
			evidence.outflow_ratio.quantize(
				Decimal("0.0001"), rounding=ROUND_HALF_UP
			)
		)

	return {
		"account_id": flagged_account.account_id,
		"evidence": [
			{
				"incoming_transaction": {
					"txn_id": evidence.incoming_transaction.txn_id,
					"account_id": evidence.incoming_transaction.account_id,
					"amount": str(evidence.incoming_transaction.amount),
					"direction": evidence.incoming_transaction.direction,
					"timestamp": evidence.incoming_transaction.timestamp.isoformat(),
				},
				"outgoing_transactions": [
					{
						"txn_id": transaction.txn_id,
						"account_id": transaction.account_id,
						"amount": str(transaction.amount),
						"direction": transaction.direction,
						"timestamp": transaction.timestamp.isoformat(),
					}
					for transaction in evidence.outgoing_transactions
				],
				"total_outgoing_amount": str(evidence.total_outgoing_amount),
				"outflow_ratio": ratio_for_display(evidence),
				"elapsed_hours": elapsed_hours(evidence),
				"window_start": evidence.window_start.isoformat(),
				"window_end": evidence.window_end.isoformat(),
			}
			for evidence in flagged_account.evidence
		],
	}


def _rapid_pass_through_request_body(
	payload: dict[str, Any], model: str
) -> bytes:
	"""Build an evidence-only prompt for pass-through explanations."""
	system_message = (
		"You are an analyst writing one concise, professional paragraph about a "
		"detector-generated rapid pass-through finding. Use only the supplied "
		"evidence. Identify the account, incoming credit ID and exact amount and "
		"timestamp, every outgoing debit ID and exact amount and timestamp, the "
		"supplied outflow ratio, and elapsed hours when supplied. Explain only that "
		"the deterministic detector identified a qualifying credit-followed-by-debit "
		"pattern. Preserve IDs, amounts, directions, and timestamps exactly. Do not "
		"recalculate, assign severity, make a flag decision, claim direct funding, "
		"trace funds, invent a destination or intent, or claim fraud or money laundering."
	)
	user_message = (
		"Explain this already-detected rapid pass-through pattern using only the "
		"following detector evidence:\n" + json.dumps(payload, indent=2)
	)
	return json.dumps(
		{
			"model": model,
			"temperature": 0,
			"messages": [
				{"role": "system", "content": system_message},
				{"role": "user", "content": user_message},
			],
		}
	).encode("utf-8")


def _request_rapid_pass_through_explanation(
	payload: dict[str, Any], api_key: str, model: str
) -> str:
	"""Send one pass-through evidence-only request to Groq."""
	request = Request(
		GROQ_ENDPOINT,
		data=_rapid_pass_through_request_body(payload, model),
		headers={
			"Authorization": f"Bearer {api_key}",
			"Content-Type": "application/json",
			"User-Agent": "TransactionPatternDetector/1.0",
		},
		method="POST",
	)

	try:
		with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
			content = _extract_content(response.read())
			required_values = [payload["account_id"]]
			for evidence in payload["evidence"]:
				required_values.extend(
					[
						evidence["incoming_transaction"]["txn_id"],
						evidence["incoming_transaction"]["amount"],
						evidence["incoming_transaction"]["timestamp"],
						evidence["outflow_ratio"],
						evidence["elapsed_hours"],
					]
				)
				required_values.extend(
					value
					for transaction in evidence["outgoing_transactions"]
					for value in (
						transaction["txn_id"],
						transaction["amount"],
						transaction["timestamp"],
					)
				)
			return _validate_explanation(content, required_values)
	except HTTPError as error:
		raise ExplainerError(_http_error_message(error, api_key)) from error
	except (URLError, TimeoutError, OSError) as error:
		raise ExplainerError("LLM API request failed.") from error


def explain_rapid_pass_through_accounts(
	flagged_accounts: Iterable[RapidPassThroughAccount],
) -> list[RapidPassThroughExplanationResult]:
	"""Explain pass-through evidence without changing detector results."""
	accounts = list(flagged_accounts)
	if not accounts:
		return []

	api_key = os.getenv("GROQ_API_KEY")
	model = os.getenv("GROQ_MODEL", DEFAULT_MODEL)
	results = []
	for flagged_account in accounts:
		if not flagged_account.evidence:
			results.append(
				RapidPassThroughExplanationResult(
					flagged_account,
					None,
					"No deterministic evidence was supplied for this account.",
				)
			)
			continue
		if not api_key:
			results.append(
				RapidPassThroughExplanationResult(
					flagged_account,
					None,
					"GROQ_API_KEY environment variable is not set.",
				)
			)
			continue

		try:
			explanation = _request_rapid_pass_through_explanation(
				_rapid_pass_through_payload(flagged_account), api_key, model
			)
		except ExplainerError as error:
			results.append(
				RapidPassThroughExplanationResult(flagged_account, None, str(error))
			)
		else:
			results.append(
				RapidPassThroughExplanationResult(flagged_account, explanation, None)
			)
	return results
