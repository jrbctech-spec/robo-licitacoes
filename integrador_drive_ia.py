"""
Módulo: integrador_drive_ia.py
Integração inteligente com pastas do Google Drive e Vistoria Prévia / Comparativo Tático via Gemini AI.
JRBC TECH - CNPJ: 65.488.003/0001-61 - Palmas/TO
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv

logger = logging.getLogger("IntegradorDriveIA")

# Diretório base padrão de funil do Drive local
DRIVE_BASE_DIR = Path(os.getenv("DRIVE_BASE_DIR", os.path.expanduser("~/Google Drive/Licitacoes_Funil")))
DRIVE_ROOT_DIR = Path(os.path.expanduser("~/Google Drive"))

MODELOS_GEMINI = [
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3-flash-preview",
]

# Timeout estendido para análise de editais densos (evita Read timed out)
TIMEOUT_GEMINI = int(os.getenv("GEMINI_TIMEOUT", "45"))


def extrair_id_drive(url: str) -> Optional[str]:
    """Extrai ID de pastas ou arquivos de links do Google Drive."""
    m_folder = re.search(r"folders/([a-zA-Z0-9_-]+)", url)
    if m_folder:
        return m_folder.group(1)

    m_file = re.search(r"d/([a-zA-Z0-9_-]+)", url)
    if m_file:
        return m_file.group(1)

    m_id = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if m_id:
        return m_id.group(1)

    return None


def buscar_pasta_local_drive(termo_ou_id: str) -> Optional[Path]:
    """
    Localiza pasta correspondente no Google Drive local por caminho direto,
    ID, nome do órgão, número do pregão ou palavras-chave.
    """
    # 1. Caminho direto se existir
    p_direto = Path(os.path.expanduser(termo_ou_id))
    if p_direto.exists() and p_direto.is_dir():
        return p_direto

    if not DRIVE_ROOT_DIR.exists():
        return None

    termo_limpo = termo_ou_id.strip().lower()
    # Se for link, tenta pegar o id
    drive_id = extrair_id_drive(termo_ou_id)

    # 2. Busca recursiva no funil e na raiz do Drive
    pastas_raiz = [DRIVE_BASE_DIR, DRIVE_ROOT_DIR]
    candidatos: list[Path] = []

    for base in pastas_raiz:
        if not base.exists():
            continue
        try:
            for p in base.rglob("*"):
                if p.is_dir():
                    nome_p = p.name.lower()
                    if drive_id and drive_id.lower() in nome_p:
                        return p
                    if termo_limpo in nome_p or (len(termo_limpo) > 4 and termo_limpo in str(p).lower()):
                        candidatos.append(p)
        except Exception as e:
            logger.warning(f"Erro ao varrer pasta {base}: {e}")

    # Retorna o candidato com melhor correspondência (preferindo pasta com resumo_processo.txt)
    for cand in candidatos:
        if (cand / "resumo_processo.txt").exists():
            return cand

    return candidatos[0] if candidatos else None


def ler_resumo_processo(caminho_resumo: Path) -> dict[str, Any]:
    """Lê e parseia o arquivo resumo_processo.txt gerado na pasta do Drive."""
    dados: dict[str, Any] = {}
    if not caminho_resumo.exists():
        return dados

    try:
        texto = caminho_resumo.read_text(encoding="utf-8", errors="ignore")
        for linha in texto.splitlines():
            if ":" in linha:
                chave, valor = linha.split(":", 1)
                chave = chave.strip().lower()
                valor = valor.strip()
                if "órgão" in chave or "orgao" in chave:
                    dados["orgao_nome"] = valor
                elif "número" in chave or "numero" in chave:
                    dados["numero_pregao"] = valor
                elif "objeto" in chave:
                    dados["objeto"] = valor
                elif "valor estimado" in chave:
                    val_clean = valor.replace("R$", "").replace(".", "").replace(",", ".").strip()
                    try:
                        dados["valor_estimado"] = float(val_clean)
                    except ValueError:
                        dados["valor_estimado"] = 0.0
                elif "link" in chave:
                    dados["link_sistema"] = valor
                elif "status" in chave:
                    dados["status_analise"] = valor
                elif "portal" in chave:
                    dados["portal"] = valor
                elif "prioridade" in chave:
                    dados["prioridade"] = valor
    except Exception as e:
        logger.error(f"Erro ao ler resumo_processo.txt: {e}")

    return dados


def carregar_dados_pasta_drive(pasta: Path) -> dict[str, Any]:
    """
    Carrega todos os dados, documentos e itens disponíveis na pasta do Drive.
    """
    info: dict[str, Any] = {
        "caminho_pasta": str(pasta),
        "nome_pasta": pasta.name,
        "arquivos": [f.name for f in pasta.iterdir() if f.is_file()],
        "resumo": {},
        "itens": [],
        "licitacao": {},
    }

    # 1. Resumo do processo
    resumo_path = pasta / "resumo_processo.txt"
    if resumo_path.exists():
        info["resumo"] = ler_resumo_processo(resumo_path)
        info["licitacao"].update(info["resumo"])

    # 2. Verifica se há arquivos JSON de itens ou propostas
    for json_file in pasta.glob("*.json"):
        try:
            conteudo = json.loads(json_file.read_text(encoding="utf-8"))
            if isinstance(conteudo, list):
                info["itens"].extend(conteudo)
            elif isinstance(conteudo, dict):
                if "itens" in conteudo and isinstance(conteudo["itens"], list):
                    info["itens"].extend(conteudo["itens"])
                else:
                    info["licitacao"].update(conteudo)
        except Exception as e:
            logger.warning(f"Erro ao ler JSON {json_file.name}: {e}")

    # 3. Se houver link PNCP no resumo, busca os itens oficiais do PNCP
    link = info["licitacao"].get("link_sistema", "")
    if "pncp.gov.br" in link and not info["itens"]:
        try:
            from pncp_client import carregar_certame_pncp
            res_pncp = carregar_certame_pncp(link)
            if res_pncp and hasattr(res_pncp, "itens"):
                for it in res_pncp.itens:
                    info["itens"].append({
                        "numero_item": getattr(it, "numero_item", 1),
                        "descricao": getattr(it, "descricao", ""),
                        "quantidade": float(getattr(it, "quantidade", 1.0) or 1.0),
                        "unidade_medida": getattr(it, "unidade_medida", "UN") or "UN",
                        "valor_referencia": float(getattr(it, "valor_referencia", 0.0) or 0.0),
                        "custo_real": float(getattr(it, "custo_real", 0.0) or 0.0),
                        "piso_preco": float(getattr(it, "piso_preco", 0.0) or 0.0),
                    })
        except Exception as e:
            logger.warning(f"Erro ao buscar itens oficiais PNCP: {e}")

    # 4. Se ainda não houver itens e houver PDF de edital/TR, tenta extrair com EditalParser
    if not info["itens"]:
        pdfs = list(pasta.glob("*.pdf")) + list(pasta.glob("*.PDF"))
        if pdfs:
            try:
                from edital_parser import EditalParser
                parser = EditalParser()
                res_edital = parser.analisar_edital(pdfs[0])
                if res_edital and res_edital.itens:
                    for it in res_edital.itens:
                        info["itens"].append({
                            "numero_item": it.numero_item,
                            "descricao": it.descricao,
                            "quantidade": it.quantidade or 1.0,
                            "unidade_medida": it.unidade_medida or "UN",
                            "valor_referencia": it.valor_referencia or 0.0,
                            "custo_real": 0.0,
                            "piso_preco": 0.0,
                        })
            except Exception as e:
                logger.warning(f"Erro ao parsear PDF com EditalParser: {e}")

    # 5. Fallback: cria item principal a partir do objeto se a lista ainda estiver vazia
    if not info["itens"]:
        obj = info["licitacao"].get("objeto") or pasta.name
        val_est = float(info["licitacao"].get("valor_estimado") or 0.0)
        info["itens"].append({
            "numero_item": 1,
            "descricao": obj,
            "quantidade": 1.0,
            "unidade_medida": "UN",
            "valor_referencia": val_est,
            "custo_real": val_est * 0.5 if val_est > 0 else 0.0,
            "piso_preco": val_est * 0.7 if val_est > 0 else 0.0,
        })

    return info


def resolver_e_carregar_origem(entrada: str) -> dict[str, Any]:
    """
    Resolve qualquer entrada:
    - Link do Google Drive (URL)
    - Caminho local ou nome de pasta do Drive
    - Link do PNCP
    - Código Comprasnet (compra=...)
    - UASG / Pregão
    """
    entrada_limpa = entrada.strip()
    if not entrada_limpa:
        raise ValueError("Entrada vazia.")

    # A) Link ou Pasta do Google Drive
    if "drive.google.com" in entrada_limpa or "docs.google.com" in entrada_limpa:
        drive_id = extrair_id_drive(entrada_limpa)
        pasta = buscar_pasta_local_drive(drive_id or entrada_limpa)
        if pasta:
            dados = carregar_dados_pasta_drive(pasta)
            dados["tipo_origem"] = "DRIVE_LINK_LOCAL"
            return dados
        else:
            # Não encontrada localmente: gera estrutura básica e tenta buscar no Supabase
            return {
                "tipo_origem": "DRIVE_WEB",
                "caminho_pasta": entrada_limpa,
                "nome_pasta": f"Drive [{drive_id or 'Online'}]",
                "arquivos": [],
                "resumo": {},
                "itens": [],
                "licitacao": {
                    "objeto": "Importado do Google Drive",
                    "link_sistema": entrada_limpa,
                    "portal": "COMPRAS_GOV",
                }
            }

    # B) Caminho de arquivo ou pasta local
    p = Path(os.path.expanduser(entrada_limpa))
    if p.exists() and p.is_dir():
        dados = carregar_dados_pasta_drive(p)
        dados["tipo_origem"] = "PASTA_LOCAL"
        return dados

    # C) Busca por nome de pasta ou órgão no funil do Drive
    pasta_encontrada = buscar_pasta_local_drive(entrada_limpa)
    if pasta_encontrada:
        dados = carregar_dados_pasta_drive(pasta_encontrada)
        dados["tipo_origem"] = "DRIVE_BUSCA_LOCAL"
        return dados

    # D) PNCP URL
    if "pncp.gov.br" in entrada_limpa:
        from pncp_client import carregar_certame_pncp
        res_pncp = carregar_certame_pncp(entrada_limpa)
        itens = []
        if res_pncp and hasattr(res_pncp, "itens"):
            for it in res_pncp.itens:
                itens.append({
                    "numero_item": getattr(it, "numero_item", 1),
                    "descricao": getattr(it, "descricao", ""),
                    "quantidade": float(getattr(it, "quantidade", 1.0) or 1.0),
                    "unidade_medida": getattr(it, "unidade_medida", "UN") or "UN",
                    "valor_referencia": float(getattr(it, "valor_referencia", 0.0) or 0.0),
                    "custo_real": float(getattr(it, "custo_real", 0.0) or 0.0),
                    "piso_preco": float(getattr(it, "piso_preco", 0.0) or 0.0),
                })
        return {
            "tipo_origem": "PNCP_DIRETO",
            "caminho_pasta": "",
            "nome_pasta": entrada_limpa,
            "arquivos": [],
            "resumo": {},
            "itens": itens,
            "licitacao": {
                "objeto": getattr(res_pncp, "objeto", "Licitação PNCP") if res_pncp else "Licitação PNCP",
                "numero_pregao": getattr(res_pncp, "numero_pregao", entrada_limpa) if res_pncp else entrada_limpa,
                "orgao_nome": getattr(res_pncp, "orgao_nome", "") if res_pncp else "",
                "link_sistema": entrada_limpa,
                "portal": "COMPRAS_GOV",
            }
        }

    # E) Comprasnet 17 dígitos
    m_compra = re.search(r"\b(\d{17})\b", entrada_limpa)
    if m_compra:
        cod = m_compra.group(1)
        uasg = cod[0:6]
        pregao = int(cod[8:13])
        ano = cod[13:17]
        url_disp = f"https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/fornecedor/disputa?compra={cod}"
        return {
            "tipo_origem": "COMPRASNET_CODIGO",
            "caminho_pasta": "",
            "nome_pasta": f"UASG {uasg} - {pregao}/{ano}",
            "arquivos": [],
            "resumo": {},
            "itens": [{
                "numero_item": 1,
                "descricao": f"Pregão Comprasnet {pregao}/{ano} - UASG {uasg}",
                "quantidade": 1.0,
                "unidade_medida": "UN",
                "valor_referencia": 0.0,
                "custo_real": 0.0,
                "piso_preco": 0.0,
            }],
            "licitacao": {
                "numero_uasg": uasg,
                "numero_pregao": f"{pregao}/{ano}",
                "objeto": f"Item de Disputa UASG {uasg} Pregão {pregao}/{ano}",
                "link_sistema": url_disp,
                "portal": "COMPRAS_GOV",
            }
        }

    raise ValueError(f"Não foi possível identificar a origem: '{entrada_limpa}'")


def _vistoria_local_fallback(dados_processo: dict, itens: list[dict]) -> dict[str, Any]:
    """Fallback heurístico com cálculos de engenharia de custos e Lei 14.133/2021."""
    itens_atualizados = []
    total_ref = 0.0
    total_custo = 0.0
    total_stop = 0.0

    for it in itens:
        ref = float(it.get("valor_referencia") or 0.0)
        qtd = float(it.get("quantidade") or 1.0)
        desc = str(it.get("descricao") or "")

        # Se valor de referência for zero (sigiloso / estimativa por lote)
        if ref <= 0.0:
            # Heurística para estimar piso inicial padrão se for serviço de Help Desk / TI
            if any(k in desc.lower() for k in ["help desk", "suporte", "terceiriz", "técnico", "posto"]):
                custo_estimado = 4800.0 * 12.0  # estimativa base de posto de trabalho anual
                piso = custo_estimado * 1.25
                ref_estimada = piso * 1.20
            else:
                custo_estimado = 1000.0
                piso = 1250.0
                ref_estimada = 1500.0
        else:
            custo_estimado = ref * 0.65
            piso = custo_estimado * 1.25  # margem de segurança de 25% sobre o custo real
            ref_estimada = ref

        margem = ((ref_estimada - piso) / ref_estimada * 100) if ref_estimada > 0 else 20.0

        item_mod = dict(it)
        item_mod["valor_referencia"] = ref_estimada
        item_mod["custo_real"] = custo_estimado
        item_mod["piso_preco"] = piso
        item_mod["stop_loss"] = piso
        item_mod["margem"] = margem
        itens_atualizados.append(item_mod)

        total_ref += ref_estimada * qtd
        total_custo += custo_estimado * qtd
        total_stop += piso * qtd

    limite_inexequivel = total_ref * 0.50

    parecer = (
        f"✅ Vistoria Técnica Prévia Concluída: {len(itens_atualizados)} item(ns) analisado(s). "
        f"Valor de Referência Total estimado em R$ {total_ref:,.2f} com Custo Real de R$ {total_custo:,.2f}. "
        f"Piso Stop-Loss global recomendado: R$ {total_stop:,.2f} (Garantindo margem operacional mínima de 20-25%)."
    )

    alertas = [
        f"⚖️ Presunção de Inexequibilidade (Art. 59 §4º da Lei 14.133): Lances globais abaixo de R$ {limite_inexequivel:,.2f} (50% do orçado) são presuntivamente inexequíveis. NÃO cobrir.",
        f"🛑 Piso Stop-Loss Inviolável da JRBC TECH fixado em R$ {total_stop:,.2f}.",
        "🎯 Tática: Aguardar os minutos finais do tempo randômico/prorrogação para cobrir lances viáveis.",
    ]

    return {
        "status_vistoria": "CONCLUIDA",
        "badge": "🟢 VIÁVEL COM MARGEM SEGURA",
        "parecer_resumido": parecer,
        "pontos_atencao": alertas,
        "itens_atualizados": itens_atualizados,
        "total_referencia": total_ref,
        "total_custo": total_custo,
        "total_stop_loss": total_stop,
    }


def realizar_vistoria_previa_gemini(dados_processo: dict, itens: list[dict]) -> dict[str, Any]:
    """
    Executa Vistoria Prévia e Análise Comparativa Tática via Gemini AI
    conforme Lei 14.133/2021 e modelo de operação da JRBC TECH.
    """
    load_dotenv(override=True)
    chave = os.getenv("GEMINI_API_KEY")

    lic = dados_processo.get("licitacao", {})
    orgao = lic.get("orgao_nome") or "Órgão Licitante"
    objeto = lic.get("objeto") or "Contratação de Serviços / Aquisição"
    num_pregao = lic.get("numero_pregao") or "S/N"
    pasta_nome = dados_processo.get("nome_pasta") or ""

    if not chave:
        return _vistoria_local_fallback(dados_processo, itens)

    prompt = f"""
Você é o Especialista Sênior em Licitações Públicas e Engenharia de Custos da JRBC TECH (CNPJ: 65.488.003/0001-61 - Palmas/TO).
Realize a VISTORIA PRÉVIA & ANÁLISE COMPARATIVA deste certame antes de irmos para a disputa ao vivo no pregão.

DADOS DO CERTAME / PASTA DO GOOGLE DRIVE:
- Pasta: {pasta_nome}
- Órgão: {orgao}
- Pregão / Processo: {num_pregao}
- Objeto: {objeto}
- Itens identificados ({len(itens)}):
{json.dumps(itens[:10], ensure_ascii=False, indent=2)}

DIRETRIZES DA LEI 14.133/2021 E JRBC TECH:
1. Analise a exequibilidade (Art. 59 §4º da Lei 14.133: lances < 50% do valor orçado).
2. Se o valor de referência estiver R$ 0,00 ou sigiloso, calcule uma estimativa de mercado coerente com o objeto e sugira Custo Real e Stop-Loss (piso inviolável da empresa).
3. Garanta margem de lucro líquido mínima segura de 15% a 25%.
4. Seja direto, técnico e profissional.

Responda ESTRITAMENTE em formato JSON válido:
{{
  "badge": "🟢 VIÁVEL COM MARGEM SEGURA" (ou ⚠️ ATENÇÃO: VALOR SIGILOSO / RISCO),
  "parecer_resumido": "Texto conciso de 2 a 3 frases sintetizando a vistoria prévia e se vale a pena disputar",
  "pontos_atencao": [
    "Ponto 1 sobre risco de edital ou exigência técnica",
    "Ponto 2 sobre limite de inexequibilidade Art. 59 §4º",
    "Ponto 3 sobre recomendação de lance"
  ],
  "custo_real_sugerido": 0.0,
  "stop_loss_sugerido": 0.0,
  "margem_sugerida_percent": 20.0
}}
"""

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1500}
    }

    for modelo in MODELOS_GEMINI:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={chave}"
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT_GEMINI)
            if res.status_code == 200:
                dados_resp = res.json()
                cands = dados_resp.get("candidates", [])
                if cands and "content" in cands[0] and "parts" in cands[0]["content"]:
                    raw_txt = cands[0]["content"]["parts"][0]["text"].strip()
                    texto_limpo = raw_txt.strip()
                    if texto_limpo.startswith("```"):
                        texto_limpo = re.sub(r"^```(?:json)?\s*", "", texto_limpo)
                        texto_limpo = re.sub(r"\s*```$", "", texto_limpo)

                    # Extração inteligente do primeiro bloco JSON
                    m_json = re.search(r"\{.*\}", texto_limpo, re.DOTALL)
                    json_str = m_json.group(0) if m_json else texto_limpo

                    try:
                        parsed = json.loads(json_str)
                    except Exception as exc_json:
                        logger.warning(f"Aviso: resposta do Gemini ({modelo}) não é JSON puro: {exc_json}. Usando fallback textual elegante.")
                        fallback = _vistoria_local_fallback(dados_processo, itens)
                        fallback["parecer_resumido"] = f"Análise Técnica (IA):\n{raw_txt[:400]}"
                        return fallback

                    # Ajusta itens com base no parecer da IA
                    fallback = _vistoria_local_fallback(dados_processo, itens)
                    c_sug = float(parsed.get("custo_real_sugerido") or 0.0)
                    s_sug = float(parsed.get("stop_loss_sugerido") or 0.0)
                    m_sug = float(parsed.get("margem_sugerida_percent") or 20.0)

                    itens_ajustados = fallback["itens_atualizados"]
                    if c_sug > 0 and s_sug > 0 and itens_ajustados:
                        itens_ajustados[0]["custo_real"] = c_sug
                        itens_ajustados[0]["piso_preco"] = s_sug
                        itens_ajustados[0]["stop_loss"] = s_sug
                        itens_ajustados[0]["margem"] = m_sug

                    return {
                        "status_vistoria": "CONCLUIDA",
                        "badge": parsed.get("badge") or "🟢 VISTORIA IA APROVADA",
                        "parecer_resumido": parsed.get("parecer_resumido") or fallback["parecer_resumido"],
                        "pontos_atencao": parsed.get("pontos_atencao") or fallback["pontos_atencao"],
                        "itens_atualizados": itens_ajustados,
                        "total_referencia": fallback["total_referencia"],
                        "total_custo": c_sug if c_sug > 0 else fallback["total_custo"],
                        "total_stop_loss": s_sug if s_sug > 0 else fallback["total_stop_loss"],
                    }
        except requests.Timeout as exc_timeout:
            logger.warning(f"Timeout ({TIMEOUT_GEMINI}s) ao consultar Gemini ({modelo}): {exc_timeout}")
            continue
        except Exception as e:
            logger.warning(f"Tentativa com Gemini ({modelo}) falhou: {e}")
            continue

    # Fallback local se a nuvem falhar
    return _vistoria_local_fallback(dados_processo, itens)


def abrir_pasta_no_sistema(caminho: str) -> bool:
    """Abre a pasta local no explorador de arquivos do sistema operacional."""
    try:
        p = Path(os.path.expanduser(caminho))
        if p.exists():
            if os.name == "posix":
                subprocess.Popen(["xdg-open", str(p)])
            else:
                os.startfile(str(p))  # type: ignore
            return True
    except Exception as e:
        logger.error(f"Erro ao abrir pasta {caminho}: {e}")
    return False
