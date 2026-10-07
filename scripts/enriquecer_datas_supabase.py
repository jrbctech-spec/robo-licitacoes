"""
scripts/enriquecer_datas_supabase.py
Varre as licitações cadastradas no Supabase que estão sem data de abertura,
data de encerramento ou valor estimado, busca os dados oficiais no PNCP e atualiza o banco.
"""
import os
import re
import time
import logging
import httpx
from dotenv import load_dotenv
from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("EnriquecedorDatas")

def main():
    load_dotenv()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        logger.error("Credenciais do Supabase não encontradas no .env")
        return

    sb = create_client(url, key)

    logger.info("Buscando certames que precisam de datas no Supabase...")
    # Pega os certames ordenados por atualizado_em desc
    res = sb.table("licitacoes").select("id,numero_pregao,link_edital,numero_controle_pncp,data_abertura,data_encerramento,valor_estimado").order("atualizado_em", desc=True).limit(300).execute()
    
    certames = res.data or []
    logger.info(f"Total de certames encontrados: {len(certames)}")

    pendentes = [
        c for c in certames 
        if not c.get("data_encerramento") or not c.get("data_abertura") or float(c.get("valor_estimado") or 0.0) == 0.0
    ]
    logger.info(f"Certames que necessitam enriquecimento: {len(pendentes)}")

    client = httpx.Client(
        follow_redirects=True, 
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
        timeout=15.0
    )

    atualizados = 0
    erros = 0

    for idx, c in enumerate(pendentes, 1):
        lic_id = c["id"]
        link = c.get("link_edital") or ""
        cnpj, ano, seq = None, None, None

        m = re.search(r"editais/(\d+)/(\d+)/(\d+)", link)
        if m:
            cnpj, ano, seq = m.groups()
            seq = str(int(seq))
        else:
            num_ctrl = c.get("numero_controle_pncp") or ""
            m2 = re.search(r"(\d+)-(\d+)-(\d+)/(\d+)", num_ctrl)
            if m2:
                cnpj, _, seq, ano = m2.groups()
                seq = str(int(seq))

        if not (cnpj and ano and seq):
            continue

        api_url = f"https://pncp.gov.br/api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{seq}"
        sucesso = False

        for tentativa in range(4):
            try:
                resp = client.get(api_url)
                if resp.status_code == 200:
                    d = resp.json()
                    abertura = d.get("dataAberturaProposta") or d.get("dataPublicacaoPncp")
                    encerramento = d.get("dataEncerramentoProposta") or d.get("dataAberturaProposta")
                    valor_pncp = float(d.get("valorTotalEstimado") or 0.0)

                    updates = {}
                    if abertura and not c.get("data_abertura"):
                        updates["data_abertura"] = abertura
                    if encerramento and not c.get("data_encerramento"):
                        updates["data_encerramento"] = encerramento
                    if valor_pncp > 0 and float(c.get("valor_estimado") or 0.0) == 0.0:
                        updates["valor_estimado"] = valor_pncp

                    if updates:
                        sb.table("licitacoes").update(updates).eq("id", lic_id).execute()
                        atualizados += 1
                        logger.info(f"[{idx}/{len(pendentes)}] Atualizado {seq}/{ano}: Abertura={abertura} | Encerramento={encerramento} | Valor={valor_pncp}")
                    else:
                        logger.debug(f"[{idx}/{len(pendentes)}] Sem novidades para {seq}/{ano}")
                    
                    sucesso = True
                    break
                elif resp.status_code == 429:
                    logger.warning(f"Rate limit 429 no PNCP. Aguardando 5s...")
                    time.sleep(5.0)
                else:
                    logger.warning(f"[{idx}/{len(pendentes)}] PNCP {resp.status_code} para {cnpj}/{ano}/{seq}")
                    break
            except Exception as e:
                time.sleep(2.0)

        if not sucesso and resp.status_code != 200:
            erros += 1

        time.sleep(1.2)

    logger.info(f"=== Enriquecimento Concluído: {atualizados} atualizados, {erros} com erro ===")

if __name__ == "__main__":
    main()
