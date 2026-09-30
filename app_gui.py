"""
app_gui.py – Interface Gráfica de Alta Performance para Múltiplos Pregões e Dispensas.

Recursos Especiais:
  1. Suporte a Múltiplos Pregões / Dispensas por dia (Catálogo Multi-Certame).
  2. Filtro Rápido Inteligente: Rejeita PDFs que não são edital em <0.01s (sem gastar API do Gemini).
  3. Painel Dashboard com Métricas Dinâmicas e navegação direta.
  4. Painel Editais & Custos: Seletor de Certame Ativo, Calculadora de Stop-Loss e Importação Rápida.
  5. Console da Sessão ao Vivo: Seletor de Disputa por Pregão, Modo Dry-Run vs Real, Badges de Status.
  6. Oportunidades PNCP: Busca em tempo real e importação direta.
  7. Tratamento de Erros e Sincronização Segura no Supabase.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional, Dict, List

import customtkinter as ctk
from tkinter import filedialog, messagebox, ttk

import csv
import re

from supabase import create_client
try:
    SUPABASE_URL = "https://szphoxostihnpcovyzvx.supabase.co"
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "SUA_CHAVE_SUPABASE")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    logging.warning(f"Não foi possível inicializar o cliente Supabase globalmente: {e}")

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    import openpyxl
except ImportError:
    openpyxl = None

try:
    from config import ModoOperacao, settings
    from engine import (
        DecisaoLance,
        EstadoItemEnum,
        MotivoBloqueio,
        MotorDecisao,
        PrecificacaoItem,
        TipoItemEnum,
        TipoIntervalo,
        RegraItem,
    )
    from edital_parser import (
        EditalParser,
        ItemEdital,
        ResultadoEdital,
        SeveridadeRisco,
    )
    from browser_bot import BrowserBot, abrir_login_nativo
    from pncp_client import (
        carregar_certame_pncp,
        extrair_params_pncp,
        sincronizar_pncp_supabase,
        buscar_licitacoes_propostas_pncp,
        buscar_publicacoes_pncp,
        baixar_anexos_edital_pncp,
    )
    from supabase_client import salvar_oportunidade, listar_oportunidades_salvas
    from proposta_parser import PropostaParser
    import drive_scanner
    from license_manager import (
        validar_licenca,
        salvar_chave_local,
        obter_chave_salva,
        obter_hwid,
        StatusLicenca,
        ResultadoValidacao,
    )
    _MODULES_OK = True
except Exception as _import_err:
    _MODULES_OK = False
    print(f"[WARNING] app_gui: Erro ao importar módulos do robô: {_import_err}")

logger = logging.getLogger(__name__)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

COR = {
    "bg_dark": "#0B0F19",
    "bg_card": "#151C2C",
    "bg_card_hover": "#1C263B",
    "border": "#222F43",
    "border_focus": "#00F2FE",
    "cyan_primary": "#00F2FE",
    "cyan_hover": "#00C3CE",
    "blue_accent": "#4FACFE",
    "green_success": "#00E676",
    "amber_warning": "#FFB300",
    "orange_warning": "#FF9800",
    "red_danger": "#FF5252",
    "purple_accent": "#7C4DFF",
    "text_primary": "#F8FAFC",
    "text_secondary": "#94A3B8",
    "text_muted": "#64748B",

    "pill_liderando_bg": "#052E16",
    "pill_liderando_fg": "#4ADE80",
    "pill_superado_bg": "#451A03",
    "pill_superado_fg": "#FBBF24",
    "pill_piso_bg": "#450A0A",
    "pill_piso_fg": "#FCA5A5",
    "pill_aguardando_bg": "#1E293B",
    "pill_aguardando_fg": "#94A3B8",
}

PALAVRAS_CHAVE_EDITAL = [
    "PREGÃO", "PREGAO", "DISPENSA", "LICITAÇÃO", "LICITACAO", 
    "TERMO DE REFERÊNCIA", "TERMO DE REFERENCIA", "CONCORRÊNCIA", 
    "CONCORRENCIA", "LEILÃO", "LEILAO", "UASG", "AVISO DE LICITAÇÃO",
    "PROCESSO ADMINISTRATIVO", "EDITAL", "COMPRAS.GOV", "INEXIGIBILIDADE"
]


def parsear_planilha_processo(caminho_arquivo: str | Path) -> ResultadoEdital:
    """
    Lê instantaneamente uma planilha (.xlsx ou .csv) contendo os itens do certame.
    Mapeia colunas flexíveis: Item, Descrição, Qtd, Unidade, Preço Ref, Custo Real, Tipo.
    Gera as regras do motor com a precificação da JRBC TECH.
    """
    caminho = Path(caminho_arquivo)
    resultado = ResultadoEdital(caminho_pdf=str(caminho))

    if not caminho.exists():
        resultado.erros_parsing.append(f"Arquivo não encontrado: {caminho}")
        return resultado

    linhas_dict: list[dict[str, Any]] = []

    # 1. Leitura via pandas/openpyxl ou csv
    if caminho.suffix.lower() == ".csv":
        try:
            with open(caminho, mode="r", encoding="utf-8-sig", errors="ignore") as f:
                reader = csv.DictReader(f)
                linhas_dict = list(reader)
        except Exception as exc:
            logger.warning(f"Erro ao ler CSV com csv.DictReader: {exc}")
    elif pd is not None and caminho.suffix.lower() in (".xlsx", ".xls"):
        try:
            df = pd.read_excel(caminho)
            linhas_dict = df.to_dict(orient="records")
        except Exception as exc:
            logger.warning(f"Erro ao ler Excel via pandas: {exc}")
    elif openpyxl is not None and caminho.suffix.lower() == ".xlsx":
        try:
            wb = openpyxl.load_workbook(caminho, data_only=True)
            sheet = wb.active
            headers = [str(cell.value or "").strip() for cell in sheet[1]]
            for row in sheet.iter_rows(min_row=2, values_only=True):
                if any(row):
                    row_dict = {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
                    linhas_dict.append(row_dict)
        except Exception as exc:
            logger.warning(f"Erro ao ler Excel via openpyxl: {exc}")

    if not linhas_dict:
        resultado.erros_parsing.append("Incapaz de extrair linhas da planilha. Verifique a extensão ou formato.")
        return resultado

    # Mapeamento flexível de colunas
    itens_extraidos: list[ItemEdital] = []
    for idx, row in enumerate(linhas_dict, 1):
        if not isinstance(row, dict):
            continue

        # Normalizar nomes de colunas
        row_norm = {str(k).strip().lower(): v for k, v in row.items() if k is not None}

        # Encontrar campo do item
        num_item = idx
        for col_name in ("item", "num", "numero", "n_item", "n_"):
            if col_name in row_norm and row_norm[col_name] is not None:
                try:
                    num_item = int(float(str(row_norm[col_name]).strip()))
                    break
                except (ValueError, TypeError):
                    pass

        # Encontrar campo de descrição
        desc = ""
        for col_name in ("descricao", "desc", "produto", "servico", "item_descricao", "objeto"):
            if col_name in row_norm and row_norm[col_name]:
                desc = str(row_norm[col_name]).strip()
                break

        if not desc:
            # Fallback: pega a primeira coluna de texto longo
            for v in row_norm.values():
                if isinstance(v, str) and len(v) > 3:
                    desc = v.strip()
                    break

        if not desc:
            continue

        # Quantidade
        qtd = 1.0
        for col_name in ("qtd", "quantidade", "quant"):
            if col_name in row_norm and row_norm[col_name] is not None:
                try:
                    val_q = float(str(row_norm[col_name]).replace(",", "."))
                    if val_q > 0:
                        qtd = val_q
                        break
                except (ValueError, TypeError):
                    pass

        # Unidade
        unidade = "UN"
        for col_name in ("unidade", "und", "un"):
            if col_name in row_norm and row_norm[col_name]:
                unidade = str(row_norm[col_name]).strip().upper()
                break

        # Preço de referência / valor estimado
        val_ref = None
        for col_name in ("preco_ref", "valor_ref", "valor_referencia", "preco_referencia", "estimado", "teto", "valor_total"):
            if col_name in row_norm and row_norm[col_name] is not None:
                try:
                    raw_val = row_norm[col_name]
                    if isinstance(raw_val, (int, float)):
                        val_r = float(raw_val)
                    else:
                        val_str = str(raw_val).replace("R$", "").strip()
                        if "," in val_str and "." in val_str:
                            val_str = val_str.replace(".", "").replace(",", ".")
                        elif "," in val_str:
                            val_str = val_str.replace(",", ".")
                        val_r = float(val_str)
                    if val_r > 0:
                        val_ref = val_r
                        break
                except (ValueError, TypeError):
                    pass

        # Custo real / Custo unitário
        custo_val = None
        for col_name in ("custo", "custo_real", "custo_unitario", "custo_base", "custo_estimado", "custo_aquisicao"):
            if col_name in row_norm and row_norm[col_name] is not None:
                try:
                    raw_val = row_norm[col_name]
                    if isinstance(raw_val, (int, float)):
                        val_c = float(raw_val)
                    else:
                        val_str = str(raw_val).replace("R$", "").strip()
                        if "," in val_str and "." in val_str:
                            val_str = val_str.replace(".", "").replace(",", ".")
                        elif "," in val_str:
                            val_str = val_str.replace(",", ".")
                        val_c = float(val_str)
                    if val_c > 0:
                        custo_val = val_c
                        break
                except (ValueError, TypeError):
                    pass

        # Piso / Stop-Loss / Preço Mínimo
        piso_val = None
        for col_name in ("piso", "stop_loss", "stoploss", "preco_minimo", "piso_seguranca", "lance_minimo", "piso_preco"):
            if col_name in row_norm and row_norm[col_name] is not None:
                try:
                    raw_val = row_norm[col_name]
                    if isinstance(raw_val, (int, float)):
                        val_p = float(raw_val)
                    else:
                        val_str = str(raw_val).replace("R$", "").strip()
                        if "," in val_str and "." in val_str:
                            val_str = val_str.replace(".", "").replace(",", ".")
                        elif "," in val_str:
                            val_str = val_str.replace(",", ".")
                        val_p = float(val_str)
                    if val_p > 0:
                        piso_val = val_p
                        break
                except (ValueError, TypeError):
                    pass

        # Tipo de item (Produto ou Serviço)
        tipo_str = "PRODUTO"
        for col_name in ("tipo", "tipo_item", "categoria"):
            if col_name in row_norm and row_norm[col_name]:
                val_t = str(row_norm[col_name]).strip().upper()
                if "SERV" in val_t:
                    tipo_str = "SERVICO"
                break

        if tipo_str == "PRODUTO" and any(s in desc.lower() for s in ("serviço", "servico", "manutenção", "manutencao", "suporte", "instalação", "instalacao")):
            tipo_str = "SERVICO"

        item_e = ItemEdital(
            numero_item=num_item,
            descricao=desc,
            unidade_medida=unidade,
            quantidade=qtd,
            valor_referencia=val_ref,
            tipo_item=tipo_str.lower(),
            custo_real=custo_val,
            piso_preco=piso_val,
        )
        itens_extraidos.append(item_e)

    resultado.itens = sorted(itens_extraidos, key=lambda x: x.numero_item)
    resultado.sucesso = len(resultado.itens) > 0
    resultado.numero_pregao = caminho.stem
    return resultado


def eh_edital_pdf_valido(caminho_pdf: str) -> bool:
    """Verifica rapidamente em <0.05s se um arquivo PDF é um edital/licitação antes de acionar IA."""
    try:
        import pypdf
        reader = pypdf.PdfReader(caminho_pdf)
        num_paginas = len(reader.pages)
        if num_paginas == 0:
            return False
        
        # Lê os textos das 3 primeiras páginas
        texto_acumulado = ""
        for i in range(min(3, num_paginas)):
            txt = reader.pages[i].extract_text() or ""
            texto_acumulado += txt.upper() + " "
        
        # Procura palavras-chave essenciais de licitação
        for kw in PALAVRAS_CHAVE_EDITAL:
            if kw in texto_acumulado:
                return True
        return False
    except Exception:
        # Se falhar a leitura rápida, aceita tentar para não ignorar falso negativo
        return True


def sugerir_url_disputa(res: ResultadoEdital, chave: str = "") -> str:
    """Gera a URL direta da sala de disputa do Compras.gov.br a partir dos dados do certame."""
    texto_busca = f"{chave} {res.numero_pregao} {res.numero_uasg} {res.caminho_pdf}"

    # 1. Procura código de compra de 16 ou 17 dígitos (ex: 92677406000052026)
    m_compra = re.search(r"\b(\d{16,17})\b", texto_busca)
    if m_compra:
        return f"https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/fornecedor/disputa?compra={m_compra.group(1)}"

    # 2. Padrão CRQ12 / Dispensa 5 / 2026 (UASG 926774 do CRQ-12)
    if "926774" in texto_busca or "crq" in texto_busca.lower() or "crq12" in texto_busca.lower():
        m_disp = re.search(r"(\d+)[\-_/]+(\d{4})", texto_busca)
        num = str(int(m_disp.group(1))).zfill(5) if m_disp else "00005"
        ano = m_disp.group(2) if m_disp else "2026"
        return f"https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/fornecedor/disputa?compra=92677406{num}{ano}"

    # 3. Procura UASG explícita (5-6 dígitos) + Modalidade + Número + Ano
    m_full = re.search(
        r"(?:uasg\s*[:\-–]?\s*)(\d{5,6})[\s\-_]*(?:dispensa|pregao|concorrencia|disp|pe)?[\s\-_]*(\d+)[\s\-_/]+(\d{4})",
        texto_busca,
        re.IGNORECASE,
    )
    if m_full:
        uasg = m_full.group(1).zfill(6)
        mod_cod = "06" if "disp" in texto_busca.lower() else "05"
        num = str(int(m_full.group(2))).zfill(5)
        ano = m_full.group(3)
        return f"https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/fornecedor/disputa?compra={uasg}{mod_cod}{num}{ano}"

    return "https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/fornecedor/disputa"


class CardFrame(ctk.CTkFrame):
    def __init__(self, master: Any, border_color: str = COR["border"], **kwargs: Any) -> None:
        super().__init__(
            master,
            fg_color=COR["bg_card"],
            corner_radius=10,
            border_width=1,
            border_color=border_color,
            **kwargs,
        )


class MetricCard(CardFrame):
    def __init__(
        self,
        master: Any,
        titulo: str,
        valor_inicial: str = "0",
        subtitulo: str = "",
        cor_destaque: str = COR["cyan_primary"],
        icone: str = "📊",
        **kwargs: Any,
    ) -> None:
        super().__init__(master, **kwargs)
        self.grid_columnconfigure(0, weight=1)

        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 2))

        ctk.CTkLabel(
            header_frame,
            text=f"{icone}  {titulo}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COR["text_secondary"],
        ).pack(side="left")

        self.lbl_valor = ctk.CTkLabel(
            self,
            text=valor_inicial,
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=cor_destaque,
        )
        self.lbl_valor.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 2))

        self.lbl_sub = ctk.CTkLabel(
            self,
            text=subtitulo,
            font=ctk.CTkFont(size=11),
            text_color=COR["text_muted"],
        )
        self.lbl_sub.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))

    def atualizar(self, valor: str, subtitulo: str = "") -> None:
        self.lbl_valor.configure(text=valor)
        if subtitulo:
            self.lbl_sub.configure(text=subtitulo)


# ─── PAINEL 1: DASHBOARD ──────────────────────────────────────────────────────
class PainelDashboard(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self._build()
        self.carregar_dados_dashboard()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        cards_frame = ctk.CTkFrame(self, fg_color="transparent")
        cards_frame.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 12))
        cards_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.card_lics_ativas = MetricCard(cards_frame, titulo="Pregões / Dispensas", valor_inicial="0", subtitulo="Carregados Hoje", cor_destaque=COR["cyan_primary"], icone="📋")
        self.card_lics_ativas.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self.card_liderando = MetricCard(cards_frame, titulo="Itens Liderando", valor_inicial="0", subtitulo="1ª Posição Confirmada", cor_destaque=COR["green_success"], icone="🏆")
        self.card_liderando.grid(row=0, column=1, padx=6, sticky="ew")

        self.card_stop_loss = MetricCard(cards_frame, titulo="Stop-Loss Ativo", valor_inicial="0", subtitulo="Trava de Piso Protegida", cor_destaque=COR["amber_warning"], icone="🛡️")
        self.card_stop_loss.grid(row=0, column=2, padx=6, sticky="ew")

        self.card_margem = MetricCard(cards_frame, titulo="Margem Protegida", valor_inicial="15.0%", subtitulo="Média Mínima", cor_destaque=COR["blue_accent"], icone="💰")
        self.card_margem.grid(row=0, column=3, padx=(6, 0), sticky="ew")

        main_content = ctk.CTkFrame(self, fg_color="transparent")
        main_content.grid(row=1, column=0, sticky="nsew")
        main_content.grid_columnconfigure(0, weight=3)
        main_content.grid_columnconfigure(1, weight=2)
        main_content.grid_rowconfigure(0, weight=1)

        card_lics = CardFrame(main_content)
        card_lics.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        card_lics.grid_rowconfigure(1, weight=1)
        card_lics.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card_lics, text="📋  Pregões & Dispensas Ativos no Sistema", font=ctk.CTkFont(size=14, weight="bold"), text_color=COR["text_primary"]).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 6))

        tbl_frame = ctk.CTkFrame(card_lics, fg_color="transparent")
        tbl_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        tbl_frame.grid_columnconfigure(0, weight=1)
        tbl_frame.grid_rowconfigure(0, weight=1)

        columns = ("pregao", "orgao", "itens", "status")
        self.tree_lics = ttk.Treeview(tbl_frame, columns=columns, show="headings", height=8)
        self.tree_lics.heading("pregao", text="Pregão / UASG")
        self.tree_lics.heading("orgao", text="Órgão / Objeto")
        self.tree_lics.heading("itens", text="Nº de Itens")
        self.tree_lics.heading("status", text="Status")
        self.tree_lics.column("pregao", width=140, anchor="w")
        self.tree_lics.column("orgao", width=250, anchor="w")
        self.tree_lics.column("itens", width=80, anchor="center")
        self.tree_lics.column("status", width=110, anchor="center")
        self.tree_lics.grid(row=0, column=0, sticky="nsew")

        sb = ctk.CTkScrollbar(tbl_frame, orientation="vertical", command=self.tree_lics.yview)
        self.tree_lics.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

        card_dicas = CardFrame(main_content)
        card_dicas.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        card_dicas.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card_dicas, text="⚡  Ações Rápidas & Atalhos", font=ctk.CTkFont(size=14, weight="bold"), text_color=COR["text_primary"]).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 10))

        ctk.CTkButton(card_dicas, text="📄  Adicionar Pregão / Dispensa", font=ctk.CTkFont(size=12, weight="bold"), fg_color=COR["cyan_primary"], text_color="#000000", hover_color=COR["cyan_hover"], height=38, command=lambda: self.app.selecionar_aba("📄  Editais & Custos")).grid(row=1, column=0, sticky="ew", padx=16, pady=4)
        ctk.CTkButton(card_dicas, text="🌐  Buscar no PNCP", font=ctk.CTkFont(size=12, weight="bold"), fg_color=COR["purple_accent"], text_color="#FFFFFF", height=38, command=lambda: self.app.selecionar_aba("🌐  Oportunidades PNCP")).grid(row=2, column=0, sticky="ew", padx=16, pady=4)
        ctk.CTkButton(card_dicas, text="🚀  Console de Lances ao Vivo", font=ctk.CTkFont(size=12, weight="bold"), fg_color=COR["green_success"], text_color="#000000", height=38, command=lambda: self.app.selecionar_aba("⚡  Console da Sessão")).grid(row=3, column=0, sticky="ew", padx=16, pady=4)

    def atualizar_catalogo(self) -> None:
        for r in self.tree_lics.get_children():
            self.tree_lics.delete(r)

        cat = self.app.catalogo_licitacoes
        self.card_lics_ativas.atualizar(str(len(cat)))

        for nome_chave, res in cat.items():
            pregao_str = f"Pregão {res.numero_pregao or 'S/N'}"
            if res.numero_uasg:
                pregao_str += f" (UASG {res.numero_uasg})"
            orgao_str = getattr(res, 'orgao_comprador', getattr(res, 'orgao', 'Órgão Não Informado'))
            objeto_str = getattr(res, 'objeto_licitacao', getattr(res, 'objeto', 'Sem descrição'))
            descricao_exibicao = orgao_str if orgao_str != "Órgão Não Informado" else objeto_str
            qtd_itens = len(res.itens)

            self.tree_lics.insert("", "end", values=(pregao_str, descricao_exibicao[:50], qtd_itens, "PRONTO"))

    def carregar_dados_dashboard(self) -> None:
        """Busca licitações salvas no Supabase e popula a tabela no Dashboard."""
        try:
            resposta = supabase.table("licitacoes")\
                .select("*")\
                .order("criado_em", desc=True)\
                .limit(50)\
                .execute()
                
            licitacoes = resposta.data or []
            
            for r in self.tree_lics.get_children():
                self.tree_lics.delete(r)
            
            total_hoje = 0
            participando = 0
            
            for lic in licitacoes:
                numero = lic.get("numero_controle_pncp") or lic.get("id") or "N/D"
                orgao_nome = lic.get("orgao_nome") or ""
                orgao = f"{lic.get('uf', '')} - {orgao_nome[:35]}..."
                status = lic.get("status_analise", "Pendente")
                itens_count = lic.get("qtd_itens", 1)
                
                self.tree_lics.insert("", "end", values=(
                    numero,
                    orgao,
                    itens_count,
                    status.upper()
                ))
                
                total_hoje += 1
                if status.lower() in ["proposta_enviada", "em_disputa", "ativo"]:
                    participando += 1
                    
            self.card_lics_ativas.atualizar(str(total_hoje))
            self.card_liderando.atualizar(str(participando))
            
        except Exception as e:
            logging.error(f"Erro ao carregar dados do Supabase no Dashboard: {e}")


# ─── PAINEL 2: EDITAIS & CUSTOS (MULTI-CERTAME) ────────────────────────────────
class PainelEditais(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self._build()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # Diretório de Processos de Licitações (Caminho Padrão)
        default_folder = "/home/jrbc/Documentos/PASTA DE PROCESSOS DE LICITAÇOES"
        if not os.path.exists(default_folder):
            user_docs = os.path.expanduser("~/Documentos")
            default_folder = user_docs if os.path.exists(user_docs) else os.path.expanduser("~")

        card_upload = CardFrame(self)
        card_upload.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 8))
        card_upload.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card_upload, text="📁  Diretório de Processos de Licitações:", font=ctk.CTkFont(size=13, weight="bold"), text_color=COR["text_primary"]).grid(row=0, column=0, padx=(16, 8), pady=10, sticky="w")

        self.txt_pasta_processos = ctk.CTkEntry(card_upload, font=ctk.CTkFont(size=12))
        self.txt_pasta_processos.insert(0, default_folder)
        self.txt_pasta_processos.grid(row=0, column=1, padx=8, pady=10, sticky="ew")

        btn_browse = ctk.CTkButton(card_upload, text="🔍 Selecionar", width=100, fg_color=COR["bg_card_hover"], border_color=COR["cyan_primary"], border_width=1, command=self._selecionar_pasta_processos)
        btn_browse.grid(row=0, column=2, padx=4, pady=10)

        btn_refresh = ctk.CTkButton(card_upload, text="🔄 Atualizar Lista de Processos", font=ctk.CTkFont(weight="bold"), fg_color=COR["cyan_primary"], text_color="#000000", command=self._escanear_pasta_processos)
        btn_refresh.grid(row=0, column=3, padx=4, pady=10)

        btn_excel = ctk.CTkButton(card_upload, text="📊 Carregar Planilha Excel do Certame", font=ctk.CTkFont(weight="bold"), fg_color=COR["purple_accent"], text_color="#FFFFFF", command=self._carregar_planilha_manual)
        btn_excel.grid(row=0, column=4, padx=(4, 16), pady=10)

        # Seletor de Certame Ativo (Multi-Pregão)
        card_seletor = CardFrame(self)
        card_seletor.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 8))
        card_seletor.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card_seletor, text="📋  Pregão / Licitação Ativa:", font=ctk.CTkFont(size=13, weight="bold"), text_color=COR["cyan_primary"]).grid(row=0, column=0, padx=(16, 8), pady=10, sticky="w")
        
        self.cmb_certames = ctk.CTkOptionMenu(
            card_seletor,
            values=["(Nenhum pregão carregado)"],
            font=ctk.CTkFont(weight="bold"),
            dynamic_resizing=True,
            command=self._on_certame_selecionado,
        )
        self.cmb_certames.grid(row=0, column=1, padx=8, pady=10, sticky="ew")

        btn_remover = ctk.CTkButton(card_seletor, text="🗑️ Remover", width=90, fg_color=COR["red_danger"], text_color="#FFFFFF", command=self._remover_certame_atual)
        btn_remover.grid(row=0, column=2, padx=(4, 16), pady=10)

        # Tabela de Itens & Stop Loss
        card_itens = CardFrame(self)
        card_itens.grid(row=3, column=0, sticky="nsew", padx=0, pady=0)
        card_itens.grid_rowconfigure(1, weight=1)
        card_itens.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card_itens, text="💰  Itens do Pregão Selecionado & Calculadora de Stop-Loss", font=ctk.CTkFont(size=14, weight="bold"), text_color=COR["text_primary"]).grid(row=0, column=0, sticky="w", padx=16, pady=(10, 4))

        tbl_f = ctk.CTkFrame(card_itens, fg_color="transparent")
        tbl_f.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        tbl_f.grid_columnconfigure(0, weight=1)
        tbl_f.grid_rowconfigure(0, weight=1)

        cols = ("num", "desc", "qtd", "un", "val_ref", "custo", "margem", "piso", "prop_inicial")
        self.tree_itens = ttk.Treeview(tbl_f, columns=cols, show="headings")
        self.tree_itens.heading("num", text="Item")
        self.tree_itens.heading("desc", text="Descrição do Produto/Serviço")
        self.tree_itens.heading("qtd", text="Qtd")
        self.tree_itens.heading("un", text="Unidade")
        self.tree_itens.heading("val_ref", text="Preço Ref (R$)")
        self.tree_itens.heading("custo", text="Custo Real (R$)")
        self.tree_itens.heading("margem", text="Margem (%)")
        self.tree_itens.heading("piso", text="Stop-Loss (R$)")
        self.tree_itens.heading("prop_inicial", text="Proposta Inicial")

        self.tree_itens.column("num", width=50, anchor="center")
        self.tree_itens.column("desc", width=260, anchor="w")
        self.tree_itens.column("qtd", width=60, anchor="center")
        self.tree_itens.column("un", width=60, anchor="center")
        self.tree_itens.column("val_ref", width=110, anchor="e")
        self.tree_itens.column("custo", width=110, anchor="e")
        self.tree_itens.column("margem", width=90, anchor="center")
        self.tree_itens.column("piso", width=110, anchor="e")
        self.tree_itens.column("prop_inicial", width=110, anchor="e")
        self.tree_itens.grid(row=0, column=0, sticky="nsew")
        self.tree_itens.bind("<Double-1>", self._on_editar_item_custo)

        sb = ctk.CTkScrollbar(tbl_f, orientation="vertical", command=self.tree_itens.yview)
        self.tree_itens.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

        lbl_hint = ctk.CTkLabel(
            card_itens,
            text="💡 Dica: Dê um duplo-clique em qualquer linha para ajustar o Custo Real ou definir o Stop-Loss (Preço Mínimo).",
            font=ctk.CTkFont(size=11, slant="italic"),
            text_color=COR["cyan_primary"],
        )
        lbl_hint.grid(row=2, column=0, sticky="w", padx=16, pady=(0, 8))

    def _on_editar_item_custo(self, event: Any) -> None:
        selected = self.tree_itens.selection()
        if not selected:
            return
        item_vals = self.tree_itens.item(selected[0])["values"]
        num_item = int(item_vals[0])

        certame_atual = self.app.certame_selecionado
        if not certame_atual or certame_atual not in self.app.catalogo_licitacoes:
            return
        res = self.app.catalogo_licitacoes[certame_atual]
        item_obj = next((it for it in res.itens if it.numero_item == num_item), None)
        if not item_obj:
            return

        from tkinter import simpledialog
        padrao_sug = item_obj.piso_preco or (item_obj.custo_real * 1.15 if item_obj.custo_real else (item_obj.valor_referencia * 0.60 if item_obj.valor_referencia else 100.0))
        novo_piso = simpledialog.askfloat(
            "Configurar Stop-Loss do Item",
            f"Item {num_item}: {item_obj.descricao[:50]}...\n\n"
            f"Preço de Referência: R$ {item_obj.valor_referencia or 0.0:,.2f}\n"
            f"Custo Atual Cadastrado: R$ {item_obj.custo_real or 0.0:,.2f}\n\n"
            "Digite o novo valor MÍNIMO de venda / STOP-LOSS (R$):\n(O robô NUNCA dará lance abaixo deste valor)",
            initialvalue=round(padrao_sug, 2),
            minvalue=0.01,
        )
        if novo_piso is not None:
            item_obj.piso_preco = float(novo_piso)
            self._exibir_certame(certame_atual)
            if hasattr(self.app, "painel_sessao"):
                self.app.painel_sessao.atualizar_tabela_inicial()
            messagebox.showinfo("Stop-Loss Atualizado", f"Item {num_item}: Stop-Loss definido com sucesso para R$ {novo_piso:,.2f}")

    def _selecionar_pasta_processos(self) -> None:
        atual = self.txt_pasta_processos.get().strip()
        doc_dir = atual if os.path.exists(atual) else (os.path.expanduser("~/Documentos") if os.path.exists(os.path.expanduser("~/Documentos")) else os.path.expanduser("~"))
        folder = filedialog.askdirectory(title="Selecionar Diretório de Processos de Licitações", initialdir=doc_dir)
        if folder:
            self.txt_pasta_processos.delete(0, "end")
            self.txt_pasta_processos.insert(0, folder)
            self._escanear_pasta_processos()

    def _escanear_pasta_processos(self) -> None:
        folder = self.txt_pasta_processos.get().strip()
        if not os.path.exists(folder):
            messagebox.showwarning("Pasta Não Encontrada", f"O diretório '{folder}' não foi encontrado.")
            return

        def _worker():
            try:
                encontrados: list[Path] = []
                p_folder = Path(folder)
                # Busca recursiva (rglob) por arquivos .xlsx e .csv
                for ext in ("*.xlsx", "*.csv", "*.xls"):
                    for file_p in p_folder.rglob(ext):
                        if not file_p.name.startswith("~$") and not file_p.name.startswith("."):
                            encontrados.append(file_p)

                adicionados = 0
                for file_path in encontrados:
                    res = parsear_planilha_processo(file_path)
                    if res and res.itens:
                        nome_c = f"Certame {res.numero_pregao or file_path.stem} - {file_path.name}"
                        self.after(0, lambda n=nome_c, r=res: self.app.adicionar_licitacao(n, r))
                        adicionados += 1

                if adicionados > 0:
                    msg = f"✅ Varredura concluída! {adicionados} planilha(s) de processo identificada(s) e adicionadas ao catálogo."
                    self.after(0, lambda: messagebox.showinfo("Lista de Processos Atualizada", msg))
                else:
                    self.after(0, lambda: messagebox.showinfo("Processos", "Nenhuma planilha (.xlsx ou .csv) válida encontrada na pasta de processos."))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Erro ao Escanear Pasta", str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def _carregar_planilha_manual(self) -> None:
        folder = self.txt_pasta_processos.get().strip()
        initial = folder if os.path.exists(folder) else os.path.expanduser("~/Documentos")
        path = filedialog.askopenfilename(
            title="Selecionar Planilha Excel do Certame",
            initialdir=initial,
            filetypes=[("Planilhas Excel/CSV", "*.xlsx *.csv"), ("Todos os arquivos", "*.*")]
        )
        if not path:
            return

        def _worker():
            try:
                res = parsear_planilha_processo(path)
                if res and res.itens:
                    nome_c = f"Certame {res.numero_pregao or Path(path).stem} - {Path(path).name}"
                    self.after(0, lambda: self.app.adicionar_licitacao(nome_c, res))
                else:
                    self.after(0, lambda: messagebox.showwarning("Planilha Sem Itens Válidos", "Não foram encontrados itens com cabeçalho reconhecido na planilha."))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Erro ao Carregar Planilha", f"Falha ao ler a planilha: {exc}"))

        threading.Thread(target=_worker, daemon=True).start()

    def atualizar_lista_certames(self) -> None:
        cat = self.app.catalogo_licitacoes
        if not cat:
            self.cmb_certames.configure(values=["(Nenhum pregão carregado)"])
            self.cmb_certames.set("(Nenhum pregão carregado)")
            for row in self.tree_itens.get_children():
                self.tree_itens.delete(row)
            return

        lista_chaves = list(cat.keys())
        self.cmb_certames.configure(values=lista_chaves)
        certame_atual = self.app.certame_selecionado or lista_chaves[0]
        self.cmb_certames.set(certame_atual)
        self._exibir_certame(certame_atual)

    def _on_certame_selecionado(self, escolha: str) -> None:
        if escolha in self.app.catalogo_licitacoes:
            self.app.certame_selecionado = escolha
            self._exibir_certame(escolha)

    def _exibir_certame(self, nome_chave: str) -> None:
        res = self.app.catalogo_licitacoes.get(nome_chave)
        if not res:
            return

        for row in self.tree_itens.get_children():
            self.tree_itens.delete(row)

        for item in res.itens:
            val_ref_str = f"R$ {item.valor_referencia:,.2f}" if item.valor_referencia else "N/A"

            # Custo real e tipo de item
            tipo_enum = TipoItemEnum.SERVICO if (item.tipo_item and "serv" in item.tipo_item.lower()) else TipoItemEnum.PRODUTO
            if item.custo_real and item.custo_real > 0:
                custo_base = item.custo_real
            elif item.valor_referencia and item.valor_referencia > 0:
                custo_base = round(item.valor_referencia * (0.50 if tipo_enum == TipoItemEnum.SERVICO else 0.65), 2)
            else:
                custo_base = 100.0

            prec = PrecificacaoItem(
                tipo_item=tipo_enum,
                custo_base=custo_base,
                valor_referencia_edital=item.valor_referencia
            )

            piso_calc = item.piso_preco if (item.piso_preco and item.piso_preco > 0) else prec.piso_seguranca
            piso_str = f"R$ {piso_calc:,.2f}"
            prop_inicial = prec.preco_abertura
            if prop_inicial <= piso_calc:
                prop_inicial = round(piso_calc * 1.10, 2)
            prop_str = f"R$ {prop_inicial:,.2f}"
            margem_str = f"{prec.margem_liquida_minima_percentual:.1f}%"

            self.tree_itens.insert(
                "",
                "end",
                values=(
                    item.numero_item,
                    item.descricao[:100],
                    item.quantidade,
                    item.unidade_medida,
                    val_ref_str,
                    f"{prec.custo_total:.2f}",
                    margem_str,
                    piso_str,
                    prop_str,
                ),
            )

    def _remover_certame_atual(self) -> None:
        certame = self.cmb_certames.get()
        if certame in self.app.catalogo_licitacoes:
            del self.app.catalogo_licitacoes[certame]
            self.app.certame_selecionado = None
            self.atualizar_lista_certames()
            self.app.painel_dashboard.atualizar_catalogo()
            messagebox.showinfo("Removido", f"Licitação '{certame}' removida do catálogo atual.")


# ─── LOG HANDLER PARA INTERFACE TKINTER ──────────────────────────────────────
class TkinterLogHandler(logging.Handler):
    """Encaminha mensagens de log do Python diretamente para a caixa de log do PainelSessao."""
    def __init__(self, painel_sessao: PainelSessao) -> None:
        super().__init__()
        self.painel = painel_sessao

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if self.painel and hasattr(self.painel, "log"):
                self.painel.after(0, lambda m=msg: self.painel.log(m))
        except Exception:
            pass


# ─── PAINEL 3: CONSOLE DA SESSÃO AO VIVO ──────────────────────────────────────
class PainelSessao(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self._bot: Optional[BrowserBot] = None
        self._motor: Optional[MotorDecisao] = None
        self._thread_bot: Optional[threading.Thread] = None
        self._item_rows: dict[int, str] = {}  # Mapeia numero_item -> item_id no Treeview
        self._build()
        self._setup_logging()

    def _setup_logging(self) -> None:
        handler = TkinterLogHandler(self)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger_bot = logging.getLogger("browser_bot")
        logger_bot.addHandler(handler)
        logger_engine = logging.getLogger("engine")
        logger_engine.addHandler(handler)

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header Control Card
        card_ctrl = CardFrame(self)
        card_ctrl.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 10))
        card_ctrl.grid_columnconfigure(0, weight=1)

        ctrl_top = ctk.CTkFrame(card_ctrl, fg_color="transparent")
        ctrl_top.grid(row=0, column=0, sticky="ew", padx=16, pady=10)
        ctrl_top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            ctrl_top,
            text="🚀  Console de Disputa de Lances ao Vivo",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=COR["text_primary"],
        ).grid(row=0, column=0, sticky="w")

        # Seletor do Pregão para Disputa
        ctk.CTkLabel(
            ctrl_top,
            text="Disputar Licitação:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COR["cyan_primary"],
        ).grid(row=0, column=1, padx=(16, 4), sticky="e")

        self.cmb_disputa = ctk.CTkOptionMenu(
            ctrl_top,
            values=["(Nenhum pregão)"],
            width=260,
            command=self._on_disputa_mudou,
        )
        self.cmb_disputa.grid(row=0, column=2, padx=4, sticky="e")

        # Switch Simulação vs Real
        self.sw_modo = ctk.CTkSwitch(
            ctrl_top,
            text="Modo Simulação (Dry-Run)",
            font=ctk.CTkFont(weight="bold"),
            progress_color=COR["green_success"],
            command=self._on_modo_switch,
        )
        self.sw_modo.select()
        self.sw_modo.grid(row=0, column=3, padx=(16, 0), sticky="e")

        # Linha de URL do Comprasnet e Botões de Ação
        ctrl_actions = ctk.CTkFrame(card_ctrl, fg_color="transparent")
        ctrl_actions.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))
        ctrl_actions.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            ctrl_actions,
            text="🌐 Sala de Disputa / URL:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COR["text_secondary"],
        ).grid(row=0, column=0, padx=(0, 6), sticky="w")

        self.txt_url = ctk.CTkEntry(
            ctrl_actions,
            placeholder_text="https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/login",
            font=ctk.CTkFont(size=11),
        )
        self.txt_url.insert(0, "https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/login")
        self.txt_url.grid(row=0, column=1, padx=6, sticky="ew")

        self.btn_login_nativo = ctk.CTkButton(
            ctrl_actions,
            text="🔑 Login Gov.br",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COR["orange_warning"],
            text_color="#000000",
            height=34,
            width=115,
            command=self._fazer_login_nativo,
        )
        self.btn_login_nativo.grid(row=0, column=2, padx=(6, 4), sticky="e")

        self.btn_abrir_url = ctk.CTkButton(
            ctrl_actions,
            text="🌐 Abrir Sala",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COR["blue_accent"],
            text_color="#FFFFFF",
            height=34,
            width=95,
            command=self._navegar_manual,
        )
        self.btn_abrir_url.grid(row=0, column=3, padx=4, sticky="e")

        self.btn_iniciar = ctk.CTkButton(
            ctrl_actions,
            text="▶️  Iniciar Disputa",
            font=ctk.CTkFont(weight="bold"),
            fg_color=COR["green_success"],
            text_color="#000000",
            height=34,
            command=self._iniciar_sessao,
        )
        self.btn_iniciar.grid(row=0, column=4, padx=4, sticky="e")

        self.btn_parar = ctk.CTkButton(
            ctrl_actions,
            text="⏹️  Parar Disputa",
            font=ctk.CTkFont(weight="bold"),
            fg_color=COR["red_danger"],
            text_color="#FFFFFF",
            height=34,
            state="disabled",
            command=self._parar_sessao,
        )
        self.btn_parar.grid(row=0, column=5, padx=4, sticky="e")

        # Monitor de Itens na Disputa
        card_monitor = CardFrame(self)
        card_monitor.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 10))
        card_monitor.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card_monitor,
            text="📊  Status dos Itens na Disputa",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COR["text_primary"],
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(10, 4))

        tbl_f = ctk.CTkFrame(card_monitor, fg_color="transparent")
        tbl_f.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        tbl_f.grid_columnconfigure(0, weight=1)

        cols = ("num", "desc", "meu_lance", "menor_lance", "posicao", "status_pill")
        self.tree_sessao = ttk.Treeview(tbl_f, columns=cols, show="headings", height=6)
        self.tree_sessao.heading("num", text="Item")
        self.tree_sessao.heading("desc", text="Descrição do Produto/Serviço")
        self.tree_sessao.heading("meu_lance", text="Meu Último Lance")
        self.tree_sessao.heading("menor_lance", text="Líder de Mercado")
        self.tree_sessao.heading("posicao", text="Posição")
        self.tree_sessao.heading("status_pill", text="Status na Disputa")

        self.tree_sessao.column("num", width=50, anchor="center")
        self.tree_sessao.column("desc", width=320, anchor="w")
        self.tree_sessao.column("meu_lance", width=130, anchor="e")
        self.tree_sessao.column("menor_lance", width=130, anchor="e")
        self.tree_sessao.column("posicao", width=80, anchor="center")
        self.tree_sessao.column("status_pill", width=150, anchor="center")
        self.tree_sessao.pack(fill="x", expand=True)

        # Terminal Log Stream
        card_log = CardFrame(self)
        card_log.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
        card_log.grid_rowconfigure(1, weight=1)
        card_log.grid_columnconfigure(0, weight=1)

        log_header = ctk.CTkFrame(card_log, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(8, 2))
        log_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            log_header,
            text="📜  Log de Eventos & Auditoria da Disputa",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COR["text_primary"],
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            log_header,
            text="Limpar Log",
            width=80,
            height=24,
            fg_color=COR["bg_card_hover"],
            font=ctk.CTkFont(size=10),
            command=self._limpar_log,
        ).grid(row=0, column=1, sticky="e")

        self.txt_log = ctk.CTkTextbox(
            card_log,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=COR["bg_dark"],
            text_color=COR["text_primary"],
        )
        self.txt_log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

    def _on_modo_switch(self) -> None:
        dry_run = bool(self.sw_modo.get())
        if dry_run:
            self.sw_modo.configure(text="Modo Simulação (Dry-Run)", progress_color=COR["green_success"])
        else:
            self.sw_modo.configure(text="⚠️ MODO REAL (Envio de Lances)", progress_color=COR["red_danger"])

    def _on_disputa_mudou(self, escolha: str) -> None:
        self.atualizar_tabela_inicial()

    def atualizar_seletor_disputa(self) -> None:
        cat = self.app.catalogo_licitacoes
        if not cat:
            self.cmb_disputa.configure(values=["(Nenhum pregão)"])
            self.cmb_disputa.set("(Nenhum pregão)")
            self.atualizar_tabela_inicial()
            return

        chaves = list(cat.keys())
        self.cmb_disputa.configure(values=chaves)
        selecionado = self.app.certame_selecionado or chaves[0]
        self.cmb_disputa.set(selecionado)
        self.atualizar_tabela_inicial()

    def atualizar_tabela_inicial(self) -> None:
        """Preenche a tabela da disputa com os itens do certame selecionado em estado aguardando."""
        for r in self.tree_sessao.get_children():
            self.tree_sessao.delete(r)
        self._item_rows.clear()

        pregao = self.cmb_disputa.get()
        if not pregao or pregao == "(Nenhum pregão)" or pregao not in self.app.catalogo_licitacoes:
            return

        res = self.app.catalogo_licitacoes[pregao]

        # Auto-preenche a URL direta da sala de disputa
        url_sug = sugerir_url_disputa(res, pregao)
        if url_sug and hasattr(self, "txt_url"):
            self.txt_url.delete(0, "end")
            self.txt_url.insert(0, url_sug)

        for it in res.itens:
            v_ref_str = f"R$ {it.valor_referencia:,.2f}" if it.valor_referencia else "—"
            row_id = self.tree_sessao.insert(
                "",
                "end",
                values=(
                    it.numero_item,
                    it.descricao[:80],
                    "—",
                    v_ref_str,
                    "—",
                    "⏳ AGUARDANDO",
                ),
            )
            self._item_rows[it.numero_item] = row_id

    def _iniciar_sessao(self) -> None:
        pregao = self.cmb_disputa.get()
        if not pregao or pregao == "(Nenhum pregão)" or pregao not in self.app.catalogo_licitacoes:
            messagebox.showwarning(
                "Selecione um Pregão",
                "Carregue ou selecione um pregão válido na aba 'Editais & Custos' antes de iniciar a disputa.",
            )
            return

        res = self.app.catalogo_licitacoes[pregao]
        if not res.itens:
            messagebox.showwarning(
                "Certame Sem Itens",
                "O certame selecionado não possui itens válidos para disputa.",
            )
            return

        dry_run = bool(self.sw_modo.get())
        if not dry_run:
            confirma = messagebox.askyesno(
                "⚠️ CONFIRMAÇÃO DE MODO REAL",
                "ATENÇÃO: O modo REAL está ativado!\n\n"
                "Lances REAIS serão enviados ao Compras.gov.br com valor legal e financeiro.\n"
                "Certifique-se de que todos os custos e stop-losses estão corretos.\n\n"
                "Deseja realmente iniciar a disputa em MODO REAL?",
                icon="warning",
            )
            if not confirma:
                self.log("⚠️ Inicialização em modo real cancelada pelo operador.")
                return

        # Monta a lista de RegraItem
        regras: list[RegraItem] = []
        for item in res.itens:
            tipo_enum = TipoItemEnum.SERVICO if (item.tipo_item and "serv" in item.tipo_item.lower()) else TipoItemEnum.PRODUTO
            if item.custo_real and item.custo_real > 0:
                custo_base = item.custo_real
            elif item.valor_referencia and item.valor_referencia > 0:
                custo_base = round(item.valor_referencia * (0.50 if tipo_enum == TipoItemEnum.SERVICO else 0.65), 2)
            else:
                custo_base = 100.0

            prec = PrecificacaoItem(
                tipo_item=tipo_enum,
                custo_base=custo_base,
                valor_referencia_edital=item.valor_referencia,
            )

            piso_calc = item.piso_preco if (item.piso_preco and item.piso_preco > 0) else prec.piso_seguranca
            preco_ini = prec.preco_abertura
            if preco_ini <= piso_calc:
                preco_ini = round(piso_calc * 1.10, 2)

            regras.append(
                RegraItem(
                    item_id=str(uuid.uuid4()),
                    numero_item=item.numero_item,
                    descricao=item.descricao,
                    custo_unitario=prec.custo_total,
                    margem_minima_percent=prec.margem_liquida_minima_percentual,
                    preco_minimo=piso_calc,
                    preco_inicial_proposta=preco_ini,
                    intervalo_minimo=item.decremento_minimo or 0.01,
                    tipo_intervalo=TipoIntervalo.MONETARIO,
                    valor_referencia=item.valor_referencia,
                    licitacao_id=res.numero_pregao or "0",
                )
            )

        sessao_id = str(uuid.uuid4())
        url_alvo = self.txt_url.get().strip() or "https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/login"

        try:
            self._motor = MotorDecisao(dry_run=dry_run)
            self._motor.carregar_regras(regras)
            self._motor.iniciar_sessao()

            self._bot = BrowserBot(
                motor=self._motor,
                sessao_id=sessao_id,
                licitacao_id=res.numero_pregao,
                callback_ciclo=self._on_ciclo_update,
            )
        except Exception as exc:
            messagebox.showerror("Erro ao Configurar Robô", f"Falha ao inicializar motor: {exc}")
            return

        self.btn_iniciar.configure(state="disabled")
        self.btn_parar.configure(state="normal")

        msg_modo = "🛡️ SIMULAÇÃO (Dry-Run)" if dry_run else "🔴 DISPUTA REAL (Envio de Lances)"
        self.log(f"🚀 Iniciando robô para '{pregao}' | {msg_modo}")
        self.log(f"   Sessão ID: {sessao_id[:13]}... | {len(regras)} itens carregados | URL: {url_alvo}")

        def _worker():
            try:
                if self._bot:
                    self._bot.iniciar(url_pregao=url_alvo, intervalo_polling_s=5.0)
            except Exception as exc_bot:
                logger.exception("Erro crítico durante a execução do robô")
                self.after(0, lambda: self.log(f"❌ Erro crítico no robô: {exc_bot}"))
            finally:
                self.after(0, self._on_bot_finalizado)

        self._thread_bot = threading.Thread(target=_worker, daemon=True)
        self._thread_bot.start()

    def _fazer_login_nativo(self) -> None:
        url = self.txt_url.get().strip() or "https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/login"
        self.log("🔑 Abrindo Google Chrome nativo para login no Gov.br...")
        sucesso = abrir_login_nativo(url)
        if sucesso:
            self.log("✅ Janela do Google Chrome aberta com o perfil do robô.")
            self.log("💡 Realize o login normalmente por CPF, QR Code ou Banco.")
            self.log("👉 Quando estiver autenticado, clique em '▶️ Iniciar Disputa'!")
        else:
            messagebox.showerror("Erro ao Abrir Navegador", "Google Chrome não encontrado no sistema.")

    def _navegar_manual(self) -> None:
        url = self.txt_url.get().strip()
        if not url:
            return
        if self._bot:
            self._bot.navegar_para(url)
            self.log(f"🌐 Solicitada abertura manual da URL: {url}")
        else:
            self.log("ℹ️ Inicie a disputa com '▶️ Iniciar Disputa' para abrir o navegador.")

    def _parar_sessao(self) -> None:
        if self._bot:
            self._bot.parar()
            self.log("⏹️ Comando de parada enviado. Encerrando sessão...")
            self.btn_parar.configure(state="disabled")
        else:
            self.log("⏹️ Nenhum robô ativo no momento.")

    def _on_bot_finalizado(self) -> None:
        self.btn_iniciar.configure(state="normal")
        self.btn_parar.configure(state="disabled")
        self.log("🏁 Sessão de monitoramento finalizada.")

    def _on_ciclo_update(self, ciclo: int, estados: list[Any], decisoes: dict[int, Any]) -> None:
        self.after(0, lambda: self._render_ciclo_ui(ciclo, estados, decisoes))

    def _render_ciclo_ui(self, ciclo: int, estados: list[Any], decisoes: dict[int, Any]) -> None:
        liderando_cnt = 0
        piso_cnt = 0

        for ep in estados:
            num = ep.numero_item
            dec = decisoes.get(num)
            meu_lance = ep.nosso_ultimo_lance
            menor_lance = ep.melhor_lance_mercado
            pos = ep.nossa_posicao

            meu_lance_str = f"R$ {meu_lance:,.2f}" if meu_lance is not None else "—"
            menor_lance_str = f"R$ {menor_lance:,.2f}" if menor_lance is not None else "—"
            pos_str = f"{pos}º" if pos is not None else "—"

            # Determina badge de status
            status_txt = "⏳ DISPUTANDO"
            if dec:
                if dec.bloqueado_stop_loss:
                    status_txt = "🛑 STOP-LOSS"
                    piso_cnt += 1
                elif dec.deve_dar_lance and dec.valor_lance is not None:
                    status_txt = f"⚡ LANCE R$ {dec.valor_lance:,.2f}"
                elif dec.motivo == MotivoBloqueio.JA_VENCENDO or (pos == 1):
                    status_txt = "🏆 LIDERANDO"
                    liderando_cnt += 1
                elif dec.motivo == MotivoBloqueio.SUPERADO or (pos and pos > 1):
                    status_txt = "🟡 SUPERADO"

            if num in self._item_rows:
                row_id = self._item_rows[num]
                desc_atual = self.tree_sessao.item(row_id)["values"][1]
                self.tree_sessao.item(
                    row_id,
                    values=(num, desc_atual, meu_lance_str, menor_lance_str, pos_str, status_txt),
                )

        # Atualiza métricas no Dashboard
        if hasattr(self.app, "painel_dashboard"):
            self.app.painel_dashboard.card_liderando.atualizar(str(liderando_cnt))
            self.app.painel_dashboard.card_stop_loss.atualizar(str(piso_cnt))

    def _limpar_log(self) -> None:
        self.txt_log.delete("1.0", "end")

    def log(self, msg: str) -> None:
        t_str = time.strftime("%H:%M:%S")
        self.txt_log.insert("end", f"[{t_str}] {msg}\n")
        self.txt_log.see("end")



# ─── PAINEL 4: OPORTUNIDADES PNCP ─────────────────────────────────────────────
class PainelPNCP(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self._build()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Form de Filtros
        card_filtros = CardFrame(self)
        card_filtros.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 10))
        card_filtros.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(card_filtros, text="🌐  Pesquisa de Licitações no Portal Nacional (PNCP)", font=ctk.CTkFont(size=14, weight="bold"), text_color=COR["text_primary"]).grid(row=0, column=0, columnspan=4, sticky="w", padx=16, pady=(10, 8))

        ctk.CTkLabel(card_filtros, text="Estado (UF):", font=ctk.CTkFont(size=12)).grid(row=1, column=0, padx=(16, 4), pady=8, sticky="w")
        self.cmb_uf = ctk.CTkComboBox(card_filtros, values=["TO", "SP", "DF", "RJ", "MG", "BA", "GO", "TODAS"], width=100)
        self.cmb_uf.set("TO")
        self.cmb_uf.grid(row=1, column=1, padx=4, pady=8, sticky="w")

        ctk.CTkLabel(card_filtros, text="Palavra-chave:", font=ctk.CTkFont(size=12)).grid(row=1, column=2, padx=(16, 4), pady=8, sticky="w")
        self.txt_kw = ctk.CTkEntry(card_filtros, placeholder_text="Ex: computador, limpeza, expediente...", width=260)
        self.txt_kw.grid(row=1, column=3, padx=4, pady=8, sticky="ew")

        ctk.CTkButton(card_filtros, text="🔍 Pesquisar PNCP", fg_color=COR["purple_accent"], text_color="#FFFFFF", font=ctk.CTkFont(weight="bold"), height=34, command=self._buscar_pncp).grid(row=1, column=4, padx=(8, 16), pady=8)

        # Resultados
        card_res = CardFrame(self)
        card_res.grid(row=1, column=0, sticky="nsew")
        card_res.grid_rowconfigure(1, weight=1)
        card_res.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card_res, text="📋  Oportunidades Encontradas", font=ctk.CTkFont(size=14, weight="bold"), text_color=COR["text_primary"]).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 6))

        tbl_f = ctk.CTkFrame(card_res, fg_color="transparent")
        tbl_f.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        tbl_f.grid_columnconfigure(0, weight=1)
        tbl_f.grid_rowconfigure(0, weight=1)

        cols = ("orgao", "objeto", "modalidade", "valor")
        self.tree_pncp = ttk.Treeview(tbl_f, columns=cols, show="headings")
        self.tree_pncp.heading("orgao", text="Órgão Entidade")
        self.tree_pncp.heading("objeto", text="Objeto da Compra")
        self.tree_pncp.heading("modalidade", text="Modalidade")
        self.tree_pncp.heading("valor", text="Valor Estimado (R$)")

        self.tree_pncp.column("orgao", width=220, anchor="w")
        self.tree_pncp.column("objeto", width=380, anchor="w")
        self.tree_pncp.column("modalidade", width=120, anchor="center")
        self.tree_pncp.column("valor", width=120, anchor="e")
        self.tree_pncp.grid(row=0, column=0, sticky="nsew")

        sb = ctk.CTkScrollbar(tbl_f, orientation="vertical", command=self.tree_pncp.yview)
        self.tree_pncp.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

    def _buscar_pncp(self) -> None:
        uf = self.cmb_uf.get()
        kw = self.txt_kw.get()

        def _worker():
            try:
                res = buscar_publicacoes_pncp(uf=uf, palavra_chave=kw, tamanho_pagina=20)
                itens = res.get("data", [])
                self.after(0, lambda: self._preencher_pncp(itens))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Erro PNCP", str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def _preencher_pncp(self, itens: list[dict]) -> None:
        for r in self.tree_pncp.get_children():
            self.tree_pncp.delete(r)

        for i in itens:
            orgao = i.get("orgaoEntidade", {}).get("razaoSocial", "N/A")
            objeto = i.get("objetoCompra", "Sem descrição")[:120]
            mod = i.get("modalidadeNome", "Pregão Eletrônico")
            val = i.get("valorTotalEstimado")
            val_str = f"R$ {val:,.2f}" if val else "N/A"

            self.tree_pncp.insert("", "end", values=(orgao[:40], objeto, mod, val_str))

        messagebox.showinfo("PNCP", f"Encontradas {len(itens)} oportunidade(s) no PNCP!")


# ─── APLICAÇÃO PRINCIPAL (APP MULTI-CERTAME) ──────────────────────────────────
class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("🤖 Robô de Licitações – Multi-Pregão & Dispensas (Lei 14.133/2021)")
        self.geometry("1280x800")
        self.minsize(1024, 720)
        self.configure(fg_color=COR["bg_dark"])

        # Catálogo Dinâmico Multi-Certame
        self.catalogo_licitacoes: Dict[str, ResultadoEdital] = {}
        self.certame_selecionado: Optional[str] = None

        self._paineis: dict[str, ctk.CTkFrame] = {}
        self._build_ui()

    def adicionar_licitacao(self, nome_chave: str, res: ResultadoEdital) -> None:
        """Adiciona um pregão/dispensa ao catálogo diário sem apagar os anteriores."""
        self.catalogo_licitacoes[nome_chave] = res
        self.certame_selecionado = nome_chave

        # Atualiza a interface
        self.painel_dashboard.atualizar_catalogo()
        self.painel_editais.atualizar_lista_certames()
        self.painel_sessao.atualizar_seletor_disputa()

        messagebox.showinfo("Licitação Adicionada", f"✅ '{nome_chave}' adicionada com sucesso!\nTotal de certames ativos hoje: {len(self.catalogo_licitacoes)}")

    def _build_ui(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # Sidebar
        sidebar = ctk.CTkFrame(self, fg_color=COR["bg_card"], width=220, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(9, weight=1)

        lbl_logo = ctk.CTkLabel(sidebar, text="🤖  ROBÔ LICITAÇÕES", font=ctk.CTkFont(size=16, weight="bold"), text_color=COR["cyan_primary"])
        lbl_logo.grid(row=0, column=0, padx=16, pady=(16, 20), sticky="w")

        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        abas = [
            ("📊  Dashboard", "dashboard"),
            ("📄  Editais & Custos", "editais"),
            ("⚡  Console da Sessão", "sessao"),
            ("🌐  Oportunidades PNCP", "pncp"),
        ]

        for idx, (nome, id_aba) in enumerate(abas, 1):
            btn = ctk.CTkButton(
                sidebar,
                text=nome,
                font=ctk.CTkFont(size=12, weight="bold"),
                anchor="w",
                fg_color="transparent",
                text_color=COR["text_secondary"],
                hover_color=COR["bg_card_hover"],
                height=38,
                command=lambda n=nome: self.selecionar_aba(n),
            )
            btn.grid(row=idx, column=0, sticky="ew", padx=10, pady=2)
            self._nav_buttons[nome] = btn

        lbl_status_lic = ctk.CTkLabel(sidebar, text="✅ Licença Ativa (Full)", font=ctk.CTkFont(size=11), text_color=COR["green_success"])
        lbl_status_lic.grid(row=9, column=0, padx=16, pady=16, sticky="w")

        # Container de Conteúdo
        self.tab_container = ctk.CTkFrame(self, fg_color="transparent")
        self.tab_container.grid(row=0, column=1, sticky="nsew", padx=16, pady=16)
        self.tab_container.grid_rowconfigure(0, weight=1)
        self.tab_container.grid_columnconfigure(0, weight=1)

        # Painéis
        self.painel_dashboard = PainelDashboard(self.tab_container, app=self)
        self.painel_editais = PainelEditais(self.tab_container, app=self)
        self.painel_sessao = PainelSessao(self.tab_container, app=self)
        self.painel_pncp = PainelPNCP(self.tab_container, app=self)

        self._paineis["📊  Dashboard"] = self.painel_dashboard
        self._paineis["📄  Editais & Custos"] = self.painel_editais
        self._paineis["⚡  Console da Sessão"] = self.painel_sessao
        self._paineis["🌐  Oportunidades PNCP"] = self.painel_pncp

        for p in self._paineis.values():
            p.grid(row=0, column=0, sticky="nsew")

        self.selecionar_aba("📊  Dashboard")

    def selecionar_aba(self, nome_aba: str) -> None:
        for n, btn in self._nav_buttons.items():
            if n == nome_aba:
                btn.configure(fg_color=COR["bg_card_hover"], text_color=COR["cyan_primary"])
            else:
                btn.configure(fg_color="transparent", text_color=COR["text_secondary"])

        if nome_aba in self._paineis:
            self._paineis[nome_aba].tkraise()

    def log(self, nivel: str, msg: str) -> None:
        logger.info(f"[{nivel}] {msg}")


def run_gui() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    run_gui()
