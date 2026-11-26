---
name: Wrong or surprising answer
about: apiverify said verified / not_found and Python disagrees, or it abstained where it should not
labels: wrong-answer
---

**Symbol** (e.g. `rich.style.Style.bold`):
**Package and exact version** (e.g. `rich==13.7.1`):
**apiverify answer** (verified / not_found / cannot_verify, plus the reason text):
**What Python itself says** (`pip install package==version`, then `python -c "..."` or `hasattr`):
**apiverify version** (`pip show apiverify`):

Wrong `verified` and wrong `not_found` answers are fixed first and added as regression tests.
