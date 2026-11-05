# -*- coding: utf-8 -*-
"""Kura Studio — server dell'app. Serve la GUI, prepara il workflow dai pochi campi (prompt, seed, steps, risoluzione) e lo
manda al motore in locale; restituisce progresso e video finito. Il motore (ComfyUI) resta sullo sfondo."""
import asyncio
import json
import os
import random
import re
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))   # il python 'embedded' non aggiunge la cartella dello script
import updater

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CFG = json.load(open(ROOT / "config.json", encoding="utf-8-sig")) if (ROOT / "config.json").exists() else {}
ENGINE = CFG.get("engine_url", "http://127.0.0.1:8188").rstrip("/")
OUT_DIR = Path(CFG.get("engine_output_dir", ""))
PORT = int(CFG.get("port", 7860))
DECODER = CFG.get("decoder", "trt")                # "trt" (motore TensorRT, solo questo PC) | "standard" (portabile)
VIDEO_FORMAT = CFG.get("video_format", "video/nvenc_h264-mp4")   # "video/h264-mp4" = codifica CPU, funziona ovunque
OBJECT_INFO = {}                                    # cache di /object_info per risolvere i nomi dei modelli
TEMPLATE = json.load(open(HERE / "workflow_api.json", encoding="utf8"))
OPTS = json.load(open(HERE / "options.json", encoding="utf8"))
JOBS = {}      # job_id -> {"prompt_id", "events": [..], "done": bool, "result": {...}}
CLIENT_ID = str(uuid.uuid4())


def aspect_size(aspect, mp, swap=False, multiple=32):
    m = re.match(r"\s*([\d.]+)\s*:\s*([\d.]+)", aspect)
    w, h = float(m.group(1)), float(m.group(2))
    if swap:
        w, h = h, w
    r = w / h
    H = (mp * 1e6 / r) ** 0.5
    W = H * r
    q = lambda x: max(multiple, int(round(x / multiple)) * multiple)
    return q(W), q(H)


def frames_for(seconds):
    import math
    return int(math.ceil((seconds * 24 - 5) / 17 - 0.02) * 17 + 5)


def pick(options, wanted):
    """Nome del modello come lo vede QUESTO motore: confronto sul nome del file, ignorando le sottocartelle."""
    base = wanted.replace("\\", "/").split("/")[-1].lower()
    for o in options or []:
        if str(o).replace("\\", "/").split("/")[-1].lower() == base:
            return o
    return wanted


def build_workflow(p):
    wf = json.loads(json.dumps(TEMPLATE))
    seed = int(p.get("seed", -1))
    if seed < 0:
        seed = random.randint(0, 2**48)
    text = p.get("prompt") or wf["10"]["inputs"]["value"]
    wf["10"]["inputs"]["value"] = text
    wf["73"]["inputs"]["noise_seed"] = seed
    wf["75"]["inputs"]["steps"] = int(p.get("steps", 4))
    # risoluzione e durata calcolate qui: cosi' non servono nodi custom
    r = wf["14"]["inputs"]
    W, H = aspect_size(p.get("aspect", r["aspect_ratio"]), float(p.get("megapixels", r["megapixels"])), bool(p.get("swap", False)))
    secs = parse_duration(text)
    length = frames_for(secs) if secs else 124
    wf["70"]["inputs"]["width"], wf["70"]["inputs"]["height"], wf["70"]["inputs"]["length"] = W, H, length
    for nid in ("14", "30", "99"):
        wf.pop(nid, None)
    adv = p.get("advanced") or {}
    if "style_lora" in adv:
        wf["96"]["inputs"]["strength_model"] = float(adv["style_lora"])
    if "tao_lora" in adv:
        wf["97"]["inputs"]["strength_model"] = float(adv["tao_lora"])
    if "shift_video" in adv:
        wf["67"]["inputs"]["shift_video"] = float(adv["shift_video"])
    if "shift_audio" in adv:
        wf["67"]["inputs"]["shift_audio"] = float(adv["shift_audio"])
    if "keep_percent" in adv:
        wf["69"]["inputs"]["selection.keep_percent"] = float(adv["keep_percent"])
    if "sampler" in adv:
        wf["74"]["inputs"]["sampler_name"] = adv["sampler"]
    if adv.get("reference_image"):
        wf["7"]["inputs"]["image"] = adv["reference_image"]
    # decoder: TensorRT solo dove esiste l'engine, altrimenti VAE standard (identico nel risultato, piu' lento)
    if DECODER != "trt":
        wf.pop("5", None)
        wf["77"]["inputs"]["vae"] = ["9", 0]
    # formato del video
    f = wf["79"]["inputs"]
    f["format"] = VIDEO_FORMAT
    if VIDEO_FORMAT == "video/h264-mp4":
        for k in ("bitrate", "megabit"):
            f.pop(k, None)
        f["crf"] = 19
    f["filename_prefix"] = "KuraStudio/video"
    # nomi dei modelli come li vede il motore
    oi = OBJECT_INFO
    def opts(cls, field):
        try:
            return oi[cls]["input"]["required"][field][0]
        except Exception:
            return None
    for nid, cls, field in (("66", "UNETLoader", "unet_name"), ("2", "CLIPLoader", "clip_name"), ("1", "VAELoader", "vae_name"),
                            ("9", "VAELoader", "vae_name"), ("96", "LoraLoaderModelOnly", "lora_name"), ("97", "LoraLoaderModelOnly", "lora_name")):
        if nid in wf and opts(cls, field):
            wf[nid]["inputs"][field] = pick(opts(cls, field), wf[nid]["inputs"][field])
    return wf, seed


def parse_duration(text):
    rx = TEMPLATE["99"]["inputs"]["regex_pattern"]
    m = re.search(rx, text or "", re.I)
    try:
        return float(m.group(1)) if m else None
    except Exception:
        return None


async def watch(job_id, prompt_id):
    """Segue il motore via websocket e traduce gli eventi in avanzamento semplice per la GUI."""
    job = JOBS[job_id]
    t0 = time.time()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(ENGINE.replace("http", "ws") + "/ws?clientId=" + CLIENT_ID, max_msg_size=0) as ws:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        d = json.loads(msg.data)
                        t, data = d.get("type"), d.get("data", {})
                        if data.get("prompt_id") not in (None, prompt_id):
                            continue
                        if t == "progress":
                            job["events"].append({"k": "progress", "v": data["value"], "m": data["max"], "t": round(time.time() - t0, 1)})
                        elif t == "executing":
                            job["events"].append({"k": "stage", "node": data.get("node"), "t": round(time.time() - t0, 1)})
                            if data.get("node") is None:
                                break
                        elif t == "execution_cached":
                            job["events"].append({"k": "cached", "n": len(data.get("nodes", []))})
                        elif t == "execution_error":
                            job["events"].append({"k": "error", "msg": data.get("exception_message", "errore")})
                            job["done"] = True
                            return
                        elif t == "executed" and "gifs" in (data.get("output") or {}):
                            g = data["output"]["gifs"][-1]
                            job["result"] = {"filename": g["filename"], "subfolder": g.get("subfolder", ""), "type": g.get("type", "output")}
        # esito finale dalla cronologia (piu' affidabile dell'evento)
        async with aiohttp.ClientSession() as s:
            for _ in range(20):
                async with s.get(f"{ENGINE}/history/{prompt_id}") as r:
                    h = (await r.json()).get(prompt_id)
                if h and h.get("outputs"):
                    for nid, out in h["outputs"].items():
                        if "gifs" in out:
                            g = out["gifs"][-1]
                            job["result"] = {"filename": g["filename"], "subfolder": g.get("subfolder", ""), "type": g.get("type", "output")}
                    break
                await asyncio.sleep(0.5)
        job["events"].append({"k": "done", "t": round(time.time() - t0, 1)})
    except Exception as e:
        job["events"].append({"k": "error", "msg": str(e)})
    job["done"] = True


async def h_index(request):
    return web.FileResponse(HERE / "web" / "index.html")


async def h_config(request):
    return web.json_response({"defaults": OPTS["defaults"], "aspects": OPTS["aspects"], "samplers": OPTS["samplers"],
                              "duration": parse_duration(OPTS["defaults"]["prompt"]), "engine_url": ENGINE})


async def h_duration(request):
    body = await request.json()
    return web.json_response({"seconds": parse_duration(body.get("prompt", ""))})


async def load_object_info():
    global OBJECT_INFO
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as ss:
            async with ss.get(ENGINE + "/object_info") as r:
                OBJECT_INFO = await r.json()
    except Exception:
        pass


async def h_health(request):
    out = {"engine": False, "missing_nodes": [], "missing_models": []}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.get(ENGINE + "/object_info") as r:
                oi = await r.json()
        out["engine"] = True
        OBJECT_INFO.update(oi)
        need = {n["class_type"] for k, n in TEMPLATE.items() if k not in ("14", "30", "99") and not (DECODER != "trt" and k == "5")}
        out["missing_nodes"] = sorted(c for c in need if c not in oi)
        try:
            man = json.load(open(ROOT / "installer" / "manifest.json", encoding="utf8"))
            have = set()
            for cls, fld in (("UNETLoader", "unet_name"), ("CLIPLoader", "clip_name"), ("VAELoader", "vae_name"), ("LoraLoaderModelOnly", "lora_name")):
                for o in oi.get(cls, {}).get("input", {}).get("required", {}).get(fld, [[]])[0]:
                    have.add(str(o).replace("\\", "/").split("/")[-1].lower())
            out["missing_models"] = [m["id"] for m in man["models"] if m["dest"].split("/")[-1].lower() not in have]
        except Exception:
            pass
    except Exception:
        pass
    return web.json_response(out)


async def h_generate(request):
    body = await request.json()
    if not OBJECT_INFO:
        await load_object_info()
    wf, seed = build_workflow(body)
    async with aiohttp.ClientSession() as s:
        async with s.post(ENGINE + "/prompt", json={"prompt": wf, "client_id": CLIENT_ID}) as r:
            data = await r.json()
            if r.status != 200 or "prompt_id" not in data:
                return web.json_response({"error": data.get("error", data), "node_errors": data.get("node_errors")}, status=400)
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"prompt_id": data["prompt_id"], "events": [], "done": False, "result": None, "seed": seed}
    asyncio.create_task(watch(job_id, data["prompt_id"]))
    return web.json_response({"job": job_id, "seed": seed})


async def h_events(request):
    job = JOBS.get(request.match_info["job"])
    if not job:
        return web.json_response({"error": "job sconosciuto"}, status=404)
    since = int(request.query.get("since", 0))
    return web.json_response({"events": job["events"][since:], "next": len(job["events"]), "done": job["done"], "result": job["result"]})


async def h_cancel(request):
    async with aiohttp.ClientSession() as s:
        await s.post(ENGINE + "/interrupt")
    return web.json_response({"ok": True})


async def h_video(request):
    fn, sub = request.query["filename"], request.query.get("subfolder", "")
    if ".." in fn or ".." in sub:
        raise web.HTTPBadRequest()
    p = OUT_DIR / sub / fn
    if not p.is_file():
        raise web.HTTPNotFound()
    headers = {}
    if request.query.get("download"):
        headers["Content-Disposition"] = f'attachment; filename="{fn}"'
    return web.FileResponse(p, headers=headers)


async def h_upload(request):
    """Immagine di riferimento: la passa al motore e restituisce il nome da usare."""
    reader = await request.multipart()
    f = await reader.next()
    data = await f.read()
    form = aiohttp.FormData()
    form.add_field("image", data, filename=f.filename, content_type=f.headers.get("Content-Type", "image/png"))
    form.add_field("overwrite", "true")
    async with aiohttp.ClientSession() as s:
        async with s.post(ENGINE + "/upload/image", data=form) as r:
            j = await r.json()
    return web.json_response({"name": (j.get("subfolder") + "/" if j.get("subfolder") else "") + j["name"]})


async def h_history(request):
    items = []
    d = OUT_DIR / "KuraStudio"
    if d.is_dir():
        for p in sorted(d.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True)[:24]:
            if p.name.endswith("-audio.mp4") or "-audio" not in p.name:
                items.append({"filename": p.name, "subfolder": "KuraStudio", "mtime": int(p.stat().st_mtime), "mb": round(p.stat().st_size / 1e6, 1)})
    return web.json_response({"items": items})


async def h_upd_check(request):
    loop = asyncio.get_running_loop()
    return web.json_response(await loop.run_in_executor(None, updater.check))


async def h_upd_apply(request):
    loop = asyncio.get_running_loop()
    try:
        v = await loop.run_in_executor(None, updater.apply)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)
    loop.call_later(1.2, updater.restart_self)       # riparte con il codice nuovo
    return web.json_response({"ok": True, "version": v, "restarting": True})


app = web.Application(client_max_size=200 * 1024 * 1024)
app.add_routes([
    web.get("/", h_index), web.get("/api/config", h_config), web.post("/api/duration", h_duration), web.get("/api/health", h_health),
    web.post("/api/generate", h_generate), web.get("/api/events/{job}", h_events), web.post("/api/cancel", h_cancel),
    web.get("/api/video", h_video), web.post("/api/upload", h_upload), web.get("/api/history", h_history),
    web.get("/api/update/check", h_upd_check), web.post("/api/update/apply", h_upd_apply),
])

if __name__ == "__main__":
    print(f"Kura Studio su http://127.0.0.1:{PORT}  (motore: {ENGINE})")
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)
