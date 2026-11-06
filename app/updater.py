# -*- coding: utf-8 -*-
"""Aggiornamento dell'app da GitHub. Due modi, scelti da soli:
  - cartella git (git installato): fetch + pull --ff-only
  - senza git: scarica lo zip del ramo e lo copia sopra i file dell'app (config.json e i dati locali non si toccano)
Repo e ramo stanno in config.json: "update_repo": "utente/nome", "update_branch": "main", "update_token": "(solo se privato)"."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "app_version.json"
KEEP = {"config.json", "app_version.json", "installer/install_report.json"}   # mai sovrascritti dall'aggiornamento
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _cfg():
    p = ROOT / "config.json"
    cfg = json.load(open(p, encoding="utf-8-sig")) if p.exists() else {}
    # La V2 vive su un ramo separato: non aggiornare accidentalmente alla V1.
    if cfg.get("update_repo", "").lower() == "azza0101/kura-studio" and cfg.get("update_branch", "main") == "main":
        cfg["update_branch"] = "v2"
    return cfg


def _git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, creationflags=NOWIN, timeout=120)
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-400:])
    return r.stdout.strip()


def _has_git():
    return (ROOT / ".git").exists() and subprocess.run(["git", "--version"], capture_output=True, creationflags=NOWIN).returncode == 0 if shutil.which("git") else False


def _api(path, c):
    h = {"User-Agent": "KuraStudio", "Accept": "application/vnd.github+json"}
    tok = c.get("update_token") or os.environ.get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = "Bearer " + tok
    return urllib.request.urlopen(urllib.request.Request("https://api.github.com" + path, headers=h), timeout=60)


def local_version():
    if _has_git():
        try:
            return {"sha": _git("rev-parse", "HEAD"), "date": _git("log", "-1", "--format=%cd", "--date=short"), "subject": _git("log", "-1", "--format=%s")}
        except Exception:
            pass
    return json.load(open(STATE, encoding="utf8")) if STATE.exists() else {"sha": "", "date": "", "subject": ""}


def check():
    c = _cfg()
    repo, br = c.get("update_repo"), c.get("update_branch", "main")
    cur = local_version()
    if not repo:
        return {"configured": False, "current": cur}
    out = {"configured": True, "repo": repo, "branch": br, "current": cur}
    try:
        if _has_git():
            _git("fetch", "origin", br)
            behind = int(_git("rev-list", "--count", f"HEAD..origin/{br}"))
            out["behind"] = behind
            out["changes"] = _git("log", f"HEAD..origin/{br}", "--format=%s", "-n", "15").splitlines() if behind else []
            out["dirty"] = bool(_git("status", "--porcelain", "--untracked-files=no", "--", ".", ":(exclude)config.json"))
        else:
            d = json.load(_api(f"/repos/{repo}/commits/{br}", c))
            out["latest"] = {"sha": d["sha"], "subject": d["commit"]["message"].splitlines()[0], "date": d["commit"]["committer"]["date"][:10]}
            out["behind"] = 0 if d["sha"] == cur.get("sha") else 1
            out["changes"] = [] if out["behind"] == 0 else [out["latest"]["subject"]]
    except Exception as e:
        out["error"] = str(e)[:300]
    return out


def apply():
    c = _cfg()
    repo, br = c.get("update_repo"), c.get("update_branch", "main")
    if not repo:
        raise RuntimeError("update_repo non impostato in config.json")
    if _has_git():
        if _git("status", "--porcelain", "--untracked-files=no", "--", ".", ":(exclude)config.json"):
            raise RuntimeError("Ci sono modifiche locali ai file dell'app: non le sovrascrivo. Fai commit o annullale e riprova.")
        _git("fetch", "origin", br)
        _git("pull", "--ff-only", "origin", br)
        return local_version()
    z = _api(f"/repos/{repo}/zipball/{br}", c).read()
    tmp = Path(tempfile.mkdtemp(prefix="kura_upd_"))
    try:
        with zipfile.ZipFile(io.BytesIO(z)) as zf:
            zf.extractall(tmp)
        src = next(p for p in tmp.iterdir() if p.is_dir())
        for f in src.rglob("*"):
            if f.is_file():
                rel = f.relative_to(src).as_posix()
                if rel in KEEP or rel.startswith(".git/"):
                    continue
                dst = ROOT / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
        sha = src.name.rsplit("-", 1)[-1]
        d = json.load(_api(f"/repos/{repo}/commits/{br}", c))
        state = {"sha": d["sha"], "date": d["commit"]["committer"]["date"][:10], "subject": d["commit"]["message"].splitlines()[0]}
        json.dump(state, open(STATE, "w", encoding="utf8"))
        return state
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def restart_self():
    """Riavvia il server con lo stesso comando, cosi' il nuovo codice parte subito."""
    os.execv(sys.executable, [sys.executable] + sys.argv)
