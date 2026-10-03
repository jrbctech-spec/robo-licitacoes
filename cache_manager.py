import os
import json
from pathlib import Path
from typing import Any, Dict

CACHE_FILE = Path(os.path.expanduser("~")) / ".robo_licitacoes_cache.json"

def carregar_cache() -> Dict[str, Any]:
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def salvar_cache(chave: str, dados: Any) -> None:
    cache = carregar_cache()
    cache[chave] = dados
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass
