# test-task — incident log analyzer

Reads an application log, finds the incident in it and writes a report: what happened, when,
the most likely root cause (separated from downstream symptoms), impact, and recommendations.

## Quick start

Requirements: **Python 3.9+** and nothing else. The tool uses only the Python standard library
(no third-party packages, nothing to install, no network needed).

Run this from the **repository root** (the bundled log is `it_task_log (2).log`; its name contains
a space, and the default path is resolved relative to the current directory):

```bash
PYTHONPATH=src python3 -m test_task
```

This analyzes the bundled log, writes `out/report.md`, `out/report-appendix.md` and `out/report.json`, and prints a
short summary to stdout. Alternative, if you use [uv](https://docs.astral.sh/uv/): `uv run test-task`.

To check Python 3.9 compatibility (needs [uv](https://docs.astral.sh/uv/), which downloads
CPython 3.9 and the dev dependencies), run from the repository root:

```bash
uv run --python 3.9 pytest
```

## Usage

```text
PYTHONPATH=src python3 -m test_task [LOG_PATH] [--out-dir DIR] [--quiet]
```

- `LOG_PATH` — path to the log file. Default: `it_task_log (2).log` in the current directory.
  To analyze your own log, pass its path; quote it if it contains spaces:
  `PYTHONPATH=src python3 -m test_task "path/to/my incident.log"`.
- `--out-dir DIR` — directory for the reports (default `out`). Created if missing; existing
  reports are overwritten.
- `--quiet` — do not print the short summary to stdout (the report files are still written).

Exit codes: `0` — report written (also when no incident is found); `1` — unexpected internal
error; `2` — bad arguments, unreadable log file or unwritable output directory.

## Output

- `out/report.md` — human-readable report (summary, timeline, root cause, symptoms, impact,
  recommendations, evidence).
- `out/report-appendix.md` — supporting detail for the main report: database signals,
  baseline comparison, request traces and input statistics.
- `out/report.json` — the same findings in machine-readable form.

With `--out-dir DIR` the files appear as `DIR/report.md`, `DIR/report-appendix.md` and
`DIR/report.json`.

A sample of the current output for the bundled log is committed in `examples/`:
[report.md](examples/report.md), [report-appendix.md](examples/report-appendix.md) and
[report.json](examples/report.json).

## Supported log format

The analyzer is not tied to the bundled log: service names, dates and thresholds are not
hard-coded, and roles are inferred from the log content (a service that reports
`active_connections`/`max_connections` is treated as a DB server, and so on). Other logs work if
each line is one of:

- text: `<ISO 8601 timestamp> <LEVEL> <service> message key=value ...`; the timestamp may use
  `T` or a space between date and time (`2026-03-27T14:00:02.968Z`, `2026-03-27 14:00:02,968`);
- JSON on one line with `ts`/`timestamp`/`time`/`@timestamp`, `level`/`severity`/`lvl` and
  `service`/`app`/`logger` keys.

Database signals are recognised by PostgreSQL / connection-pool field names and keywords
(`pool_idle`, `active_connections`, `pq:`, `jdbc`, `hikari`, `sqlstate`, ...). If more than 10%
of non-blank lines cannot be parsed, a warning is printed to stderr and placed at the top of
`report.md`: the format probably differs and the conclusions may be unreliable.

## Approach

The analysis is a deterministic pipeline of plain heuristics; no model "guesses" the cause.

1. **Parse** lines into events (timestamp, level, service, key=value fields); stack-trace
   continuation lines are attached to the preceding event; unparseable lines are counted, not fatal.
2. **Baseline.** The normal profile (p50/p95 per service and metric) is built from the quiet
   beginning of the log, before the first anomalies. If anomalies start immediately, a fixed
   fraction of the log start is used instead.
3. **Anomaly thresholds.** An event is anomalous if it is WARNING/ERROR or has failure keywords;
   if a pool is saturated (`pool_idle == 0`, waiting clients, connections near the maximum);
   or if a metric deviates from the baseline (value > 3 × baseline p95 and by a minimum absolute
   delta; or a "lower is worse" metric falling below half the baseline median).
4. **Grouping.** Similar events of the same type and service are grouped into patterns; a large
   gap in time starts a new group.
5. **Episodes.** Anomalies are bucketed per minute; active buckets close together are merged
   into an episode with onset, end and a recovery check.
6. **Cause vs symptom.** Findings that start within a short window of the episode onset are cause
   candidates. A service-dependency graph is built from explicit peer fields, database access
   and request-trace order; the finding upstream-most in that graph (the one others depend on)
   is the cause, findings downstream of it are symptoms.
7. **Confidence.** Each hypothesis gets a confidence level from how much independent evidence
   supports it (explicit peer/DB evidence, timing, multiple sources).
8. **Uncertainty.** Where evidence is weak or conflicting, the report says so explicitly and lists
   alternative hypotheses instead of hiding them.

Why heuristics: the input is a single log, there is no labelled data, and a reviewer must be able
to trace every claim to log lines. Simple, explainable rules with parameters independent of the
specific log file are easier to verify than an opaque score.

Limitations (honest list):

- The baseline window is fixed (beginning of the log); a log that is noisy from the start, or
  whose "normal" drifts, gives a weak baseline.
- Request-trace order (`TRACE_ORDER`) is only a heuristic for dependency direction: the order in
  which services appear in a trace is not proof of causation.
- Thresholds (deviation factor, minimum deltas, gaps, windows) are reasonable defaults and were
  not calibrated on a corpus of real incidents.
- Only one log at a time is analyzed; no cross-log correlation.

## Severity scale

Every finding gets one of four severities:

- **critical** — User-facing requests fail (5xx/timeouts at an entry point) or a data store is unavailable (crash, restart, connections refused).
- **high** — Errors inside a service or exhausted shared resources (connection pool, DB connections) that cause failed or aborted operations.
- **medium** — Degradation without outright failure: latency well above baseline, retries that eventually succeed, growing queues/backlogs.
- **low** — Isolated warnings or minor deviations with no visible effect on requests.

## AI usage

### Tools

- **Claude Code** (Anthropic's Claude models, run through the Claude Agent SDK) inside a
  multi-role development harness: separate sessions for architect, planner, test author,
  test critic and implementer. Each role works in its own git worktree with limited write access.

### Where AI was used

- **Requirements:** extracting and numbering requirements from the task statement.
- **Contracts:** the architect role designed 12 module contracts (dataclasses and `Protocol`s).
  A second round added `build_narrative` after a gap was found.
- **Decomposition:** the planner split the work into 15 tasks with acceptance criteria.
- **Tests:** the test author wrote acceptance tests plus a hidden hold-out set on different
  data; a test critic reviewed them.
- **Implementation:** each task (parser, signals, baseline, causality, report, CLI, this README)
  was implemented by an AI session.

The report is built deterministically by plain code and needs no network; no model is called
when the analyzer runs. AI was used only during development.

How AI output was checked:

- Human approval of contracts, the plan and the tests before implementation started.
- Deterministic gates on every task: allowed-files scope, anti-cheating checks, `mypy`, `ruff`,
  visible acceptance tests, hidden hold-out tests and coverage.
- Manual review by a human of the results and of the points the gates cannot judge.

### What worked

- Fixing contracts first and approving them gave every implementation task a precise target.
- Hidden hold-out tests on other data discourage code fitted to the visible tests.
- A deterministic pipeline whose every claim traces back to log lines is easy to verify;
  there is no model in the analysis path.
- Having a test critic review the tests found real gaps (for example, untested cause-window
  and severity cases) before any code existed.

### What did not work

- An optional LLM path for the Incident Summary (with a fallback) was designed and implemented,
  then removed as unnecessary: the deterministic summary was enough, and the extra path only
  added code, contract rounds and re-planning.
- Some tests were wrong at first: the time-shift test compared free text, and the hex-token
  test assumed a format the contract does not define. A human had to point these out.
- Some requirement wording was ambiguous (for example, what `Episode.recovery` means, or what
  makes a hypothesis confidence HIGH), so tests could not be written until a human decided.
- Heuristic thresholds were not calibrated on real incidents; the AI could not validate them
  beyond the one bundled log and synthetic fixtures.
- The removed LLM path was never connected to a real client and was tested only with fakes,
  which proved little; deleting it was the honest fix.
