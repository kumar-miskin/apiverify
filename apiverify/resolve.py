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
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

from .fetch import fetch_source, FetchError

MAX_DEPTH = 12
MAX_PARSE_BYTES = 3 * 1024 * 1024
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
                if os.path.getsize(path) > MAX_PARSE_BYTES:
                    raise ValueError("file too large")
                with open(path, "rb") as fh:
                    res = (path, is_pkg, ast.parse(fh.read(), filename=path))
            except (SyntaxError, ValueError) as e:
                self.flags.append(f"cannot parse {os.path.relpath(path, self.base)}: {e.__class__.__name__}")
        self.cache[dotted] = res
        return res


def _is_tc(test):
    try:
        t = ast.unparse(test)
    except Exception:
        return False
    return t == "TYPE_CHECKING" or t.endswith(".TYPE_CHECKING")


def _is_not_tc(test):
    return isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and _is_tc(test.operand)


def _bound_names(body):
    """Names a statement list binds at its own scope (used to tell if both branches of an if define a name)."""
    out = set()
    for n in body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.add("*" if a.name == "*" else (a.asname or a.name.split(".")[0]))
        elif isinstance(n, (ast.If, ast.Try, ast.With)):
            out |= _bound_names(n.body) | _bound_names(getattr(n, "orelse", []))
            if isinstance(n, ast.Try):
                out |= _bound_names(n.finalbody)
                for h in n.handlers:
                    out |= _bound_names(h.body)
        else:
            out |= set(_targets(n))
    return out


def _flat(body, cond=False):
    """Statements at this scope, descending into if/try/with but not defs.
    Each yielded node gets ._cond = True when it only runs under a condition that
    the other branch does not also cover (version, platform, feature checks).
    Bodies guarded by TYPE_CHECKING never run at runtime and are skipped."""
    for n in body:
        n._cond = cond
        yield n
        if isinstance(n, ast.If):
            if _is_tc(n.test):
                yield from _flat(n.orelse, cond)
            elif _is_not_tc(n.test):
                yield from _flat(n.body, cond)
            else:
                for branch, other in ((n.body, n.orelse), (n.orelse, n.body)):
                    ob = _bound_names(other)
                    for c in branch:
                        nm = _bound_names([c]) if not isinstance(c, (ast.If, ast.Try, ast.With)) else set()
                        covered = bool(nm) and "*" not in nm and nm <= ob
                        yield from _flat([c], cond or not covered)
        elif isinstance(n, ast.Try):
            yield from _flat(n.body, cond); yield from _flat(n.orelse, cond); yield from _flat(n.finalbody, cond)
            for h in n.handlers:
                yield from _flat(h.body, cond)
        elif isinstance(n, (ast.With,)):
            yield from _flat(n.body, cond)


def _is_overload(n):
    for d in getattr(n, "decorator_list", []):
        dd = _dotted(d.func if isinstance(d, ast.Call) else d) or []
        if dd and dd[-1] == "overload":
            return True
    return False


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
    overloads = []
    flat = list(_flat(tree.body))
    for n in flat:
        if isinstance(n, ast.Delete) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets):
            ctx.flags.append(f"{name} is deleted with del at line {n.lineno} in {modname}")
            return None
    for n in tree.body:
        if isinstance(n, ast.If) and _is_tc(n.test) and name in _bound_names(n.body):
            unknown.append(f"{name} is only defined under TYPE_CHECKING (not present at runtime)")
    for n in flat:
        if getattr(n, "_cond", False) and not isinstance(n, (ast.If, ast.Try, ast.With)) \
                and (name in _bound_names([n]) or "*" in _bound_names([n])):
            unknown.append(f"{name} is defined only under a condition at line {n.lineno} (version, platform or feature check)")
            continue
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name and _is_overload(n):
            overloads.append(n)
            continue
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            if tail:
                unknown.append(f"{name} is a function; attribute {tail[0]} cannot be checked statically")
                continue
            return Found("function", path, n.lineno, n, modname)
        if isinstance(n, ast.ClassDef) and n.name == name:
            if not tail:
                return Found("class", path, n.lineno, n, modname)
            if _bind_count(flat, name) > 1:
                unknown.append(f"{name} is bound more than once in {modname} (redefined or rebound)")
                continue
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
    if overloads:
        return Found("function", path, overloads[0].lineno, overloads[0], modname, {"overload_only": True})
    for n in flat:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "__getattr__":
            unknown.append(f"{modname} defines module-level __getattr__ (names may be created lazily)")
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            fn = _dotted(n.func)
            if fn and fn[-1] in ("exec", "eval", "globals", "__import__") and len(fn) == 1:
                unknown.append(f"{modname} uses {fn[-1]}() (names may be created dynamically)")
            elif fn == ["setattr"] and n.args and isinstance(n.args[0], (ast.Name, ast.Subscript)):
                a0 = n.args[0]
                if isinstance(a0, ast.Subscript) or a0.id in ("__builtins__",):
                    unknown.append(f"{modname} calls setattr on a module")
        elif isinstance(n, ast.Subscript):
            d = _dotted(n.value)
            if d == ["sys", "modules"] or d == ["globals"]:
                if isinstance(n.ctx, ast.Store):
                    unknown.append(f"{modname} writes to sys.modules or globals()")
    if unknown:
        ctx.flags.extend(unknown)
    return None


def _class_members(cls):
    out, cond, deleted = {}, set(), set()
    for n in _flat(cls.body):
        if isinstance(n, ast.Delete):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    deleted.add(t.id)
            continue
        if isinstance(n, (ast.If, ast.Try, ast.With)):
            continue
        names = _bound_names([n])
        if getattr(n, "_cond", False):
            cond |= names
            continue
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_overload(n) and n.name in out:
                continue
            out[n.name] = ("function", n)
        elif isinstance(n, ast.ClassDef):
            out[n.name] = ("class", n)
        else:
            for t in _targets(n):
                out[t] = ("variable", n)
    return out, cond, deleted


def _flatten_targets(t):
    if isinstance(t, (ast.Tuple, ast.List)):
        for e in t.elts:
            yield from _flatten_targets(e)
    elif isinstance(t, ast.Starred):
        yield from _flatten_targets(t.value)
    else:
        yield t


def _store_targets(n):
    raw = []
    if isinstance(n, ast.Assign):
        raw = list(n.targets)
    elif isinstance(n, ast.AnnAssign):
        raw = [n.target] if n.value is not None else []   # `A.x: int` alone assigns nothing
    elif isinstance(n, ast.AugAssign):
        raw = [n.target]
    elif isinstance(n, (ast.For, ast.AsyncFor)):
        raw = [n.target]
    elif isinstance(n, (ast.With, ast.AsyncWith)):
        raw = [i.optional_vars for i in n.items if i.optional_vars is not None]
    out = []
    for t in raw:
        out.extend(_flatten_targets(t))
    return out


def _delete_targets(n):
    out = []
    for t in n.targets:
        out.extend(_flatten_targets(t))
    return out


def _bind_count(flat, name):
    return sum(1 for n in flat if not isinstance(n, (ast.If, ast.Try, ast.With)) and name in _bound_names([n]))


def _aliases(tree, clsname):
    """Names assigned (directly or via other aliases) from the class name. Returns (names, unstable):
    unstable aliases are bound more than once, conditionally, or somewhere other than module scope."""
    names = {clsname}
    changed = True
    while changed:
        changed = False
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Name) and n.value.id in names:
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id not in names:
                        names.add(t.id); changed = True
    flat = list(_flat(tree.body))
    stores = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            stores[n.id] = stores.get(n.id, 0) + 1
        elif isinstance(n, ast.arg):
            stores[n.arg] = stores.get(n.arg, 0) + 1
    unstable = set()
    for a in names - {clsname}:
        top = [n for n in flat if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == a for t in n.targets)]
        if stores.get(a, 0) != 1 or len(top) != 1 or getattr(top[0], "_cond", False):
            unstable.add(a)
    return names, unstable


_OBJECT_MEMBERS = set(dir(object)) | {"__dict__", "__module__", "__weakref__", "__qualname__", "__name__",
                                      "__mro__", "__bases__", "__subclasses__", "__annotations__"}
_HARMLESS_CLASS_DECORATORS = {"final", "runtime_checkable", "type_check_only"}


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
    members, condmem, deleted = _class_members(cls)
    if name in deleted:
        ctx.flags.append(f"{name} is deleted in class {cls.name}")
        return None
    mm = ctx.load(modname)
    if mm is not None:
        names, _unst = _aliases(mm[2], cls.name)
        for n in ast.walk(mm[2]):
            if isinstance(n, ast.Delete):
                for t in _delete_targets(n):
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in names and t.attr == name:
                        ctx.flags.append(f"{cls.name}.{name} is deleted with del at line {n.lineno}")
                        return None
    if "__slots__" in members and members["__slots__"][0] == "variable":
        sv = getattr(members["__slots__"][1], "value", None)
        try:
            slots = ast.literal_eval(sv)
            slots = [slots] if isinstance(slots, str) else list(slots)
        except Exception:
            slots = None
            ctx.flags.append(f"class {cls.name} has a __slots__ that is not a literal")
        if slots is not None and name in slots and not tail:
            return Found("slot", path, members["__slots__"][1].lineno, None, modname, {"cls": cls.name})
    if name in members and name not in condmem:
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
    if not tail and name in _OBJECT_MEMBERS:
        return Found("member inherited from object", path, cls.lineno, None, modname, {"cls": cls.name})
    if name in condmem:
        ctx.flags.append(f"{name} is defined in class {cls.name} only under a condition (version, platform or feature check)")
    for d in cls.decorator_list:
        dd = _dotted(d.func if isinstance(d, ast.Call) else d) or ["?"]
        if dd[-1] not in _HARMLESS_CLASS_DECORATORS:
            ctx.flags.append(f"class {cls.name} has decorator @{'.'.join(dd)} which may generate members")
    m = ctx.load(modname)
    if m is not None:
        names, unstable = _aliases(m[2], cls.name)
        for a in sorted(unstable):
            ctx.flags.append(f"alias {a} of class {cls.name} is rebound or conditional; mutations through it cannot be tracked")
        for n in _flat(m[2].body):
            for t in _store_targets(n):
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in names and t.attr == name and not tail:
                    if getattr(n, "_cond", False):
                        ctx.flags.append(f"{cls.name}.{name} is assigned conditionally at line {n.lineno}")
                    elif t.value.id in unstable:
                        ctx.flags.append(f"{cls.name}.{name} is assigned through an unstable alias at line {n.lineno}")
                    else:
                        return Found("class attribute (assigned after class body)", path, n.lineno, None, modname, {"cls": cls.name})
        for n in ast.walk(m[2]):
            for t in _store_targets(n):
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in names and t.attr == name:
                    ctx.flags.append(f"{cls.name}.{name} is assigned at line {n.lineno} outside the class body")
            if isinstance(n, ast.Call):
                fn = _dotted(n.func)
                if fn in (["setattr"], ["type", "__setattr__"]) and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id in names:
                    ctx.flags.append(f"setattr on class {cls.name} (members may be added dynamically)")
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
    if _is_overload(node):
        return {k: "cannot_verify (only @overload stubs found)" for k in kwargs}
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
_PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_VER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*$")


def verify_symbol(symbol, version="latest", package=None, kwargs=None, _root=None):
    top = symbol.split(".")[0] if isinstance(symbol, str) else ""
    package = package or top
    res = Result(status="cannot_verify", package=package, version=version, symbol=symbol)
    if isinstance(kwargs, str):
        kwargs = [kwargs]
    if kwargs is not None and not (isinstance(kwargs, (list, tuple)) and all(isinstance(k, str) for k in kwargs)):
        res.reason = "kwargs must be a list of strings"
        return res
    if not isinstance(symbol, str) or not _PKG_RE.match(package or "") or not (version in (None, "latest") or _VER_RE.match(str(version))):
        res.reason = "invalid symbol, package or version"
        return res
    if not symbol or not isinstance(symbol, str) or any(not p.isidentifier() for p in symbol.split(".")):
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
