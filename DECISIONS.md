# DECISIONS.md

## 1. The core boundary / constraint decision

The deterministic detector is the sole authority for account flags, totals, windows, and transaction evidence. The LLM receives only detector-generated evidence and writes a concise explanation; it cannot inspect the CSV or change a detection result. This boundary was proposed during AI coding-agent collaboration and accepted because it is the clearest way to keep the compliance decision reproducible and auditable.

## 2. What you considered and rejected

The rejected alternative was to give the LLM the raw transaction list and ask it to decide whether an account showed structuring. That approach could recalculate the pattern differently, invent unsupported reasoning, and make the result non-deterministic, so it would violate the assignment's central constraint. We also considered returning only one evidence window per account, but retained every distinct minimal qualifying window so evidence is not silently discarded; strict supersets are removed as redundant.

The detector uses these rule interpretations: individual qualifying amounts are strictly below `reporting_threshold`; the combined amount must be strictly greater; and a transaction exactly at the rolling-window boundary is included. This makes the exact-threshold and exact-boundary tests explicit rather than relying on an accidental comparison choice.

The severity feature is deliberately separate from both detectors but is run by the CLI after detection. It applies the approved deterministic formulas to existing evidence, clamps scores to 0-100, rounds to two decimal places, and treats invalid or missing evidence as zero. The score is a prioritization heuristic for ordering analyst attention, not a calibrated fraud probability, and it is displayed directly rather than supplied to the LLM.

## 3. Where you used an AI coding agent, and what you did with its output

- I used GitHub Copilot as an AI coding agent to inspect the assignment document, supplied CSV, configuration, and template before implementation. I kept the data findings and used them to define the local test cases; no code was accepted without running tests.
- I asked for a simple configuration loader. The agent produced a validated frozen configuration object, clear configuration errors, and tests for missing, invalid, non-positive, and fractional values. I kept the `Decimal` handling and validation structure after testing it.
- I asked for the deterministic detector. The agent produced CSV validation, typed transaction and evidence objects, deterministic grouping and rolling-window detection, and tests for threshold, timing, direction, count, multiple-account, malformed-input, and multiple-window cases. I kept the strict comparisons, inclusive boundary, and minimal-window evidence behavior because they match the assignment and are covered by tests.
- I asked for the LLM explainer. The agent proposed Groq's free tier and an evidence-only payload. I kept the separation, environment-based key loading, mocked API tests, failure handling, and preservation of deterministic evidence. I rejected sending the raw CSV to the model and rejected allowing model text to determine flags.
- I asked for CLI integration. The agent produced `main.py` that calls the existing loaders, detector, and explainer and prints deterministic evidence even when explanation generation fails. I kept the thin orchestration design and added integration tests.
- During validation, the agent updated HTTP error handling to include safe status, Content-Type, structured error code/message, or a redacted response excerpt capped at 500 characters. It also added a descriptive application User-Agent and strengthened the prompt against duration calculations, currency assumptions, and unsupported intent claims. These changes were retained because they improve diagnosability and factuality without changing detection.
- I reviewed the generated implementation through focused tests and the complete suite. The rapid pass-through detector was added as a separate module with FIFO, no-debit-reuse, duplicate-ID, and timezone-consistency rules, then integrated into the CLI without changing structuring detection.
- The severity scorer was added as a separate pure module and integrated into CLI output. It remains outside detection and prompting: the LLM receives evidence only and never receives or assigns severity. No Streamlit UI, database, or persistence layer was added because those are out of scope or optional stretch work.

## 4. What you're least sure about

The weakest point is the external Groq dependency: free-tier availability, provider-side HTTP behavior, and model output quality cannot be fully verified without relying on the external service. The application handles those failures without losing deterministic results, and the tests use mocked responses, but the live API path remains the least controlled part of the submission.

## 5. Time spent and where it went

Approximately 2 hours of actual effort spent so far.

## 6. Anything you'd do differently with more time

With more time, I would add a small controlled provider-adapter test matrix for additional Groq response shapes and improve packaging of the test dependency. I would not move detection into the LLM or add optional stretch features before strengthening the core walkthrough and documentation.
