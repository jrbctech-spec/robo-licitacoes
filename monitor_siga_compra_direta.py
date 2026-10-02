"""
Módulo: monitor_siga_compra_direta.py
Finalidade: Monitoramento contínuo das compras diretas abertas no SIGA-TO
usando sessão autenticada com Playwright.
JRBC TECH - Palmas/TO
"""

import os
import re
import json
import logging
from datetime import datetime
from pathlib import Path
import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from supabase_client import inserir_licitacao

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SigaCompraDireta")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PERFIL_SIGA = Path(__file__).resolve().parent / "browser_profile_siga"
CACHE_FILE = Path(__file__).resolve().parent / "data" / "siga_compras_notificadas.json"
URL_BASE_FORNECEDOR = "https://www.siga.to.gov.br/compra-direta/#/fornecedor"

PALAVRAS_CHAVE_TI = [
    "informatica", "computador", "notebook", "suporte", "manutencao",
    "cabeamento", "rede", "toner", "cartucho", "nobreak", "cftv",
    "camera", "estacao de trabalho", "help desk", "switch", "servidor"
]

def carregar_notificados() -> set:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not CACHE_FILE.exists():
        return set()
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f).get("notificados", []))
    except Exception:
        return set()

def salvar_notificados(notificados: set):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "notificados": list(notificados),
                "atualizado_em": datetime.now().isoformat()
            }, f, indent=2)
    except Exception as e:
        logger.error(f"Erro cache: {e}")

def enviar_alerta_telegram_siga(compra: dict) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    id_compra = compra.get("id_compra", "S/N")
    orgao = compra.get("orgao", "Governo do Estado do Tocantins")
    objeto = compra.get("objeto", "Sem descrição informada")
    prazo = compra.get("prazo_encerramento", "Verificar no portal")
    link = compra.get("link_direto", f"https://www.siga.to.gov.br/compra-direta/#/fornecedor/detalhes/{id_compra}")

    texto = (
        "🌲 **COMPRA DIRETA ABERTA - SIGA TOCANTINS**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏛 **Órgão:** {orgao.upper()}\n"
        f"📋 **Compra Direta nº:** {id_compra}\n"
        "📍 **Local de Entrega/Execução:** Tocantins (Palmas/Região)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📝 **OBJETO:**\n"
        f"*{objeto[:400]}...*\n\n"
        f"⏰ **Prazo Limite Envio:** {prazo}\n\n"
        "🏷 #SIGA_TO #TOCANTINS #PALMAS #COMPRA_DIRETA #TI\n"
    )

    url_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    body = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "Markdown",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "🚀 Acessar e Cadastrar Proposta no SIGA", "url": link}]
            ]
        }
    }
    try:
        r = requests.post(url_api, json=body, timeout=12)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Erro Telegram SIGA: {e}")
        return False

def extrair_dados_linha(el) -> dict:
    textos_colunas = el.locator("td, mat-cell").all_inner_texts()
    texto_completo = " | ".join(textos_colunas) if textos_colunas else el.inner_text()
    
    # id_compra: Pega número com 6 ou 7 dígitos
    match_id = re.search(r'\b(\d{6,8})\b', texto_completo)
    id_compra = match_id.group(1) if match_id else None
    
    if not id_compra:
        return None
        
    # prazo_encerramento
    match_prazo = re.search(r'\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}', texto_completo)
    prazo = match_prazo.group(0) if match_prazo else "A verificar"
    
    # extrair orgão e objeto (heurística com base em quebra de linha ou colunas)
    orgao = "Órgão do Governo (TO)"
    objeto = texto_completo
    
    if textos_colunas and len(textos_colunas) >= 3:
        # Geralmente: Col 1 = Número, Col 2 = Órgão, Col 3 = Objeto
        orgao_possivel = textos_colunas[1].strip()
        if len(orgao_possivel) > 2:
            orgao = orgao_possivel
        objeto = textos_colunas[2].strip() if len(textos_colunas) > 2 else texto_completo
    else:
        linhas = [linha.strip() for linha in el.inner_text().split('\n') if linha.strip()]
        if len(linhas) > 1:
            orgao = linhas[0]
            objeto = " ".join(linhas[1:])
            
    link_direto = f"https://www.siga.to.gov.br/compra-direta/#/fornecedor/detalhes/{id_compra}"
    
    # Parse data_iso para Supabase
    data_iso = None
    if match_prazo:
        try:
            dt = datetime.strptime(match_prazo.group(0), "%d/%m/%Y %H:%M")
            data_iso = dt.isoformat()
        except Exception:
            pass

    return {
        "id_compra": id_compra,
        "orgao": orgao,
        "objeto": objeto,
        "prazo_encerramento": prazo,
        "link_direto": link_direto,
        "data_iso": data_iso
    }

def executar_varredura_siga():
    logger.info("=== INICIANDO MONITORAMENTO COMPRA DIRETA SIGA-TO ===")
    notificados = carregar_notificados()
    PERFIL_SIGA.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PERFIL_SIGA),
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = context.new_page()

        try:
            logger.info(f"Navegando para: {URL_BASE_FORNECEDOR}")
            page.goto(URL_BASE_FORNECEDOR, wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(5000)

            if "login" in page.url or "selecionar-dominio" in page.url:
                logger.warning("⚠️ O SIGA-TO pediu login. Execute login_siga.py para salvar sua sessão.")
                context.close()
                return

            elementos = page.locator("tr, .card-compra, mat-row").all()
            logger.info(f"Linhas carregadas no painel do fornecedor: {len(elementos)}")

            for el in elementos:
                texto_el = el.inner_text()
                if any(termo in texto_el.lower() for termo in PALAVRAS_CHAVE_TI):
                    dados = extrair_dados_linha(el)
                    if not dados:
                        continue
                        
                    id_compra = dados["id_compra"]

                    if id_compra not in notificados:
                        # Salvar no Supabase
                        dados_licitacao = {
                            "numero_uasg": "SIGA-TO",
                            "numero_pregao": id_compra,
                            "orgao": dados["orgao"],
                            "objeto": dados["objeto"][:2000],
                            "modalidade": "Compra Direta Estadual (SIGA-TO)",
                            "uf": "TO",
                            "municipio": "Palmas/TO",
                            "data_fim_proposta": dados["data_iso"],
                            "link_sistema": dados["link_direto"],
                            "status_analise": "pendente"
                        }
                        
                        try:
                            inserir_licitacao(dados_licitacao)
                            logger.info(f"💾 Compra SIGA {id_compra} salva no Supabase.")
                        except Exception as e:
                            logger.error(f"Erro ao salvar no Supabase: {e}")

                        if enviar_alerta_telegram_siga(dados):
                            logger.info(f"🔔 Nova Compra Direta SIGA notificada no Telegram: {id_compra}")
                            notificados.add(id_compra)
                            salvar_notificados(notificados)

        except Exception as e:
            logger.error(f"Erro na varredura do SIGA-TO: {e}")
        finally:
            context.close()

    logger.info("=== VARREDURA SIGA-TO FINALIZADA ===")

if __name__ == "__main__":
    executar_varredura_siga()
