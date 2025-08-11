
import os, sys, json, tempfile, shutil, zipfile, hashlib
from urllib.request import urlopen, Request

CURRENT_VERSION = "1.0.2"

def _http_get(url, timeout=20):
    req = Request(url, headers={"User-Agent":"Mozilla/5.0 (MTScanner)"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()

def _semver_tuple(v):
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except Exception:
        return (0,0,0)

def check_and_update(version_json_url: str, app_dir: str) -> dict:
    info = {"updated": False, "current": CURRENT_VERSION, "remote": None, "message": ""}
    try:
        raw = _http_get(version_json_url).decode("utf-8")
        j = json.loads(raw)
        remote_v = j.get("version","0.0.0")
        info["remote"] = remote_v
        if _semver_tuple(remote_v) <= _semver_tuple(CURRENT_VERSION):
            info["message"] = "Brak nowszej wersji."
            return info
        url = j.get("url"); sha256 = j.get("sha256")
        if not url:
            info["message"] = "Brak URL do aktualizacji w version.json."
            return info
        data = _http_get(url, timeout=60)
        if sha256:
            h = hashlib.sha256(data).hexdigest()
            if h.lower() != sha256.lower():
                info["message"] = "Nie zgadza się suma SHA256 pobranej paczki."
                return info
        tmpdir = tempfile.mkdtemp(prefix="mtupd_")
        zpath = os.path.join(tmpdir, "update.zip")
        with open(zpath, "wb") as f: f.write(data)
        with zipfile.ZipFile(zpath, "r") as zf: zf.extractall(tmpdir)
        for rootd, dirs, files in os.walk(tmpdir):
            for name in files:
                src = os.path.join(rootd, name)
                if src == zpath: continue
                rel = os.path.relpath(src, tmpdir)
                dst = os.path.join(app_dir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        info["updated"] = True
        info["message"] = f"Zaktualizowano do {remote_v}. Uruchom ponownie aplikację."
        return info
    except Exception as e:
        info["message"] = f"Błąd aktualizacji: {e}"
        return info
