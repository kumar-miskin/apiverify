"""Batch 2 runtime comparison across more popular pure-Python libraries.

Ground truth = Python's own getattr chain after importing the exact installed version.
Candidates: public names to depth 2 of the top module and its directly importable submodules,
the union across versions, plus typo decoys. apiverify never imports the package.
Usage: python eval/run_eval2.py [pkg ...]
"""
import json, os, subprocess, sys, collections
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apiverify import verify_symbol

PLAN = {  # pip name: (import name, versions)
 "httpx": ("httpx", ["0.24.1", "0.27.2"]),
 "rich": ("rich", ["12.6.0", "13.7.1"]),
 "packaging": ("packaging", ["21.3", "24.1"]),
 "attrs": ("attr", ["22.2.0", "23.2.0"]),
 "urllib3": ("urllib3", ["1.26.18", "2.2.2"]),
 "jinja2": ("jinja2", ["2.11.3", "3.1.4"]),
 "markupsafe": ("markupsafe", ["2.0.1", "2.1.5"]),
 "python-dateutil": ("dateutil", ["2.8.2", "2.9.0.post0"]),
 "tqdm": ("tqdm", ["4.64.1", "4.66.4"]),
 "pyparsing": ("pyparsing", ["2.4.7", "3.1.2"]),
 "typing-extensions": ("typing_extensions", ["4.5.0", "4.12.2"]),
 "idna": ("idna", ["2.10", "3.7"]),
 "werkzeug": ("werkzeug", ["2.0.3", "3.0.3"]),
 "starlette": ("starlette", ["0.27.0", "0.37.2"]),
}
ENV = "/tmp/apiverify-eval-env2"

def install(pkg, ver):
    d = f"{ENV}/{pkg}-{ver}"
    if not os.path.isdir(d):
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--target", d, f"{pkg}=={ver}"],
                       check=True, capture_output=True)
    return d

ENUM = r'''
import sys, json, importlib, pkgutil
name = sys.argv[1]
m = importlib.import_module(name)
mods = {name: m}
if hasattr(m, "__path__"):
    for mi in pkgutil.iter_modules(m.__path__, name + "."):
        if mi.name.split(".")[-1].startswith("_") or ".tests" in mi.name or ".test" in mi.name: continue
        try: mods[mi.name] = importlib.import_module(mi.name)
        except BaseException: pass
out = set()
for mn, mod in mods.items():
    rel = mn[len(name):].lstrip(".")
    pre = (rel + ".") if rel else ""
    for n in dir(mod):
        if n.startswith("_"): continue
        out.add(pre + n)
        o = getattr(mod, n, None)
        if isinstance(o, type):
            for k in dir(o):
                if not k.startswith("_"): out.add(pre + n + "." + k)
print(json.dumps(sorted(out)))
'''
PROBE = r'''
import sys, json, importlib
name = sys.argv[1]; cands = json.loads(sys.stdin.read())
m = importlib.import_module(name)
out = {}
for c in cands:
    o = m; ok = True; path = name
    for part in c.split("."):
        try: o = getattr(o, part)
        except AttributeError:
            try: o = importlib.import_module(path + "." + part)
            except BaseException:
                import inspect
                try: o = inspect.getattr_static(o, part)  # descriptors may raise on class access
                except BaseException: ok = False; break
        except BaseException:
            # descriptors can raise on class access; the name still exists statically
            import inspect
            try: o = inspect.getattr_static(o, part)
            except BaseException: ok = False; break
        path += "." + part
    out[c] = ok
print(json.dumps(out))
'''
def run(code, d, imp, arg=None):
    a = [sys.executable, "-W", "ignore", "-c", code, imp]
    r = subprocess.run(a, env={**os.environ, "PYTHONPATH": d}, capture_output=True, text=True, input=arg)
    try: return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception: return None

def main():
    want = sys.argv[1:]
    stats = collections.Counter(); rows = []
    for pkg, (imp, vers) in PLAN.items():
        if want and pkg not in want: continue
        dirs = {}
        for v in vers:
            try: dirs[v] = install(pkg, v)
            except Exception: print("skip install", pkg, v)
        cands = set()
        for v, d in dirs.items():
            e = run(ENUM, d, imp) or []
            cands |= set(e)
        cands |= {c[:-1] + ("x" if c[-1] != "x" else "y") for c in list(cands)[:40]}  # typo decoys
        cands = sorted(cands)
        for v, d in dirs.items():
            truth = run(PROBE, d, imp, json.dumps(cands))
            if truth is None: print("skip probe", pkg, v); continue
            for c in cands:
                res = verify_symbol(f"{imp}.{c}", v, pkg)
                t = truth[c]
                if res.status == "cannot_verify": k = "abstained"
                elif res.status == "verified" and res.kind == "instance attribute": k = "instance_attr_unscored"
                elif res.status == "verified": k = "correct" if t else "false_positive"
                else: k = "correct" if not t else "false_negative"
                stats[k] += 1; stats[f"{pkg}:{k}"] += 1
                if k in ("false_positive", "false_negative"):
                    rows.append((pkg, v, c, res.status, t, res.reason))
        print(pkg, {k.split(":")[1]: n for k, n in stats.items() if k.startswith(pkg + ":")}, flush=True)
    print(json.dumps({k: n for k, n in stats.items() if ":" not in k}))
    for r in rows[:200]: print("MISS", r)
    json.dump({"stats": dict(stats), "misses": rows}, open(os.path.join(os.path.dirname(__file__), "results2.json"), "w"), indent=2, default=str)
if __name__ == "__main__":
    main()
