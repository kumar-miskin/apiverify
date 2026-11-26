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


if __name__ == "__main__":
    unittest.main()
