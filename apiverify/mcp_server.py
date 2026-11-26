"""Minimal MCP server over stdio (JSON-RPC 2.0, newline-delimited). No dependencies."""
import json
import sys

from .resolve import verify_symbol

PROTOCOL = "2024-11-05"
TOOL = {
    "name": "verify_python_api",
    "description": (
        "Check whether a Python class, function, method or attribute exists in an EXACT released version of a PyPI package, "
        "by reading that version's published source. Returns status verified (with file, line, signature, deprecation signals), "
        "not_found (the name is absent from the release's SOURCE under a limited static syntax boundary; this is not a runtime guarantee), or cannot_verify (dynamic, conditional or external code; do not assume either way). A verified 'instance attribute' is set on self in a method, not necessarily a class attribute. "
        "Use before writing code that calls a library API you are not certain about for the project's pinned version."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Dotted path, e.g. requests.Session.get"},
            "version": {"type": "string", "description": "Exact version (e.g. 2.31.0) or 'latest'"},
            "package": {"type": "string", "description": "PyPI name if different from the import name"},
            "kwargs": {"type": "array", "items": {"type": "string"}, "description": "Keyword arguments to check against the signature"},
        },
        "required": ["symbol", "version"],
    },
}


def handle(msg):
    m, i = msg.get("method"), msg.get("id")
    if m == "initialize":
        return {"jsonrpc": "2.0", "id": i, "result": {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                "serverInfo": {"name": "apiverify", "version": "0.1.2"}}}
    if m == "tools/list":
        return {"jsonrpc": "2.0", "id": i, "result": {"tools": [TOOL]}}
    if m == "tools/call":
        p = msg.get("params", {})
        if p.get("name") != TOOL["name"]:
            return {"jsonrpc": "2.0", "id": i, "error": {"code": -32602, "message": "unknown tool"}}
        a = p.get("arguments") or {}
        try:
            if not isinstance(a.get("symbol"), str) or not a.get("symbol"):
                out, err = {"status": "cannot_verify", "reason": "argument 'symbol' (string) is required"}, True
            else:
                r = verify_symbol(a["symbol"], a.get("version", "latest"), a.get("package"), a.get("kwargs"))
                out, err = r.to_dict(), False
        except Exception as e:  # never crash the server
            out, err = {"status": "cannot_verify", "reason": f"internal error: {e.__class__.__name__}"}, True
        return {"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": json.dumps(out, indent=2)}], "isError": err}}
    if m == "ping":
        return {"jsonrpc": "2.0", "id": i, "result": {}}
    if i is None:
        return None  # notification
    return {"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": "method not found"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
