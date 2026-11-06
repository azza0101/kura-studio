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
import diagnostics

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP_VERSION = (HERE / "VERSION").read_text(encoding="utf-8").strip()
CFG = json.load(open(ROOT / "config.json", encoding="utf-8-sig")) if (ROOT / "config.json").exists() else {}
ENGINE = CFG.get("engine_url", "http://127.0.0.1:8188").rstrip("/")
OUT_DIR = Path(CFG.get("engine_output_dir", ""))
PORT = int(CFG.get("port", 7860))
DECODER = CFG.get("decoder", "trt")                # "trt" (motore TensorRT, solo questo PC) | "standard" (portabile)
VIDEO_FORMAT = CFG.get("video_format", "video/nvenc_h264-mp4")   # "video/h264-mp4" = codifica CPU, funziona ovunque
OBJECT_INFO = {}                                    # cache di /object_info per risolvere i nomi dei modelli
TEMPLATE = json.load(open(HERE / "workflow_api.json", encoding="utf8"))
OPTS = json.load(open(HERE / "options.json", encoding="utf8"))
DEFAULT_FIXED_PROMPT = (HERE / "assets" / "global_kura_style.txt").read_text(encoding="utf-8").strip()
GENERIC_FIXED_PROMPT = (HERE / "assets" / "global_kura_generic.txt").read_text(encoding="utf-8").strip()
JOBS = {}      # job_id -> {"prompt_id", "events": [..], "done": bool, "result": {...}}
REPORTS = {}
CLIENT_ID = str(uuid.uuid4())
DEFAULT_REFERENCE = {}
BUILTIN_REFERENCES = {"giulia": HERE / "assets" / "giulia-reference.jpg", "generic": HERE / "assets" / "generic-reference.png"}

def reference_path(name):
    if name not in BUILTIN_REFERENCES:
        raise ValueError("Reference non riconosciuta")
    return BUILTIN_REFERENCES[name]

async def h_reference(request):
    try:
        path = reference_path(request.match_info["name"])
    except ValueError:
        raise web.HTTPNotFound()
    return web.FileResponse(path)


def read_timing(video):
    try:
        value = json.loads(video.with_suffix(video.suffix + '.kura.json').read_text(encoding='utf-8'))['elapsed_seconds']
        return float(value) if 0 <= float(value) < 1e8 else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def finish_timing(job):
    if job.get('elapsed_seconds') is not None or not job.get('result') or job.get('started_at') is None:
        return
    result = job['result']
    filename = result.get('filename', '')
    if Path(filename).name != filename or result.get('subfolder') != 'KuraStudio':
        return
    job['elapsed_seconds'] = round(max(0, time.monotonic() - job['started_at']), 1)
    video = OUT_DIR / 'KuraStudio' / filename
    try:
        if video.is_file():
            video.with_suffix(video.suffix + '.kura.json').write_text(json.dumps({'elapsed_seconds': job['elapsed_seconds']}), encoding='utf-8')
    except OSError:
        pass


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


def node_choices(info, cls, field):
    try:
        spec = info[cls]["input"]["required"][field]
        if isinstance(spec[0], list):
            return spec[0]
        if len(spec) > 1 and isinstance(spec[1], dict):
            return spec[1].get("options", [])
    except (KeyError, IndexError, TypeError):
        pass
    return []


def build_workflow(p):
    wf = json.loads(json.dumps(TEMPLATE))
    seed = int(p.get("seed", -1))
    if seed < 0:
        seed = random.randint(0, 2**48)
    text = p.get("prompt") or wf["10"]["inputs"]["value"]
    fixed = p.get("fixed_prompt", "")
    if not isinstance(fixed, str):
        raise ValueError("Il prompt fisso deve essere testo")
    fixed = fixed.strip()
    old_block = DEFAULT_FIXED_PROMPT.split("\n\nREGOLA PERMANENTE — EYES", 1)[0]
    if fixed == DEFAULT_FIXED_PROMPT and old_block in text and fixed not in text:
        text = text.replace(old_block, fixed, 1)
    wf["10"]["inputs"]["value"] = fixed + "\n\n" + text if fixed and fixed not in text else text
    wf["73"]["inputs"]["noise_seed"] = seed
    wf["75"]["inputs"]["steps"] = int(p.get("steps", 4))
    # risoluzione e durata calcolate qui: cosi' non servono nodi custom
    r = wf["14"]["inputs"]
    W, H = aspect_size(p.get("aspect", r["aspect_ratio"]), float(p.get("megapixels", r["megapixels"])), bool(p.get("swap", False)))
    secs = p.get("seconds") or parse_duration(text)
    length = frames_for(secs) if secs else 124
    wf["70"]["inputs"]["width"], wf["70"]["inputs"]["height"], wf["70"]["inputs"]["length"] = W, H, length
    for nid in ("14", "30", "99"):
        wf.pop(nid, None)
    adv = p.get("advanced") or {}
    for key in ("denoise",):
        if key in adv:
            wf["75"]["inputs"][key] = float(adv[key])
    if "scheduler" in adv:
        wf["75"]["inputs"]["scheduler"] = adv["scheduler"]
    for key in ("start_percent", "end_percent"):
        if key in adv:
            wf["69"]["inputs"][key] = float(adv[key])
    for key in ("min_tokens", "extra_tokens"):
        if key in adv:
            wf["69"]["inputs"][key] = int(adv[key])
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
    if "frame_rate" in adv:
        f["frame_rate"] = int(adv["frame_rate"])
    if "loop_count" in adv:
        f["loop_count"] = int(adv["loop_count"])
    if "bitrate" in adv:
        f["bitrate"] = int(adv["bitrate"])
    if VIDEO_FORMAT == "video/h264-mp4":
        for k in ("bitrate", "megabit"):
            f.pop(k, None)
        f["crf"] = int(adv.get("crf", 19))
    f["filename_prefix"] = "KuraStudio/video"
    # nomi dei modelli come li vede il motore
    oi = OBJECT_INFO
    def opts(cls, field):
        return node_choices(oi, cls, field)
    for nid, cls, field in (("66", "UNETLoader", "unet_name"), ("2", "CLIPLoader", "clip_name"), ("1", "VAELoader", "vae_name"),
                            ("9", "VAELoader", "vae_name"), ("96", "LoraLoaderModelOnly", "lora_name"), ("97", "LoraLoaderModelOnly", "lora_name")):
        if nid in wf and opts(cls, field):
            wf[nid]["inputs"][field] = pick(opts(cls, field), wf[nid]["inputs"][field])
    return wf, seed


def parse_duration(text):
    rx = TEMPLATE["99"]["inputs"]["regex_pattern"]
    m = re.search(rx, text or "", re.I)
    try:
        if m:
            return float(m.group(1))
        ends = re.findall(r'-->\s*(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})', text or '')
        durations = [int(h)*3600 + int(minutes)*60 + int(sec) + int(ms.ljust(3, '0'))/1000
                     for h, minutes, sec, ms in ends if int(minutes) < 60 and int(sec) < 60]
        return max(durations) if durations else None
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
                            diagnostics.record("generation_error", data.get("exception_type"), data.get("node_type"))
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
        diagnostics.record("engine_unavailable")
        job["events"].append({"k": "error", "msg": str(e)})
    finish_timing(job)
    job["done"] = True


async def h_index(request):
    return web.FileResponse(HERE / "web" / "index.html")


async def h_config(request):
    if not OBJECT_INFO:
        await load_object_info()
    def choices(cls, field, fallback):
        return node_choices(OBJECT_INFO, cls, field) or fallback
    fallback_samplers = OPTS["samplers"] if isinstance(OPTS["samplers"], list) else [OPTS["defaults"]["sampler"]]
    return web.json_response({"version": APP_VERSION, "defaults": OPTS["defaults"], "fixed_prompt_original": DEFAULT_FIXED_PROMPT, "fixed_prompt_generic": GENERIC_FIXED_PROMPT, "aspects": OPTS["aspects"], "samplers": choices("KSamplerSelect", "sampler_name", fallback_samplers),
                              "schedulers": choices("BasicScheduler", "scheduler", ["beta"]),
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
                for o in node_choices(oi, cls, fld):
                    have.add(str(o).replace("\\", "/").split("/")[-1].lower())
            out["missing_models"] = [m["id"] for m in man["models"] if m["dest"].split("/")[-1].lower() not in have]
        except Exception:
            pass
    except Exception:
        pass
    return web.json_response(out)


async def h_generate(request):
    global DEFAULT_REFERENCE
    body = await request.json()
    if not OBJECT_INFO:
        await load_object_info()
    wf, seed = build_workflow(body)
    async with aiohttp.ClientSession() as s:
        if not (body.get("advanced") or {}).get("reference_image"):
            selected = body.get("reference", "giulia")
            try:
                path = reference_path(selected)
            except ValueError:
                return web.json_response({"error": "Reference non riconosciuta"}, status=400)
            if not path.is_file():
                return web.json_response({"error": "Immagine di riferimento mancante"}, status=500)
            if selected not in DEFAULT_REFERENCE:
                import hashlib
                image = path.read_bytes()
                filename = "kura-" + selected + "-" + hashlib.sha256(image).hexdigest()[:12] + path.suffix
                form = aiohttp.FormData()
                form.add_field("image", image, filename=filename, content_type="image/png" if path.suffix == ".png" else "image/jpeg")
                form.add_field("overwrite", "true")
                async with s.post(ENGINE + "/upload/image", data=form) as response:
                    if response.status != 200:
                        return web.json_response({"error": "Impossibile caricare la reference nel motore"}, status=502)
                    info = await response.json()
                    DEFAULT_REFERENCE[selected] = (info.get("subfolder", "") + "/" if info.get("subfolder") else "") + info["name"]
            wf["7"]["inputs"]["image"] = DEFAULT_REFERENCE[selected]
        started_at = time.monotonic()
        async with s.post(ENGINE + "/prompt", json={"prompt": wf, "client_id": CLIENT_ID}) as r:
            data = await r.json()
            if r.status != 200 or "prompt_id" not in data:
                return web.json_response({"error": data.get("error", data), "node_errors": data.get("node_errors")}, status=400)
    job_id = uuid.uuid4().hex[:10]
    diagnostics.record("generation_submitted")
    JOBS[job_id] = {"prompt_id": data["prompt_id"], "events": [], "done": False, "result": None, "seed": seed, "started_at": started_at}
    asyncio.create_task(watch(job_id, data["prompt_id"]))
    asyncio.create_task(watch_history(job_id, data["prompt_id"]))
    return web.json_response({"job": job_id, "seed": seed})


async def watch_history(job_id, prompt_id):
    """Recupera l'esito anche se un evento websocket arriva prima della connessione."""
    job = JOBS[job_id]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        while not job["done"]:
            try:
                async with s.get(f"{ENGINE}/history/{prompt_id}") as response:
                    history = (await response.json()).get(prompt_id)
                if history and history.get("status", {}).get("completed"):
                    diagnostics.record("generation_completed")
                    for output in history.get("outputs", {}).values():
                        if output.get("gifs"):
                            g = output["gifs"][-1]
                            job["result"] = {"filename": g["filename"], "subfolder": g.get("subfolder", ""), "type": g.get("type", "output")}
                    finish_timing(job)
                    job["done"] = True
                    return
                if history and history.get("status", {}).get("status_str") == "error":
                    diagnostics.record("generation_error")
                    job["events"].append({"k": "error", "msg": "Il motore ha segnalato un errore; controlla il log di ComfyUI."})
                    job["done"] = True
                    return
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(2)


async def h_events(request):
    job = JOBS.get(request.match_info["job"])
    if not job:
        return web.json_response({"error": "job sconosciuto"}, status=404)
    since = int(request.query.get("since", 0))
    return web.json_response({"events": job["events"][since:], "next": len(job["events"]), "done": job["done"], "result": job["result"], "elapsed_seconds": job.get("elapsed_seconds")})


async def h_cancel(request):
    body = await request.json()
    job = JOBS.get(body.get("job"))
    if not job or job["done"]:
        return web.json_response({"error": "Generazione non attiva"}, status=404)
    async with aiohttp.ClientSession() as s:
        async with s.get(ENGINE + "/queue") as r:
            queue = await r.json()
        prompt_id = job["prompt_id"]
        pending = any(len(item) > 1 and item[1] == prompt_id for item in queue.get("queue_pending", []))
        running = any(len(item) > 1 and item[1] == prompt_id for item in queue.get("queue_running", []))
        if pending:
            async with s.post(ENGINE + "/queue", json={"delete": [prompt_id]}) as r:
                if r.status >= 400:
                    raise web.HTTPBadGateway(text="Impossibile rimuovere il lavoro dalla coda")
            job["events"].append({"k": "error", "msg": "Generazione annullata in coda"})
            job["done"] = True
        elif running:
            async with s.post(ENGINE + "/interrupt", json={"prompt_id": prompt_id}) as r:
                if r.status >= 400:
                    raise web.HTTPBadGateway(text="Impossibile arrestare la generazione")
        else:
            return web.json_response({"error": "Il lavoro non è più in coda"}, status=409)
    diagnostics.record("generation_cancelled")
    return web.json_response({"ok": True})


async def h_debug_preview(request):
    health = await h_health(request)
    status = json.loads(health.text)
    version = updater.local_version().get("sha", "")[:12]
    body = diagnostics.report(version, status["engine"], status["missing_nodes"])
    report_id = uuid.uuid4().hex
    REPORTS.clear()
    REPORTS[report_id] = body
    return web.json_response({"id": report_id, "body": body})


async def h_debug_send(request):
    origin = request.headers.get("Origin")
    if origin and origin != f"http://{request.host}":
        raise web.HTTPForbidden()
    data = await request.json()
    body = REPORTS.pop(data.get("id"), None)
    if body is None:
        return web.json_response({"error": "Anteprima scaduta: prepara un nuovo report."}, status=400)
    try:
        url = await asyncio.get_running_loop().run_in_executor(None, diagnostics.send, body, CFG.get("debug_token"))
        return web.json_response({"url": url})
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def h_debug_token(request):
    origin = request.headers.get("Origin")
    if origin and origin != f"http://{request.host}":
        raise web.HTTPForbidden()
    data = await request.json()
    token = data.get("token", "").strip()
    if not token or len(token) > 1024 or any(c in token for c in "\r\n"):
        return web.json_response({"error": "Token non valido"}, status=400)
    CFG["debug_token"] = token
    config_path = ROOT / "config.json"
    config_path.write_text(json.dumps(CFG, ensure_ascii=False, indent=2), encoding="utf-8")
    return web.json_response({"ok": True})


async def h_video(request):
    fn, sub = request.query["filename"], request.query.get("subfolder", "")
    if ".." in fn or ".." in sub or Path(fn).name != fn or Path(sub).is_absolute():
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
    form.add_field("overwrite", "false")
    async with aiohttp.ClientSession() as s:
        async with s.post(ENGINE + "/upload/image", data=form) as r:
            j = await r.json()
    return web.json_response({"name": (j.get("subfolder") + "/" if j.get("subfolder") else "") + j["name"]})


async def h_history(request):
    items = []
    d = OUT_DIR / "KuraStudio"
    if d.is_dir():
        for p in sorted(d.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
            if not p.name.endswith("-audio.mp4") and p.with_name(p.stem + "-audio.mp4").exists():
                continue
            items.append({"filename": p.name, "subfolder": "KuraStudio", "mtime": int(p.stat().st_mtime), "mb": round(p.stat().st_size / 1e6, 1), "elapsed_seconds": read_timing(p)})
            if len(items) >= 100:
                break
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
    web.get("/", h_index), web.get("/api/reference/{name}", h_reference), web.get("/api/config", h_config), web.post("/api/duration", h_duration), web.get("/api/health", h_health),
    web.post("/api/generate", h_generate), web.get("/api/events/{job}", h_events), web.post("/api/cancel", h_cancel),
    web.get("/api/video", h_video), web.post("/api/upload", h_upload), web.get("/api/history", h_history),
    web.get("/api/update/check", h_upd_check), web.post("/api/update/apply", h_upd_apply),
    web.get("/api/debug/preview", h_debug_preview), web.post("/api/debug/send", h_debug_send),
    web.post("/api/debug/token", h_debug_token),
])

if __name__ == "__main__":
    print(f"Kura Studio {APP_VERSION} su http://127.0.0.1:{PORT}  (motore: {ENGINE})")
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)
