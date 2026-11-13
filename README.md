# Transaction Pattern Detector

## Overview

This project detects possible structuring patterns in transaction data. Structuring is represented here as multiple qualifying credit transactions for the same account, each below a reporting threshold, occurring within a rolling time window, and exceeding the threshold in total.

The project deliberately separates three responsibilities:

1. Deterministic detectors make the flag decisions and produce exact evidence.
2. Deterministic severity scoring calculates prioritization scores from that evidence.
3. Optional Groq-powered explainers turn detector evidence into short analyst-friendly explanations.

The LLM never receives the complete CSV and never decides which accounts are flagged.

## Features

- Loads and validates configurable detection rules from JSON.
- Loads and validates transaction data from CSV.
- Uses `Decimal` for monetary values.
- Considers credits only.
- Requires each individual transaction to be strictly below the reporting threshold.
- Uses an inclusive rolling-window boundary.
- Requires the minimum transaction count and a combined amount strictly greater than the threshold.
- Preserves distinct, minimal evidence windows with transaction IDs, amounts, timestamps, counts, totals, and window boundaries.
- Provides a standalone deterministic rapid pass-through detector with FIFO debit matching.
- Provides standalone deterministic severity scores for structuring and rapid pass-through evidence.
- Generates optional explanations through Groq's free API tier.
- Keeps deterministic results available when the API key is missing or the LLM request fails.
- Provides focused unit and integration tests.

## Architecture

```text
main.py            Command-line orchestration and readable output
config_loader.py   JSON configuration loading and validation
detector.py        CSV validation and deterministic structuring detection
pass_through_detector.py
                   Deterministic rapid pass-through matching and evidence
severity.py        Deterministic 0-100 prioritization scoring
explainer.py       Evidence-only Groq request and explanation handling

data/
  config.json      Supplied detector configuration
  transactions.csv Supplied transaction data

tests/             Configuration, detector, explainer, and CLI tests
```

The main execution path is:

```text
load_config()
    -> load_transactions()
    -> detect_structuring()
  -> detect_rapid_pass_through()
  -> score each evidence item
  -> explain each finding using evidence only
  -> print findings, evidence, scores, and optional explanations
```

The structuring detector returns `FlaggedAccount` objects containing `EvidenceWindow` objects. The pass-through detector returns `RapidPassThroughAccount` objects containing `RapidPassThroughEvidence` objects. The severity module scores these objects without changing them. Separate explainer paths receive only their respective detector evidence and return results that retain the original deterministic evidence.

## Severity Scoring

`severity.py` calculates scores entirely in Python from detector evidence. It does not call the LLM and does not change detection decisions. Scores are clamped to 0-100 and rounded to two decimal places.

Structuring evidence uses:

```text
min(transaction_count / 6, 1) * 30
+ min(combined_amount / reporting_threshold, 2) * 35
+ average(individual_amount / reporting_threshold) * 20
+ 15 if all detector conditions are satisfied
```

Rapid pass-through evidence uses:

```text
min(outflow_ratio, 1.5) / 1.5 * 40
+ max(0, 1 - elapsed_hours / configured_window_hours) * 30
+ min(outgoing_transaction_count / 3, 1) * 15
+ 15 if all detector conditions are satisfied
```

The score is a deterministic prioritization heuristic. It is not a calibrated probability of fraud, money laundering, or any other wrongdoing. A higher score means the supplied evidence has more of the configured prioritization characteristics; analysts still need to review the exact evidence. Scores are displayed directly by the CLI and are never assigned or recalculated by the LLM.

## Prerequisites

- Python 3.12
- Internet access only when using Groq explanations
- A Groq account and free-tier API key for LLM explanations
- `pytest` for running tests

The application still runs its deterministic detector without a Groq API key.

## Installation

From the project directory, create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install the runtime and test dependencies:

```powershell
python -m pip install -r requirements.txt
```

## Environment Variables

Create a `.env` file in the project root. Do not commit it:

```text
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=openai/gpt-oss-20b
```

`GROQ_MODEL` is optional and defaults to `openai/gpt-oss-20b`. The project loads `.env` with `python-dotenv`, and `.env` is ignored by Git.

## Run The Application

Run with the supplied data and configuration:

```powershell
python main.py
```

Optional input paths can be supplied:

```powershell
python main.py --config path\to\config.json --transactions path\to\transactions.csv
```

Without `GROQ_API_KEY`, the application still prints every deterministic finding and severity score and reports that explanations are unavailable. With a valid key, it makes one evidence-only explanation request per finding. LLM failures do not remove deterministic findings or scores.

## Run Tests

```powershell
python -m pytest -q
```

The test suite covers configuration validation, threshold and time-window edge cases, multiple accounts and evidence windows, malformed input, mocked LLM responses, API failures, prompt constraints, HTTP error handling, and CLI integration.

## Sample Output

The supplied data produces 10 findings: six structuring findings and four rapid pass-through findings. A representative CLI run includes:

```text
Flagged findings: 10

Pattern: Structuring
Account: ACC-1001
Severity score: 77.00
  Evidence window 1:
    Transaction count: 4
    Combined amount: 52500
    Window start: 2026-01-06T08:00:00
    Window end: 2026-01-06T17:00:00
    Transactions:
      T1000: 14800 at 2026-01-06T08:00:00
      T1001: 13200 at 2026-01-06T11:00:00
      T1002: 12900 at 2026-01-06T14:00:00
      T1003: 11600 at 2026-01-06T17:00:00

Pattern: Rapid Pass-Through
Account: ACC-5054
Severity score: 47.72
  Incoming: T1152: 45250.95 at 2026-01-15T16:36:00
  Outgoing transactions:
    T1051: 46461.86 at 2026-01-18T15:47:00
  Total outgoing amount: 46461.86
  Outflow ratio: 1.026759880179311152583536920
  Window start: 2026-01-15T16:36:00
  Window end: 2026-01-18T15:47:00

```

## Known Limitations

- The CLI displays both detector types, but rapid pass-through and severity scoring are optional stretch features and are not part of the original core assignment.
- The LLM explanation paths are optional and require a valid key, network access, and available free-tier capacity.
- Round-tripping is not implemented.
- Rapid pass-through requires unique transaction IDs and consistent timestamp timezone awareness within one input collection.
- There is no UI, database, persistence layer, or batch job.
- The CLI reads input files from paths. Uploaded-file handling is not implemented.
- The application makes one LLM request per deterministic finding and does not retry or batch requests.
- The LLM output is not used for detection and cannot change deterministic results.
- Groq free-tier rate limits or provider errors can make explanations unavailable; deterministic findings and scores remain available.
