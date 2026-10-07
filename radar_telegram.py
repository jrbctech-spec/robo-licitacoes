"""
radar_telegram.py – Varredor Automático de Oportunidades no PNCP para Tocantins e Região.
JRBC TECH - CNPJ: 65.488.003/0001-61 - Palmas/TO

Consulta a API oficial do PNCP, filtra oportunidades em TI/Infraestrutura/Equipamentos
no estado do Tocantins (TO) e órgãos federais da região, enviando cards diretamente
para o Telegram via TelegramService.
"""
from __future__ import annotations

import json
import logging
import re
import ssl
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Optional

from telegram_service import TelegramService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("RadarTelegram")

# 1. Indicadores obrigatórios de prestação de serviços
PADROES_SERVICO = [
    r"\b(presta[çc][ãa]o de servi[çc]os?|contrata[çc][ãa]o de empresa especializada|contrata[çc][ãa]o de servi[çc]os?|servi[çc]os? t[ée]cnicos?|servi[çc]os? especializados?|servi[çc]os? continuados?)\b",
    r"\b(manuten[çc][ãa]o preventiva|manuten[çc][ãa]o corretiva|manuten[çc][ãa]o de|manuten[çc][ãa]o em|reparo|conserto|assist[êe]ncia t[ée]cnica)\b",
    r"\b(suporte( t[ée]cnico)?|helpdesk|service desk|sustenta[çc][ãa]o)\b",
    r"\b(instala[çc][ãa]o|implanta[çc][ãa]o|configura[çc][ãa]o|ativa[çc][ãa]o)\b",
    r"\b(loca[çc][ãa]o|aluguel|outsourcing)\b",
    r"\b(cabeamento estruturado|lan[çc]amento de cabos?|conectoriza[çc][ãa]o|fus[ãa]o de fibra)\b",
    r"\b(recarga|remanufatura de cartuchos?|remanufatura de toners?)\b",
    r"\b(treinamento|capacita[çc][ãa]o( em inform[áa]tica)?)\b",
    r"\b(fornecer e instalar|fornecimento e instala[çc][ãa]o|fornecimento com instala[çc][ãa]o|implanta[çc][ãa]o de rede|instala[çc][ãa]o de rede)\b",
    r"\b(material e servi[çc]o|pe[çc]as e servi[çc]o)\b"
]

# 2. Objetos compatíveis com os CNAEs da JRBC TECH
PADROES_CNAE = [
    # 95.11-8-00: Computadores, Periféricos e Hardware (excluindo servidores públicos humanos)
    r"\b(computador|computadores|notebook|notebooks|desktop|desktops|servidor\b(?!s?\s+(efetivo|tempor[áa]rio|p[úu]blico|do\s+poder|da\s+prefeitura|da\s+c[âa]mara|da\s+secretaria|municipa|estadua|federa))|nobreak|nobreaks|impressora|impressoras|multifuncional|multifuncionais|scanner|scanners|hardware|perif[ée]ricos?)\b",
    # 47.51-2-02: Suprimentos de Impressão
    r"\b(toner|toners|cartucho|cartuchos)\b",
    # 95.12-6-00 e 61.90-6-99: Telecom, Redes, Conectividade e Infraestrutura de TI
    r"\b(rede l[óo]gica|rede de dados|cabeamento estruturado|fibra [oó]ptica|switch|switches|roteador|roteadores|rack 19|patch panel|access point|infraestrutura tecnol[oó]gica|infraestrutura de ti)\b",
    r"\b(telefonia|pabx|central telef[ôo]nica|radiocomunica[çc][ãa]o|interfonia)\b",
    # CFTV / Segurança Eletrônica
    r"\b(cftv|c[âa]meras? de seguran[çc]a|c[âa]meras? ip|seguran[çc]a eletr[ôo]nica|monitoramento eletr[ôo]nico|dvr|nvr)\b",
    # 47.53-9-00: Áudio e Vídeo
    r"\b([áa]udio e v[íi]deo|videoconfer[êe]ncia|sonoriza[çc][ãa]o|podcast)\b",
    # 77.33-1-00: Outsourcing / Locação
    r"\b(outsourcing de impress[ãa]o|loca[çc][ãa]o de computadores?|loca[çc][ãa]o de equipamentos de ti)\b",
    # 85.99-6-03: Sistemas, TI e Treinamento
    r"\b(sistema de gest[ãa]o|software web|treinamento em inform[áa]tica|ferramentas de ti|certificado digital|ti|inform[áa]tica|tecnologia da informa[çc][ãa]o)\b"
]

# 3. Lista Negra (Exclusão sumária se contiver qualquer termo)
TERMOS_BLOQUEADOS = [
    # Saúde / Médico / Laboratorial / Odonto
    "médic", "medic", "hospitalar", "saúde", "saude", "odontol", "farmác", "farmac",
    "laborat", "reagente", "microscópio", "purificador", "bebedouro",
    # Vestuário / Merenda / Alimentos
    "vestuário", "vestuario", "fantasia", "uniforme", "merenda", "alimento", "alimentação", "alimentacao",
    "refeição", "refeicao", "auxílio-alimentação", "auxilio alimentação", "vale alimentação", "vale refeição",
    "cartão", "cartao", "coffee break", "buffet",
    # Limpeza / Portaria / Conservação / Veículos / Combustíveis
    "limpeza", "conservação", "conservacao", "asfalto", "pavimentação", "combustível", "combustivel",
    "veículo", "veiculo", "veículos", "ambulânc", "ambulanc", "automot", "mecânic", "mecanic", "pneu",
    # Climatização / Refrigeração
    "ar condicionado", "ar-condicionado", "climatiz", "refrigeração", "refrigeracao",
    # Obras civis / Predial
    "alvenaria", "reforma predial", "vigilância armada", "portaria", "copeiragem", "coleta de lixo", "obras civis", "engenharia civil",
    # Eventos / Festas
    "tenda", "tendas", "palco", "palcos", "rally", "festividade", "festa", "festas", "show", "shows", "evento", "eventos",
    # Papelaria / Expediente / Gráfica
    "material de expediente", "papelaria", "impressos gráficos", "impressos graficos", "container"
]

# 4. Contextos de TI obrigatórios para termos genéricos ('infraestrutura' ou 'suporte')
CONTEXTOS_TI_OBRIGATORIOS = [
    r"\b(tecnol[oó]gica|tecnologia da informa[çc][ãa]o|ti|tic|inform[áa]tica)\b",
    r"\b(computador|computadores|notebook|notebooks|desktop|desktops|servidor|servidores|nobreak|nobreaks)\b",
    r"\b(impressora|impressoras|multifuncional|multifuncionais|scanner|scanners|toner|toners|cartucho|cartuchos)\b",
    r"\b(rede de dados|rede l[óo]gica|cabeamento estruturado|fibra [oó]ptica|switch|switches|roteador|roteadores|patch panel|access point|rack 19)\b",
    r"\b(hardware|perif[ée]ricos?|software|sistemas? web|sistema de gest[ãa]o|helpdesk|service desk)\b",
    r"\b(cftv|c[âa]meras? ip|c[âa]meras? de seguran[çc]a|seguran[çc]a eletr[ôo]nica|dvr|nvr|videoconfer[êe]ncia|sonoriza[çc][ãa]o)\b",
    r"\b(telefonia|pabx|central telef[ôo]nica|radiocomunica[çc][ãa]o|interfonia)\b"
]

BLOQUEIO_INFRA_SUPORTE_NAO_TI = [
    r"\binfraestrutura\s+(urbana|vi[áa]ria|predial|civil|de\s+eventos|tur[íi]stica|esportiva|escolar|hidr[áa]ulica|el[ée]trica)\b",
    r"\bsuporte\s+(administrativo|operacional|pedag[oó]gico|log[íi]stico|jur[íi]dico|financeiro|psicol[oó]gico|nutricional|social)\b",
    r"\bmanuten[çc][ãa]o\s+(das?\s+atividades?|das?\s+rotinas?|das?\s+a[çc][õo]es|da\s+frota|de\s+ve[íi]culos?|predial|de\s+vias?|da\s+secretaria|do\s+setor|dos\s+trabalhos?)\b"
]

# 5. Detecção de compra pura de mercadorias sem serviços
RE_COMPRA_PURA = r"^\s*(\[.*?\]\s*-\s*)?(aquisi[çc][ãa]o|compra|registro de pre[çc]os? para (aquisi[çc][ãa]o|compra)|despesa referente a aquisi[çc][ãa]o)\s+(de\s+)?(equipamentos?|materiais?|material|suprimentos?|perif[ée]ricos?|pe[çc]as?|cabos?|microcomputadores?|computadores?|notebooks?|monitores?|fontes?|ferramentas?|insumos?|toner|toners|cartuchos?)\b"
RE_AGREGACAO_SERVICO = r"\b(com\s+instala[çc][ãa]o|e\s+instala[çc][ãa]o|instalar|e\s+servi[çc]os?|com\s+servi[çc]os?|e\s+configura[çc][ãa]o|com\s+configura[çc][ãa]o|e\s+manuten[çc][ãa]o|com\s+manuten[çc][ãa]o|e\s+suporte|com\s+suporte|e\s+m[ãa]o\s+de\s+obra|recarga)\b"


def validar_edital_jrbc(objeto: str) -> bool:
    texto = (objeto or "").lower()

    # Regra 1: Não pode ter nenhum termo da lista negra
    if any(b in texto for b in TERMOS_BLOQUEADOS):
        return False

    # Regra 1.1: Bloqueio específico de suporte, manutenção ou infraestrutura não-TI
    if any(re.search(p, texto, re.IGNORECASE) for p in BLOQUEIO_INFRA_SUPORTE_NAO_TI):
        return False

    # Regra 1.2: Para termos como 'infraestrutura' ou 'suporte', obrigatoriamente devem vir com contexto de TI
    tem_infra_ou_suporte = bool(re.search(r"\b(infraestrutura|suporte)\b", texto, re.IGNORECASE))
    if tem_infra_ou_suporte:
        tem_contexto_ti = any(re.search(p, texto, re.IGNORECASE) for p in CONTEXTOS_TI_OBRIGATORIOS)
        if not tem_contexto_ti:
            return False

    # Regra 1.3: Descarta compras puras de produtos sem serviços
    if re.search(RE_COMPRA_PURA, texto, re.IGNORECASE) and not re.search(RE_AGREGACAO_SERVICO, texto, re.IGNORECASE):
        return False

    # Regra 2: Deve conter obrigatoriamente um indicador de serviço (puro ou com produto)
    tem_servico = any(re.search(p, texto, re.IGNORECASE) for p in PADROES_SERVICO)
    if not tem_servico:
        return False

    # Regra 3: O objeto do serviço deve pertencer aos CNAEs de TI/Telecom/CFTV/Áudio/Vídeo da JRBC TECH
    tem_cnae = any(re.search(p, texto, re.IGNORECASE) for p in PADROES_CNAE)
    return tem_cnae


def formatar_link_pncp(item: dict) -> str:
    # 1. Se o órgão informou o link direto da sala (ex: Compras.gov.br), usa direto
    link_origem = item.get("linkSistemaOrigem")
    if link_origem and "http" in link_origem:
        return link_origem

    # 2. Tenta extrair diretamente dos campos da API do PNCP
    cnpj = item.get("cnpjOrgao") or item.get("orgaoEntidade", {}).get("cnpj")
    ano = item.get("anoCompra")
    seq = item.get("sequencialCompra")
    
    if cnpj and ano and seq:
        try:
            return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{int(seq)}"
        except Exception:
            return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"

    # 3. Fallback via regex no numeroControlePNCP (ex: 26063838000118-1-000129/2026)
    num_controle = item.get("numeroControlePNCP") or ""
    m = re.match(r"(\d+)-\d+-(\d+)/(\d+)", num_controle)
    if m:
        cnpj_extraido = m.group(1)
        seq_extraido = int(m.group(2))
        ano_extraido = m.group(3)
        return f"https://pncp.gov.br/app/editais/{cnpj_extraido}/{ano_extraido}/{seq_extraido}"

    return "https://pncp.gov.br"


def varrer_pncp(uf: str = "TO", dias_atras: int = 1, persistir_supabase: bool = True) -> list[dict[str, Any]]:
    """
    Consulta a API pública do PNCP em busca de contratações/dispensas/pregões
    na UF especificada e envia os editais relevantes para o Telegram.
    """
    data_inicio = (datetime.now() - timedelta(days=dias_atras)).strftime("%Y%m%d")
    data_fim = datetime.now().strftime("%Y%m%d")

    editais: list[dict[str, Any]] = []
    logger.info(f"[RADAR] Consultando PNCP para {uf} entre {data_inicio} e {data_fim}...")
    contexto_ssl = ssl._create_unverified_context()

    # Consulta modalidades principais: 8 (Dispensa Eletrônica) e 6 (Pregão Eletrônico)
    for mod in (8, 6):
        url = (
            f"https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
            f"?dataInicial={data_inicio}&dataFinal={data_fim}"
            f"&codigoModalidadeContratacao={mod}&uf={uf}&pagina=1&tamanhoPagina=50"
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (JRBC-Tech-Radar)"}
        )
        try:
            with urllib.request.urlopen(req, context=contexto_ssl, timeout=15) as resp:
                resposta = json.loads(resp.read().decode("utf-8"))
                editais.extend(resposta.get("data", []))
        except Exception as e:
            logger.debug(f"[RADAR] Aviso ao consultar modalidade {mod}: {e}")

    encontrados: list[dict[str, Any]] = []
    for item in editais:
        objeto = item.get("objetoCompra") or ""
        if validar_edital_jrbc(objeto):
            orgao = item.get("orgaoEntidade", {}).get("razaoSocial", "Órgão Não Informado")
            municipio = item.get("unidadeOrgao", {}).get("municipioNome", uf)
            modalidade = item.get("modalidadeNome", "Licitação")
            link = formatar_link_pncp(item)
            valor_estimado = item.get("valorTotalEstimado")

            encontrados.append({
                "numero_controle": item.get("numeroControlePNCP"),
                "orgao": orgao,
                "municipio": municipio,
                "modalidade": modalidade,
                "objeto": objeto,
                "link": link,
                "valor": valor_estimado,
                "data_publicacao": item.get("dataPublicacaoPncp"),
            })

    if not encontrados:
        logger.info(f"[RADAR] Nenhuma nova oportunidade de TI identificada em {uf}.")
        return []

    logger.info(f"[RADAR] {len(encontrados)} oportunidade(s) relevante(s) encontrada(s)!")

    # 1. Envia cabeçalho no Telegram
    TelegramService.enviar_mensagem(
        f"📡 *RADAR DE OPORTUNIDADES JRBC TECH*\n"
        f"Foram identificados *{len(encontrados)} edital(is)* de TI/Tecnologia em *{uf}*."
    )

    # 2. Envia cada card individual com link clicável (limita a 10 para evitar flood)
    for op in encontrados[:10]:
        val_str = f"R$ {op['valor']:,.2f}" if op['valor'] else "Não informado"
        card = (
            f"🏛 *{op['orgao']}* ({op['municipio']}/{uf})\n"
            f"📋 *Modalidade:* {op['modalidade']}\n"
            f"💵 *Valor Estimado:* {val_str}\n"
            f"📝 *Objeto:* _{op['objeto'][:180]}..._\n\n"
            f"🔗 [Acessar Edital / Portal]({op['link']})"
        )
        TelegramService.enviar_mensagem(card)

    # 3. Opcional: Persistir no Supabase com portal='COMPRAS_GOV'
    if persistir_supabase:
        try:
            from supabase_client import get_supabase_client
            client = get_supabase_client()
            if client:
                for op in encontrados:
                    num_ctrl = op.get("numero_controle") or f"PNCP-{datetime.now().strftime('%Y%m%d%H%M')}"
                    row = {
                        "numero_controle_pncp": str(num_ctrl),
                        "numero_pregao": str(num_ctrl),
                        "objeto": op["objeto"],
                        "orgao_nome": op["orgao"],
                        "uf": uf,
                        "modalidade": op["modalidade"],
                        "valor_estimado": float(op["valor"]) if op["valor"] else None,
                        "link_edital": op["link"],
                        "portal": "COMPRAS_GOV",
                        "status": "pendente",
                        "status_analise": "NOVA",
                        "prioridade": "ALTA",
                        "atualizado_em": datetime.now().isoformat(),
                    }
                    try:
                        client.table("licitacoes").upsert(row, on_conflict="numero_controle_pncp").execute()
                    except Exception as err_db:
                        logger.debug(f"[SUPABASE] Upsert ignorado ou erro: {err_db}")
        except Exception as e:
            logger.debug(f"[RADAR] Supabase opcional indisponível: {e}")

    return encontrados


if __name__ == "__main__":
    varrer_pncp(uf="TO", dias_atras=2)
