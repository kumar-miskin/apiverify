"""Static, evidence-first resolution of a dotted Python symbol inside an unpacked
package. It never imports or executes package code.

Three outcomes, by design:
  verified       the symbol was found; location and signature come from the source
  not_found      every lookup path was followed to the end and the name is absent
  cannot_verify  the answer depends on something static analysis cannot settle
                 (dynamic __getattr__, an external base class or re-export, a
                 module missing from the artifact, an unparseable file)
A "not_found" is only returned when no unresolved escape hatch exists.
"""
import ast
import builtins
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

from .fetch import fetch_source, FetchError

MAX_DEPTH = 12
_WRAP_OK = {"staticmethod", "classmethod", "property", "overload", "abstractmethod",
            "cached_property", "functools.cached_property", "typing.overload",
            "abc.abstractmethod", "override", "typing.override"}


@dataclass
class Result:
    status: str
    package: str
    version: Optional[str]
    symbol: str
    kind: Optional[str] = None
    signature: Optional[str] = None
    location: Optional[dict] = None
    deprecation_signals: list = field(default_factory=list)
    params: dict = field(default_factory=dict)
    reason: Optional[str] = None
    notes: list = field(default_factory=list)
    artifact: Optional[dict] = None

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v not in (None, [], {})} | {"status": self.status}


class Found:
    def __init__(self, kind, path, line, node=None, modname=None, extra=None):
        self.kind, self.path, self.line, self.node, self.modname = kind, path, line, node, modname
        self.extra = extra or {}


class Unknown(Exception):
    pass


class Ctx:
    def __init__(self, base):
        self.base = base
        self.cache = {}
        self.flags = []  # reasons that block a not_found conclusion

    def modpath(self, dotted):
        parts = dotted.split(".")
        d = os.path.join(self.base, *parts)
        if os.path.isfile(os.path.join(d, "__init__.py")):
            return os.path.join(d, "__init__.py"), True
        if os.path.isfile(d + ".py"):
            return d + ".py", False
        return None, False

    def load(self, dotted):
        if dotted in self.cache:
            return self.cache[dotted]
        path, is_pkg = self.modpath(dotted)
        res = None
        if path:
            try:
                with open(path, "rb") as fh:
                    res = (path, is_pkg, ast.parse(fh.read(), filename=path))
            except (SyntaxError, ValueError) as e:
                self.flags.append(f"cannot parse {os.path.relpath(path, self.base)}: {e.__class__.__name__}")
        self.cache[dotted] = res
        return res


def _flat(body):
    """Statements at this scope, descending into if/try/with but not defs."""
    for n in body:
        yield n
        if isinstance(n, ast.If):
            yield from _flat(n.body); yield from _flat(n.orelse)
        elif isinstance(n, ast.Try):
            yield from _flat(n.body); yield from _flat(n.orelse); yield from _flat(n.finalbody)
            for h in n.handlers:
                yield from _flat(h.body)
        elif isinstance(n, (ast.With,)):
            yield from _flat(n.body)


def _targets(n):
    if isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Name):
                yield t.id
            elif isinstance(t, (ast.Tuple, ast.List)):
                for e in t.elts:
                    if isinstance(e, ast.Name):
                        yield e.id
    elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and isinstance(n.target, ast.Name):
        yield n.target.id


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr); node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return list(reversed(parts))
    return None


def _abs_module(modname, is_pkg, level, module):
    if level == 0:
        return module or ""
    pkg = modname.split(".") if is_pkg else modname.split(".")[:-1]
    if level - 1 > len(pkg):
        return None
    pkg = pkg[: len(pkg) - (level - 1)]
    return ".".join(pkg + ([module] if module else []))


def lookup_module(ctx, modname, rest, depth=0, seen=None):
    seen = seen or set()
    if depth > MAX_DEPTH:
        raise Unknown("import chain too deep")
    key = (modname, tuple(rest))
    if key in seen:
        return None
    seen = seen | {key}
    m = ctx.load(modname)
    if m is None:
        raise Unknown(f"module {modname} is not in the released artifact (external or unparseable)")
    path, is_pkg, tree = m
    if not rest:
        return Found("module", path, 1, tree, modname)
    name, tail = rest[0], rest[1:]
    unknown = []
    for n in _flat(tree.body):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            if tail:
                unknown.append(f"{name} is a function; attribute {tail[0]} cannot be checked statically")
                continue
            return Found("function", path, n.lineno, n, modname)
        if isinstance(n, ast.ClassDef) and n.name == name:
            if not tail:
                return Found("class", path, n.lineno, n, modname)
            r = lookup_class(ctx, modname, is_pkg, n, tail, depth + 1, seen)
            if r is not None:
                return r
            continue
        if name in set(_targets(n)):
            val = getattr(n, "value", None)
            d = _dotted(val) if val is not None else None
            if d:
                try:
                    r = lookup_module(ctx, modname, d + tail, depth + 1, seen)
                    if r is not None:
                        return r
                except Unknown as e:
                    unknown.append(str(e))
                    continue
            if not tail:
                return Found("variable", path, n.lineno, n, modname)
            unknown.append(f"{name} is assigned at line {n.lineno}; attributes of its value cannot be checked statically")
            continue
        if isinstance(n, ast.ImportFrom):
            target = _abs_module(modname, is_pkg, n.level, n.module)
            for a in n.names:
                if a.name == "*":
                    if target is None:
                        continue
                    try:
                        r = lookup_module(ctx, target, rest, depth + 1, seen)
                        if r is not None:
                            return r
                    except Unknown as e:
                        unknown.append(f"star import from {target}: {e}")
                elif (a.asname or a.name) == name:
                    if target is None:
                        unknown.append("relative import outside package")
                        continue
                    if ctx.modpath(target)[0] is None and not ctx.load(target):
                        if not tail:
                            return Found("name imported from external module", path, n.lineno, None, modname,
                                         {"external": target})
                        unknown.append(f"{name} comes from external module {target}; {tail[0]} cannot be checked")
                        continue
                    try:
                        r = lookup_module(ctx, target, [a.name] + tail, depth + 1, seen)
                        if r is None and ctx.modpath(f"{target}.{a.name}")[0]:
                            r = lookup_module(ctx, f"{target}.{a.name}", tail, depth + 1, seen)
                        if r is not None:
                            return r
                    except Unknown as e:
                        unknown.append(f"re-exported from {target}: {e}")
        elif isinstance(n, ast.Import):
            for a in n.names:
                bound = a.asname or a.name.split(".")[0]
                if bound == name and ctx.modpath(a.name.split(".")[0])[0] is None:
                    if not tail:
                        return Found("imported external module", path, n.lineno, None, modname, {"external": a.name})
                    unknown.append(f"{name} is external module {a.name}; {tail[0]} cannot be checked")
                    continue
                if a.asname == name:
                    try:
                        r = lookup_module(ctx, a.name, tail, depth + 1, seen)
                        if r is not None:
                            return r
                    except Unknown as e:
                        unknown.append(str(e))
    # submodule of a package
    if is_pkg and ctx.modpath(f"{modname}.{name}")[0]:
        r = lookup_module(ctx, f"{modname}.{name}", tail, depth + 1, seen)
        if r is not None:
            return r
    for n in _flat(tree.body):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "__getattr__":
            unknown.append(f"{modname} defines module-level __getattr__ (names may be created lazily)")
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
            fn = _dotted(n.value.func)
            if fn and fn[-1] in ("setattr",) or (fn and fn[:1] == ["globals"]):
                unknown.append(f"{modname} sets names dynamically")
    for n in ast.walk(tree):
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Call):
            fn = _dotted(n.value.func)
            if fn == ["globals"] and isinstance(n.ctx, ast.Store):
                unknown.append(f"{modname} writes to globals()")
    if unknown:
        ctx.flags.extend(unknown)
    return None


def _class_members(cls):
    out = {}
    for n in _flat(cls.body):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[n.name] = ("function", n)
        elif isinstance(n, ast.ClassDef):
            out[n.name] = ("class", n)
        else:
            for t in _targets(n):
                out[t] = ("variable", n)
    return out


def _instance_attrs(cls):
    attrs = {}
    for n in _flat(cls.body):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for s in ast.walk(n):
                tg = []
                if isinstance(s, ast.Assign):
                    tg = s.targets
                elif isinstance(s, (ast.AnnAssign, ast.AugAssign)):
                    tg = [s.target]
                for t in tg:
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                        attrs.setdefault(t.attr, s.lineno)
    return attrs


def lookup_class(ctx, modname, is_pkg, cls, rest, depth, seen):
    name, tail = rest[0], rest[1:]
    path = ctx.load(modname)[0]
    members = _class_members(cls)
    if name in members:
        kind, node = members[name]
        if tail:
            if kind == "class":
                return lookup_class(ctx, modname, is_pkg, node, tail, depth + 1, seen)
            ctx.flags.append(f"{name} is not a class; {tail[0]} cannot be checked statically")
            return None
        k = "method" if kind == "function" else ("class" if kind == "class" else "class attribute")
        if kind == "function":
            for d in node.decorator_list:
                dn = ".".join(_dotted(d) or []) if not isinstance(d, ast.Call) else ".".join(_dotted(d.func) or [])
                if dn.split(".")[-1] in ("property", "cached_property"):
                    k = "property"
        return Found(k, path, node.lineno, node, modname, {"cls": cls.name})
    ia = _instance_attrs(cls)
    if name in ia and not tail:
        return Found("instance attribute", path, ia[name], None, modname, {"cls": cls.name})
    if name in ("__getattr__",):
        pass
    if "__getattr__" in members or "__getattribute__" in members:
        ctx.flags.append(f"class {cls.name} defines __getattr__ (attributes may be dynamic)")
    if any(k.arg == "metaclass" for k in cls.keywords):
        ctx.flags.append(f"class {cls.name} uses a custom metaclass")
    for b in cls.bases:
        d = _dotted(b)
        if not d:
            ctx.flags.append(f"class {cls.name} has a base that is not a plain name")
            continue
        if len(d) == 1 and hasattr(builtins, d[0]):
            bt = getattr(builtins, d[0])
            if not tail and hasattr(bt, name):
                return Found("inherited builtin member", path, cls.lineno, None, modname, {"cls": cls.name, "base": d[0]})
            continue
        try:
            r = lookup_module(ctx, modname, d, depth + 1, seen)
        except Unknown as e:
            ctx.flags.append(f"base class {'.'.join(d)} of {cls.name} cannot be resolved: {e}")
            continue
        if r is None or r.kind != "class":
            ctx.flags.append(f"base class {'.'.join(d)} of {cls.name} cannot be resolved in this artifact")
            continue
        sub = lookup_class(ctx, r.modname, ctx.load(r.modname)[1], r.node, rest, depth + 1, seen)
        if sub is not None:
            sub.extra["inherited_from"] = r.node.name
            return sub
    return None


# ---------- details ----------
def _sig(node):
    a = node.args
    try:
        return f"{node.name}({ast.unparse(a)})" + (f" -> {ast.unparse(node.returns)}" if node.returns else "")
    except Exception:
        return None


def _deprecation(node):
    sig = []
    for d in getattr(node, "decorator_list", []):
        try:
            t = ast.unparse(d)
        except Exception:
            continue
        if "deprecat" in t.lower():
            sig.append(f"decorator @{t[:80]} at line {d.lineno}")
    doc = ast.get_docstring(node) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else None
    if doc and "deprecated" in doc.lower():
        sig.append("docstring mentions 'deprecated'")
    for s in ast.walk(node) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else []:
        if isinstance(s, ast.Call):
            fn = _dotted(s.func)
            if fn and fn[-1] == "warn":
                try:
                    if "deprecat" in ast.unparse(s).lower():
                        sig.append(f"warns about deprecation at line {s.lineno}")
                        break
                except Exception:
                    pass
    return sig


def _param_status(node, kwargs, ctx, found):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return {k: "cannot_verify" for k in kwargs}
    a = node.args
    named = {x.arg for x in a.args + a.kwonlyargs}
    altered = False
    for d in node.decorator_list:
        dn = ".".join(_dotted(d.func if isinstance(d, ast.Call) else d) or ["?"])
        if dn not in _WRAP_OK and dn.split(".")[-1] not in _WRAP_OK:
            altered = True
    out = {}
    for k in kwargs:
        if k in named:
            out[k] = "accepted"
        elif a.kwarg is not None:
            out[k] = "cannot_verify (function takes **%s)" % a.kwarg.arg
        elif altered:
            out[k] = "cannot_verify (decorated, signature may differ)"
        else:
            out[k] = "not_accepted"
    return out


_CTXS = {}


def verify_symbol(symbol, version="latest", package=None, kwargs=None, _root=None):
    top = symbol.split(".")[0]
    package = package or top
    res = Result(status="cannot_verify", package=package, version=version, symbol=symbol)
    if not symbol or any(not p.isidentifier() for p in symbol.split(".")):
        res.reason = "symbol must be a dotted Python path like requests.Session.get"
        return res
    try:
        if _root:
            info = {"root": _root, "filename": "local", "sha256": None, "url": None, "version": version}
        else:
            info = fetch_source(package, version)
    except FetchError as e:
        res.reason = f"could not obtain package source: {e}"
        return res
    res.version = info["version"]
    res.artifact = {k: info[k] for k in ("filename", "sha256", "url")}
    base = None
    cands = [info["root"]] + [os.path.join(info["root"], d) for d in sorted(os.listdir(info["root"]))]
    cands += [os.path.join(c, "src") for c in list(cands)]
    for c in cands:
        if os.path.isdir(c) and (os.path.isfile(os.path.join(c, top, "__init__.py")) or os.path.isfile(os.path.join(c, top + ".py"))):
            base = c
            break
    if base is None:
        res.reason = f"top-level module '{top}' not found in {package}=={res.version}; pass package= if the PyPI name differs from the import name"
        return res
    ctx = _CTXS.setdefault(base, Ctx(base))
    ctx.flags = []
    parts = symbol.split(".")
    try:
        found = lookup_module(ctx, parts[0], parts[1:])
    except Unknown as e:
        ctx.flags.append(str(e))
        found = None
    if found is None:
        if ctx.flags:
            res.reason = "not found by static lookup, but it may exist: " + "; ".join(dict.fromkeys(ctx.flags))[:600]
            res.status = "cannot_verify"
        else:
            res.status = "not_found"
            res.reason = f"no definition, import, or submodule named '{parts[-1]}' on that path in {package}=={res.version}"
        return res
    res.status = "verified"
    res.kind = found.kind
    rel = os.path.relpath(found.path, base)
    res.location = {"file": rel, "line": found.line}
    if "inherited_from" in found.extra:
        res.notes.append(f"inherited from {found.extra['inherited_from']}")
    node = found.node
    sig_node = node
    if found.kind == "class" and node is not None:
        c2 = Ctx(base); c2.cache = ctx.cache
        try:
            init = lookup_class(c2, found.modname, ctx.load(found.modname)[1], node, ["__init__"], 0, set())
        except Unknown:
            init = None
        if init is not None and init.node is not None:
            sig_node = init.node
            res.notes.append("signature shown is __init__")
        else:
            sig_node = None
            res.notes.append("no __init__ found statically (inherited, dataclass, or generated); signature not shown")
    if sig_node is not None and isinstance(sig_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        res.signature = _sig(sig_node)
    if node is not None:
        res.deprecation_signals = _deprecation(node)
    if kwargs:
        if sig_node is not None and isinstance(sig_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            res.params = _param_status(sig_node, kwargs, ctx, found)
        else:
            res.params = {k: "cannot_verify (no static signature)" for k in kwargs}
    return res
