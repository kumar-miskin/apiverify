# Evaluator handoff
Repo: https://github.com/kumar-miskin/apiverify  (v0.1.0, commit pinned in PyPI sdist)
Install: `pip install apiverify==0.1.0`  or clone.
Tests (offline, 14): `python -m unittest discover tests`
NOTE: an independent evaluation (14,864 probes on urllib3, jinja2, attrs, tqdm, pydantic) found real wrong answers in v0.1.0; see the README warning. The numbers below are the author's own run and do not establish reliability.
Runtime eval (needs network to PyPI): `python eval/run_eval.py`; prior output in eval/last_run.txt:
requests x4, click x3, flask x3 versions: 2,411 correct, 0 false positives, 0 false negatives,
442 abstained (all flask), 2 instance attributes unscored. Self-run by the author, not independent.
Use: `apiverify <dotted.symbol> --package P --version V`; MCP tool `verify_python_api`.
Known limits: static ast parse only (no runtime/dynamic attrs, C extensions, instance attrs);
Python on PyPI only; kwarg and deprecation signals unscored; no Context7 comparison.
Suggested checks: dynamic-attribute packages, re-exports, namespace packages, sdist-only releases,
very large packages (timeouts), yanked versions.
