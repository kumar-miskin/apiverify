import json, os, subprocess, sys, tempfile, textwrap, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from apiverify.resolve import verify_symbol, _CTXS
from apiverify import mcp_server


def make(files):
    root = tempfile.mkdtemp()
    for p, c in files.items():
        fp = os.path.join(root, p)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        open(fp, "w").write(textwrap.dedent(c))
    _CTXS.clear()
    return root


LIB = {
    "lib/__init__.py": """
        from .core import Client, make_client
        from . import util
        from .compat import *
        VERSION = "1"
    """,
    "lib/core.py": """
        import warnings
        from .base import Base
        from external_pkg import Thing
        class Client(Base):
            retries = 3
            def __init__(self, url, *, timeout=5, verify=True):
                self.url = url
                self.session = None
            def get(self, path, params=None):
                return 1
            @property
            def ready(self): return True
            def old(self):
                '''Deprecated: use get.'''
                warnings.warn("old is deprecated", DeprecationWarning)
        class Odd(Thing):
            pass
        def make_client(url, **opts): pass
    """,
    "lib/base.py": """
        class Base:
            def close(self): pass
    """,
    "lib/util.py": """
        def helper(a, b=2): pass
    """,
    "lib/compat.py": """
        def legacy_name(): pass
    """,
    "lib/lazy/__init__.py": """
        def __getattr__(name): raise AttributeError(name)
    """,
}


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = make(LIB)

    def v(self, sym, **kw):
        return verify_symbol(sym, "1.0", "lib", _root=self.root, **kw)

    def test_class_and_signature(self):
        r = self.v("lib.Client")
        self.assertEqual(r.status, "verified")
        self.assertIn("timeout=5", r.signature)
        self.assertEqual(r.location["file"], "lib/core.py")

    def test_inherited_method(self):
        r = self.v("lib.Client.close")
        self.assertEqual(r.status, "verified")
        self.assertIn("inherited from Base", r.notes)

    def test_reexport_and_star_import(self):
        self.assertEqual(self.v("lib.make_client").status, "verified")
        self.assertEqual(self.v("lib.legacy_name").status, "verified")
        self.assertEqual(self.v("lib.util.helper").status, "verified")

    def test_missing_is_not_found(self):
        self.assertEqual(self.v("lib.Client.fetch").status, "not_found")
        self.assertEqual(self.v("lib.nothing").status, "not_found")
        self.assertEqual(self.v("lib.util.nothing").status, "not_found")

    def test_external_base_abstains(self):
        r = self.v("lib.core.Odd.anything")
        self.assertEqual(r.status, "cannot_verify")
        self.assertIn("Thing", r.reason)

    def test_module_getattr_abstains(self):
        r = self.v("lib.lazy.whatever")
        self.assertEqual(r.status, "cannot_verify")

    def test_external_reexport_exists_but_attrs_unknown(self):
        self.assertEqual(self.v("lib.core.Thing").status, "verified")
        self.assertEqual(self.v("lib.core.Thing.x").status, "cannot_verify")

    def test_instance_attribute_and_property(self):
        self.assertEqual(self.v("lib.Client.session").kind, "instance attribute")
        self.assertEqual(self.v("lib.Client.ready").kind, "property")

    def test_deprecation_signals(self):
        r = self.v("lib.Client.old")
        self.assertTrue(r.deprecation_signals)
        self.assertFalse(self.v("lib.Client.get").deprecation_signals)

    def test_kwargs(self):
        r = self.v("lib.Client", kwargs=["timeout", "verify", "retry"])
        self.assertEqual(r.params["timeout"], "accepted")
        self.assertEqual(r.params["retry"], "not_accepted")
        r = self.v("lib.make_client", kwargs=["anything"])
        self.assertIn("cannot_verify", r.params["anything"])

    def test_bad_input(self):
        self.assertEqual(verify_symbol("not a symbol", "1", _root=self.root).status, "cannot_verify")

    def test_syntax_error_abstains(self):
        root = make({"bad/__init__.py": "def (:\n"})
        r = verify_symbol("bad.x", "1", "bad", _root=root)
        self.assertEqual(r.status, "cannot_verify")

    def test_wrong_top_module(self):
        r = verify_symbol("other.x", "1", "lib", _root=self.root)
        self.assertEqual(r.status, "cannot_verify")


class MCP(unittest.TestCase):
    def test_protocol(self):
        self.assertEqual(mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})["result"]["serverInfo"]["name"], "apiverify")
        tl = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(tl["result"]["tools"][0]["name"], "verify_python_api")
        self.assertIsNone(mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        bad = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "zzz"}})
        self.assertIn("error", bad)



class Regressions(unittest.TestCase):
    """Cases found wrong by the independent evaluation of 0.1.0."""
    def run1(self, src, sym, **kw):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "pk"))
        with open(os.path.join(root, "pk", "__init__.py"), "w") as fh:
            fh.write(textwrap.dedent(src))
        _CTXS.clear()
        return verify_symbol(sym, "1", None, _root=root, **kw)

    def test_object_members_exist(self):
        for m in ("__repr__", "__eq__", "__hash__", "__dict__", "__doc__", "__class__", "__setattr__"):
            self.assertEqual(self.run1("class A:\n    pass\n", "pk.A." + m).status, "verified", m)

    def test_plain_missing_still_not_found(self):
        self.assertEqual(self.run1("class A:\n    pass\n", "pk.A.nope").status, "not_found")

    def test_decorated_class_abstains(self):
        r = self.run1("import dataclasses\n@dataclasses.dataclass\nclass A:\n    x:int=0\n", "pk.A.fields_x")
        self.assertEqual(r.status, "cannot_verify")

    def test_assigned_after_class(self):
        r = self.run1("class A:\n    pass\nA.later = 1\n", "pk.A.later")
        self.assertEqual(r.status, "verified")

    def test_setattr_on_class_abstains(self):
        r = self.run1("class A:\n    pass\nsetattr(A, 'x', 1)\n", "pk.A.y")
        self.assertEqual(r.status, "cannot_verify")

    def test_dynamic_module_names_abstain(self):
        for src in ("globals().update({'zz': 1})\n", "exec('def zz(): pass')\n", "for n in 'ab':\n    globals()[n] = 1\n"):
            self.assertEqual(self.run1(src, "pk.zz").status, "cannot_verify", src)

    def test_type_checking_only_not_verified(self):
        r = self.run1("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from os import path as Thing\n", "pk.Thing")
        self.assertEqual(r.status, "cannot_verify")

    def test_deleted_name_not_verified(self):
        r = self.run1("def helper(): pass\ndel helper\n", "pk.helper")
        self.assertEqual(r.status, "cannot_verify")

    def test_version_conditional_not_verified(self):
        r = self.run1("import sys\nif sys.version_info < (3, 0):\n    def old(): pass\n", "pk.old")
        self.assertEqual(r.status, "cannot_verify")

    def test_defined_in_both_branches_is_verified(self):
        src = "import sys\nif sys.version_info >= (3, 8):\n    def f(): pass\nelse:\n    def f(): pass\n"
        self.assertEqual(self.run1(src, "pk.f").status, "verified")

    def test_overload_kwarg_uses_implementation(self):
        src = ("from typing import overload\n@overload\ndef f(a: int) -> int: ...\n@overload\n"
               "def f(a: int, *, b: int) -> int: ...\ndef f(a, *, b=0): return a\n")
        r = self.run1(src, "pk.f", kwargs=["b"])
        self.assertEqual(r.params["b"], "accepted")

    def test_overload_only_stubs_abstain_on_kwargs(self):
        src = "from typing import overload\n@overload\ndef f(a: int) -> int: ...\n"
        r = self.run1(src, "pk.f", kwargs=["a"])
        self.assertTrue(r.params["a"].startswith("cannot_verify"))

    def test_kwargs_string_and_bad_input(self):
        r = self.run1("def f(abc): pass\n", "pk.f", kwargs="abc")
        self.assertEqual(r.params, {"abc": "accepted"})
        r = verify_symbol("requests.get", "../requests/2.31.0")
        self.assertEqual(r.status, "cannot_verify")

    def test_zip_bomb_is_not_unpacked(self):
        import io, zipfile
        from apiverify.fetch import _safe_extract_zip, MAX_MEMBER, FetchError
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("b/__init__.py", "x=1\n")
            z.writestr("b/big.bin", b"\0" * (20 * 1024 * 1024))
            z.writestr("b/huge.py", b"#" * (MAX_MEMBER + 10))
        d = tempfile.mkdtemp()
        with self.assertRaises(FetchError):
            _safe_extract_zip(zipfile.ZipFile(buf), d)
        self.assertFalse(os.path.exists(os.path.join(d, "b", "big.bin")))

    def test_mcp_missing_symbol_is_clean_error(self):
        r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "verify_python_api", "arguments": {"version": "1"}}})
        txt = r["result"]["content"][0]["text"]
        self.assertIn("symbol", txt)
        self.assertNotIn("KeyError", txt)

    def test_retest_cases(self):
        C = "class A:\n    pass\n"
        cases = [
            ("import sys\nclass A: pass\nif sys.platform=='win32':\n    A.w=1\n", "pk.A.w", "cannot_verify"),
            ("class A:\n    x=1\ndel A.x\n", "pk.A.x", "cannot_verify"),
            ("class A: pass\nAlias=A\nAlias.viaalias=1\n", "pk.A.viaalias", "verified"),
            ("class A:\n    __slots__=('s',)\n", "pk.A.s", "verified"),
            ("class A:\n    __slots__=('s',)\n", "pk.A.t", "not_found"),
            ("class A:\n    __slots__=tuple('ab')\n", "pk.A.a", "cannot_verify"),
            ("class A: pass\nclass B: pass\nB.later=1\n", "pk.A.later", "not_found"),
        ]
        for src, sym, want in cases:
            self.assertEqual(self.run1(src, sym).status, want, src)

    def test_retest2_cases(self):
        cases = [
            ("class A: pass\nclass B: pass\nAl=A\nAl=B\nAl.x=1\n", "pk.A.x", "cannot_verify"),
            ("class A: pass\nclass B: pass\nAl=A\nAl=B\nAl.x=1\n", "pk.B.x", "cannot_verify"),
            ("import sys\nclass A: pass\nif sys.platform=='win32':\n    Al=A\nelse:\n    Al=object\nAl.y=1\n", "pk.A.y", "cannot_verify"),
            ("class A: pass\nB=A\nC=B\nC.z=1\n", "pk.A.z", "verified"),
            ("class A: pass\nA.y: int\n", "pk.A.y", "not_found"),
            ("class A:\n    x=1\nclass A:\n    y=1\n", "pk.A.x", "cannot_verify"),
            ("class A: pass\nA.a, A.b = 1, 2\n", "pk.A.b", "verified"),
            ("class A:\n    x=1\nAl=A\ndel Al.x\n", "pk.A.x", "cannot_verify"),
            ("class A: pass\nfor A.k in (1,):\n    pass\n", "pk.A.k", "cannot_verify"),
        ]
        for src, sym, want in cases:
            self.assertEqual(self.run1(src, sym).status, want, src)


if __name__ == "__main__":
    unittest.main()
