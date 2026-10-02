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

load_dotenv()
logger = logging.getLogger("CopilotoGemini")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

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

def consultar_copiloto(estado_pregao: dict, mensagem_operador: str = "") -> str:
    """
    Envia o snapshot atual da disputa e a dúvida do operador para o Gemini.
    """
    if not GEMINI_API_KEY:
        return "⚠️ Chave GEMINI_API_KEY não configurada no .env."

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}

    contexto = f"""
SITUAÇÃO DO PREGÃO NESTE SEGUNDO:
- Órgão: {estado_pregao.get('orgao', 'Órgão Público')}
- Item / Lote: {estado_pregao.get('item', 'Item 1')}
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

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=12)
        if res.status_code == 200:
            dados = res.json()
            return dados["candidates"][0]["content"]["parts"][0]["text"].strip()
        else:
            return f"Erro Gemini ({res.status_code}): {res.text[:120]}"
    except Exception as e:
        return f"Falha de conexão com o Gemini: {e}"
