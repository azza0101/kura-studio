# -*- coding: utf-8 -*-
"""Kura Studio - installer dei componenti sul PC dove c'e' gia' un motore (ComfyUI).

  python install.py --engine "C:\\ComfyUI_windows_portable\\ComfyUI" [--mirror "D:\\copia\\models" ...] [--check]

Fa tre cose: (1) installa i nodi mancanti (git clone), (2) scarica i pesi dalle fonti pubbliche, ripartendo da dove si era
interrotto e verificando la dimensione, (3) copia da una cartella "mirror" i file senza fonte pubblica.
Non sovrascrive mai un file gia' presente con la dimensione giusta (lo cerca anche in altre sottocartelle di models/)."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAN = json.load(open(HERE / "manifest.json", encoding="utf8"))


def human(n):
    return f"{n / 1e9:.2f} GB" if n > 1e9 else f"{n / 1e6:.1f} MB"


def model_roots(eng):
    """Cartelle models/ del motore: quella interna piu' quelle indicate in extra_model_paths.yaml (modelli su un altro disco)."""
    roots = [eng / "models"]
    y = eng / "extra_model_paths.yaml"
    if y.exists():
        try:
            import yaml
            for sect in (yaml.safe_load(y.read_text(encoding="utf8")) or {}).values():
                if isinstance(sect, dict) and sect.get("base_path"):
                    b = Path(str(sect["base_path"]))
                    for cand in (b / "models", b):
                        if cand.is_dir() and cand not in roots:
                            roots.append(cand)
        except Exception:
            pass
    return roots


def find_existing(roots, rel, size):
    name = Path(rel).name
    for models_dir in roots:
        dest = models_dir / rel
        if dest.is_file() and dest.stat().st_size == size:
            return dest
    for models_dir in roots:
        for p in models_dir.rglob(name):
            if p.is_file() and p.stat().st_size == size:
                return p
    return None


def download(url, dest, size):
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = Path(str(dest) + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "KuraStudio-installer"}
    tok = os.environ.get("HF_TOKEN")
    if tok:
        headers["Authorization"] = "Bearer " + tok
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab" if have else "wb") as f:
        if have and r.status != 206:
            f.truncate(0)
            have = 0
        done, last = have, 0
        while True:
            chunk = r.read(8 * 1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if done - last > 256 * 1024 * 1024 or done == size:
                print(f"    {human(done)} / {human(size)}  ({done * 100 // size}%)", flush=True)
                last = done
    if part.stat().st_size != size:
        raise RuntimeError(f"dimensione {part.stat().st_size} diversa da quella attesa {size}")
    os.replace(part, dest)


def copy_with_progress(src, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(dest) + ".part")
    with open(src, "rb") as a, open(tmp, "wb") as b:
        shutil.copyfileobj(a, b, 16 * 1024 * 1024)
    os.replace(tmp, dest)


def ensure_node(custom_dir, node, py):
    target = custom_dir / node["name"]
    if target.is_dir():
        return "gia' presente"
    custom_dir.mkdir(parents=True, exist_ok=True)
    if shutil.which("git"):
        subprocess.check_call(["git", "clone", "--depth", "1", node["git"], str(target)])
    else:
        z = custom_dir / (node["name"] + ".zip")
        urllib.request.urlretrieve(node["git"] + "/archive/refs/heads/main.zip", z)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(custom_dir)
        for d in custom_dir.glob(node["name"] + "-*"):
            d.rename(target)
        z.unlink()
    req = target / "requirements.txt"
    if node.get("requirements") and req.exists():
        subprocess.check_call([py, "-m", "pip", "install", "-r", str(req)])
    return "installato"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True, help="cartella ComfyUI (quella con main.py)")
    ap.add_argument("--mirror", action="append", default=[], help="cartella dove cercare i file senza fonte pubblica (anche rete)")
    ap.add_argument("--python", help="python del motore (default: python_embeded accanto alla cartella)")
    ap.add_argument("--check", action="store_true", help="solo verifica, non scarica")
    a = ap.parse_args()
    eng = Path(a.engine)
    if not (eng / "main.py").exists():
        sys.exit("Non trovo main.py in " + str(eng))
    models, custom = eng / "models", eng / "custom_nodes"
    roots = model_roots(eng)
    emb = eng.parent / "python_embeded" / "python.exe"
    py = a.python or (str(emb) if emb.exists() else sys.executable)
    report = {"nodes": {}, "models": {}}

    ver = ""
    vf = eng / "comfyui_version.py"
    if vf.exists():
        ver = next((l.split('"')[1] for l in vf.read_text(encoding="utf8").splitlines() if "__version__" in l), "")
    print(f"Motore: {eng}  versione {ver or '?'}  (richiesta >= {MAN['min_engine_version']})")
    if ver and tuple(int(x) for x in ver.split(".")[:3]) < tuple(int(x) for x in MAN["min_engine_version"].split(".")):
        print("  ATTENZIONE: motore troppo vecchio, aggiornalo prima (servono i nodi MiniMax H3).")

    print("\n== Nodi")
    for n in MAN["nodes"]:
        if a.check:
            st = "presente" if (custom / n["name"]).is_dir() else "MANCA"
        else:
            try:
                st = ensure_node(custom, n, py)
            except Exception as e:
                st = "ERRORE: " + str(e)
        report["nodes"][n["name"]] = st
        print(f"  {n['name']:<28} {st}" + ("  (opzionale)" if n.get("optional") else ""))

    print("\n== Pesi")
    for m in MAN["models"]:
        ex = find_existing(roots, m["dest"], m["size"])
        if ex:
            report["models"][m["id"]] = "presente"
            print(f"  {m['id']:<14} gia' presente ({human(m['size'])})")
            continue
        if a.check:
            report["models"][m["id"]] = "MANCA"
            print(f"  {m['id']:<14} MANCA ({human(m['size'])})")
            continue
        done = False
        for mir in a.mirror:
            hits = [p for p in Path(mir).rglob(Path(m["dest"]).name) if p.is_file() and p.stat().st_size == m["size"]]
            if hits:
                print(f"  {m['id']:<14} copio da {hits[0]}")
                copy_with_progress(hits[0], models / m["dest"])
                done = True
                break
        for url in ([] if done else m["urls"]):
            try:
                print(f"  {m['id']:<14} scarico {Path(m['dest']).name} ({human(m['size'])})")
                download(url, models / m["dest"], m["size"])
                done = True
                break
            except Exception as e:
                print("    errore:", e)
        report["models"][m["id"]] = "ok" if done else "DA COPIARE A MANO"
        if not done:
            print(f"  {m['id']:<14} NON DISPONIBILE: {m.get('note', '')}\n    destinazione: {models / m['dest']}")
    json.dump(report, open(HERE / "install_report.json", "w", encoding="utf8"), indent=1)
    bad = [k for k, v in report["models"].items() if v not in ("ok", "presente")]
    print("\nFatto." if not bad else f"\nIncompleto: {', '.join(bad)}")
    sys.exit(0 if not bad else 2)


if __name__ == "__main__":
    main()
