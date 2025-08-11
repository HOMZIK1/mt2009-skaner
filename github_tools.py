
import base64, json, os, re, time
import requests

API = "https://api.github.com"

class GhError(Exception):
    pass

def _headers(token:str):
    return {"Authorization": f"token {token}", "Accept":"application/vnd.github+json", "User-Agent":"MTScanner-Updater"}

def create_or_get_release(owner_repo:str, tag:str, token:str, title:str=None, draft=False, prerelease=False):
    owner, repo = owner_repo.split("/", 1)
    # check existing
    r = requests.get(f"{API}/repos/{owner}/{repo}/releases/tags/{tag}", headers=_headers(token))
    if r.status_code == 200:
        return r.json()
    # create
    payload = {"tag_name": tag, "name": title or tag, "draft": draft, "prerelease": prerelease}
    r = requests.post(f"{API}/repos/{owner}/{repo}/releases", headers=_headers(token), json=payload)
    if r.status_code not in (200,201):
        raise GhError(f"create release failed: {r.status_code} {r.text}")
    return r.json()

def upload_asset(release_json:dict, filepath:str, token:str, name:str="update.zip"):
    upload_url = release_json["upload_url"].split("{",1)[0] + f"?name={name}"
    with open(filepath, "rb") as f:
        data = f.read()
    r = requests.post(upload_url, headers={**_headers(token), "Content-Type":"application/zip"}, data=data)
    if r.status_code not in (200,201):
        # If asset already exists, delete & reupload
        if r.status_code == 422 and "already_exists" in r.text:
            # find existing asset by name and delete
            assets = requests.get(release_json["assets_url"], headers=_headers(token)).json()
            for a in assets:
                if a["name"] == name:
                    requests.delete(a["url"], headers=_headers(token))
                    break
            r = requests.post(upload_url, headers={**_headers(token), "Content-Type":"application/zip"}, data=data)
            if r.status_code not in (200,201):
                raise GhError(f"upload asset failed: {r.status_code} {r.text}")
        else:
            raise GhError(f"upload asset failed: {r.status_code} {r.text}")
    return r.json()["browser_download_url"]

def update_version_json(owner_repo:str, token:str, version:str, url:str, notes:str=""):
    owner, repo = owner_repo.split("/", 1)
    path = "version.json"
    # get current to obtain sha
    r = requests.get(f"{API}/repos/{owner}/{repo}/contents/{path}", headers=_headers(token))
    sha = None
    if r.status_code == 200:
        sha = r.json()["sha"]
    new_json = {"version": version, "url": url, "sha256":"", "notes": notes}
    content = base64.b64encode(json.dumps(new_json, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii")
    payload = {"message": f"Update version.json to {version}", "content": content}
    if sha: payload["sha"] = sha
    r = requests.put(f"{API}/repos/{owner}/{repo}/contents/{path}", headers=_headers(token), json=payload)
    if r.status_code not in (200,201):
        raise GhError(f"update version.json failed: {r.status_code} {r.text}")
    return True
