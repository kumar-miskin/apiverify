"""Adversarial runtime check: small synthetic packages using tricky constructs.
For each case Python imports the package and answers for each symbol with getattr; apiverify answers
from source (local root). A wrong decisive answer (verified but absent, or not_found but present) is printed.
Synthetic cases chosen by the author: they show behaviour on known tricky constructs, not reliability on real code."""
import importlib, inspect, os, sys, tempfile, textwrap, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apiverify.resolve import verify_symbol, _CTXS

CASES = {
 "try_import": ("try:\n    from json import dumps as X\nexcept ImportError:\n    X = None\n", ["X", "Y"]),
 "ifelse_def": ("import sys\nif sys.version_info >= (3, 0):\n    def a(): pass\nelse:\n    def b(): pass\n", ["a", "b"]),
 "try_def": ("try:\n    import nonexistent_zz\n    def a(): pass\nexcept ImportError:\n    def b(): pass\n", ["a", "b"]),
 "for_def": ("for _n in ('p','q'):\n    globals()[_n] = 1\n", ["p", "q", "r"]),
 "setattr_mod": ("import sys\nsetattr(sys.modules[__name__], 'dyn', 1)\n", ["dyn"]),
 "all_only": ("__all__ = ['ghost']\ndef real(): pass\n", ["ghost", "real"]),
 "walrus": ("if (w := 5):\n    pass\n", ["w"]),
 "with_assign": ("import contextlib\nwith contextlib.suppress(Exception):\n    ww = 1\n", ["ww"]),
 "class_in_func": ("def f():\n    class Inner: pass\n    return Inner\n", ["f", "Inner", "f.Inner"]),
 "nested_class": ("class A:\n    class B:\n        v = 1\n", ["A.B", "A.B.v", "A.B.w"]),
 "inherit_object": ("class A: pass\n", ["A.__class__", "A.__dict__", "A.__init__", "A.mro"]),
 "metaclass_attr": ("class M(type):\n    extra = 1\nclass A(metaclass=M): pass\n", ["A.extra", "A.zzz"]),
 "property_class": ("class A:\n    @property\n    def p(self): return 1\n", ["A.p", "A.q"]),
 "namedtuple": ("from collections import namedtuple\nP = namedtuple('P', 'x y')\n", ["P.x", "P.y", "P.z", "P._fields"]),
 "enum": ("import enum\nclass E(enum.Enum):\n    A = 1\n    B = 2\n", ["E.A", "E.B", "E.C", "E.name"]),
 "dataclass": ("from dataclasses import dataclass\n@dataclass\nclass D:\n    y: int\n    x: int = 0\n", ["D.x", "D.y", "D.__dataclass_fields__"]),
 "slots": ("class S:\n    __slots__ = ('a',)\n", ["S.a", "S.b"]),
 "del_name": ("def a(): pass\ndel a\n", ["a"]),
 "rebind": ("def a(): pass\na = 5\n", ["a"]),
 "alias_chain": ("class A:\n    x = 1\nB = A\nB.y = 2\n", ["A.y", "B.y", "A.x"]),
 "classattr_outside": ("class A: pass\nA.late = 1\n", ["A.late"]),
 "decorator_class": ("def deco(c):\n    c.added = 1\n    return c\n@deco\nclass A: pass\n", ["A.added"]),
 "mod_getattr": ("def __getattr__(n):\n    if n == 'lazy': return 1\n    raise AttributeError(n)\n", ["lazy", "nope"]),
 "star_all": ("__all__ = ['x']\nx = 1\ny = 2\n", ["x", "y"]),
 "import_star_sub": ("from .sub import *\n", ["pub", "_priv", "hidden"]),
 "annotation_only": ("v: int\nclass A:\n    w: int\n", ["v", "A.w"]),
 "typing_overload": ("from typing import overload\n@overload\ndef f(a: int) -> int: ...\n@overload\ndef f(a: str) -> str: ...\ndef f(a): return a\n", ["f"]),
 "lambda_assign": ("f = lambda: 1\n", ["f"]),
 "augassign": ("x = 1\nx += 1\n", ["x"]),
 "tuple_unpack": ("a, (b, c) = 1, (2, 3)\n", ["a", "b", "c"]),
 "global_in_func": ("def f():\n    global g\n    g = 1\n", ["g", "f"]),
 "exec_def": ("exec('def made(): pass')\n", ["made"]),
 "class_body_cond": ("import sys\nclass A:\n    if sys.platform == 'win32':\n        w = 1\n    else:\n        n = 1\n", ["A.w", "A.n"]),
 "inherit_local": ("class A:\n    a = 1\nclass B(A):\n    b = 2\n", ["B.a", "B.b", "B.c"]),
 "inherit_builtin": ("class E(Exception):\n    pass\nclass D(dict):\n    pass\n", ["E.args", "E.with_traceback", "D.keys", "D.nokey"]),
 "multi_inherit": ("class A:\n    a = 1\nclass B:\n    b = 1\nclass C(A, B): pass\n", ["C.a", "C.b", "C.c"]),
 "staticmethods": ("class A:\n    @staticmethod\n    def s(): pass\n    @classmethod\n    def c(cls): pass\n", ["A.s", "A.c", "A.d"]),
 "instance_attr": ("class A:\n    def __init__(self):\n        self.i = 1\n", ["A.i"]),
 "type_checking": ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from json import loads as tc\n", ["tc"]),
 "relative_reexport": ("from .sub import pub as renamed\n", ["renamed", "pub"]),
 "submodule_attr": ("", ["sub", "sub.pub", "sub.nothere"]),
 "for_bind": ("for k in range(2):\n    inside = k\n", ["k", "inside", "nope"]),
 "while_bind": ("i = 0\nwhile i < 1:\n    w1 = i\n    i += 1\n", ["w1", "i"]),
 "with_as": ("import io\nwith io.StringIO() as fh:\n    pass\n", ["fh"]),
 "except_as": ("try:\n    1/0\nexcept ZeroDivisionError as exc:\n    saved = 1\n", ["exc", "saved"]),
 "import_dotted": ("import os.path\nimport json.decoder as jd\n", ["os", "os.path", "jd", "json"]),
 "chained_assign": ("p1 = p2 = 3\n", ["p1", "p2"]),
 "match_bind": ("m = (1, 2)\nmatch m:\n    case (a1, b1):\n        pass\n", ["a1", "b1"]),
 "class_for": ("class A:\n    for _i in range(2):\n        loopattr = _i\n", ["A.loopattr", "A._i", "A.other"]),
 "class_with": ("import contextlib\nclass A:\n    with contextlib.suppress(Exception):\n        wa = 1\n", ["A.wa"]),
 "class_try": ("class A:\n    try:\n        import zzz_missing\n        ta = 1\n    except ImportError:\n        tb = 1\n", ["A.ta", "A.tb"]),
 "class_walrus": ("class A:\n    if (cw := 1):\n        pass\n", ["A.cw"]),
 "class_tuple": ("class A:\n    q1, (q2, q3) = 1, (2, 3)\n", ["A.q1", "A.q2", "A.q3"]),
 "class_ann_only": ("class A:\n    only: int\n    both: int = 1\n", ["A.only", "A.both"]),
 "func_attr": ("def f(): pass\nf.extra = 1\n", ["f.extra", "f"]),
 "star_priv_all": ("from .sub import *\n", ["pub", "hidden"]),
 "nested_try_import": ("try:\n    try:\n        from json import dumps as d1\n    except ImportError:\n        d1 = None\nexcept Exception:\n    d1 = 0\n", ["d1"]),
 "else_branch": ("try:\n    import json\nexcept ImportError:\n    json = None\nelse:\n    e1 = 1\n", ["e1", "json"]),
 "finally_bind": ("try:\n    pass\nfinally:\n    f1 = 1\n", ["f1"]),
 "global_stmt": ("def init():\n    global late_g\n    late_g = 1\ninit()\n", ["late_g", "init"]),
 "cond_class_attr_alias": ("class A: pass\nif True:\n    A.c1 = 1\n", ["A.c1"]),
 "reexport_chain": ("from .sub import pub as pp\nfrom . import sub as ss\n", ["pp", "ss", "ss.pub", "ss.hidden", "ss.zz"]),
 "init_subclass": ("class A:\n    def __init_subclass__(cls, **k):\n        cls.made = 1\nclass B(A): pass\n", ["B.made", "A.made"]),
 "class_dict_update": ("class A: pass\nA.__dict__\nvars(A)\nA.attr_x = 1\n", ["A.attr_x", "A.attr_y"]),
}
SUB = "pub = 1\n_priv = 2\n__all__ = ['pub']\nhidden = 3\n"

def main():
    bad = 0; stats = {"correct": 0, "abstained": 0, "instance_unscored": 0, "wrong": 0}
    for name, (src, syms) in CASES.items():
        root = tempfile.mkdtemp(); pkg = f"advpkg_{name}"
        os.makedirs(os.path.join(root, pkg))
        open(os.path.join(root, pkg, "__init__.py"), "w").write(src)
        open(os.path.join(root, pkg, "sub.py"), "w").write(SUB)
        sys.path.insert(0, root)
        try: m = importlib.import_module(pkg)
        except BaseException as e:
            print("skip", name, repr(e)); sys.path.pop(0); continue
        for s in syms:
            o = m; ok = True
            for part in s.split("."):
                try: o = getattr(o, part)
                except AttributeError:
                    try: o = importlib.import_module(f"{pkg}.{part}")
                    except BaseException: ok = False; break
            _CTXS.clear()
            r = verify_symbol(f"{pkg}.{s}", "1.0", pkg, _root=root)
            if r.status == "cannot_verify": stats["abstained"] += 1; continue
            if r.status == "verified" and getattr(r, "kind", "") == "instance attribute": stats["instance_unscored"] += 1; continue
            right = (r.status == "verified") == ok
            stats["correct" if right else "wrong"] += 1
            if not right: print("WRONG", name, s, r.status, "runtime_has=", ok, getattr(r, "kind", ""), getattr(r, "reason", ""))
        sys.path.pop(0)
    print(json.dumps(stats))
    return stats

if __name__ == "__main__":
    main()
