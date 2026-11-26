"""Compare apiverify against runtime ground truth.

For each (package, version) we pip-install that exact version into its own
directory, import it, and ask Python itself whether each candidate symbol
exists (getattr chain). Candidates are the union of public names across all
tested versions of the package (so version-specific differences are included)
plus invented decoys. apiverify never imports the package.

Outcome per candidate:
  correct            verified & exists, or not_found & absent
  false_positive     verified but absent at runtime   (worst: tells an agent a missing API exists)
  false_negative     not_found but exists at runtime  (tells an agent to avoid a real API)
  abstained          cannot_verify
"""
import json, os, subprocess, sys, collections
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apiverify import verify_symbol

PLAN = {
    "requests": ["2.20.0", "2.26.0", "2.27.0", "2.31.0"],
    "click": ["7.1.2", "8.0.0", "8.1.7"],
    "flask": ["2.2.5", "2.3.3", "3.0.3"],
}
CLASSES = {"requests": ["Session", "Response", "exceptions", "adapters", "utils"],
           "click": ["Command", "Group", "Context", "types", "utils"],
           "flask": ["Flask", "Blueprint", "Request", "Response", "cli"]}
DECOYS = {"requests": ["Session.fetch", "get_json", "Response.parse", "exceptions.Timeouterror", "async_get"],
          "click": ["Command.execute", "run", "Context.exit_now", "types.Url", "echo_json"],
          "flask": ["Flask.run_async", "Blueprint.mount", "Request.get_body", "send_json", "cli.build"]}
ENV = "/tmp/apiverify-eval-env"

def install(pkg, ver):
    d = f"{ENV}/{pkg}-{ver}"
    if not os.path.isdir(d):
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--target", d, f"{pkg}=={ver}"],
                       check=True, capture_output=True)
    return d

PROBE = r'''
import sys, json, importlib
pkg = sys.argv[1]; cands = json.loads(sys.argv[2])
mod = importlib.import_module(pkg)
out = {}
for c in cands:
    o = mod; ok = True
    for part in c.split("."):
        try: o = getattr(o, part)
        except Exception: ok = False; break
    out[c] = ok
print(json.dumps(out))
'''

def public(pkg, d, names):
    code = ("import sys,json,importlib\nm=importlib.import_module(%r)\nr=set(n for n in dir(m) if not n.startswith('_'))\n"
            "for c in %r:\n  o=getattr(m,c,None)\n  if o is not None:\n    for n in dir(o):\n      if not n.startswith('_'): r.add(c+'.'+n)\nprint(json.dumps(sorted(r)))" % (pkg, names))
    r = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": d}, capture_output=True, text=True)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return []

def main():
    stats = collections.Counter(); rows = []
    for pkg, vers in PLAN.items():
        dirs = {}
        for v in vers:
            try: dirs[v] = install(pkg, v)
            except Exception as e: print("skip", pkg, v, "install failed"); 
        cands = set(DECOYS[pkg])
        for v, d in dirs.items():
            cands |= set(public(pkg, d, CLASSES[pkg]))
        cands = sorted(cands)
        for v, d in dirs.items():
            r = subprocess.run([sys.executable, "-c", PROBE, pkg, json.dumps(cands)], env={**os.environ, "PYTHONPATH": d}, capture_output=True, text=True)
            try: truth = json.loads(r.stdout.strip().splitlines()[-1])
            except Exception: print("skip probe", pkg, v, r.stderr[-200:]); continue
            for c in cands:
                res = verify_symbol(f"{pkg}.{c}", v, pkg)
                t = truth[c]
                if res.status == "cannot_verify": k = "abstained"
                elif res.status == "verified" and res.kind == "instance attribute": k = "instance_attr_unscored"
                elif res.status == "verified": k = "correct" if t else "false_positive"
                else: k = "correct" if not t else "false_negative"
                stats[k] += 1; stats[f"{pkg}:{k}"] += 1
                if k in ("false_positive", "false_negative"):
                    rows.append((pkg, v, c, res.status, t, res.reason))
    print(json.dumps(dict(stats), indent=2))
    for r in rows: print("MISS", r)
    json.dump({"stats": dict(stats), "misses": rows}, open(os.path.join(os.path.dirname(__file__), "results.json"), "w"), indent=2, default=str)

if __name__ == "__main__":
    main()
