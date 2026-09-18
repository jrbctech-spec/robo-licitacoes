#!/usr/bin/env python3
"""Baixa o OpenAPI spec da PNCP v3 com retries."""
import httpx
import json
import sys
import time

URL = "https://pncp.gov.br/api/consulta/v3/api-docs"
OUT = "/tmp/pncp_v3_spec.json"

headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

for attempt in range(5):
    try:
        with httpx.Client(timeout=180.0, headers=headers, follow_redirects=True) as client:
            resp = client.get(URL)
        if resp.status_code == 200:
            data = resp.json()
            with open(OUT, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"OK: salvo {len(data.get('paths', {}))} paths em {OUT}")
            sys.exit(0)
        else:
            print(f"T{attempt+1}: HTTP {resp.status_code} — tentando novamente...")
    except Exception as e:
        print(f"T{attempt+1}: {e} — tentando novamente...")
    time.sleep(5 * (attempt + 1))

print("FALHOU após 5 tentativas")
sys.exit(1)
