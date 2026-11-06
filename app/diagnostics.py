"""Report tecnico locale, senza prompt, percorsi o messaggi di errore arbitrari."""
import platform
import re
import os
import shutil
import subprocess
import tempfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

EVENTS = deque(maxlen=100)
ALLOWED = {'generation_submitted', 'generation_completed', 'generation_error', 'generation_cancelled', 'engine_unavailable'}


def record(event, error_type=None, node_type=None):
    if event in ALLOWED:
        safe = lambda x: x if isinstance(x, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]{0,79}', x) else ''
        EVENTS.append((datetime.now(timezone.utc).isoformat(timespec='seconds'), event, safe(error_type), safe(node_type)))


def report(version, engine, missing_nodes):
    lines = ['# Diagnostica Kura Studio', f'Versione: {version or "sconosciuta"}',
             f'Sistema: {platform.system()} {platform.release()}',
             f'Motore raggiungibile: {"sì" if engine else "no"}',
             f'Nodi mancanti: {len(missing_nodes)}', '', 'Eventi tecnici recenti:']
    lines.extend(f'- {time}: {event}' + (f' · errore: {error}' if error else '') + (f' · nodo: {node}' if node else '') for time, event, error, node in EVENTS)
    if not EVENTS:
        lines.append('- Nessun evento registrato nella sessione corrente.')
    lines.extend(['', 'Il report non contiene prompt, immagini, percorsi, nomi dei file o credenziali.'])
    return '\n'.join(lines)


def send(body, token=None):
    gh = shutil.which('gh')
    if not gh:
        raise RuntimeError('GitHub CLI non trovato. Installa gh ed esegui gh auth login sul PC.')
    with tempfile.TemporaryDirectory(prefix='kura_debug_') as tmp:
        path = Path(tmp) / 'report.md'
        path.write_text(body, encoding='utf-8')
        try:
            env = os.environ.copy()
            if token:
                env['GH_TOKEN'] = token
            result = subprocess.run([gh, 'issue', 'create', '--repo', 'azza0101/kura-studio',
                                     '--title', 'Kura Studio V2 — segnalazione diagnostica',
                                     '--body-file', str(path)], capture_output=True, text=True, timeout=60,
                                    env=env,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            raise RuntimeError('Invio scaduto. Controlla le issue prima di riprovare.')
        if result.returncode:
            raise RuntimeError('Invio non riuscito. Controlla la connessione e gh auth status.')
        return result.stdout.strip()
