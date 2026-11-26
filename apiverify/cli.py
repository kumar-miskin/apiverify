import argparse
import json
import sys

from .resolve import verify_symbol


def main(argv=None):
    p = argparse.ArgumentParser(prog="apiverify", description="Does this Python API exist in this exact package version?")
    p.add_argument("symbol", help="dotted path, e.g. requests.Session.get")
    p.add_argument("--version", "-v", default="latest", help="exact version on PyPI (default: latest)")
    p.add_argument("--package", "-p", help="PyPI name if it differs from the import name (e.g. pillow for PIL)")
    p.add_argument("--kwarg", "-k", action="append", default=[], help="keyword argument to check (repeatable)")
    a = p.parse_args(argv)
    r = verify_symbol(a.symbol, a.version, a.package, a.kwarg or None)
    print(json.dumps(r.to_dict(), indent=2))
    return {"verified": 0, "not_found": 1, "cannot_verify": 2}[r.status]


if __name__ == "__main__":
    sys.exit(main())
