# Changelog

## 0.1.3
Found by `eval/adversarial.py`, which imports small synthetic packages and compares apiverify with Python itself. 0.1.2 gave wrong decisive answers on these constructs, all now fixed with regression coverage (`tests/test_adversarial.py`):
- `verified` for a name defined in a `try:` body that only runs if the import before it works (try bodies are now conditional unless every handler also binds the name).
- `not_found` for names bound by an assignment expression (`:=`), nested tuple unpacking (`a, (b, c) = ...`), `for`/`while` bodies, `with ... as`, `except ... as`, `match` captures, `global` in a function, and class-body loops/walrus.
- `not_found` for metaclass members of a class (`A.mro`), and for subclass members added by `__init_subclass__`.
- `verified` for names that a star import does not export (underscore names, or names outside the source's literal `__all__`), and for annotation-only names (`x: int` with no value).
- `not_found`/`verified` mix-up for a `global` statement in a class body (assignment binds a module name, not a class attribute); now `cannot_verify`. Found by the independent evaluator.
Cost: more `cannot_verify` answers (about 2% more abstentions on rich in our own run). Runtime comparison on 13 popular libraries (own run, `eval/last_run2.txt`): no wrong answers found before or after.
