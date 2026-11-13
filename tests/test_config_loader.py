import json
from decimal import Decimal

import pytest

from config_loader import ConfigurationError, load_config


def write_config(tmp_path, values):
	"""Write test values to a temporary JSON file and return its path."""
	config_path = tmp_path / "config.json"
	config_path.write_text(json.dumps(values), encoding="utf-8")
	return config_path


def valid_values():
	"""Return a fresh copy of the smallest valid configuration."""
	return {
		"reporting_threshold": 50000,
		"rolling_window_hours": 72,
		"min_transaction_count": 3,
		"rapid_pass_through_window_hours": 72,
		"rapid_pass_through_min_outflow_ratio": 0.8,
	}


def test_load_config_returns_validated_configuration(tmp_path):
	config_path = write_config(tmp_path, valid_values())

	config = load_config(config_path)

	assert config.reporting_threshold == Decimal("50000")
	assert config.rolling_window_hours == Decimal("72")
	assert config.min_transaction_count == 3
	assert config.rapid_pass_through_window_hours == Decimal("72")
	assert config.rapid_pass_through_min_outflow_ratio == Decimal("0.8")


def test_load_config_uses_default_data_path():
	config = load_config()

	assert config.reporting_threshold == Decimal("50000")
	assert config.rolling_window_hours == Decimal("72")
	assert config.min_transaction_count == 3
	assert config.rapid_pass_through_window_hours == Decimal("72")
	assert config.rapid_pass_through_min_outflow_ratio == Decimal("0.8")


def test_load_config_reports_missing_required_values(tmp_path):
	values = valid_values()
	del values["min_transaction_count"]
	config_path = write_config(tmp_path, values)

	with pytest.raises(ConfigurationError, match="min_transaction_count"):
		load_config(config_path)


@pytest.mark.parametrize("field", [
	"rapid_pass_through_window_hours",
	"rapid_pass_through_min_outflow_ratio",
])
def test_load_config_reports_missing_pass_through_values(tmp_path, field):
	values = valid_values()
	del values[field]
	config_path = write_config(tmp_path, values)

	with pytest.raises(ConfigurationError, match=field):
		load_config(config_path)


@pytest.mark.parametrize("field", [
	"reporting_threshold",
	"rolling_window_hours",
	"min_transaction_count",
])
def test_load_config_rejects_non_positive_values(tmp_path, field):
	values = valid_values()
	values[field] = 0
	config_path = write_config(tmp_path, values)

	with pytest.raises(ConfigurationError, match=field):
		load_config(config_path)


def test_load_config_rejects_non_numeric_values(tmp_path):
	values = valid_values()
	values["reporting_threshold"] = "50000"
	config_path = write_config(tmp_path, values)

	with pytest.raises(ConfigurationError, match="reporting_threshold"):
		load_config(config_path)


def test_load_config_rejects_fractional_transaction_count(tmp_path):
	values = valid_values()
	values["min_transaction_count"] = 3.5
	config_path = write_config(tmp_path, values)

	with pytest.raises(ConfigurationError, match="whole number"):
		load_config(config_path)


@pytest.mark.parametrize("value", [0, -1])
def test_load_config_rejects_invalid_pass_through_window(tmp_path, value):
	values = valid_values()
	values["rapid_pass_through_window_hours"] = value
	config_path = write_config(tmp_path, values)

	with pytest.raises(ConfigurationError, match="rapid_pass_through_window_hours"):
		load_config(config_path)


def test_load_config_rejects_zero_outflow_ratio(tmp_path):
	values = valid_values()
	values["rapid_pass_through_min_outflow_ratio"] = 0
	config_path = write_config(tmp_path, values)

	with pytest.raises(ConfigurationError, match="rapid_pass_through_min_outflow_ratio"):
		load_config(config_path)


def test_load_config_rejects_outflow_ratio_greater_than_one(tmp_path):
	values = valid_values()
	values["rapid_pass_through_min_outflow_ratio"] = 1.01
	config_path = write_config(tmp_path, values)

	with pytest.raises(ConfigurationError, match="less than or equal to 1"):
		load_config(config_path)


@pytest.mark.parametrize("field", [
	"rapid_pass_through_window_hours",
	"rapid_pass_through_min_outflow_ratio",
])
def test_load_config_rejects_invalid_pass_through_data_types(tmp_path, field):
	values = valid_values()
	values[field] = "invalid"
	config_path = write_config(tmp_path, values)

	with pytest.raises(ConfigurationError, match=field):
		load_config(config_path)


def test_load_config_reports_missing_file(tmp_path):
	missing_path = tmp_path / "missing.json"

	with pytest.raises(ConfigurationError, match="was not found"):
		load_config(missing_path)


def test_load_config_reports_invalid_json(tmp_path):
	config_path = tmp_path / "config.json"
	config_path.write_text("{not valid json", encoding="utf-8")

	with pytest.raises(ConfigurationError, match="invalid JSON"):
		load_config(config_path)
