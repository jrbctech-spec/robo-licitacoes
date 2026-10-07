"""
Módulo: copiloto_gemini.py
Assessor Tático de Disputa de Licitações com Gemini.
JRBC TECH - CNPJ: 65.488.003/0001-61 - Palmas/TO
"""

import os
import requests
import json
import logging
from dotenv import load_dotenv

logger = logging.getLogger("CopilotoGemini")

PROMPT_SISTEMA = """
Você é o Copiloto Tático de Licitações da empresa JRBC TECH (CNPJ: 65.488.003/0001-61), sediada em Palmas/TO.
Seu usuário e operador é o Jakson. Você o orienta durante a sessão pública de lances ao vivo no Compras.gov.br (Lei 14.133/2021).

Diretrizes de Atuação:
1. Respostas ultra concisas e diretas (máximo 2 a 3 frases). O tempo no pregão é contado em segundos.
2. Jamais recomende lances abaixo do Stop-Loss.
3. Se um concorrente der lance menor que 50% do valor de referência, alerte sobre proposta INEXEQUÍVEL (Art. 59 §4º) e recomende não cobrir para pedir desclassificação.
4. Oriente o tempo ideal de cobrir lances (segurar até os últimos segundos da prorrogação de 2 minutos).
5. Se houver mensagem do pregoeiro, alerte sobre prazos urgentes de proposta readequada e anexos.
"""

MODELOS_GEMINI = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]


def _analise_tatica_local(estado_pregao: dict, mensagem_operador: str) -> str:
    """Fallback tático local caso o endpoint do Gemini esteja temporariamente indisponível."""
    try:
        ref_str = str(estado_pregao.get("valor_referencia", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip()
        ref = float(ref_str) if ref_str else 0.0
    except Exception:
        ref = 0.0

    try:
        lider_str = str(estado_pregao.get("melhor_lance", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip()
        lider = float(lider_str) if lider_str and lider_str != "N/D" else 0.0
    except Exception:
        lider = 0.0

    try:
        stop_str = str(estado_pregao.get("stop_loss", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip()
        stop = float(stop_str) if stop_str and stop_str != "N/D" else 0.0
    except Exception:
        stop = 0.0

    # Inexequibilidade (Art. 59 §4º da Lei 14.133/2021)
    if ref > 0 and lider > 0 and lider < (ref * 0.5):
        return (
            "🚨 ALERTA TÁTICO: O líder deu lance inferior a 50% do valor de referência "
            f"(R$ {lider:,.2f} vs Ref R$ {ref:,.2f}). Presunção de INEXEQUÍVEL segundo Art. 59 §4º da Lei 14.133! "
            "NÃO cubra esse lance. Deixe fechar e protocole pedido de diligência/desclassificação."
        )

    if stop > 0 and lider > 0 and lider <= stop:
        return (
            f"🛑 STOP-LOSS ATINGIDO: O menor lance atual (R$ {lider:,.2f}) está no limite do nosso piso de segurança (R$ {stop:,.2f}). "
            "Não baixe mais para não comprometer a margem de lucro da JRBC TECH."
        )

    if "inexequ" in (mensagem_operador or "").lower():
        return (
            "⚖️ Conforme Art. 59, § 4º da Lei 14.133/2021, lances inferiores a 50% do valor orçado pela Administração "
            "têm presunção relativa de inexequibilidade. Exija a comprovação de custos via planilha detalhada no chat."
        )

    return (
        f"🎯 ORIENTAÇÃO TÁTICA: Mantenha a disciplina de lances. "
        f"Nosso Stop-Loss é R$ {stop:,.2f}. Aguarde os minutos finais da prorrogação para cobrir o menor lance se viável."
    )


def consultar_copiloto(estado_pregao: dict, mensagem_operador: str = "") -> str:
    """
    Envia o snapshot atual da disputa e a dúvida do operador para o Gemini com fallback resiliente.
    """
    load_dotenv(override=True)
    chave = os.getenv("GEMINI_API_KEY")
    if not chave:
        return "⚠️ Chave GEMINI_API_KEY não configurada no .env."

    contexto = f"""
SITUAÇÃO DO PREGÃO NESTE SEGUNDO:
- Órgão: {estado_pregao.get('orgao', 'Órgão Público')}
- Item / Lote: {estado_pregao.get('item', 'Item 1')}
- Descrição: {estado_pregao.get('descricao', 'N/D')}
- Quantidade / Unidade: {estado_pregao.get('quantidade', '1')} {estado_pregao.get('unidade', 'UN')}
- Valor de Referência: R$ {estado_pregao.get('valor_referencia', '0,00')}
- Nosso Último Lance: R$ {estado_pregao.get('meu_lance', 'N/D')}
- Líder de Mercado Atual: R$ {estado_pregao.get('melhor_lance', 'N/D')}
- Nosso Piso Inviolável (Stop-Loss): R$ {estado_pregao.get('stop_loss', 'N/D')}
- Posição Atual: {estado_pregao.get('posicao', 'N/D')}º Lugar
- Cronômetro / Fase: {estado_pregao.get('tempo_status', 'Disputa Aberta')}
- Último aviso no Chat do Pregoeiro: {estado_pregao.get('chat_pregoeiro', 'Nenhum')}

MENSAGEM DO JAKSON:
"{mensagem_operador or 'Qual a melhor jogada tática neste momento?'}"
"""

    payload = {
        "system_instruction": {"parts": [{"text": PROMPT_SISTEMA}]},
        "contents": [{"parts": [{"text": contexto}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 250}
    }
    headers = {"Content-Type": "application/json"}

    # Tentativa em cadeia de modelos para tolerância máxima a picos de demanda
    erros_acumulados = []
    for modelo in MODELOS_GEMINI:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={chave}"
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=8)
            if res.status_code == 200:
                dados = res.json()
                cands = dados.get("candidates", [])
                if cands and "content" in cands[0] and "parts" in cands[0]["content"]:
                    return cands[0]["content"]["parts"][0]["text"].strip()
            elif res.status_code in (503, 429, 404):
                erros_acumulados.append(f"{modelo} ({res.status_code})")
                continue
            else:
                erros_acumulados.append(f"{modelo} ({res.status_code})")
        except requests.RequestException as e:
            erros_acumulados.append(f"{modelo} (timeout/rede)")
            continue

    # Se todos os modelos da nuvem falharem por cota/503, ativa inteligência tática local
    analise_local = _analise_tatica_local(estado_pregao, mensagem_operador)
    return f"🤖 Copiloto (Tático Direto):\n{analise_local}"
