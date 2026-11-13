"""Load and validate detector configuration values."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any


class ConfigurationError(ValueError):
	"""Explain that the configuration file cannot be used."""


@dataclass(frozen=True)
class Configuration:
	"""Store the validated values needed by the transaction detector."""

	reporting_threshold: Decimal
	rolling_window_hours: Decimal
	min_transaction_count: int
	rapid_pass_through_window_hours: Decimal = Decimal("72")
	rapid_pass_through_min_outflow_ratio: Decimal = Decimal("0.8")


def _positive_number(value: Any, name: str) -> Decimal:
	"""Convert one JSON number to Decimal and require it to be positive."""
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		raise ConfigurationError(f"'{name}' must be a positive number.")

	try:
		number = Decimal(str(value))
	except InvalidOperation as error:
		raise ConfigurationError(f"'{name}' must be a positive number.") from error

	if not number.is_finite() or number <= 0:
		raise ConfigurationError(f"'{name}' must be a positive number.")
	return number


def _positive_integer(value: Any, name: str) -> int:
	"""Convert a JSON number to a positive whole number for transaction counts."""
	number = _positive_number(value, name)
	if number != number.to_integral_value():
		raise ConfigurationError(f"'{name}' must be a positive whole number.")
	return int(number)


def _outflow_ratio(value: Any, name: str) -> Decimal:
	"""Convert an outflow ratio and require a value greater than zero up to one."""
	ratio = _positive_number(value, name)
	if ratio > 1:
		raise ConfigurationError(f"'{name}' must be greater than 0 and less than or equal to 1.")
	return ratio


def load_config(path: str | Path = "data/config.json") -> Configuration:
	"""Read, validate, and return the detector configuration from a JSON file."""
	config_path = Path(path)

	try:
		with config_path.open(encoding="utf-8") as config_file:
			raw_config = json.load(config_file)
	except FileNotFoundError as error:
		raise ConfigurationError(
			f"Configuration file was not found: {config_path}"
		) from error
	except json.JSONDecodeError as error:
		raise ConfigurationError(
			f"Configuration file contains invalid JSON: {config_path}"
		) from error
	except OSError as error:
		raise ConfigurationError(
			f"Configuration file could not be read: {config_path}"
		) from error

	if not isinstance(raw_config, dict):
		raise ConfigurationError("Configuration must contain a JSON object.")

	required_keys = {
		"reporting_threshold",
		"rolling_window_hours",
		"min_transaction_count",
		"rapid_pass_through_window_hours",
		"rapid_pass_through_min_outflow_ratio",
	}
	missing_keys = sorted(required_keys - raw_config.keys())
	if missing_keys:
		missing = ", ".join(missing_keys)
		raise ConfigurationError(f"Configuration is missing required value(s): {missing}.")

	return Configuration(
		reporting_threshold=_positive_number(
			raw_config["reporting_threshold"], "reporting_threshold"
		),
		rolling_window_hours=_positive_number(
			raw_config["rolling_window_hours"], "rolling_window_hours"
		),
		min_transaction_count=_positive_integer(
			raw_config["min_transaction_count"], "min_transaction_count"
		),
		rapid_pass_through_window_hours=_positive_number(
			raw_config["rapid_pass_through_window_hours"],
			"rapid_pass_through_window_hours",
		),
		rapid_pass_through_min_outflow_ratio=_outflow_ratio(
			raw_config["rapid_pass_through_min_outflow_ratio"],
			"rapid_pass_through_min_outflow_ratio",
		),
	)
