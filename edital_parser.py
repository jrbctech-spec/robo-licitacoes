"""
Módulo: edital_parser.py
Baixa o Termo de Referência (TR) e extrai requisitos cruciais usando IA.
"""

import os
import io
import json
import logging
import requests
import google.generativeai as genai

try:
    import PyPDF2
except ImportError:
    logging.warning("PyPDF2 não instalado. Execute: pip install PyPDF2")

logging.basicConfig(level=logging.INFO, format="%(message)s")

# Configurar API Key do Gemini (esperada como variável de ambiente ou injetada)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "SUA_API_KEY_AQUI")
if GEMINI_API_KEY and GEMINI_API_KEY != "SUA_API_KEY_AQUI":
    genai.configure(api_key=GEMINI_API_KEY)

def baixar_pdf_pncp(url_arquivo: str) -> io.BytesIO:
    """Baixa o PDF do edital/TR do portal PNCP."""
    headers = {
        "User-Agent": "JRBC-Tech-Licitacoes/1.0 (contato@jrbctech.com.br)"
    }
    res = requests.get(url_arquivo, headers=headers, timeout=30)
    res.raise_for_status()
    return io.BytesIO(res.content)

def extrair_texto_pdf(pdf_stream: io.BytesIO) -> str:
    """Extrai as primeiras páginas do PDF onde normalmente estão os requisitos principais."""
    try:
        reader = PyPDF2.PdfReader(pdf_stream)
        texto_completo = ""
        # Limitar a análise às primeiras 15 páginas para focar no Termo de Referência e economizar tokens
        for i in range(min(15, len(reader.pages))):
            pagina = reader.pages[i]
            texto_extraido = pagina.extract_text()
            if texto_extraido:
                texto_completo += texto_extraido + "\n"
        return texto_completo
    except Exception as e:
        logging.error(f"Erro ao extrair texto do PDF: {e}")
        return ""

def analisar_tr_com_llm(texto_edital: str) -> dict:
    """
    Usa a API do Gemini para responder às 3 perguntas cruciais de viabilidade técnica.
    """
    if not texto_edital.strip():
        return {"erro": "Texto do edital vazio ou PDF não pesquisável (scaneado)."}
        
    prompt = f"""
    Você é um analista especialista em licitações públicas de TI no Brasil.
    Leia os trechos extraídos do Termo de Referência (TR) abaixo e responda APENAS às seguintes três perguntas em formato JSON estrito:
    1. atendimento: O escopo exige presença "local" (visita técnica) ou pode ser 100% "remoto"?
    2. cidade: Qual a cidade e estado exato de prestação do serviço/entrega? (Se não for especificado ou for nacional, responda "Nao especificado")
    3. pecas_inclusas: O contrato exige fornecimento de "pecas e mao de obra" ou "apenas mao de obra"?

    TEXTO DO EDITAL/TR:
    {texto_edital[:35000]}  # Limite para contexto do prompt
    
    O JSON de saída deve conter EXATAMENTE e APENAS as chaves: "atendimento", "cidade", "pecas_inclusas".
    Não inclua blocos markdown como ```json, retorne apenas o texto cru.
    """
    
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        
        # Processamento simples para extrair o JSON puro caso o modelo retorne com blocos
        texto_resposta = response.text.replace('```json', '').replace('```', '').strip()
        resultado = json.loads(texto_resposta)
        return resultado
    except Exception as e:
        logging.error(f"Falha na inferência da IA: {e}")
        return {"erro": str(e)}

def realizar_leitura_tr(url_pdf: str) -> dict:
    """Fluxo completo da Etapa 2: Baixa o TR e roda o LLM para triagem."""
    try:
        logging.info("📥 Baixando Termo de Referência...")
        pdf_stream = baixar_pdf_pncp(url_pdf)
        
        logging.info("📄 Lendo conteúdo do PDF...")
        texto = extrair_texto_pdf(pdf_stream)
        
        logging.info("🤖 Analisando requisitos com Inteligência Artificial (Gemini)...")
        return analisar_tr_com_llm(texto)
    except Exception as e:
        logging.error(f"❌ Erro na Etapa 2 de Leitura do TR: {e}")
        return {"erro": str(e)}
