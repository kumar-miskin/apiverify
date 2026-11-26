<!-- mcp-name: io.github.kumar-miskin/apiverify -->
# apiverify

Does this Python API exist in this exact package version? apiverify answers from the
published source of that version, not from docs or model memory.

Three answers only:

- `verified`: found. Returns file, line, signature, and deprecation signals.
- `not_found`: every lookup path was followed and the name is absent.
- `cannot_verify`: the answer depends on something static analysis cannot settle
  (module-level `__getattr__`, a base class or re-export from outside the package,
  a file that will not parse). The agent should not assume either way.

It downloads the release from PyPI, checks the sha256 PyPI lists for it, unpacks it
safely, and reads it with `ast`. It never imports or runs package code. No dependencies.

## Use

```
python -m apiverify.cli requests.Session.get --version 2.31.0
python -m apiverify.cli requests.exceptions.JSONDecodeError -v 2.26.0   # not_found
python -m apiverify.cli requests.exceptions.JSONDecodeError -v 2.27.0   # verified
python -m apiverify.cli PIL.Image.open -v 10.0.0 --package pillow -k mode
```

Exit code: 0 verified, 1 not_found, 2 cannot_verify.

## Install

```
pip install apiverify        # or: uvx apiverify-mcp   (MCP server)
```

## As an MCP server (stdio)

Tool: `verify_python_api` with `symbol`, `version`, optional `package`, `kwargs`.

```json
{ "mcpServers": { "apiverify": { "command": "uvx", "args": ["--from", "apiverify", "apiverify-mcp"] } } }
```

(From a clone you can also use `python -m apiverify.mcp_server`.)

Suggested line for an agent's instructions file (AGENTS.md / CLAUDE.md):
"Before calling a library API you are not certain exists in the project's pinned
version, call verify_python_api. Treat cannot_verify as unknown, not as yes."

## Tested scope (see `eval/`)

Unit tests: `python -m unittest discover -s tests` (offline fixtures, 14 tests).

Runtime comparison: `python eval/run_eval.py` installs exact versions of requests (4),
click (3) and flask (3), asks Python itself whether each candidate symbol exists, and
compares. Candidates are all public names found across the tested versions plus invented
decoys. Result of the last run is in `eval/last_run.txt`: 2,411 correct, 0 false
positives, 0 false negatives, 442 abstentions (all flask, mostly names re-exported
from werkzeug/jinja2, which live outside the flask artifact), 2 instance attributes
not scored.

## Limits

- Python on PyPI only. No JavaScript, Rust or Go yet.
- Static analysis. Names created at runtime are reported as `cannot_verify`, not guessed.
- Not scored in the eval: keyword-argument checks, deprecation signals, and
  `instance attribute` results.
- The eval's candidates mostly come from names that exist in some version, with only a
  few decoys per library, so it measures version differences well and hallucinated
  names weakly. It is three libraries, not a benchmark.
- It has not been compared against Context7 or any retrieval tool. That comparison
  needs an agent harness and model spend, and is not done.
- No evidence yet that developers would pay for it or that agents call it unprompted.
- Needs network access to pypi.org and files.pythonhosted.org. Artifacts are cached in
  `$APIVERIFY_CACHE`.

## Reaching agents

See `DISTRIBUTION.md`.
