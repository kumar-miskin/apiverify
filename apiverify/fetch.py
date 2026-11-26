"""Download the exact released artifact from PyPI and unpack it.

Evidence comes from the published package itself, verified against the
sha256 PyPI lists for that file. Nothing is guessed from docs or memory.
"""
import hashlib
import json
import os
import re
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile

CACHE = os.environ.get("APIVERIFY_CACHE", os.path.join(tempfile.gettempdir(), "apiverify-cache"))
PYPI = os.environ.get("APIVERIFY_PYPI", "https://pypi.org/pypi")
MAX_BYTES = 80 * 1024 * 1024


class FetchError(Exception):
    pass


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "apiverify/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code} for {url}")
    except Exception as e:  # network, DNS, timeout
        raise FetchError(f"network error for {url}: {e}")
    if len(data) > MAX_BYTES:
        raise FetchError("artifact too large")
    return data


def _safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)


def _pick_file(files):
    """Prefer a pure-python wheel, then an sdist. Never run setup code."""
    wheels = [f for f in files if f["packagetype"] == "bdist_wheel" and not f.get("yanked")]
    pure = [f for f in wheels if f["filename"].endswith("-none-any.whl")]
    if pure:
        return pure[0]
    sdists = [f for f in files if f["packagetype"] == "sdist" and not f.get("yanked")]
    if sdists:
        return sdists[0]
    if wheels:
        return wheels[0]
    return None


def _safe_extract_zip(zf, dest):
    base = os.path.realpath(dest)
    for m in zf.namelist():
        p = os.path.realpath(os.path.join(dest, m))
        if not p.startswith(base + os.sep) and p != base:
            raise FetchError("unsafe path in archive")
    zf.extractall(dest)


def _safe_extract_tar(tf, dest):
    base = os.path.realpath(dest)
    for m in tf.getmembers():
        p = os.path.realpath(os.path.join(dest, m.name))
        if not p.startswith(base + os.sep) and p != base:
            raise FetchError("unsafe path in archive")
        if m.issym() or m.islnk() or m.isdev():
            continue
        tf.extract(m, dest)


_MEMO = {}


def fetch_source(package, version):
    k = (package, version)
    if k not in _MEMO:
        _MEMO[k] = _fetch_source(package, version)
    return _MEMO[k]


def _fetch_source(package, version):
    """Return dict(root, filename, sha256, url, version). Raises FetchError."""
    if version in (None, "", "latest"):
        meta_url = f"{PYPI}/{package}/json"
    else:
        meta_url = f"{PYPI}/{package}/{version}/json"
    try:
        meta = json.loads(_get(meta_url))
    except json.JSONDecodeError:
        raise FetchError("bad metadata from index")
    files = meta.get("urls") or []
    f = _pick_file(files)
    if not f:
        raise FetchError(f"no downloadable non-yanked file for {package}=={version}")
    sha = f["digests"]["sha256"]
    resolved = meta["info"]["version"]
    dest = os.path.join(CACHE, _safe_name(package), _safe_name(resolved), sha[:16])
    marker = os.path.join(dest, ".ok")
    info = {"filename": f["filename"], "sha256": sha, "url": f["url"], "version": meta["info"]["version"]}
    if os.path.exists(marker):
        info["root"] = os.path.join(dest, "src")
        return info
    data = _get(f["url"], timeout=60)
    if hashlib.sha256(data).hexdigest() != sha:
        raise FetchError("sha256 mismatch against index metadata")
    src = os.path.join(dest, "src")
    os.makedirs(src, exist_ok=True)
    tmp = os.path.join(dest, f["filename"])
    with open(tmp, "wb") as fh:
        fh.write(data)
    try:
        if f["filename"].endswith((".whl", ".zip")):
            with zipfile.ZipFile(tmp) as zf:
                _safe_extract_zip(zf, src)
        else:
            with tarfile.open(tmp) as tf:
                _safe_extract_tar(tf, src)
    except (zipfile.BadZipFile, tarfile.TarError) as e:
        raise FetchError(f"cannot unpack artifact: {e}")
    open(marker, "w").close()
    info["root"] = src
    return info
