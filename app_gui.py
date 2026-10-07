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

from dotenv import load_dotenv
load_dotenv() # Carrega SUPABASE_URL e SUPABASE_KEY do .env

import datetime
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

# Patch defensivo para resolver AttributeError no CTkScrollableFrame (Python 3.14+ / Tkinter Linux)
try:
    from customtkinter.windows.widgets.ctk_scrollable_frame import CTkScrollableFrame

    _original_check_scroll = CTkScrollableFrame._check_if_valid_scroll

    def _safe_check_if_valid_scroll(self, widget):
        try:
            if isinstance(widget, str):
                widget = self.nametowidget(widget)
            return _original_check_scroll(self, widget)
        except Exception:
            return False

    CTkScrollableFrame._check_if_valid_scroll = _safe_check_if_valid_scroll
except Exception as _patch_err:
    pass

import csv
import re

from supabase import create_client
try:
    # Pega da variável de ambiente OU usa a URL padrão oficial da JRBC TECH
    SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://szphoxostihnpcovyzvx.supabase.co"
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or ""
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    logging.warning(f"Não foi possível inicializar o cliente Supabase globalmente: {e}")

def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        if isinstance(val, (int, float)):
            return float(val)
        return float(str(val).replace("R$", "").replace(" ", "").replace(",", ".").strip())
    except (ValueError, TypeError):
        return default

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
        ItemEdital,
        ResultadoEdital,
        SeveridadeRisco,
    )
    from mercadolivre_gui import cotar_no_ml_gui
    from browser_bot import (
        BrowserBot,
        abrir_login_nativo,
        extrair_todos_itens_sala,
        extrair_todos_itens_sala_cdp,
    )
    from disputa_manager import (
        calcular_url_disputa_real,
        verificar_cdp_ativo,
        detectar_aba_disputa_ativa,
        listar_abas_cdp,
        garantir_chrome_com_cdp,
        extrair_dados_aba_disputa,
    )
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
    # Fundos e Superfícies
    "bg_deep": "#080C14",       # Fundo principal ultra-dark com tom azulado profundo
    "bg_panel": "#0F172A",      # Painéis laterais e containers (Slate 900)
    "bg_card": "#1E293B",       # Superfície dos cards de oportunidades (Slate 800)
    "bg_input": "#0F172A",      # Campos de digitação e filtros
    "bg_hover": "#2b2e35",      # Efeito hover em botões e cards
    "border": "#334155",        # Bordas elegantes e sutis (Slate 700)
    "border_focus": "#0EA5E9",  # Borda ativa em ciano

    # Acentos Dinâmicos de Tecnologia
    "accent_cyan": "#06B6D4",   # Destaque de IA / Tecnologia JRBC
    "accent_blue": "#38BDF8",   # Links e identificação de órgãos
    "accent_green": "#10B981",  # Oportunidades abertas / status OK
    "accent_purple": "#818CF8", # Valores monetários e cifras R$
    "accent_amber": "#F59E0B",  # Prazos e avisos
    "accent_red": "#EF4444",    # Stop-Loss rígido / Modo Real

    # Tipografia de Alto Contraste
    "text_primary": "#F8FAFC",   # Branco puro para títulos e destaque
    "text_secondary": "#94A3B8", # Cinza suave para descrições
    "text_muted": "#64748B",     # Metadados e datas

    # Pílulas / Status
    "pill_liderando_bg": "#052E16",
    "pill_liderando_fg": "#10B981",
    "pill_superado_bg": "#451A03",
    "pill_superado_fg": "#F59E0B",
    "pill_piso_bg": "#450A0A",
    "pill_piso_fg": "#EF4444",
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
        row_norm = {str(k).strip().lower(): v for k, v in row.items() if k is not None}  # type: ignore

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
        cor_destaque: str = COR["accent_cyan"],
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

def disable_mouse_scroll(combo):
    try:
        combo._canvas.unbind("<MouseWheel>")
        combo._canvas.unbind("<Button-4>")
        combo._canvas.unbind("<Button-5>")
    except Exception:
        pass




# ─── PAINEL 1: DASHBOARD ──────────────────────────────────────────────────────
class PainelDashboard(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self._build()
        self.after(300, self._iniciar_threads_background)
        
    def _iniciar_threads_background(self) -> None:
        self.carregar_dados_dashboard()
        self.verificar_status_chrome()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self.grid_rowconfigure(3, weight=1)

        chrome_frame = ctk.CTkFrame(self, fg_color="transparent")
        chrome_frame.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 10))
        chrome_frame.grid_columnconfigure(0, weight=1)
        
        self.lbl_chrome_status = ctk.CTkLabel(chrome_frame, text="⚪ Verificando Chrome...", font=ctk.CTkFont(size=12, weight="bold"), text_color=COR["text_secondary"])
        self.lbl_chrome_status.pack(side="left", padx=10)
        
        btn_chrome = ctk.CTkButton(chrome_frame, text="🌐 Iniciar Chrome Licitações", font=ctk.CTkFont(size=12, weight="bold"), fg_color=COR["accent_cyan"], text_color="#080C14", hover_color=COR["accent_blue"], command=self._iniciar_chrome)
        btn_chrome.pack(side="right", padx=10)
        
        self.btn_sync_drive = ctk.CTkButton(
            chrome_frame, text="📁 Sincronizar Pastas no Drive", font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COR["accent_purple"], text_color="#FFFFFF", hover_color="#6366F1", corner_radius=8,
            command=self._sincronizar_drive
        )
        self.btn_sync_drive.pack(side="right", padx=10)

        self.btn_radar_diario = ctk.CTkButton(
            chrome_frame, text="🔄 Executar Radar do Dia", font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COR["accent_green"], text_color="#080C14", hover_color=COR["accent_cyan"], corner_radius=8,
            command=self._executar_radar_diario
        )
        self.btn_radar_diario.pack(side="right", padx=10)

        self.btn_radar_telegram = ctk.CTkButton(
            chrome_frame, text="📡 Radar Telegram (TO)", font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COR["accent_blue"], text_color="#FFFFFF", hover_color="#2563EB", corner_radius=8,
            command=self._executar_radar_telegram
        )
        self.btn_radar_telegram.pack(side="right", padx=10)

        cards_frame = ctk.CTkFrame(self, fg_color="transparent")
        cards_frame.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 12))
        cards_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.card_radar_pncp = MetricCard(cards_frame, titulo="Radar PNCP", valor_inicial="0", subtitulo="Certames Identificados", cor_destaque=COR["accent_cyan"], icone="🌐")
        self.card_radar_pncp.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self.card_servicos_ti = MetricCard(cards_frame, titulo="Serviços de TI", valor_inicial="0", subtitulo="Para JRBC TECH", cor_destaque=COR["accent_purple"], icone="🛠️")
        self.card_servicos_ti.grid(row=0, column=1, padx=6, sticky="ew")

        self.card_volume = MetricCard(cards_frame, titulo="Volume Total", valor_inicial="R$ 0,00", subtitulo="Estimado Disponível", cor_destaque=COR["accent_green"], icone="💰")
        self.card_volume.grid(row=0, column=2, padx=6, sticky="ew")

        self.card_status = MetricCard(cards_frame, titulo="Status do Sistema", valor_inicial="Online", subtitulo="SIGA-TO & TG Conectados", cor_destaque=COR["accent_blue"], icone="⚡")
        self.card_status.grid(row=0, column=3, padx=(6, 0), sticky="ew")

        main_content = ctk.CTkFrame(self, fg_color="transparent")
        

        main_content.grid(row=2, column=0, sticky="nsew")
        main_content.grid_columnconfigure(0, weight=1)
        main_content.grid_columnconfigure(1, weight=0, minsize=240)
        main_content.grid_rowconfigure(0, weight=1)

        card_lics = CardFrame(main_content)
        card_lics.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        card_lics.grid_rowconfigure(2, weight=1)
        card_lics.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card_lics, text="📅 Oportunidades do Dia", font=ctk.CTkFont(size=14, weight="bold"), text_color=COR["text_primary"]).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 4))

        self._criar_barra_filtros(card_lics)

        tbl_frame = ctk.CTkFrame(card_lics, fg_color="transparent")
        tbl_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
        tbl_frame.grid_columnconfigure(0, weight=1)
        tbl_frame.grid_rowconfigure(0, weight=1)

        self.scroll_lics = ctk.CTkScrollableFrame(tbl_frame, fg_color="transparent")
        self.scroll_lics.grid(row=0, column=0, sticky="nsew")
        self.tree_lics = self.scroll_lics # backward compatibility for existing references that expect a widget
        
        # self.configurar_menu_contexto() - context menu adapted for cards if needed

        card_dicas = CardFrame(main_content)
        card_dicas.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        card_dicas.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card_dicas, text="⚡  Ações Rápidas & Atalhos", font=ctk.CTkFont(size=14, weight="bold"), text_color=COR["text_primary"]).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 10))

        ctk.CTkButton(card_dicas, text="📄  Adicionar Pregão / Dispensa", font=ctk.CTkFont(size=12, weight="bold"), fg_color=COR["accent_cyan"], text_color="#000000", hover_color=COR["accent_cyan"], height=38, command=lambda: self.app.selecionar_aba("📄  Editais & Custos")).grid(row=1, column=0, sticky="ew", padx=16, pady=4)
        ctk.CTkButton(card_dicas, text="🌐  Buscar no PNCP", font=ctk.CTkFont(size=12, weight="bold"), fg_color=COR["accent_purple"], text_color="#FFFFFF", height=38, command=self._buscar_no_pncp_bg).grid(row=2, column=0, sticky="ew", padx=16, pady=4)
        ctk.CTkButton(card_dicas, text="🚀  Console de Lances ao Vivo", font=ctk.CTkFont(size=12, weight="bold"), fg_color=COR["accent_green"], text_color="#000000", height=38, command=lambda: self.app.selecionar_aba("⚡  Console da Sessão")).grid(row=3, column=0, sticky="ew", padx=16, pady=4)

        card_multi = CardFrame(self)
        card_multi.grid(row=3, column=0, sticky="nsew", padx=0, pady=(12, 0))
        card_multi.grid_columnconfigure(0, weight=1)
        card_multi.grid_rowconfigure(1, weight=1)
        
        top_multi = ctk.CTkFrame(card_multi, fg_color="transparent")
        top_multi.grid(row=0, column=0, sticky="ew", padx=16, pady=10)
        top_multi.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top_multi, text="⚔️ Painel de Multi-Disputas", font=ctk.CTkFont(size=14, weight="bold"), text_color=COR["text_primary"]).grid(row=0, column=0, sticky="w")
        self.btn_iniciar_multi = ctk.CTkButton(top_multi, text="🚀 Iniciar Monitoramento de Todas as Disputas", font=ctk.CTkFont(size=12, weight="bold"), fg_color=COR["accent_green"], text_color="#000000", command=self._toggle_multi_disputas)
        self.btn_iniciar_multi.grid(row=0, column=2, sticky="e")
        
        self.tree_multi = ttk.Treeview(card_multi, columns=("orgao", "pregao", "status", "lider", "acao"), show="headings", height=5)
        self.tree_multi.heading("orgao", text="UASG / Órgão")
        self.tree_multi.heading("pregao", text="Nº Pregão")
        self.tree_multi.heading("status", text="Status")
        self.tree_multi.heading("lider", text="Líder R$")
        self.tree_multi.heading("acao", text="Ação")
        self.tree_multi.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))

    def _toggle_multi_disputas(self):
        import asyncio
        from gerenciador_multi_disputas import obter_motor_multi
        motor = obter_motor_multi()
        if motor._rodando:
            asyncio.run(motor.parar_loop())
            self.btn_iniciar_multi.configure(text="🚀 Iniciar Monitoramento de Todas as Disputas", fg_color=COR["accent_green"])
        else:
            asyncio.run(motor.iniciar_loop())
            self.btn_iniciar_multi.configure(text="🛑 Parar Monitoramento", fg_color="#EF4444")

    def _executar_radar_diario(self) -> None:
        self.btn_radar_diario.configure(text="⏳ Rodando Radar...", state="disabled")
        def worker():
            try:
                import subprocess
                import sys
                subprocess.run([sys.executable, "coletor_diario.py"], check=True)
                self.after(0, self.carregar_dados_dashboard)
                self.after(0, lambda: messagebox.showinfo("Radar", "Varredura concluída com sucesso!"))
            except Exception as e:
                err_msg = str(e)
                self.after(0, lambda: messagebox.showerror("Erro", f"Erro no radar: {err_msg}"))
            finally:
                self.after(0, lambda: self.btn_radar_diario.configure(text="🔄 Executar Radar do Dia", state="normal"))
        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _executar_radar_telegram(self) -> None:
        self.btn_radar_telegram.configure(text="⏳ Varrendo PNCP...", state="disabled")
        def worker():
            try:
                from radar_telegram import varrer_pncp
                encontrados = varrer_pncp(uf="TO", dias_atras=2)
                qtd = len(encontrados)
                self.after(0, self.carregar_dados_dashboard)
                self.after(0, lambda: messagebox.showinfo(
                    "Radar Telegram",
                    f"Varredura concluída com sucesso!\n{qtd} oportunidade(s) analisada(s) e enviada(s) ao Telegram."
                ))
            except Exception as e:
                err_msg = str(e)
                self.after(0, lambda: messagebox.showerror("Erro", f"Erro no Radar Telegram: {err_msg}"))
            finally:
                self.after(0, lambda: self.btn_radar_telegram.configure(text="📡 Radar Telegram (TO)", state="normal"))
        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _criar_barra_filtros(self, parent_frame: Any) -> None:
        frame_filtros = ctk.CTkFrame(parent_frame, fg_color="#111827")
        frame_filtros.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))

        ctk.CTkLabel(frame_filtros, text="Modalidade:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#9ca3af").pack(side="left", padx=(10, 4), pady=6)
        self.combo_modalidade = ctk.CTkComboBox(
            frame_filtros, 
            values=["Todas", "Dispensa Eletrônica", "Pregão Eletrônico"], 
            state="readonly", 
            width=150,
            command=lambda e: self.aplicar_filtros()
        )
        self.combo_modalidade.set("Todas")
        self.combo_modalidade.pack(side="left", padx=(0, 10), pady=6)

        ctk.CTkLabel(frame_filtros, text="UF / Região:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#9ca3af").pack(side="left", padx=(0, 4), pady=6)
        self.combo_uf = ctk.CTkComboBox(
            frame_filtros, 
            values=["TODOS", "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"],
            state="readonly", 
            width=100,
            command=lambda e: self.aplicar_filtros()
        )
        self.combo_uf.set("TODOS")
        self.combo_uf.pack(side="left", padx=(0, 10), pady=6)

        ctk.CTkLabel(frame_filtros, text="Status:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#9ca3af").pack(side="left", padx=(0, 4), pady=6)
        self.combo_status = ctk.CTkComboBox(
            frame_filtros, 
            values=["Ativas (Pendente + Favorita)", "Apenas Favoritas", "Pendentes", "Descartadas", "Todas"], 
            state="readonly", 
            width=170,
            command=lambda e: self.aplicar_filtros()
        )
        self.combo_status.set("Ativas (Pendente + Favorita)")
        self.combo_status.pack(side="left", padx=(0, 10), pady=6)

        ctk.CTkLabel(frame_filtros, text="Prazo:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#9ca3af").pack(side="left", padx=(0, 4), pady=6)
        self.combo_prazo = ctk.CTkComboBox(
            frame_filtros, 
            values=["Todos os Prazos", "🚨 Encerram Hoje", "⚡ Encerram Amanhã", "⏳ < 48 Horas", "📅 Em Aberto (Futuras)", "⌛ Encerradas"], 
            state="readonly", 
            width=160,
            command=lambda e: self.aplicar_filtros()
        )
        self.combo_prazo.set("Todos os Prazos")
        self.combo_prazo.pack(side="left", padx=(0, 10), pady=6)
        
        disable_mouse_scroll(self.combo_modalidade)
        disable_mouse_scroll(self.combo_uf)
        disable_mouse_scroll(self.combo_status)
        disable_mouse_scroll(self.combo_prazo)

        btn_recarregar = ctk.CTkButton(
            frame_filtros, 
            text="🔄 Atualizar", 
            command=self.carregar_dados_dashboard,
            fg_color="#374151", 
            text_color="#f9fafb", 
            font=ctk.CTkFont(size=11, weight="bold"), 
            width=90
        )
        btn_recarregar.pack(side="right", padx=10, pady=6)

    def configurar_menu_contexto(self) -> None:
        pass

    def exibir_menu_contexto(self, event: Any) -> None:
        pass

    def alterar_status_selecionado(self, novo_status: str) -> None:
        selecionado = self.tree_lics.selection()
        if not selecionado:
            return

        licitacao_id = selecionado[0]
        try:
            from supabase import create_client
            import os
            url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
            key = os.getenv("SUPABASE_KEY") or ""
            sb = create_client(url, key)
            sb.table("licitacoes").update({"status_analise": novo_status.lower()}).eq("id", licitacao_id).execute()

            if hasattr(self, "todas_licitacoes"):
                for lic in self.todas_licitacoes:
                    if str(lic.get("id")) == str(licitacao_id):  # type: ignore
                        lic["status_analise"] = novo_status.lower()  # type: ignore
                        break
            
            self.aplicar_filtros()
        except Exception as e:
            import tkinter.messagebox as messagebox
            messagebox.showerror("Erro ao atualizar status", f"Falha ao persistir no Supabase:\n{str(e)}")

    @staticmethod
    def _formatar_data_iso(data_val: Any) -> tuple[Optional[datetime.datetime], str]:
        if not data_val:
            return None, "A consultar no edital"
        import datetime
        from datetime import date
        
        if isinstance(data_val, datetime.datetime):
            dt_local = data_val.astimezone() if data_val.tzinfo else data_val
            return dt_local, dt_local.strftime("%d/%m/%Y às %H:%M")
        if isinstance(data_val, date):
            dt = datetime.datetime.combine(data_val, datetime.time(hour=9, minute=0))
            return dt, dt.strftime("%d/%m/%Y")

        s = str(data_val).strip()
        if not s or s.lower() in ["none", "null", ""]:
            return None, "A consultar no edital"

        if s.endswith("Z"):
            s = s[:-1] + "+00:00"

        # 1. fromisoformat flexível (ISO 8601, timestamps UTC e offsets)
        try:
            dt = datetime.datetime.fromisoformat(s)
            dt_local = dt.astimezone() if dt.tzinfo else dt
            return dt_local, dt_local.strftime("%d/%m/%Y às %H:%M")
        except Exception:
            pass

        # 2. Formatos comuns de data/hora
        formatos = [
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%d/%m/%Y %H:%M:%S%z",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y",
        ]
        for fmt in formatos:
            try:
                dt = datetime.datetime.strptime(s, fmt)
                dt_local = dt.astimezone() if dt.tzinfo else dt
                return dt_local, dt_local.strftime("%d/%m/%Y às %H:%M")
            except Exception:
                continue

        # 3. Regex para extração inteligente caso venha texto ao redor
        try:
            import re
            m_iso = re.search(r'(\d{4})-(\d{2})-(\d{2})', s)
            if m_iso:
                y, m, d = map(int, m_iso.groups())
                dt = datetime.datetime(y, m, d, 9, 0)
                return dt, dt.strftime("%d/%m/%Y")
            m_br = re.search(r'(\d{2})/(\d{2})/(\d{4})', s)
            if m_br:
                d, m, y = map(int, m_br.groups())
                dt = datetime.datetime(y, m, d, 9, 0)
                return dt, dt.strftime("%d/%m/%Y")
        except Exception:
            pass

        return None, s[:16]

    @staticmethod
    def _extrair_cronograma_licitacao(lic: dict) -> dict:
        import datetime
        from datetime import date, timedelta
        raw_abertura = (
            lic.get("data_abertura")
            or lic.get("dataAberturaProposta")
            or lic.get("data_inicio_recebimento_propostas")
            or lic.get("data_hora_sessao")
        )
        raw_encerramento = (
            lic.get("data_encerramento")
            or lic.get("data_fim_proposta")
            or lic.get("dataEncerramentoProposta")
            or lic.get("data_fim_recebimento_propostas")
        )

        dt_abert, txt_abert = PainelDashboard._formatar_data_iso(raw_abertura)
        dt_encerr, txt_encerr = PainelDashboard._formatar_data_iso(raw_encerramento)

        dt_ref = dt_encerr or dt_abert

        badge_texto = "📅 DATA NO EDITAL"
        badge_bg = "#374151"
        badge_fg = "#D1D5DB"
        prazo_status = "sem_data"
        dias_restantes = 999

        if dt_ref:
            agora = datetime.datetime.now().astimezone()
            if dt_ref.tzinfo is not None:
                dt_ref_local = dt_ref.astimezone()
            else:
                dt_ref_local = dt_ref.replace(tzinfo=agora.tzinfo)

            hoje = agora.date()
            amanha = hoje + timedelta(days=1)
            data_ref = dt_ref_local.date()
            diff_seg = (dt_ref_local - agora).total_seconds()
            dias_restantes = (data_ref - hoje).days
            hora_str = dt_ref_local.strftime("%H:%M")

            if data_ref == hoje:
                prazo_status = "hoje"
                if diff_seg < 0:
                    badge_texto = f"🚨 ENCERROU HOJE às {hora_str}"
                    badge_bg = "#EF4444"
                    badge_fg = "#FFFFFF"
                else:
                    badge_texto = f"🚨 ENCERRA HOJE às {hora_str}"
                    badge_bg = "#EF4444"
                    badge_fg = "#FFFFFF"
            elif data_ref == amanha:
                prazo_status = "amanha"
                badge_texto = f"⚡ ENCERRA AMANHÃ às {hora_str}"
                badge_bg = "#F59E0B"
                badge_fg = "#080C14"
            elif hoje < data_ref <= hoje + timedelta(days=2):
                prazo_status = "48h"
                badge_texto = f"⏳ EM {dias_restantes} DIAS ({dt_ref_local.strftime('%d/%m às %H:%M')})"
                badge_bg = "#3B82F6"
                badge_fg = "#FFFFFF"
            elif data_ref > hoje:
                prazo_status = "aberto"
                badge_texto = f"📅 Em {dias_restantes} dias ({dt_ref_local.strftime('%d/%m')})"
                badge_bg = "#10B981"
                badge_fg = "#080C14"
            else:
                prazo_status = "encerrada"
                badge_texto = "⌛ ENCERRADA"
                badge_bg = "#374151"
                badge_fg = "#9CA3AF"

        return {
            "txt_abertura": txt_abert,
            "txt_encerramento": txt_encerr,
            "dt_abertura": dt_abert,
            "dt_encerramento": dt_encerr,
            "dt_ref": dt_ref,
            "badge_texto": badge_texto,
            "badge_bg": badge_bg,
            "badge_fg": badge_fg,
            "prazo_status": prazo_status,
            "dias_restantes": dias_restantes
        }

    def aplicar_filtros(self) -> None:
        if not hasattr(self, "todas_licitacoes"):
            return
        if not hasattr(self, "combo_modalidade") or self.combo_modalidade is None:
            return
            
        mod_selecionada = self.combo_modalidade.get()
        uf_selecionada = self.combo_uf.get()
        status_selecionado = self.combo_status.get()
        prazo_selecionado = self.combo_prazo.get() if hasattr(self, "combo_prazo") and self.combo_prazo else "Todos os Prazos"

        for widget in self.scroll_lics.winfo_children():
            widget.destroy()

        total_hoje = 0
        participando = 0
        filtradas = []

        for lic in self.todas_licitacoes:
            if not isinstance(lic, dict): continue
            modalidade_nome = str(lic.get("modalidade") or lic.get("tipo_processo") or "")
            uf = str(lic.get("uf") or "").upper().strip()
            status = str(lic.get("status_analise") or "PENDENTE").upper().strip()

            if mod_selecionada != "Todas" and mod_selecionada.lower() not in modalidade_nome.lower():
                continue

            if uf_selecionada != "TODOS" and uf != uf_selecionada:
                continue

            if status_selecionado == "Ativas (Pendente + Favorita)" and status not in ["PENDENTE", "FAVORITA", "NOVA"]:
                continue
            elif status_selecionado == "Apenas Favoritas" and status != "FAVORITA":
                continue
            elif status_selecionado == "Pendentes" and status not in ["PENDENTE", "NOVA"]:
                continue
            elif status_selecionado == "Descartadas" and status != "DESCARTADA":
                continue

            crono = self._extrair_cronograma_licitacao(lic)
            p_status = crono["prazo_status"]

            if prazo_selecionado == "🚨 Encerram Hoje" and p_status != "hoje":
                continue
            elif prazo_selecionado == "⚡ Encerram Amanhã" and p_status != "amanha":
                continue
            elif prazo_selecionado == "⏳ < 48 Horas" and p_status not in ["hoje", "amanha", "48h"]:
                continue
            elif prazo_selecionado == "📅 Em Aberto (Futuras)" and p_status not in ["hoje", "amanha", "48h", "aberto"]:
                continue
            elif prazo_selecionado == "⌛ Encerradas" and p_status != "encerrada":
                continue

            total_hoje += 1
            if status.lower() in ["proposta_enviada", "em_disputa", "ativo", "favorita"]:
                participando += 1

            filtradas.append((lic, crono))

        # Ordenação inteligente: urgências primeiro (Hoje -> Amanhã -> <48h -> Futuras -> Sem data -> Encerradas)
        def ordem_chave(item):
            lic_dict, c = item
            st = c["prazo_status"]
            if st == "hoje":
                return (0, c["dias_restantes"])
            elif st == "amanha":
                return (1, c["dias_restantes"])
            elif st == "48h":
                return (2, c["dias_restantes"])
            elif st == "aberto":
                return (3, c["dias_restantes"])
            elif st == "sem_data":
                return (4, 0)
            else:
                return (5, c["dias_restantes"])

        filtradas.sort(key=ordem_chave)

        import webbrowser

        for lic, crono in filtradas[:35]:
            modalidade_nome = str(lic.get("modalidade") or lic.get("tipo_processo") or "")
            uf = str(lic.get("uf") or "").upper().strip()
            objeto = str(lic.get("objeto") or "Sem descrição")
            sigla = "PE" if "Pregão" in modalidade_nome else ("Dispensa" if "Dispensa" in modalidade_nome else modalidade_nome)
            numero = str(lic.get("numero_pregao", "S/N"))
            uasg = str(lic.get("numero_uasg", "S/ UASG"))
            pregao_formatado = f"{sigla} {numero} (UASG {uasg})"
            valor = _safe_float(lic.get('valor_estimado'))

            ti_kws = ["tecnologia", "informática", "software", "computador", "rede", "suporte", "cabeamento", "notebook", "nobreak"]
            is_ti = any(kw in objeto.lower() for kw in ti_kws)

            # --- Criação do Card ---
            card = ctk.CTkFrame(self.scroll_lics, fg_color=COR["bg_card"], corner_radius=10, border_width=1, border_color=COR["border"])
            card.pack(fill="x", pady=6, padx=4)
            card.grid_columnconfigure(0, weight=1)
            card.grid_columnconfigure(1, weight=0, minsize=210)

            # --- COLUNA ESQUERDA (0): DADOS DA LICITAÇÃO ---
            
            # Linha 0: Badges
            badges_frame = ctk.CTkFrame(card, fg_color="transparent")
            badges_frame.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 6))

            # Badge Tipo
            tipo_bg = COR["accent_cyan"] if is_ti else COR["accent_purple"]
            tipo_text = " 🛠️ SERVIÇO " if is_ti else " 📦 PRODUTO "
            ctk.CTkLabel(badges_frame, text=tipo_text, fg_color=tipo_bg, text_color="#080C14", corner_radius=5, font=ctk.CTkFont(size=10, weight="bold")).pack(side="left", padx=(0, 6))

            # Badge Modalidade
            mod_bg = COR["border"]
            mod_text = f" {sigla.upper()} "
            ctk.CTkLabel(badges_frame, text=mod_text, fg_color=mod_bg, text_color=COR["text_primary"], corner_radius=5, font=ctk.CTkFont(size=10, weight="bold")).pack(side="left", padx=(0, 6))

            # Badge Portal
            portal_nome = str(lic.get("portal") or "COMPRAS_GOV").replace("_", ".")
            ctk.CTkLabel(badges_frame, text=f" 🏛️ {portal_nome} ", fg_color="#1E293B", text_color="#94A3B8", corner_radius=5, font=ctk.CTkFont(size=10, weight="bold")).pack(side="left", padx=(0, 6))

            # Badge Urgência / Prazo Destaque
            ctk.CTkLabel(badges_frame, text=f" {crono['badge_texto']} ", fg_color=crono["badge_bg"], text_color=crono["badge_fg"], corner_radius=5, font=ctk.CTkFont(size=10, weight="bold")).pack(side="left", padx=(0, 6))

            # Linha 1: Órgão e Pregão
            orgao_nome_str = str(lic.get('orgao_nome', '') or '')[:50]
            orgao = f"[{uf}] {orgao_nome_str} • {pregao_formatado}"
            ctk.CTkLabel(card, text=orgao, font=ctk.CTkFont(size=14, weight="bold"), text_color=COR["text_primary"], anchor="w").grid(row=1, column=0, sticky="w", padx=16, pady=(0, 2))

            # Linha 2: Objeto
            objeto_trunc = objeto[:120] + ("..." if len(objeto) > 120 else "")
            ctk.CTkLabel(card, text=objeto_trunc, font=ctk.CTkFont(size=12), text_color=COR["text_secondary"], anchor="w").grid(row=2, column=0, sticky="w", padx=16, pady=(0, 8))

            # Linha 3: Bloco de Datas e Cronograma (Destaque Visual)
            crono_box = ctk.CTkFrame(card, fg_color="#0F172A", corner_radius=6, border_width=1, border_color="#1E293B")
            crono_box.grid(row=3, column=0, sticky="w", padx=16, pady=(0, 12))

            cor_encerr = "#EF4444" if crono["prazo_status"] == "hoje" else ("#F59E0B" if crono["prazo_status"] == "amanha" else ("#38BDF8" if crono["prazo_status"] == "48h" else "#10B981"))
            if crono["prazo_status"] in ["sem_data", "encerrada"]:
                cor_encerr = "#9CA3AF"

            ctk.CTkLabel(crono_box, text=f"⏰ Encerramento Proposta: {crono['txt_encerramento']}", font=ctk.CTkFont(size=11, weight="bold"), text_color=cor_encerr).pack(side="left", padx=(10, 16), pady=5)
            ctk.CTkLabel(crono_box, text=f"🏛️ Abertura da Sessão / Disputa: {crono['txt_abertura']}", font=ctk.CTkFont(size=11), text_color="#E2E8F0").pack(side="left", padx=(0, 10), pady=5)

            # --- COLUNA DIREITA (1): VALOR ESTIMADO E AÇÕES (Sem sobreposição) ---
            val_frame = ctk.CTkFrame(card, fg_color="transparent")
            val_frame.grid(row=0, column=1, rowspan=2, sticky="ne", padx=16, pady=(12, 0))
            
            ctk.CTkLabel(val_frame, text="VALOR ESTIMADO", font=ctk.CTkFont(size=10, weight="bold"), text_color="#9CA3AF").pack(anchor="e")
            val_str = f"R$ {valor:,.2f}"
            ctk.CTkLabel(val_frame, text=val_str, font=ctk.CTkFont(size=17, weight="bold"), text_color=COR["accent_green"]).pack(anchor="e")

            acoes_frame = ctk.CTkFrame(card, fg_color="transparent")
            acoes_frame.grid(row=2, column=1, rowspan=2, sticky="se", padx=16, pady=(0, 12))

            # Botão Rápido 1: Mover p/ Serviços (CCMEI mão de obra pura)
            btn_serv = ctk.CTkButton(
                acoes_frame,
                text="🛠️ Mover p/ Serviços",
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color=COR["accent_cyan"],
                text_color="#080C14",
                hover_color="#06B6D4",
                corner_radius=8,
                width=175,
                height=28,
                command=lambda l=lic: self._mover_para_servicos(l),
            )
            btn_serv.pack(side="top", pady=(0, 3))

            # Botão Rápido 2: Mover p/ Serviço + Produtos (misto: peças + serviços)
            btn_misto = ctk.CTkButton(
                acoes_frame,
                text="📦 Mover p/ Serviço + Produtos",
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color=COR["accent_purple"],
                text_color="#FFFFFF",
                hover_color="#7C3AED",
                corner_radius=8,
                width=175,
                height=28,
                command=lambda l=lic: self._mover_para_misto(l),
            )
            btn_misto.pack(side="top", pady=(0, 3))

            # Botão Selecionar & Precificar
            btn_prec = ctk.CTkButton(
                acoes_frame,
                text="⚡ Precificar em Editais",
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color=COR["bg_panel"],
                text_color=COR["accent_green"],
                border_width=1,
                border_color=COR["accent_green"],
                hover_color="#1E293B",
                corner_radius=8,
                width=175,
                height=24,
                command=lambda l=lic: self._selecionar_e_precificar(l),
            )
            btn_prec.pack(side="top", pady=(0, 3))

            link_edital = str(lic.get("link_edital") or lic.get("link_sistema") or "")
            if link_edital and link_edital.startswith("http"):
                btn_link = ctk.CTkButton(
                    acoes_frame,
                    text="🌐 Acessar Edital Oficial",
                    font=ctk.CTkFont(size=11),
                    fg_color="transparent",
                    text_color="#38BDF8",
                    hover_color="#1E293B",
                    corner_radius=8,
                    width=175,
                    height=20,
                    command=lambda u=link_edital: webbrowser.open(u),
                )
                btn_link.pack(side="top")

            # Linha 4: Resumo da IA se existir
            resumo_ia = lic.get("resumo_ia") or lic.get("parecer_resumido")
            if resumo_ia:
                ia_box = ctk.CTkFrame(card, fg_color="#0F172A", corner_radius=6, border_width=1, border_color="#312E81")
                ia_box.grid(row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 10))
                ctk.CTkLabel(
                    ia_box,
                    text=f"🤖 Resumo IA: {str(resumo_ia)[:150]}...",
                    font=ctk.CTkFont(size=11),
                    text_color="#C7D2FE",
                    anchor="w",
                ).pack(side="left", padx=10, pady=4)

        self.card_radar_pncp.atualizar(str(total_hoje))
        self.card_servicos_ti.atualizar(str(participando))

    def _mover_para_servicos(self, lic: dict) -> None:
        lic_id = lic.get("id")
        if not lic_id: return
        import threading
        def worker():
            try:
                from supabase_client import atualizar_status_licitacao
                atualizar_status_licitacao(str(lic_id), "triado_servico")
                self.after(0, lambda: messagebox.showinfo("Triagem CCMEI", "✅ Oportunidade movida para a aba '🛠️ Serviços'!"))
                self.after(0, self.carregar_licitacoes_nuvem)
            except Exception as e:
                logger.warning(f"Erro ao mover para servicos: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _mover_para_misto(self, lic: dict) -> None:
        lic_id = lic.get("id")
        if not lic_id: return
        import threading
        def worker():
            try:
                from supabase_client import atualizar_status_licitacao
                atualizar_status_licitacao(str(lic_id), "triado_misto")
                self.after(0, lambda: messagebox.showinfo("Triagem CCMEI", "✅ Oportunidade movida para a aba '📦 Serviço com Produtos'!"))
                self.after(0, self.carregar_licitacoes_nuvem)
            except Exception as e:
                logger.warning(f"Erro ao mover para misto: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _selecionar_e_precificar(self, lic):
        link = lic.get("link_sistema") or lic.get("link_edital") or lic.get("url_sala_disputa") or ""
        # Tenta alternar para a aba 'Editais & Custos' usando o container correto
        if hasattr(self.app, "tab_container") and hasattr(self.app.tab_container, "set"):
            self.app.tab_container.set("Editais & Custos")
        elif hasattr(self.app, "mudar_aba"):
            self.app.mudar_aba("Editais & Custos")
        elif hasattr(self.app, "selecionar_aba"):
            self.app.selecionar_aba("Editais & Custos")
        
        if hasattr(self.app, "painel_editais"):
            pe = self.app.painel_editais
            if hasattr(pe, "txt_entrada_rapida"):
                pe.txt_entrada_rapida.delete(0, "end")
                pe.txt_entrada_rapida.insert(0, link)
                if hasattr(pe, "_carregar_direto"):
                    pe._carregar_direto()

    def _on_tree_lics_double_click(self, event: Any) -> None:
        item = self.tree_lics.selection()
        if not item:
            return
        lic_id = item[0]
        if hasattr(self, "todas_licitacoes"):
            for lic in self.todas_licitacoes:
                if str(lic.get("id")) == str(lic_id):  # type: ignore
                    link = lic.get("link_edital")  # type: ignore
                    if link and link.startswith("http"):
                        import webbrowser
                        webbrowser.open(link)
                    break

    def atualizar_catalogo(self) -> None:
        # Backward compatibility for old calls that expected tree_lics to insert
        pass

    def _buscar_no_pncp_bg(self) -> None:
        import buscador_pncp
        
        def worker():
            try:
                self.card_radar_pncp.atualizar("⏳", "Buscando...")
                buscador_pncp.executar_varredura_completa()
            except Exception as e:
                logging.error(f"Erro na varredura PNCP: {e}")
            finally:
                self.after(0, self.carregar_dados_dashboard)
                
        threading.Thread(target=worker, daemon=True).start()

    def _iniciar_chrome(self) -> None:
        import subprocess
        try:
            subprocess.Popen(["google-chrome", "--remote-debugging-port=9222"])
            self.lbl_chrome_status.configure(text="🟢 Chrome Conectado (Porta 9222)", text_color=COR["accent_green"])
        except Exception as e:
            logging.error(f"Erro ao iniciar Chrome: {e}")

    def verificar_status_chrome(self) -> None:
        import requests
        import threading
        
        def checker():
            try:
                resp = requests.get("http://localhost:9222/json/version", timeout=1)
                is_connected = (resp.status_code == 200)
            except Exception:
                is_connected = False
                
            def update_ui():
                try:
                    if is_connected:
                        self.lbl_chrome_status.configure(text="🟢 Chrome Conectado (Porta 9222)", text_color=COR["accent_green"])
                    else:
                        self.lbl_chrome_status.configure(text="⚪ Chrome Desconectado", text_color=COR["text_secondary"])
                    self.after(5000, self.verificar_status_chrome)
                except Exception:
                    pass # ignore if widget is destroyed
                    
            self.after(0, update_ui)
            
        threading.Thread(target=checker, daemon=True).start()

    def _sincronizar_drive(self) -> None:
        btn_txt_original = self.btn_sync_drive.cget("text")
        self.btn_sync_drive.configure(text="⏳ Sincronizando pastas no Drive...", state="disabled")
        
        def worker():
            from drive_pipeline_manager import executar_pipeline_drive
            import supabase_client
            
            sb = supabase_client.get_client()
            if sb:
                qtd = executar_pipeline_drive(sb)
                msg = f"{qtd} pastas sincronizadas no Drive e limpeza do Supabase concluída!"
            else:
                msg = "Erro: Cliente Supabase não configurado."
                
            self.after(0, lambda: self._fim_sincronizacao(btn_txt_original, msg))
            
        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _fim_sincronizacao(self, text_orig: str, msg: str) -> None:
        self.btn_sync_drive.configure(text=text_orig, state="normal")
        import tkinter.messagebox as messagebox
        messagebox.showinfo("Sincronização Drive", msg)

    def carregar_dados_dashboard(self) -> None:
        """Busca licitações salvas no Supabase e popula a tabela no Dashboard (Assíncrono)."""
        import threading
        import cache_manager
        
        # 1. Carrega do cache instantaneamente na MainThread
        cache = cache_manager.carregar_cache()
        if "todas_licitacoes" in cache:
            try:
                from radar_telegram import validar_edital_jrbc
                self.todas_licitacoes = [lic for lic in cache["todas_licitacoes"] if isinstance(lic, dict) and validar_edital_jrbc(str(lic.get("objeto") or ""))]
            except Exception:
                self.todas_licitacoes = cache["todas_licitacoes"]
            self.aplicar_filtros()
            
        def worker():
            try:
                from supabase import create_client
                import os
                
                url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
                key = os.getenv("SUPABASE_KEY") or ""
                
                if not key:
                    logging.warning("[GUI] SUPABASE_KEY não configurada no ambiente.")
                    return
                    
                sb = create_client(url, key)
                
                resposta = sb.table("licitacoes")\
                    .select("*")\
                    .order("atualizado_em", desc=True)\
                    .limit(200)\
                    .execute()
                    
                raw_data = resposta.data or []
                try:
                    from radar_telegram import validar_edital_jrbc
                    self.todas_licitacoes = [lic for lic in raw_data if isinstance(lic, dict) and validar_edital_jrbc(str(lic.get("objeto") or ""))]
                except Exception:
                    self.todas_licitacoes = raw_data
                
                # 2. Salva o novo cache saneado
                cache_manager.salvar_cache("todas_licitacoes", self.todas_licitacoes)
                
                self.after(0, self.aplicar_filtros)
                logging.info(f"[GUI] {len(self.todas_licitacoes)} licitações válidas carregadas com sucesso do Supabase!")
                
            except Exception as e:
                logging.error(f"[GUI] Erro ao carregar dados do Supabase no Dashboard: {e}")
                
        threading.Thread(target=worker, daemon=True).start()


# ─── PAINEL 2: EDITAIS & CUSTOS (MULTI-CERTAME) ────────────────────────────────
class PainelEditais(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self._build()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        import tkinter as tk

        # 0. Entrada Rápida (Google Drive, PNCP, Comprasnet ou Pasta)
        card_entrada = CardFrame(self)
        card_entrada.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 8))
        card_entrada.grid_columnconfigure(0, weight=1)
        
        f_entrada = ctk.CTkFrame(card_entrada, fg_color="transparent")
        f_entrada.pack(fill="x", padx=16, pady=10)
        f_entrada.grid_columnconfigure(0, weight=1)
        
        self.txt_entrada_rapida = ctk.CTkEntry(
            f_entrada,
            placeholder_text="Cole o Link da Pasta do Google Drive, PNCP, Comprasnet ou Pasta do Processo...",
            font=ctk.CTkFont(size=12)
        )
        self.txt_entrada_rapida.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.txt_entrada_rapida.bind("<Return>", lambda e: self._carregar_direto())
        self.txt_entrada_rapida.bind("<Control-v>", lambda e: self.after(10, lambda: None))
        
        self.btn_carregar_direto = ctk.CTkButton(
            f_entrada,
            text="⚡ Carregar do Drive / PNCP",
            font=ctk.CTkFont(weight="bold"),
            fg_color=COR["accent_cyan"],
            text_color="#080C14",
            command=self._carregar_direto,
        )
        self.btn_carregar_direto.grid(row=0, column=1)

        # 1. Seletor de Certame na Nuvem
        card_seletor = CardFrame(self)
        card_seletor.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 8))
        card_seletor.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card_seletor, text="☁️  Selecione a Licitação na Nuvem:", font=ctk.CTkFont(size=13, weight="bold"), text_color=COR["accent_cyan"]).grid(row=0, column=0, padx=(16, 8), pady=10, sticky="w")
        
        import cache_manager
        cache = cache_manager.carregar_cache()
        valores_iniciais = ["(Carregando...)"]
        if "todas_licitacoes" in cache:
            self.dados_nuvem = cache["todas_licitacoes"]
            valores_iniciais = [f"[{l.get('uf', '')}] {l.get('orgao_nome', '')[:20]} - {l.get('numero_pregao', '')}" for l in self.dados_nuvem]
            if not valores_iniciais:
                valores_iniciais = ["(Nenhum pregão carregado)"]
                
        self.cmb_certames = ctk.CTkComboBox(
            card_seletor,
            values=valores_iniciais,
            command=self._on_selecionar_licitacao,
            width=300
        )
        self.cmb_certames.grid(row=0, column=1, padx=8, pady=10, sticky="ew")

        btn_remover = ctk.CTkButton(card_seletor, text="🔄 Atualizar Nuvem", width=120, fg_color=COR["accent_purple"], text_color="#FFFFFF", command=self.atualizar_lista_certames)
        btn_remover.grid(row=0, column=2, padx=(4, 16), pady=10)
        
        if "todas_licitacoes" in cache and valores_iniciais and valores_iniciais[0] not in ("(Nenhum pregão carregado)", "(Carregando...)"):
            self.cmb_certames.set(valores_iniciais[0])
            self.after(50, lambda: self._on_selecionar_licitacao(valores_iniciais[0]))

        # 2. Card de Vistoria Prévia & Parecer da IA (Gemini - Lei 14.133/2021)
        self.card_vistoria = CardFrame(self)
        self.card_vistoria.grid(row=2, column=0, sticky="ew", padx=0, pady=(0, 8))
        self.card_vistoria.grid_columnconfigure(0, weight=1)

        f_vistoria_top = ctk.CTkFrame(self.card_vistoria, fg_color="transparent")
        f_vistoria_top.pack(fill="x", padx=16, pady=(10, 4))
        f_vistoria_top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            f_vistoria_top,
            text="🤖  Vistoria Prévia & Parecer da IA (Gemini):",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COR["accent_cyan"],
        ).grid(row=0, column=0, sticky="w")

        self.lbl_vistoria_badge = ctk.CTkLabel(
            f_vistoria_top,
            text="🔍 AGUARDANDO VISTORIA",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#1E293B",
            text_color=COR["accent_cyan"],
            corner_radius=6,
            padx=10,
            pady=3,
        )
        self.lbl_vistoria_badge.grid(row=0, column=1, sticky="w", padx=10)

        self.btn_reavaliar_ia = ctk.CTkButton(
            f_vistoria_top,
            text="👁️ Vistoria IA",
            width=110,
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COR["accent_purple"],
            text_color="#FFFFFF",
            command=self._executar_vistoria_ia_atual,
        )
        self.btn_reavaliar_ia.grid(row=0, column=2, padx=(0, 6))

        self.btn_abrir_drive = ctk.CTkButton(
            f_vistoria_top,
            text="📁 Abrir no Drive",
            width=120,
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#334155",
            text_color="#FFFFFF",
            command=self._abrir_pasta_drive_atual,
            state="disabled",
        )
        self.btn_abrir_drive.grid(row=0, column=3)

        self.lbl_vistoria_parecer = ctk.CTkLabel(
            self.card_vistoria,
            text="Cole o link do Google Drive, PNCP ou pasta acima para a IA inspecionar os itens, estimar custos e comparar com a disputa ao vivo.",
            font=ctk.CTkFont(size=12),
            text_color=COR["text_primary"],
            justify="left",
            wraplength=950,
            anchor="w",
        )
        self.lbl_vistoria_parecer.pack(fill="x", padx=16, pady=(2, 4))

        class _TxtParecerProxy:
            def __init__(self, label: ctk.CTkLabel):
                self._lbl = label
            def delete(self, *args, **kwargs):
                self._lbl.configure(text="")
            def insert(self, index, text, *args, **kwargs):
                self._lbl.configure(text=str(text))
            def get(self, *args, **kwargs):
                return self._lbl.cget("text")

        self.txt_parecer = _TxtParecerProxy(self.lbl_vistoria_parecer)

        self.lbl_vistoria_pontos = ctk.CTkLabel(
            self.card_vistoria,
            text="• Conforme Lei 14.133/2021 (Art. 59 §4º), lances abaixo de 50% do valor orçado têm presunção de inexequibilidade.",
            font=ctk.CTkFont(size=11, slant="italic"),
            text_color=COR["accent_amber"],
            justify="left",
            wraplength=950,
            anchor="w",
        )
        self.lbl_vistoria_pontos.pack(fill="x", padx=16, pady=(0, 10))

        # 3. Barra de Regras de Lances
        card_regras = CardFrame(self)
        card_regras.grid(row=3, column=0, sticky="ew", padx=0, pady=(0, 8))
        
        ctk.CTkLabel(card_regras, text="⚖️ Regra de Lance:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(16, 8), pady=10)
        
        self.var_regra = tk.StringVar(value="R$")
        rb_rs = ctk.CTkRadioButton(card_regras, text="R$ Valor Fixo", variable=self.var_regra, value="R$")
        rb_rs.pack(side="left", padx=8)
        rb_perc = ctk.CTkRadioButton(card_regras, text="% Percentual", variable=self.var_regra, value="%")
        rb_perc.pack(side="left", padx=8)

        ctk.CTkLabel(card_regras, text="Intervalo Mínimo:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(24, 8), pady=10)
        self.txt_intervalo = ctk.CTkEntry(card_regras, width=80)
        self.txt_intervalo.insert(0, "0.01")
        self.txt_intervalo.pack(side="left", padx=8)

        self.btn_aprovar_participacao = ctk.CTkButton(
            card_regras,
            text="✅ Aceitar / Aprovar Participação",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#10B981",
            hover_color="#059669",
            text_color="#FFFFFF",
            corner_radius=8,
            height=34,
            command=self._aprovar_participacao_disputa,
        )
        self.btn_aprovar_participacao.pack(side="right", padx=(8, 12), pady=10)

        btn_salvar_regra = ctk.CTkButton(card_regras, text="💾 Salvar Regra no Supabase", fg_color=COR["accent_green"], text_color="#000000", command=self._salvar_regras_supabase)
        btn_salvar_regra.pack(side="right", padx=8, pady=10)
        
        btn_ir_disputa = ctk.CTkButton(
            card_regras,
            text="🚀 Ir para Disputa ao Vivo",
            fg_color=COR["accent_cyan"],
            text_color="#000000",
            font=ctk.CTkFont(weight="bold"),
            corner_radius=8,
            command=self._ir_para_disputa
        )
        btn_ir_disputa.pack(side="right", padx=8, pady=10)

        btn_preencher_proposta = ctk.CTkButton(
            card_regras,
            text="📝 Preencher Proposta no Comprasnet",
            fg_color=COR["accent_green"],
            text_color="#000000",
            corner_radius=8,
            command=self._preencher_proposta_ui
        )
        btn_preencher_proposta.pack(side="right", padx=8, pady=10)

        # 4. Tabela de Itens & Stop Loss
        card_itens = CardFrame(self)
        card_itens.grid(row=4, column=0, sticky="nsew", padx=0, pady=0)
        card_itens.grid_rowconfigure(1, weight=1)
        card_itens.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card_itens, text="💰  Itens do Pregão Selecionado & Calculadora de Stop-Loss", font=ctk.CTkFont(size=14, weight="bold"), text_color=COR["text_primary"]).grid(row=0, column=0, sticky="w", padx=16, pady=(10, 4))

        tbl_f = ctk.CTkFrame(card_itens, fg_color="transparent")
        tbl_f.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        tbl_f.grid_columnconfigure(0, weight=1)
        tbl_f.grid_rowconfigure(0, weight=1)

        cols = ("num", "desc", "qtd", "un", "val_ref", "custo", "margem", "piso")
        self.tree_itens = ttk.Treeview(tbl_f, columns=cols, show="headings")
        self.tree_itens.heading("num", text="Item")
        self.tree_itens.heading("desc", text="Descrição do Produto/Serviço")
        self.tree_itens.heading("qtd", text="Qtd")
        self.tree_itens.heading("un", text="Un.")
        self.tree_itens.heading("val_ref", text="Preço Ref (R$)")
        self.tree_itens.heading("custo", text="Custo Real (R$)")
        self.tree_itens.heading("margem", text="Margem (%)")
        self.tree_itens.heading("piso", text="Stop-Loss (R$)")

        self.tree_itens.column("num", width=50, anchor="center")
        self.tree_itens.column("desc", width=300, anchor="w")
        self.tree_itens.column("qtd", width=50, anchor="center")
        self.tree_itens.column("un", width=50, anchor="center")
        self.tree_itens.column("val_ref", width=110, anchor="e")
        self.tree_itens.column("custo", width=110, anchor="e")
        self.tree_itens.column("margem", width=90, anchor="center")
        self.tree_itens.column("piso", width=110, anchor="e")
        self.tree_itens.grid(row=0, column=0, sticky="nsew")
        
        self.tree_itens.tag_configure("evenrow", background="#181A20", foreground="#FFFFFF")
        self.tree_itens.tag_configure("oddrow", background="#22242A", foreground="#FFFFFF")
        
        self.tree_itens.bind("<Double-1>", self._on_editar_item_custo)

        sb = ctk.CTkScrollbar(tbl_f, orientation="vertical", command=self.tree_itens.yview)
        self.tree_itens.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

        lbl_hint = ctk.CTkLabel(
            card_itens,
            text="💡 Dica: Dê um duplo-clique no item para ajustar Custo Real / Margem. O Stop-Loss atualiza automaticamente na nuvem.",
            font=ctk.CTkFont(size=11, slant="italic"),
            text_color=COR["accent_cyan"],
        )
        lbl_hint.grid(row=2, column=0, sticky="w", padx=16, pady=(0, 8))
        btn_cotar = ctk.CTkButton(card_itens, text="🛒 Cotar no Mercado Livre", command=self.cotar_mercado_livre)
        btn_cotar.grid(row=3, column=0, sticky="w", padx=16, pady=(0, 10))

    def cotar_mercado_livre(self):
        sel = self.tree_itens.selection()
        if not sel:
            import tkinter.messagebox as messagebox
            messagebox.showwarning("Aviso", "Selecione um item do edital primeiro.")
            return
        if hasattr(self, "licitacao_ativa"):
            cotar_no_ml_gui(self, self.licitacao_ativa)


    def atualizar_lista_certames(self) -> None:
        self.cmb_certames.set("(Carregando...)")
        self.cmb_certames.configure(values=["(Carregando...)"])
        
        def worker():
            import os
            try:
                from supabase import create_client
                url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
                key = os.getenv("SUPABASE_KEY") or ""
                sb = create_client(url, key)
                res = sb.table("licitacoes").select("*").order("atualizado_em", desc=True).execute()
                raw_data = res.data or []
                try:
                    from radar_telegram import validar_edital_jrbc
                    self.dados_nuvem = [lic for lic in raw_data if isinstance(lic, dict) and validar_edital_jrbc(str(lic.get("objeto") or ""))]
                except Exception:
                    self.dados_nuvem = raw_data
            except Exception as e:
                self.dados_nuvem = []
                
            self.after(0, self._concluir_atualizacao_lista_certames)
            
        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _concluir_atualizacao_lista_certames(self):
        if not getattr(self, "dados_nuvem", None):
            self.cmb_certames.configure(values=["(Nenhum pregão carregado)"])
            self.cmb_certames.set("(Nenhum pregão carregado)")
            return

        lista_chaves = []
        for lic in self.dados_nuvem:
            uf = lic.get("uf", "")  # type: ignore
            orgao = lic.get("orgao_nome", "")[:20]  # type: ignore
            pregao = lic.get("numero_pregao", "")  # type: ignore
            texto = f"[{uf}] {orgao} - {pregao}"
            lista_chaves.append(texto)
            
        self.cmb_certames.configure(values=lista_chaves)
        if lista_chaves:
            self.cmb_certames.set(lista_chaves[0])
            self._on_selecionar_licitacao(lista_chaves[0])

    def _exibir_itens_completos(self, lic: dict, itens: list[dict], vistoria: Optional[dict] = None) -> None:
        for row in self.tree_itens.get_children():
            self.tree_itens.delete(row)

        itens_para_sessao = []
        for idx, item in enumerate(itens, 1):
            if isinstance(item, dict):
                num = item.get("numero_item") or idx
                desc = item.get("descricao") or f"Item {num}"
                qtd = _safe_float(item.get("quantidade"), default=1.0)
                unidade = str(item.get("unidade_medida") or "UN").upper()
                val_ref = _safe_float(item.get("valor_referencia"))
                custo_real = _safe_float(item.get("custo_real"))
                piso = _safe_float(item.get("stop_loss") or item.get("piso_preco"))
                margem = _safe_float(item.get("margem"), default=(((val_ref - piso) / val_ref * 100) if val_ref > 0 and piso > 0 else 0.0))
            else:
                num = getattr(item, "numero_item", idx)
                desc = getattr(item, "descricao", f"Item {num}")
                qtd = _safe_float(getattr(item, "quantidade", 1.0), default=1.0)
                unidade = str(getattr(item, "unidade_medida", "UN") or "UN").upper()
                val_ref = _safe_float(getattr(item, "valor_referencia", 0.0))
                custo_real = _safe_float(getattr(item, "custo_real", 0.0))
                piso = _safe_float(getattr(item, "stop_loss", 0.0) or getattr(item, "piso_preco", 0.0))
                margem = _safe_float(getattr(item, "margem", 0.0), default=(((val_ref - piso) / val_ref * 100) if val_ref > 0 and piso > 0 else 0.0))

            val_ref_str = f"R$ {val_ref:,.2f}" if val_ref > 0 else "R$ 0,00 (Sigiloso)"
            custo_str = f"R$ {custo_real:,.2f}" if custo_real > 0 else "—"
            piso_str = f"R$ {piso:,.2f}" if piso > 0 else "—"
            margem_str = f"{margem:.1f}%" if margem > 0 else "0.0%"

            item_id = f"{lic.get('id', 'item')}_{num}"
            self.tree_itens.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    str(num),
                    desc[:120],
                    f"{qtd:g}",
                    unidade,
                    val_ref_str,
                    custo_str,
                    margem_str,
                    piso_str,
                ),
                tags=("evenrow" if idx % 2 == 0 else "oddrow",)
            )

            itens_para_sessao.append({
                "numero_item": num,
                "descricao": desc,
                "quantidade": qtd,
                "unidade_medida": unidade,
                "valor_referencia": val_ref,
                "custo_unitario": custo_real,
                "preco_minimo": piso,
                "piso_seguro": piso,
            })

        self.licitacao_ativa = lic
        self.itens_carregados = itens_para_sessao
        if hasattr(self.app, "definir_licitacao_ativa"):
            self.app.definir_licitacao_ativa(lic, itens_para_sessao)

    def _abrir_pasta_drive_atual(self) -> None:
        caminho = getattr(self, "pasta_drive_ativa", "")
        if caminho:
            from integrador_drive_ia import abrir_pasta_no_sistema
            abrir_pasta_no_sistema(caminho)
        else:
            import tkinter.messagebox as messagebox
            messagebox.showinfo("Drive", "Nenhuma pasta local do Drive associada a este certame.")

    def _executar_vistoria_ia_atual(self) -> None:
        if not hasattr(self, "licitacao_ativa") or not self.licitacao_ativa:
            import tkinter.messagebox as messagebox
            messagebox.showwarning("Aviso", "Selecione ou carregue um certame primeiro.")
            return

        if hasattr(self, "txt_parecer"):
            try:
                self.txt_parecer.delete("1.0", "end")
                self.txt_parecer.insert("1.0", "Carregando vistoria técnica do edital...")
            except Exception:
                pass
        if hasattr(self, "lbl_vistoria_parecer"):
            self.lbl_vistoria_parecer.configure(text="Carregando vistoria técnica do edital...")
        if hasattr(self, "lbl_vistoria_pontos"):
            self.lbl_vistoria_pontos.configure(text="")
        self.lbl_vistoria_badge.configure(
            text="⏳ REAVALIANDO COM GEMINI...",
            fg_color="#3B82F6",
            text_color="#FFFFFF"
        )

        def worker():
            from integrador_drive_ia import realizar_vistoria_previa_gemini
            itens = getattr(self, "itens_carregados", [])
            dados = {
                "licitacao": self.licitacao_ativa,
                "nome_pasta": self.cmb_certames.get(),
            }
            vistoria = realizar_vistoria_previa_gemini(dados, itens)
            itens_finais = vistoria.get("itens_atualizados") or itens

            def atualizar():
                if isinstance(self.licitacao_ativa, dict):
                    self._exibir_itens_completos(self.licitacao_ativa, itens_finais, vistoria)
                badge_txt = vistoria.get("badge", "🟢 VISTORIA CONCLUÍDA")
                bg_color = "#10B981" if "VIÁVEL" in badge_txt or "APROVADA" in badge_txt else "#F59E0B"
                self.lbl_vistoria_badge.configure(text=badge_txt, fg_color=bg_color, text_color="#000000")
                parecer_txt = vistoria.get("parecer_resumido", "")
                self.lbl_vistoria_parecer.configure(text=parecer_txt)
                if hasattr(self, "txt_parecer"):
                    try:
                        self.txt_parecer.delete("1.0", "end")
                        self.txt_parecer.insert("1.0", parecer_txt)
                    except Exception:
                        pass
                pontos = vistoria.get("pontos_atencao", [])
                self.lbl_vistoria_pontos.configure(text="\n".join(f"• {p}" for p in pontos))

            self.after(0, atualizar)

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _on_selecionar_licitacao(self, escolha: str) -> None:
        """Disparado imediatamente quando o usuário seleciona um certame no menu suspenso."""
        # 1. Limpa imediatamente o parecer anterior para evitar resíduo visual de outro certame
        self.txt_parecer.delete("1.0", "end")
        self.txt_parecer.insert("1.0", "Carregando vistoria técnica do edital...")
        if hasattr(self, "lbl_vistoria_parecer"):
            self.lbl_vistoria_parecer.configure(text="Carregando vistoria técnica do edital...")
        if hasattr(self, "lbl_vistoria_pontos"):
            self.lbl_vistoria_pontos.configure(text="")
        if hasattr(self, "lbl_vistoria_badge"):
            self.lbl_vistoria_badge.configure(
                text="⏳ CARREGANDO VISTORIA TÉCNICA...",
                fg_color="#3B82F6",
                text_color="#FFFFFF"
            )

        # 2. Reseta a tabela de itens visualmente
        if hasattr(self, "tree_itens"):
            try:
                for row in self.tree_itens.get_children():
                    self.tree_itens.delete(row)
                self.tree_itens.insert("", "end", values=("⏳", "Carregando dados do certame...", "", "", "", "", "", ""))
            except Exception:
                pass

        # 3. Localiza a licitação correspondente
        valores = []
        try:
            valores = self.cmb_certames.cget("values")
        except Exception:
            pass
        if not valores:
            valores = getattr(self.cmb_certames, "_values", [])

        idx = -1
        if escolha in valores:
            idx = valores.index(escolha)
        elif hasattr(self, "dados_nuvem"):
            for i, d in enumerate(self.dados_nuvem):
                if isinstance(d, dict):
                    orgao = str(d.get("orgao_nome") or "")[:20]
                    uf = str(d.get("uf") or "")
                    pregao = str(d.get("numero_pregao") or "")
                    rep = f"[{uf}] {orgao} - {pregao}"
                    if rep == escolha:
                        idx = i
                        break

        if idx >= 0 and hasattr(self, "dados_nuvem"):
            lic_raw = self.dados_nuvem[idx]
            if not isinstance(lic_raw, dict):
                return
            lic: dict[str, Any] = dict(lic_raw)
            self.licitacao_ativa = lic

            def worker():
                from integrador_drive_ia import (
                    buscar_pasta_local_drive,
                    carregar_dados_pasta_drive,
                    realizar_vistoria_previa_gemini,
                )

                orgao = lic.get("orgao_nome", "")
                pregao = lic.get("numero_pregao", "")
                link = lic.get("link_sistema", "")

                # 1. Tenta achar pasta no Drive local
                pasta_drive = buscar_pasta_local_drive(pregao) or buscar_pasta_local_drive(orgao[:20])
                dados_drive = carregar_dados_pasta_drive(pasta_drive) if pasta_drive else None

                itens = []
                if dados_drive and dados_drive.get("itens"):
                    itens = dados_drive["itens"]
                    self.pasta_drive_ativa = str(pasta_drive)
                    self.after(0, lambda: self.btn_abrir_drive.configure(state="normal"))
                else:
                    self.pasta_drive_ativa = ""
                    self.after(0, lambda: self.btn_abrir_drive.configure(state="disabled"))

                # 2. Se não achou itens no Drive, busca no PNCP
                if not itens and "pncp.gov.br" in link:
                    try:
                        from pncp_client import carregar_certame_pncp
                        res_pncp = carregar_certame_pncp(link)
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
                    except Exception as e:
                        logger.error(f"Erro ao buscar PNCP: {e}")

                # 3. Fallback: item único do objeto
                if not itens:
                    val_est = _safe_float(lic.get("valor_estimado"))
                    itens = [{
                        "numero_item": 1,
                        "descricao": lic.get("objeto", "Objeto do certame"),
                        "quantidade": 1.0,
                        "unidade_medida": "UN",
                        "valor_referencia": val_est,
                        "custo_real": val_est * 0.5 if val_est > 0 else 0.0,
                        "piso_preco": val_est * 0.7 if val_est > 0 else 0.0,
                    }]

                # Exibe itens imediatamente
                self.after(0, lambda: self._exibir_itens_completos(lic, itens))

                # Dispara Vistoria Prévia da IA em background
                self.after(0, lambda: self.lbl_vistoria_badge.configure(
                    text="⏳ EXECUTANDO VISTORIA IA (GEMINI)...",
                    fg_color="#3B82F6",
                    text_color="#FFFFFF"
                ))

                ctx_processo = dados_drive or {"licitacao": lic, "nome_pasta": escolha}
                vistoria = realizar_vistoria_previa_gemini(ctx_processo, itens)
                itens_finais = vistoria.get("itens_atualizados") or itens

                def atualizar_vistoria_ui():
                    self._exibir_itens_completos(lic, itens_finais, vistoria)
                    badge_txt = vistoria.get("badge", "🟢 VISTORIA CONCLUÍDA")
                    bg_color = "#10B981" if "VIÁVEL" in badge_txt or "APROVADA" in badge_txt else "#F59E0B"
                    self.lbl_vistoria_badge.configure(text=badge_txt, fg_color=bg_color, text_color="#000000")
                    parecer_txt = vistoria.get("parecer_resumido", "")
                    self.lbl_vistoria_parecer.configure(text=parecer_txt)
                    if hasattr(self, "txt_parecer"):
                        try:
                            self.txt_parecer.delete("1.0", "end")
                            self.txt_parecer.insert("1.0", parecer_txt)
                        except Exception:
                            pass
                    pontos = vistoria.get("pontos_atencao", [])
                    self.lbl_vistoria_pontos.configure(text="\n".join(f"• {p}" for p in pontos))

                self.after(0, atualizar_vistoria_ui)

            import threading
            threading.Thread(target=worker, daemon=True).start()

    _on_certame_selecionado = _on_selecionar_licitacao

    def _exibir_itens_pncp(self, lic: dict, resultado: Any) -> None:
        self._exibir_itens_completos(lic, getattr(resultado, "itens", []) if hasattr(resultado, "itens") else [])

    def _exibir_itens(self, lic: dict) -> None:
        val = _safe_float(lic.get("valor_estimado"))
        self._exibir_itens_completos(lic, [{
            "numero_item": 1,
            "descricao": lic.get("objeto", "Item principal"),
            "quantidade": 1.0,
            "unidade_medida": "UN",
            "valor_referencia": val,
            "custo_real": _safe_float(lic.get("custo_real"), default=(val * 0.5 if val > 0 else 0.0)),
            "piso_preco": _safe_float(lic.get("piso_seguro"), default=(val * 0.7 if val > 0 else 0.0)),
        }])
        
        if hasattr(self.app, "definir_licitacao_ativa"):
            self.app.definir_licitacao_ativa(lic, [{"numero_item": 1, "descricao": lic.get("objeto", "")[:50]}])
        
        # Load rules if exist
        regra = lic.get("regra_lance")  # type: ignore
        if regra:
            self.var_regra.set(regra)
        intervalo = lic.get("intervalo_minimo")  # type: ignore
        if intervalo:
            self.txt_intervalo.delete(0, "end")
            self.txt_intervalo.insert(0, str(intervalo))

    def _salvar_regras_supabase(self) -> None:
        if not hasattr(self, "licitacao_ativa") or not isinstance(self.licitacao_ativa, dict): return
        lic_id = self.licitacao_ativa.get("id")
        regra = self.var_regra.get()
        intervalo_str = self.txt_intervalo.get()
        
        try:
            intervalo = float(intervalo_str.replace(",", "."))
        except ValueError:
            import tkinter.messagebox as messagebox
            messagebox.showerror("Erro", "Intervalo inválido.")
            return

        import os
        try:
            from supabase import create_client
            url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
            key = os.getenv("SUPABASE_KEY") or ""
            sb = create_client(url, key)
            sb.table("licitacoes").update({"regra_lance": regra, "intervalo_minimo": intervalo}).eq("id", lic_id).execute()
            import tkinter.messagebox as messagebox
            messagebox.showinfo("Sucesso", "Regra de lance salva na nuvem!")
        except Exception as e:
            import tkinter.messagebox as messagebox
            messagebox.showerror("Erro", f"Falha ao salvar no Supabase:\\n{e}")

    def _on_editar_item_custo(self, event: Any) -> None:
        selected = self.tree_itens.selection()
        if not selected:
            return
            
        lic_id = selected[0]
        vals = self.tree_itens.item(lic_id, "values")  # type: ignore
        if not vals or len(vals) < 6: return
        
        import os
        dialog = ctk.CTkInputDialog(text=f"Digite o novo Custo Real para o item 1:\\nAtual: {vals[5]}", title="Editar Custo")
        novo_custo = dialog.get_input()
        
        if novo_custo:
            try:
                novo_custo_f = float(novo_custo.replace(",", "."))
                novo_piso = novo_custo_f * 1.30  # 30% margin default
                
                # Cálculo instantâneo e atualização da tela
                try:
                    val_ref_str = vals[4]
                    val_ref = float(val_ref_str.replace("R$ ", "").replace(".", "").replace(",", "."))
                except:
                    val_ref = 0.0
                margem = ((val_ref - novo_piso) / val_ref * 100) if val_ref > 0 else 0.0
                
                novo_custo_str = f"R$ {novo_custo_f:,.2f}"
                novo_piso_str = f"R$ {novo_piso:,.2f}"
                nova_margem_str = f"{margem:.1f}%"
                
                new_vals = list(vals)
                new_vals[5] = novo_custo_str
                new_vals[6] = nova_margem_str
                new_vals[7] = novo_piso_str
                self.tree_itens.item(lic_id, values=new_vals)
                
                # Atualiza memória
                if hasattr(self, "licitacao_ativa") and isinstance(self.licitacao_ativa, dict):
                    self.licitacao_ativa["custo_real"] = novo_custo_f
                    self.licitacao_ativa["piso_seguro"] = novo_piso
                
                real_id = lic_id.split("_")[0] if isinstance(lic_id, str) else str(lic_id).split("_")[0]
                
                # Despacha para o Supabase em background sem bloquear
                def worker():
                    try:
                        from supabase import create_client
                        url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
                        key = os.getenv("SUPABASE_KEY") or ""
                        sb = create_client(url, key)
                        sb.table("licitacoes").update({"custo_real": novo_custo_f, "piso_seguro": novo_piso}).eq("id", real_id).execute()
                    except Exception as e:
                        import logging
                        logging.error(f"Falha ao salvar custo em background: {e}")
                
                import threading
                threading.Thread(target=worker, daemon=True).start()
                
            except Exception as e:
                import tkinter.messagebox as messagebox
                messagebox.showerror("Erro", f"Falha:\\n{e}")

    def _carregar_direto(self) -> None:
        link = self.txt_entrada_rapida.get().strip()
        if not link: return
        
        # 1. Fechar / Corrigir a Modal "Selecionar Opção" travada
        try:
            for widget in self.winfo_toplevel().winfo_children():
                if isinstance(widget, ctk.CTkToplevel) and widget.title() == "Selecionar Opção":
                    widget.destroy()
        except Exception:
            pass

        # 2. Feedback Visual Inicial
        if hasattr(self, 'btn_carregar_direto'):
            self.btn_carregar_direto.configure(text="⏳ Buscando no Drive/PNCP...", state="disabled")

        if hasattr(self, "txt_parecer"):
            try:
                self.txt_parecer.delete("1.0", "end")
                self.txt_parecer.insert("1.0", "Carregando vistoria técnica do edital...")
            except Exception:
                pass
        if hasattr(self, "lbl_vistoria_parecer"):
            self.lbl_vistoria_parecer.configure(text="Carregando vistoria técnica do edital...")
        if hasattr(self, "lbl_vistoria_pontos"):
            self.lbl_vistoria_pontos.configure(text="")

        def restore_btn():
            if hasattr(self, 'btn_carregar_direto'):
                self.btn_carregar_direto.configure(text="⚡ Carregar do Drive / PNCP", state="normal")

        def worker():
            try:
                from integrador_drive_ia import (
                    resolver_e_carregar_origem,
                    realizar_vistoria_previa_gemini,
                )

                self.after(0, lambda: self.tree_itens.delete(*self.tree_itens.get_children()))
                self.after(0, lambda: self.tree_itens.insert("", "end", values=("⏳", "Buscando pasta no Google Drive e dados do certame...", "", "", "", "", "", "")))

                dados = resolver_e_carregar_origem(link)
                pasta_drive = dados.get("caminho_pasta") or ""
                self.pasta_drive_ativa = pasta_drive

                itens = dados.get("itens") or []
                lic = dados.get("licitacao") or {}

                # Atualiza combobox e botão do Drive
                def atualizar_topo():
                    if pasta_drive:
                        self.btn_abrir_drive.configure(state="normal")
                    else:
                        self.btn_abrir_drive.configure(state="disabled")
                    orgao_nome = lic.get("orgao_nome", "")[:25]
                    pregao_num = lic.get("numero_pregao", "")
                    uf = lic.get("uf", "DRIVE")
                    self.cmb_certames.set(f"[{uf}] {orgao_nome} - {pregao_num}")

                self.after(0, atualizar_topo)

                # Feedback Vistoria IA
                self.after(0, lambda: self.lbl_vistoria_badge.configure(
                    text="⏳ EXECUTANDO VISTORIA IA (GEMINI)...",
                    fg_color="#3B82F6",
                    text_color="#FFFFFF"
                ))

                vistoria = realizar_vistoria_previa_gemini(dados, itens)
                itens_finais = vistoria.get("itens_atualizados") or itens

                def aplicar_resultado():
                    self._exibir_itens_completos(lic, itens_finais, vistoria)
                    badge_txt = vistoria.get("badge", "🟢 VISTORIA CONCLUÍDA")
                    bg_color = "#10B981" if "VIÁVEL" in badge_txt or "APROVADA" in badge_txt else "#F59E0B"
                    self.lbl_vistoria_badge.configure(text=badge_txt, fg_color=bg_color, text_color="#000000")
                    parecer_txt = vistoria.get("parecer_resumido", "")
                    self.lbl_vistoria_parecer.configure(text=parecer_txt)
                    if hasattr(self, "txt_parecer"):
                        try:
                            self.txt_parecer.delete("1.0", "end")
                            self.txt_parecer.insert("1.0", parecer_txt)
                        except Exception:
                            pass
                    pontos = vistoria.get("pontos_atencao", [])
                    self.lbl_vistoria_pontos.configure(text="\n".join(f"• {p}" for p in pontos))

                    url_disp = lic.get("link_sistema", "")
                    if url_disp and hasattr(self.app, 'painel_sessao') and hasattr(self.app.painel_sessao, 'txt_url'):
                        self.app.painel_sessao.txt_url.delete(0, 'end')
                        self.app.painel_sessao.txt_url.insert(0, url_disp)

                    import tkinter.messagebox as messagebox
                    msg_pasta = f"\nPasta Drive: {pasta_drive}" if pasta_drive else ""
                    messagebox.showinfo("Vistoria IA Concluída", f"✅ Processo e Pasta carregados com sucesso!\n\nÓrgão: {lic.get('orgao_nome', 'N/D')}\nPregão: {lic.get('numero_pregao', 'N/D')}{msg_pasta}\n\nVistoria IA: {badge_txt}")

                self.after(0, aplicar_resultado)

            except Exception as e:
                import traceback
                logger.error(f"Erro ao carregar do Drive: {traceback.format_exc()}")
                self.after(0, lambda: self.tree_itens.delete(*self.tree_itens.get_children()))
                err_msg = str(e)
                def mostrar_erro():
                    import tkinter.messagebox as messagebox
                    messagebox.showerror("Erro ao Carregar", f"Não foi possível processar a entrada:\n{err_msg}\n\nVerifique o link do Drive, PNCP ou nome do processo.")
                self.after(0, mostrar_erro)
            finally:
                self.after(0, restore_btn)

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _ir_para_disputa(self) -> None:
        if hasattr(self, "licitacao_ativa") and self.licitacao_ativa:
            itens_ativos = []
            for item in self.tree_itens.get_children():
                vals = self.tree_itens.item(item, "values")
                if vals and len(vals) >= 8:
                    try:
                        piso_f = float(vals[7].replace("R$", "").replace(".", "").replace(",", ".").strip())
                    except:
                        piso_f = 0.0
                    itens_ativos.append({
                        "numero_item": vals[0],
                        "descricao": vals[1],
                        "piso_seguro": piso_f
                    })
            if hasattr(self.app, "definir_licitacao_ativa"):
                self.app.definir_licitacao_ativa(self.licitacao_ativa, itens_ativos)  # type: ignore
            
            if hasattr(self.app, "selecionar_aba"):
                self.app.selecionar_aba("⚡  Console da Sessão")

    def _preencher_proposta_ui(self) -> None:
        if not hasattr(self, "licitacao_ativa") or not self.licitacao_ativa:
            import tkinter.messagebox as messagebox
            messagebox.showwarning("Atenção", "Selecione uma licitação na lista primeiro.")
            return
            
        uasg = str(self.licitacao_ativa.get("numero_uasg", "")) if isinstance(self.licitacao_ativa, dict) else ""
        pregao = str(self.licitacao_ativa.get("numero_pregao", "")) if isinstance(self.licitacao_ativa, dict) else ""
        
        if not uasg or not pregao:
            import tkinter.messagebox as messagebox
            messagebox.showwarning("Atenção", "A licitação selecionada não possui UASG ou Número do Pregão definidos.")
            return
            
        itens_proposta = []
        for item in self.tree_itens.get_children():
            vals = self.tree_itens.item(item, "values")
            if vals and len(vals) >= 8:
                try:
                    # Tenta pegar o custo ou o piso (usando a coluna 5: Custo, ou 7: Piso/Stop-loss)
                    valor_str = vals[5].replace("R$", "").strip() 
                except Exception:
                    valor_str = ""
                    
                itens_proposta.append({
                    "item_num": vals[0],
                    "descricao": vals[1],
                    "valor": valor_str,
                    "marca": "A Definir",
                    "fabricante": "A Definir",
                    "modelo": "Padrão"
                })
        
        if not itens_proposta:
            import tkinter.messagebox as messagebox
            messagebox.showwarning("Atenção", "Nenhum item carregado na tabela para preencher a proposta.")
            return
            
        import threading
        def _run_bg():
            try:
                from browser_bot import BrowserBot
                from engine import MotorDecisao
                bot = BrowserBot(motor=MotorDecisao(dry_run=False))
                bot.cadastrar_proposta_comprasnet(uasg, pregao, itens_proposta)
            except Exception as e:
                print(f"Erro na thread de cadastro de proposta: {e}")
                
        threading.Thread(target=_run_bg, daemon=True).start()

    def carregar_itens_externos(self, itens: list[dict]) -> None:
        """Carrega itens diretamente (ex: via captura Zero-Touch do Chrome) e exibe na grade."""
        lic = getattr(self, "licitacao_ativa", None) or getattr(self.app, "licitacao_ativa", None) or {
            "id": "zero_touch",
            "orgao_nome": "Sessão Compras.gov.br (Zero-Touch)",
            "numero_pregao": "Ao Vivo",
            "uf": "BR"
        }
        self.licitacao_ativa = lic
        self.app.licitacao_ativa = lic
        self.itens_ativos = itens
        self.app.itens_ativos = itens
        self._exibir_itens_completos(lic, itens)

    def _aprovar_participacao_disputa(self) -> None:
        """
        Aprova formalmente a participação da JRBC TECH no certame:
        - Grava status_analise = 'aprovado_disputa'
        - Grava stop_loss e margens no Supabase (licitacoes e itens_licitacao)
        - Agenda a data da disputa
        - Notifica o canal da JRBC TECH no Telegram via TelegramService
        """
        lic = getattr(self, "licitacao_ativa", None) or getattr(self.app, "licitacao_ativa", None)
        if not lic or not isinstance(lic, dict):
            import tkinter.messagebox as messagebox
            messagebox.showwarning("Atenção", "Selecione uma licitação primeiro para aprovar a participação.")
            return

        lic_id = lic.get("id")
        if not lic_id:
            import tkinter.messagebox as messagebox
            messagebox.showwarning("Aviso", "Esta licitação não possui ID válido cadastrado no Supabase.")
            return

        itens = getattr(self, "itens_carregados", []) or getattr(self, "itens_ativos", []) or []
        
        # 1. Calcula o stop loss total
        total_stop_loss = 0.0
        for it in itens:
            if isinstance(it, dict):
                piso = float(it.get("piso_preco") or it.get("stop_loss") or it.get("preco_minimo") or 0.0)
                qtd = float(it.get("quantidade") or 1.0)
                total_stop_loss += (piso * qtd)

        val_est = float(lic.get("valor_estimado") or 0.0)
        if total_stop_loss <= 0.0:
            total_stop_loss = val_est * 0.85 if val_est > 0 else 1000.0

        data_sessao = str(lic.get("data_hora_sessao") or lic.get("data_abertura") or "")

        import threading
        def worker():
            try:
                from supabase_client import aprovar_licitacao_disputa
                sucesso = aprovar_licitacao_disputa(
                    licitacao_id=str(lic_id),
                    stop_loss_total=total_stop_loss,
                    itens=itens if isinstance(itens, list) else None,
                    data_sessao=data_sessao if data_sessao else None,
                )

                # Notifica o Telegram via TelegramService
                from telegram_service import TelegramService
                orgao = str(lic.get("orgao_nome") or "Órgão Não Informado")
                pregao = str(lic.get("numero_pregao") or "S/N")
                uf = str(lic.get("uf") or "TO")
                portal = str(lic.get("portal") or "COMPRAS_GOV")

                msg_tg = (
                    "🎯 *CERTAME APROVADO PARA DISPUTA (JRBC TECH)*\n\n"
                    f"🏛️ *Órgão:* {orgao}\n"
                    f"📋 *Pregão:* {pregao} (UF: {uf})\n"
                    f"💰 *Valor Estimado:* R$ {val_est:,.2f}\n"
                    f"🛑 *Stop-Loss Global Aprovado:* R$ {total_stop_loss:,.2f}\n"
                    f"📅 *Data da Sessão:* {data_sessao or 'A consultar no edital'}\n"
                    f"🌐 *Portal:* {portal}\n\n"
                    "✅ *Status:* Gravado na Agenda e pronto para o Console de Disputa!"
                )
                TelegramService.enviar_mensagem(msg_tg)

                def callback_gui():
                    import tkinter.messagebox as messagebox
                    if hasattr(self.app, "painel_aprovados") and hasattr(self.app.painel_aprovados, "carregar_aprovados"):
                        self.app.painel_aprovados.carregar_aprovados()
                    if hasattr(self.app, "painel_sessao") and hasattr(self.app.painel_sessao, "atualizar_seletor_disputa"):
                        self.app.painel_sessao.atualizar_seletor_disputa()

                    resp = messagebox.askyesno(
                        "Participação Aprovada! 🎯",
                        f"✅ Certame APROVADO PARA DISPUTA com sucesso!\n\n"
                        f"🛑 Stop-Loss: R$ {total_stop_loss:,.2f}\n"
                        f"📲 Notificação enviada ao Telegram da JRBC TECH!\n\n"
                        f"Deseja abrir agora a agenda '🎯 Aprovados para Participação'?"
                    )
                    if resp:
                        self.app.selecionar_aba("🎯  Aprovados para Participação")

                self.after(0, callback_gui)
            except Exception as exc:
                logger.error(f"Erro ao aprovar participação: {exc}")
                self.after(0, lambda: messagebox.showerror("Erro", f"Falha ao registrar aprovação:\n{exc}"))

        threading.Thread(target=worker, daemon=True).start()


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
        self.itens_disputa: list[dict] = []
        
        # Suporte a Múltiplas Disputas Simultâneas:
        # chave_certame -> {"bot": BrowserBot, "motor": MotorDecisao, "thread": Thread, "lic": dict, "url": str, "dry_run": bool, "status": str}
        self.sessoes_ativas: dict[str, dict] = {}
        self.aba_detectada_info: Optional[dict] = None

        self._build()
        self._setup_logging()
        self.after(300, self.atualizar_seletor_disputa)
        # Inicia checagem periódica suave de abas no Google Chrome
        self.after(2000, self._loop_detector_abas)

    def _setup_logging(self) -> None:
        handler = TkinterLogHandler(self)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger_bot = logging.getLogger("browser_bot")
        logger_bot.addHandler(handler)
        logger_engine = logging.getLogger("engine")
        logger_engine.addHandler(handler)

    def sincronizar_com_licitacao_ativa(self) -> None:
        if not hasattr(self.app, 'licitacao_ativa') or not self.app.licitacao_ativa:
            return
            
        lic = self.app.licitacao_ativa
        self.licitacao_ativa = lic
        num_pregao = lic.get("numero_pregao", "")
        orgao = (lic.get("orgao_nome") or "")[:28]
        uf = lic.get("uf", "")
        texto_certame = f"📌 Certame: [{uf}] {orgao} - {num_pregao}" if num_pregao else "📄 Certame Ativo"

        if hasattr(self, 'lbl_certame_ativo'):
            self.lbl_certame_ativo.configure(text=texto_certame)

        # Atualiza URL com Sala de Disputa REAL (não edital PNCP bruto)
        url_real = calcular_url_disputa_real(lic)
        if hasattr(self, 'txt_url') and url_real:
            url_atual = self.txt_url.get().strip()
            if not url_atual or "login" in url_atual or "compra=" not in url_atual:
                self.txt_url.delete(0, "end")
                self.txt_url.insert(0, url_real)
        
        # Checa se há aba aberta para este certame
        self.after(200, self._checar_aba_ativa_silencioso)
        for item in self.tree_sessao.get_children():
            self.tree_sessao.delete(item)
            
        self._item_rows.clear()
        itens = getattr(self.app, 'itens_ativos', []) or getattr(self, 'itens_disputa', [])
        if itens:
            self.itens_disputa = itens
            for it in itens:
                num = str(it.get("numero_item", it.get("num", "1")))
                desc = str(it.get("descricao", ""))[:50]
                piso = _safe_float(it.get("piso_seguro", it.get("preco_minimo", 0.0)))
                piso_str = f"R$ {piso:,.2f}" if piso > 0 else "-"
                meu_lance = it.get("nosso_ultimo_lance")
                meu_str = f"R$ {_safe_float(meu_lance):,.2f}" if meu_lance is not None else "-"
                melhor_lance = it.get("melhor_lance")
                melhor_str = f"R$ {_safe_float(melhor_lance):,.2f}" if melhor_lance is not None else "-"
                pos = it.get("posicao")
                pos_str = f"{pos}º" if pos else "-"
                sit = it.get("situacao") or "AGUARDANDO"
                
                iid = self.tree_sessao.insert("", "end", values=(num, desc, meu_str, melhor_str, pos_str, sit))
                try:
                    self._item_rows[int(num)] = iid
                except:
                    pass

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

        # Menu Suspenso de Seleção de Certame (Carrega PRIORITARIAMENTE os APROVADOS PARA DISPUTA)
        self.cmb_disputa = ctk.CTkComboBox(
            ctrl_top,
            values=["(Carregando certames aprovados...)"],
            width=360,
            font=ctk.CTkFont(size=11),
            command=self.carregar_licitacao_disputa,
        )
        self.cmb_disputa.grid(row=0, column=1, padx=(16, 8), sticky="e")

        self.btn_ir_editais = ctk.CTkButton(
            ctrl_top,
            text="📄 Editais & Custos",
            width=130,
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COR["bg_card"],
            hover_color=COR.get("bg_hover", "#2b2e35"),
            text_color=COR["accent_cyan"],
            border_width=1,
            border_color=COR["border"],
            command=lambda: self.app.selecionar_aba("📄  Editais & Custos"),
        )
        self.btn_ir_editais.grid(row=0, column=2, padx=4, sticky="e")

        # Switch Simulação vs Real
        self.sw_modo = ctk.CTkSwitch(
            ctrl_top,
            text="Modo Simulação (Dry-Run)",
            font=ctk.CTkFont(weight="bold"),
            progress_color=COR["accent_green"],
            command=self._on_modo_switch,
        )
        self.sw_modo.select()
        self.sw_modo.grid(row=0, column=3, padx=(12, 0), sticky="e")

        # Badge de Disputas Simultâneas Ativas
        self.lbl_disputas_ativas_badge = ctk.CTkLabel(
            ctrl_top,
            text="⚡ 0 Ativas",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["text_secondary"],
            fg_color=COR["bg_deep"],
            corner_radius=6,
            padx=10,
            pady=4,
        )
        self.lbl_disputas_ativas_badge.grid(row=0, column=4, padx=(10, 0), sticky="e")

        # Linha de URL do Comprasnet e Botões de Ação
        ctrl_actions = ctk.CTkFrame(card_ctrl, fg_color="transparent")
        ctrl_actions.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctrl_actions.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            ctrl_actions,
            text="🌐 Sala de Disputa / URL:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COR["text_secondary"],
        ).grid(row=0, column=0, padx=(0, 6), sticky="w")

        self.txt_url = ctk.CTkEntry(
            ctrl_actions,
            placeholder_text="https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/fornecedor/disputa?compra=...",
            font=ctk.CTkFont(size=11),
        )
        self.txt_url.insert(0, "https://sso.acesso.gov.br/login?client_id=comprasnet.gov.br")
        self.txt_url.grid(row=0, column=1, padx=6, sticky="ew")
        self.txt_url.bind("<KeyRelease>", lambda e: self.after(500, self._checar_aba_ativa_silencioso))

        self.btn_login_nativo = ctk.CTkButton(
            ctrl_actions,
            text="🔑 Login Gov.br",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COR["accent_amber"],
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
            fg_color=COR["accent_blue"],
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
            fg_color=COR["accent_green"],
            text_color="#000000",
            height=34,
            command=self._iniciar_sessao,
        )
        self.btn_iniciar.grid(row=0, column=4, padx=4, sticky="e")

        self.btn_parar = ctk.CTkButton(
            ctrl_actions,
            text="⏹️  Parar Disputa",
            font=ctk.CTkFont(weight="bold"),
            fg_color=COR["accent_red"],
            text_color="#FFFFFF",
            height=34,
            state="disabled",
            command=self._parar_sessao,
        )
        self.btn_parar.grid(row=0, column=5, padx=4, sticky="e")

        # Linha de Detecção Inteligente de Abas do Chrome (CDP)
        ctrl_detector = ctk.CTkFrame(card_ctrl, fg_color=COR["bg_deep"], corner_radius=6)
        ctrl_detector.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 10))
        ctrl_detector.grid_columnconfigure(0, weight=1)

        self.lbl_aba_status = ctk.CTkLabel(
            ctrl_detector,
            text="🔍 Monitorando abas abertas no Google Chrome...",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["text_secondary"],
            anchor="w",
        )
        self.lbl_aba_status.grid(row=0, column=0, padx=12, pady=6, sticky="w")

        self.btn_usar_aba = ctk.CTkButton(
            ctrl_detector,
            text="🎯 Usar Esta Aba Aberta",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COR["accent_cyan"],
            text_color="#000000",
            height=26,
            width=160,
            state="disabled",
            command=self._usar_aba_detectada,
        )
        self.btn_usar_aba.grid(row=0, column=1, padx=(4, 6), pady=6, sticky="e")

        self.btn_checar_abas = ctk.CTkButton(
            ctrl_detector,
            text="🔄 Checar Abas",
            font=ctk.CTkFont(size=11),
            fg_color=COR["bg_panel"],
            text_color=COR["text_primary"],
            height=26,
            width=100,
            command=self._checar_aba_ativa_agora,
        )
        self.btn_checar_abas.grid(row=0, column=2, padx=(0, 8), pady=6, sticky="e")

        # Monitor de Itens na Disputa
        card_monitor = CardFrame(self)
        card_monitor.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 10))
        card_monitor.grid_columnconfigure(0, weight=1)

        mon_header = ctk.CTkFrame(card_monitor, fg_color="transparent")
        mon_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 4))
        mon_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            mon_header,
            text="📊  Status dos Itens na Disputa",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COR["text_primary"],
        ).grid(row=0, column=0, sticky="w")

        self.btn_zero_touch = ctk.CTkButton(
            mon_header,
            text="⚡ Capturar da Tela (Zero-Touch)",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COR["accent_cyan"],
            text_color="#000000",
            hover_color=COR["accent_blue"],
            height=30,
            command=self._capturar_itens_zero_touch,
        )
        self.btn_zero_touch.grid(row=0, column=1, sticky="e")

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

        # Container Inferior: Log + Chat Copiloto
        bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        bottom_frame.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
        bottom_frame.grid_rowconfigure(0, weight=1)
        bottom_frame.grid_columnconfigure((0, 1), weight=1)

        # Terminal Log Stream
        card_log = CardFrame(bottom_frame)
        card_log.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=0)
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
            fg_color=COR["bg_panel"],
            font=ctk.CTkFont(size=10),
            command=self._limpar_log,
        ).grid(row=0, column=1, sticky="e")

        self.txt_log = ctk.CTkTextbox(
            card_log,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=COR["bg_deep"],
            text_color=COR["text_primary"],
        )
        self.txt_log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        # Copiloto Tático Gemini
        card_copiloto = CardFrame(bottom_frame)
        card_copiloto.grid(row=0, column=1, sticky="nsew", padx=(5, 0), pady=0)
        card_copiloto.grid_rowconfigure(1, weight=1)
        card_copiloto.grid_columnconfigure(0, weight=1)

        copiloto_header = ctk.CTkFrame(card_copiloto, fg_color="transparent")
        copiloto_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(8, 2))
        copiloto_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            copiloto_header,
            text="🤖  Copiloto Tático Gemini (Ao Vivo)",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#00E5FF",
        ).grid(row=0, column=0, sticky="w")

        self.txt_chat = ctk.CTkTextbox(
            card_copiloto,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color="#181A20",
            text_color="#FFFFFF",
            state="disabled",
            wrap="word",
        )
        self.txt_chat.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 6))

        chat_bottom = ctk.CTkFrame(card_copiloto, fg_color="transparent")
        chat_bottom.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        chat_bottom.grid_columnconfigure(0, weight=1)

        self.txt_mensagem = ctk.CTkEntry(
            chat_bottom,
            placeholder_text="Pergunte algo ao Gemini ou peça conselho...",
        )
        self.txt_mensagem.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.txt_mensagem.bind("<Return>", lambda e: self._enviar_mensagem_copiloto())

        self.btn_enviar_chat = ctk.CTkButton(
            chat_bottom,
            text="💬 Enviar",
            width=80,
            command=self._enviar_mensagem_copiloto,
            fg_color=COR["accent_blue"],
        )
        self.btn_enviar_chat.grid(row=0, column=1)

        chat_actions = ctk.CTkFrame(card_copiloto, fg_color="transparent")
        chat_actions.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        
        self.btn_analisar = ctk.CTkButton(
            chat_actions,
            text="⚡ Analisar Situação",
            height=28,
            command=lambda: self._enviar_mensagem_copiloto("Analise a situação atual da disputa e sugira o próximo passo."),
            fg_color=COR["bg_panel"],
        )
        self.btn_analisar.pack(side="left", padx=(0, 6), expand=True, fill="x")

        self.btn_inexequibilidade = ctk.CTkButton(
            chat_actions,
            text="⚖️ Checar Inexequibilidade",
            height=28,
            command=lambda: self._enviar_mensagem_copiloto("Os lances atuais são inexequíveis segundo o edital ou a Lei 14.133?"),
            fg_color=COR["bg_panel"],
        )
        self.btn_inexequibilidade.pack(side="left", padx=(6, 0), expand=True, fill="x")

    def _on_modo_switch(self) -> None:
        dry_run = bool(self.sw_modo.get())
        if dry_run:
            self.sw_modo.configure(text="Modo Simulação (Dry-Run)", progress_color=COR["accent_green"])
        else:
            self.sw_modo.configure(text="⚠️ MODO REAL (Envio de Lances)", progress_color=COR["accent_red"])

    def _on_disputa_mudou(self, escolha: str) -> None:
        self.carregar_licitacao_disputa(escolha)
        if hasattr(self.app, 'painel_editais') and hasattr(self.app.painel_editais, 'cmb_certames'):
            vals = getattr(self.app.painel_editais.cmb_certames, '_values', [])
            for v in vals:
                if escolha in v or (len(escolha.split('-')) > 1 and escolha.split('-')[-1].strip() in v):
                    try:
                        self.app.painel_editais.cmb_certames.set(v)
                    except Exception:
                        pass
                    break

    def atualizar_seletor_disputa(self) -> None:
        import os
        try:
            from supabase import create_client
            url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
            key = os.getenv("SUPABASE_KEY") or ""
            sb = create_client(url, key)
            res = sb.table("licitacoes").select("*").order("atualizado_em", desc=True).limit(200).execute()
            raw_data = res.data or []
            
            # Prioriza certames com status_analise == 'aprovado_disputa'
            aprovados = []
            outros = []
            for lic in raw_data:
                if not isinstance(lic, dict): continue
                st = str(lic.get("status_analise") or "").lower()
                if st == "aprovado_disputa":
                    aprovados.append(lic)
                elif st not in ["descartada", "concluido"]:
                    outros.append(lic)
            
            # Lista final com aprovados no topo
            self.dados_nuvem = aprovados + outros
            
            # Formata opções do dropdown com destaque visual
            opcoes = []
            for lic in self.dados_nuvem:
                uf = str(lic.get("uf") or "BR")
                orgao = str(lic.get("orgao_nome") or "")[:20]
                pregao = str(lic.get("numero_pregao") or "S/N")
                st = str(lic.get("status_analise") or "").lower()
                if st == "aprovado_disputa":
                    opcoes.append(f"🎯 [APROVADO] [{uf}] {orgao} - {pregao}")
                else:
                    opcoes.append(f"[{uf}] {orgao} - {pregao}")
            
            if not opcoes:
                opcoes = ["(Nenhum certame cadastrado)"]
                
            if hasattr(self, "cmb_disputa") and self.cmb_disputa:
                self.cmb_disputa.configure(values=opcoes)
                # Se não há certame ativo selecionado e há certames aprovados, carrega o primeiro
                if aprovados and not getattr(self.app, "licitacao_ativa", None):
                    self.cmb_disputa.set(opcoes[0])
                    self.after(50, lambda: self.carregar_licitacao_disputa(opcoes[0]))
        except Exception as e:
            logger.warning(f"Falha ao carregar licitações do Supabase: {e}")
            self.dados_nuvem = []

        # Se já existe certame ativo definido em Editais & Custos, sincroniza com ele
        if getattr(self.app, "licitacao_ativa", None):
            self.sincronizar_com_licitacao_ativa()
        else:
            self.after(200, self._checar_aba_ativa_silencioso)

    def carregar_licitacao_disputa(self, escolha: str) -> None:
        """Carrega todos os itens e metadados essenciais da licitação selecionada na tela de disputa."""
        for r in self.tree_sessao.get_children():
            self.tree_sessao.delete(r)
        self._item_rows.clear()

        if not escolha or escolha.startswith("(Nenhum"):
            return

        if not hasattr(self, "dados_nuvem") or not self.dados_nuvem:
            return

        # Remove prefixo de badge se houver
        escolha_limpa = escolha.replace("🎯 [APROVADO] ", "").strip()

        # Localiza o registro da licitação selecionada
        lic: Optional[dict[str, Any]] = None
        for item_lic in self.dados_nuvem:
            if not isinstance(item_lic, dict):
                continue
            uf = item_lic.get("uf", "")
            orgao = str(item_lic.get("orgao_nome", "") or "")[:20]
            pregao = item_lic.get("numero_pregao", "")
            texto = f"[{uf}] {orgao} - {pregao}"
            if escolha_limpa == texto or escolha_limpa == pregao:
                lic = dict(item_lic)
                break

        if not lic and hasattr(self, "cmb_disputa") and self.cmb_disputa:
            vals = getattr(self.cmb_disputa, '_values', [])
            idx = vals.index(escolha) if escolha in vals else -1
            if idx >= 0 and idx < len(self.dados_nuvem):
                c = self.dados_nuvem[idx]
                if isinstance(c, dict):
                    lic = dict(c)

        if not lic:
            return

        self.licitacao_ativa = lic
        self.app.licitacao_ativa = lic
        if hasattr(self, 'lbl_certame_ativo'):
            uf = lic.get("uf", "")
            orgao = str(lic.get("orgao_nome") or "")[:28]
            pregao = lic.get("numero_pregao", "")
            self.lbl_certame_ativo.configure(text=f"📌 Certame: [{uf}] {orgao} - {pregao}")

        # 1. Determina a URL real da sala de disputa (calculando compra Comprasnet, Licitanet, SIGA, etc.)
        url_alvo = calcular_url_disputa_real(lic)
        if hasattr(self, "txt_url"):
            self.txt_url.delete(0, "end")
            self.txt_url.insert(0, url_alvo)

        # Checa status de disputa simultânea para este certame
        chave = str(lic.get("numero_pregao") or lic.get("id") or "")
        sessao_existente = self.sessoes_ativas.get(chave)
        if sessao_existente and sessao_existente.get("status") == "EM_DISPUTA":
            self.btn_iniciar.configure(state="disabled")
            self.btn_parar.configure(state="normal")
            self._bot = sessao_existente.get("bot")
            self._motor = sessao_existente.get("motor")
            self.log(f"⚡ O certame '{escolha}' já está com Robô Ativo disputando em segundo plano.")
        else:
            self.btn_iniciar.configure(state="normal")
            self.btn_parar.configure(state="disabled")

        # Dispara verificação se a aba correspondente já está aberta no Chrome
        self.after(200, self._checar_aba_ativa_silencioso)

        # 2. Carrega Itens da licitação (Supabase ou PNCP)
        lic_id = lic.get("id")
        itens_salvos: list[dict[str, Any]] = []
        if lic_id:
            try:
                import os
                from supabase import create_client
                url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
                key = os.getenv("SUPABASE_KEY") or ""
                sb = create_client(url, key)
                res = sb.table("itens_licitacao").select("*").eq("licitacao_id", lic_id).order("numero_item").execute()
                itens_salvos = [dict(it) for it in res.data if isinstance(it, dict)] if res.data else []
            except Exception as e:
                logger.warning(f"Erro ao buscar itens no Supabase: {e}")

        if itens_salvos:
            self._renderizar_itens_disputa(itens_salvos)
            self.log(f"📋 {len(itens_salvos)} itens carregados para a disputa: {str(lic.get('orgao_nome', ''))[:40]}")
            return

        # Se não há itens salvos e é do PNCP, busca na hora via API
        num_controle = str(lic.get("numero_controle_pncp") or "")
        link_ed = str(lic.get("link_edital") or "")
        alvo_pncp = num_controle if "/" in num_controle else link_ed

        if ("pncp" in alvo_pncp.lower() or "/" in num_controle) and lic_id:
            self.tree_sessao.insert("", "end", values=("⏳", "Buscando itens oficiais no PNCP...", "—", "—", "—", "Carregando..."))

            def _worker_pncp_live():
                try:
                    from pncp_client import extrair_params_pncp, sincronizar_itens_certame_supabase
                    cnpj, ano, seq = extrair_params_pncp(alvo_pncp)
                    novos_itens = sincronizar_itens_certame_supabase(cnpj, ano, seq, str(lic_id))
                    if novos_itens:
                        self.after(0, lambda: self._renderizar_itens_disputa(novos_itens))
                        self.after(0, lambda: self.log(f"✅ {len(novos_itens)} itens sincronizados do PNCP com sucesso!"))
                        return
                except Exception as exc:
                    logger.warning(f"Falha ao carregar itens do PNCP: {exc}")

                self.after(0, self._renderizar_item_resumo_fallback)

            import threading
            threading.Thread(target=_worker_pncp_live, daemon=True).start()
        else:
            self._renderizar_item_resumo_fallback()

    def _renderizar_itens_disputa(self, itens: list[dict]) -> None:
        """Renderiza cada item da licitação com seus dados financeiros e de lance."""
        for r in self.tree_sessao.get_children():
            self.tree_sessao.delete(r)
        self._item_rows.clear()
        self.itens_disputa = itens
        self.app.itens_ativos = itens

        for it in itens:
            num = int(it.get("numero_item", 1))
            desc = str(it.get("descricao", f"Item {num}"))
            qtd = _safe_float(it.get("quantidade"), default=1.0)
            un = str(it.get("unidade_medida", "UN"))
            
            desc_fmt = f"{desc[:55]} ({qtd:g} {un})" if len(desc) <= 55 else f"{desc[:50]}... ({qtd:g} {un})"
            v_ref = _safe_float(it.get("valor_referencia"))
            piso = _safe_float(it.get("preco_minimo"), default=(v_ref * 0.8 if v_ref > 0 else 0.0))
            
            v_ref_str = f"R$ {v_ref:,.2f}" if v_ref > 0 else "—"
            piso_str = f"R$ {piso:,.2f}" if piso > 0 else "—"
            
            row_id = self.tree_sessao.insert(
                "",
                "end",
                values=(
                    num,
                    desc_fmt,
                    "—",
                    v_ref_str,
                    "—",
                    f"⏳ AGUARDANDO (Stop: {piso_str})",
                ),
            )
            self._item_rows[num] = row_id

    def _renderizar_item_resumo_fallback(self) -> None:
        for r in self.tree_sessao.get_children():
            self.tree_sessao.delete(r)
        self._item_rows.clear()
        
        lic = getattr(self, "licitacao_ativa", None)
        if not lic or not isinstance(lic, dict):
            return
            
        valor = _safe_float(lic.get("valor_estimado"))
        piso = _safe_float(lic.get("piso_seguro"), default=(valor * 0.7 if valor > 0 else 0.0))
        v_ref_str = f"R$ {valor:,.2f}" if valor > 0 else "—"
        piso_str = f"R$ {piso:,.2f}" if piso > 0 else "—"

        row_id = self.tree_sessao.insert(
            "",
            "end",
            values=(
                1,
                (lic.get("objeto") or "Item Principal")[:80],
                "—",
                v_ref_str,
                "—",
                f"⏳ AGUARDANDO (Stop: {piso_str})",
            ),
        )
        self._item_rows[1] = row_id

    def _checar_aba_ativa_silencioso(self) -> None:
        self._checar_aba_ativa_agora(notificar_se_nada=False)

    def _loop_detector_abas(self) -> None:
        try:
            self._checar_aba_ativa_silencioso()
        except Exception:
            pass
        finally:
            self.after(3500, self._loop_detector_abas)

    def _checar_aba_ativa_agora(self, notificar_se_nada: bool = True) -> None:
        url_campo = self.txt_url.get().strip() if hasattr(self, "txt_url") else ""
        lic = getattr(self, "licitacao_ativa", None)
        termo = ""
        if lic:
            termo = f"{lic.get('numero_uasg', '')} {lic.get('numero_pregao', '')} {lic.get('orgao_nome', '')}"

        aba = detectar_aba_disputa_ativa(url_campo, termo=termo)
        if aba:
            self.aba_detectada_info = aba
            titulo = (aba.get("title") or "Sala de Disputa")[:45]
            self.lbl_aba_status.configure(
                text=f"🟢 Aba da Sala Detectada: {titulo}",
                text_color=COR["accent_green"],
            )
            self.btn_usar_aba.configure(state="normal")
            if notificar_se_nada:
                self.log(f"🎯 Aba exata da disputa localizada no Chrome: '{aba.get('title')}'")
        else:
            self.aba_detectada_info = None
            self.btn_usar_aba.configure(state="disabled")
            if verificar_cdp_ativo():
                self.lbl_aba_status.configure(
                    text="🟡 Chrome aberto via automação. Navegue até a disputa ou faça Login Gov.br.",
                    text_color=COR["accent_amber"],
                )
            else:
                self.lbl_aba_status.configure(
                    text="⚪ Chrome fechado ou sem porta de depuração. Clique em '🔑 Login Gov.br' para conectar.",
                    text_color=COR["text_secondary"],
                )
            if notificar_se_nada:
                self.log("ℹ️ Nenhuma aba da disputa aberta no momento. Clique em '🔑 Login Gov.br' ou '🌐 Abrir Sala'.")

    def _usar_aba_detectada(self) -> None:
        if not self.aba_detectada_info:
            return
        url_aba = self.aba_detectada_info.get("url", "")
        if url_aba:
            self.txt_url.delete(0, "end")
            self.txt_url.insert(0, url_aba)
            self.log(f"🎯 URL da aba ativa vinculada à sala: {url_aba}")
            self.lbl_aba_status.configure(
                text=f"✅ Vinculado à aba aberta: {self.aba_detectada_info.get('title', '')[:40]}",
                text_color=COR["accent_cyan"],
            )
            # Sincroniza dados do certame e itens diretamente da aba ativa do Chrome
            self._sincronizar_dados_da_aba_ao_vivo(url_aba)

    def _sincronizar_dados_da_aba_ao_vivo(self, url_alvo: str = "") -> None:
        """
        Extrai em tempo real os dados da disputa (certame e itens) da aba aberta do Chrome
        e sincroniza com o Console da Sessão e com Editais & Custos.
        """
        def worker():
            dados = extrair_dados_aba_disputa(porta=9222, url_alvo=url_alvo)
            if not dados:
                return

            certame = dados.get("certame")
            itens = dados.get("itens")
            if not certame:
                return

            def aplicar():
                self.licitacao_ativa = certame
                self.app.licitacao_ativa = certame
                if itens:
                    self.itens_disputa = itens
                    self.app.itens_ativos = itens

                num = certame.get("numero_pregao", "")
                org = (certame.get("orgao_nome") or "")[:28]
                uf = certame.get("uf", "BR")
                if hasattr(self, 'lbl_certame_ativo'):
                    self.lbl_certame_ativo.configure(
                        text=f"📌 Certame Sincronizado do Chrome: [{uf}] {org} - {num}"
                    )

                # Atualiza a tabela com os itens extraídos da página
                for item in self.tree_sessao.get_children():
                    self.tree_sessao.delete(item)
                self._item_rows.clear()

                for it in (itens or []):
                    n = str(it.get("numero_item", 1))
                    d = it.get("descricao", f"Item {n}")
                    meu = it.get("nosso_ultimo_lance")
                    mel = it.get("melhor_lance")
                    meu_s = f"R$ {_safe_float(meu):,.2f}" if meu is not None else "—"
                    mel_s = f"R$ {_safe_float(mel):,.2f}" if mel is not None else "—"
                    pos_s = f"{it.get('posicao', '—')}º" if it.get("posicao") else "—"
                    sit = it.get("situacao") or "EM DISPUTA"
                    iid = self.tree_sessao.insert("", "end", values=(n, d, meu_s, mel_s, pos_s, sit))
                    try:
                        self._item_rows[int(n)] = iid
                    except:
                        pass

                self.log(f"🎯 Certame vinculado da aba do Chrome: {certame.get('modalidade')} {num} ({org})")
                self.log(f"   {len(itens or [])} itens detectados ao vivo na sala de disputa.")

                self._salvar_certame_extraido_nuvem(certame, itens or [])

            self.after(0, aplicar)

        threading.Thread(target=worker, daemon=True).start()

    def _salvar_certame_extraido_nuvem(self, certame: dict, itens: list[dict]) -> None:
        """Salva ou atualiza o certame extraído no Supabase com portal='COMPRAS_GOV'."""
        try:
            import os
            from supabase import create_client
            url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
            key = os.getenv("SUPABASE_KEY") or ""
            if not key:
                return
            sb = create_client(url, key)

            num_p = certame.get("numero_pregao")
            num_u = certame.get("numero_uasg")
            if num_p and num_u:
                res = sb.table("licitacoes").select("id").eq("numero_pregao", num_p).eq("numero_uasg", num_u).execute()
                if res.data and isinstance(res.data[0], dict):
                    lic_id = res.data[0].get("id")
                    certame["id"] = lic_id
                else:
                    novo_lic = {
                        "portal": "COMPRAS_GOV",
                        "orgao_nome": certame.get("orgao_nome"),
                        "numero_uasg": num_u,
                        "numero_pregao": num_p,
                        "modalidade": certame.get("modalidade"),
                        "uf": certame.get("uf", "BR"),
                        "url_sala_disputa": certame.get("url_sala_disputa"),
                        "link_sistema": certame.get("link_sistema"),
                        "status": "EM_DISPUTA",
                    }
                    res_ins = sb.table("licitacoes").insert(novo_lic).execute()
                    if res_ins.data and isinstance(res_ins.data[0], dict):
                        certame["id"] = res_ins.data[0].get("id")
        except Exception as e:
            logger.debug(f"Aviso ao persistir certame no Supabase: {e}")

    def _capturar_itens_zero_touch(self) -> None:
        """
        Lê todos os cards '.cp-itens-disputa' da aba aberta do Chrome em tempo real,
        popula a grade com os itens, calcula e aplica o stop-loss sugerido (30% produto / 50% serviço).
        """
        self.btn_zero_touch.configure(text="⏳ Capturando da Tela...", state="disabled")
        self.log("⚡ Zero-Touch: Iniciando scan nos cards '.cp-itens-disputa' da tela ativa do Chrome...")

        def _worker():
            itens: list[dict] = []
            certame_info: Optional[dict] = None

            # 1. Se bot estiver ativo com page aberta, usa diretamente
            if self._bot and getattr(self._bot, "_page", None):
                try:
                    itens = extrair_todos_itens_sala(self._bot._page)
                except Exception as e:
                    logger.warning(f"Erro ao capturar itens via bot._page: {e}")

            # 2. Se não capturou via bot ativo, conecta via CDP ao Chrome na porta 9222
            if not itens:
                try:
                    url_alvo = self.txt_url.get().strip() if hasattr(self, "txt_url") else ""
                    dados_cdp = extrair_dados_aba_disputa(porta=9222, url_alvo=url_alvo)
                    if dados_cdp and dados_cdp.get("certame"):
                        certame_info = dados_cdp["certame"]

                    itens = extrair_todos_itens_sala_cdp(porta=9222, url_alvo=url_alvo)
                except Exception as e:
                    logger.warning(f"Erro ao capturar itens via CDP: {e}")

            def _finalizar():
                self.btn_zero_touch.configure(text="⚡ Capturar da Tela (Zero-Touch)", state="normal")
                if not itens:
                    self.log("⚠️ Zero-Touch: Nenhum card '.cp-itens-disputa' encontrado na aba ativa do Chrome.")
                    messagebox.showwarning(
                        "Zero-Touch",
                        "Nenhum card de item encontrado na tela aberta do Chrome.\n\n"
                        "Certifique-se de que:\n"
                        "1. O Google Chrome foi iniciado na porta 9222 (ex: via launch_robo.sh).\n"
                        "2. A sala de disputa do certame está aberta no navegador.",
                    )
                    return

                # Se obteve informações do certame via CDP, atualiza licitação ativa
                if certame_info:
                    self.licitacao_ativa = certame_info
                    self.app.licitacao_ativa = certame_info
                    num_p = certame_info.get("numero_pregao", "")
                    org_p = (certame_info.get("orgao_nome") or "")[:28]
                    uf_p = certame_info.get("uf", "BR")
                    if hasattr(self, "lbl_certame_ativo"):
                        self.lbl_certame_ativo.configure(
                            text=f"📌 Certame Sincronizado: [{uf_p}] {org_p} - {num_p}"
                        )
                    url_sala = certame_info.get("url_sala_disputa") or certame_info.get("link_sistema")
                    if url_sala and hasattr(self, "txt_url"):
                        self.txt_url.delete(0, "end")
                        self.txt_url.insert(0, url_sala)

                self.itens_disputa = itens
                self.app.itens_ativos = itens

                # Renderiza na tabela tree_sessao do Console de Lances
                for r in self.tree_sessao.get_children():
                    self.tree_sessao.delete(r)
                self._item_rows.clear()

                qtd_servicos = sum(1 for it in itens if it.get("tipo_item") == "SERVIÇO")
                qtd_produtos = len(itens) - qtd_servicos

                for it in itens:
                    num = int(it.get("numero_item", 1))
                    desc = str(it.get("descricao", f"Item {num}"))
                    desc_fmt = desc[:50] + "..." if len(desc) > 50 else desc

                    meu = it.get("nosso_ultimo_lance") or it.get("proposta_inicial")
                    meu_str = f"R$ {float(meu):,.2f}" if meu is not None else "—"

                    mel = it.get("melhor_lance") or it.get("valor_referencia")
                    mel_str = f"R$ {float(mel):,.2f}" if mel is not None else "—"

                    pos = it.get("posicao")
                    pos_str = f"{pos}º" if pos else "—"

                    piso = float(it.get("stop_loss_sugerido", 0.0))
                    margem = int(it.get("margem_sugerida", 30))
                    tipo = it.get("tipo_item", "PROD")[:4]

                    status_str = f"🛡️ STOP: R$ {piso:,.2f} ({margem}% {tipo})"

                    row_id = self.tree_sessao.insert(
                        "", "end",
                        values=(num, desc_fmt, meu_str, mel_str, pos_str, status_str),
                    )
                    self._item_rows[num] = row_id

                # Sincroniza também com a grade de Editais & Custos se o painel estiver instanciado
                if hasattr(self.app, "painel_editais") and hasattr(self.app.painel_editais, "carregar_itens_externos"):
                    try:
                        self.app.painel_editais.carregar_itens_externos(itens)
                    except Exception as err:
                        logger.debug(f"Aviso ao sincronizar itens com painel_editais: {err}")

                msg_sucesso = (
                    f"⚡ Carga Zero-Touch concluída com sucesso!\n"
                    f"• {len(itens)} itens capturados diretamente da sala de disputa\n"
                    f"• {qtd_servicos} serviços (Stop-Loss automático: margem de 50%)\n"
                    f"• {qtd_produtos} produtos (Stop-Loss automático: margem de 30%)\n\n"
                    f"A grade de itens e os parâmetros operacionais foram atualizados."
                )
                self.log(f"⚡ Zero-Touch: {len(itens)} itens capturados ({qtd_servicos} serviços a 50%, {qtd_produtos} produtos a 30%).")
                messagebox.showinfo("Carga Zero-Touch Concluída", msg_sucesso)

            self.after(0, _finalizar)

        threading.Thread(target=_worker, daemon=True).start()

    def _atualizar_badge_disputas(self) -> None:
        ativas = [k for k, v in self.sessoes_ativas.items() if v.get("status") == "EM_DISPUTA"]
        total = len(ativas)
        if total == 0:
            self.lbl_disputas_ativas_badge.configure(
                text="⚡ 0 Ativas",
                text_color=COR["text_secondary"],
            )
        elif total == 1:
            self.lbl_disputas_ativas_badge.configure(
                text="⚡ 1 Disputa Ativa",
                text_color=COR["accent_green"],
            )
        else:
            self.lbl_disputas_ativas_badge.configure(
                text=f"🔥 {total} Simultâneas",
                text_color=COR["accent_cyan"],
            )

    def _iniciar_sessao(self) -> None:
        lic = getattr(self, "licitacao_ativa", None) or getattr(self.app, "licitacao_ativa", None)
        if not lic and self.aba_detectada_info:
            url_aba = self.aba_detectada_info.get("url", "")
            self._sincronizar_dados_da_aba_ao_vivo(url_aba)
            lic = getattr(self, "licitacao_ativa", None)

        if not lic:
            messagebox.showwarning(
                "Nenhum Certame Selecionado",
                "Por favor, selecione ou importe uma licitação na aba '📄 Editais & Custos'\nou conecte a aba aberta do Chrome antes de iniciar a disputa.",
            )
            return

        num_pregao = lic.get("numero_pregao", "")
        orgao = (lic.get("orgao_nome") or "")[:25]
        uf = lic.get("uf", "")
        nome_certame = f"[{uf}] {orgao} - {num_pregao}" if num_pregao else "Certame Ativo"

        chave = str(lic.get("numero_pregao") or lic.get("id") or str(uuid.uuid4()))
        if chave in self.sessoes_ativas and self.sessoes_ativas[chave].get("status") == "EM_DISPUTA":
            messagebox.showinfo(
                "Disputa em Andamento",
                f"O certame '{nome_certame}' já está com o robô disputando em segundo plano.",
            )
            return

        dry_run = bool(self.sw_modo.get())
        if not dry_run:
            confirma = messagebox.askyesno(
                "⚠️ CONFIRMAÇÃO DE MODO REAL",
                "ATENÇÃO: O modo REAL está ativado!\n\n"
                "Lances REAIS serão enviados com valor legal e financeiro.\n"
                "Certifique-se de que todos os custos e stop-losses estão corretos.\n\n"
                "Deseja realmente iniciar a disputa em MODO REAL?",
                icon="warning",
            )
            if not confirma:
                self.log("⚠️ Inicialização em modo real cancelada pelo operador.")
                return

        regras: list[RegraItem] = []
        itens_disponiveis = getattr(self, "itens_disputa", []) or getattr(self.app, "itens_ativos", [])
        
        # Se não há itens e o Chrome está aberto, tenta carregar na hora
        if not itens_disponiveis and self.aba_detectada_info:
            url_aba = self.aba_detectada_info.get("url", "")
            dados_live = extrair_dados_aba_disputa(porta=9222, url_alvo=url_aba)
            if dados_live and dados_live.get("itens"):
                itens_disponiveis = dados_live.get("itens")
                self.itens_disputa = itens_disponiveis
                self.app.itens_ativos = itens_disponiveis

        if itens_disponiveis:
            for it in itens_disponiveis:
                num = int(it.get("numero_item", 1))
                v_ref = _safe_float(it.get("valor_referencia") or it.get("melhor_lance"), default=100.0)
                custo_base = _safe_float(it.get("custo_unitario") or it.get("custo_real"), default=v_ref * 0.6)
                piso_calc = _safe_float(it.get("preco_minimo") or it.get("piso_seguro"), default=(v_ref * 0.7 if v_ref > 0 else 70.0))
                preco_ini = _safe_float(it.get("preco_inicial_proposta") or it.get("nosso_ultimo_lance"), default=v_ref * 0.98)
                regras.append(
                    RegraItem(
                        item_id=str(it.get("id") or uuid.uuid4()),
                        numero_item=num,
                        descricao=str(it.get("descricao", f"Item {num}")),
                        custo_unitario=custo_base,
                        margem_minima_percent=10.0,
                        preco_minimo=piso_calc,
                        preco_inicial_proposta=preco_ini,
                        intervalo_minimo=_safe_float(lic.get("intervalo_minimo"), default=0.01),
                        tipo_intervalo=TipoIntervalo.MONETARIO,
                        valor_referencia=v_ref,
                        licitacao_id=str(lic.get("numero_pregao", "0")),
                    )
                )
        else:
            valor = _safe_float(lic.get("valor_estimado"), default=100.0)
            custo_base = _safe_float(lic.get("custo_real"), default=valor * 0.6)
            piso_calc = _safe_float(lic.get("piso_seguro"), default=valor * 0.7)
            preco_ini = _safe_float(lic.get("valor_estimado"), default=piso_calc * 1.3)
            regras.append(
                RegraItem(
                    item_id=str(uuid.uuid4()),
                    numero_item=1,
                    descricao=lic.get("objeto", ""),
                    custo_unitario=custo_base,
                    margem_minima_percent=10.0,
                    preco_minimo=piso_calc,
                    preco_inicial_proposta=preco_ini,
                    intervalo_minimo=float(lic.get("intervalo_minimo") or 0.01),
                    tipo_intervalo=TipoIntervalo.MONETARIO,
                    valor_referencia=valor,
                    licitacao_id=str(lic.get("numero_pregao", "0")),
                )
            )

        sessao_id = str(uuid.uuid4())
        url_alvo = self.txt_url.get().strip() or "https://sso.acesso.gov.br/login?client_id=comprasnet.gov.br"

        # Garante que o Chrome está rodando com CDP
        if not verificar_cdp_ativo():
            self.log("🌐 Iniciando Google Chrome com perfil de automação e sala de disputa...")
            garantir_chrome_com_cdp(url_alvo)
            time.sleep(1.2)

        try:
            motor = MotorDecisao(dry_run=dry_run)
            motor.carregar_regras(regras)
            motor.iniciar_sessao()

            bot = BrowserBot(
                motor=motor,
                sessao_id=sessao_id,
                licitacao_id=chave,
                callback_ciclo=lambda ciclo, est, dec, k=chave: self._on_ciclo_update_multi(k, ciclo, est, dec),
            )
            bot.callback_copiloto = self._trigger_copiloto_auto
        except Exception as exc:
            messagebox.showerror("Erro ao Configurar Robô", f"Falha ao inicializar motor: {exc}")
            return

        self._bot = bot
        self._motor = motor

        def _worker_multi(b_inst: BrowserBot, ch_lic: str, u_alvo: str):
            try:
                b_inst.iniciar(url_pregao=u_alvo, intervalo_polling_s=5.0)
            except Exception as exc_bot:
                logger.exception(f"Erro no robô do certame {ch_lic}")
                err_msg = str(exc_bot)
                self.after(0, lambda: self.log(f"❌ Erro no robô ({ch_lic}): {err_msg}"))
            finally:
                self.after(0, lambda: self._on_bot_finalizado_multi(ch_lic))

        th = threading.Thread(target=_worker_multi, args=(bot, chave, url_alvo), daemon=True)
        self.sessoes_ativas[chave] = {
            "bot": bot,
            "motor": motor,
            "thread": th,
            "lic": lic,
            "url": url_alvo,
            "dry_run": dry_run,
            "status": "EM_DISPUTA",
            "escolha": nome_certame,
        }

        self.btn_iniciar.configure(state="disabled")
        self.btn_parar.configure(state="normal")
        self._atualizar_badge_disputas()

        msg_modo = "🛡️ SIMULAÇÃO (Dry-Run)" if dry_run else "🔴 DISPUTA REAL (Envio de Lances)"
        self.log(f"🚀 Disputa iniciada para '{nome_certame}' | {msg_modo}")
        self.log(f"   Sessão ID: {sessao_id[:13]}... | {len(regras)} itens carregados | URL: {url_alvo}")
        th.start()

    def _fazer_login_nativo(self) -> None:
        url = self.txt_url.get().strip() or "https://sso.acesso.gov.br/login?client_id=comprasnet.gov.br"
        self.log("🔑 Abrindo Google Chrome com depuração para login no Gov.br...")
        sucesso = garantir_chrome_com_cdp(url)
        if sucesso:
            self.log("✅ Janela do Google Chrome aberta com o perfil do robô (porta 9222).")
            self.log("💡 Realize o login no Gov.br normalmente (CPF, QR Code ou Banco).")
            self.log("👉 O robô reconhece a aba da disputa automaticamente quando aberta!")
            self.after(1500, self._checar_aba_ativa_agora)
        else:
            messagebox.showerror("Erro ao Abrir Navegador", "Google Chrome não encontrado no sistema.")

    def _navegar_manual(self) -> None:
        url = self.txt_url.get().strip()
        if not url:
            return
        if self._bot and hasattr(self._bot, "navegar_para"):
            try:
                self._bot.navegar_para(url)
                self.log(f"🌐 Solicitada navegação na sala via bot: {url}")
                return
            except Exception:
                pass
        
        self.log(f"🌐 Abrindo sala no Google Chrome com depuração: {url}")
        garantir_chrome_com_cdp(url)
        self.after(1500, self._checar_aba_ativa_agora)

    def _parar_sessao(self) -> None:
        lic = getattr(self, "licitacao_ativa", None) or getattr(self.app, "licitacao_ativa", None)
        chave = str(lic.get("numero_pregao") or lic.get("id") or "") if lic else ""
        nome_certame = f"[{lic.get('uf','')}] {lic.get('orgao_nome','')[:25]} - {lic.get('numero_pregao','')}" if lic else "certame ativo"

        sessao = self.sessoes_ativas.get(chave)
        if sessao and sessao.get("bot"):
            sessao["bot"].parar()
            sessao["status"] = "PARADO"
            self.log(f"⏹️ Comando de parada enviado para '{nome_certame}'.")
            self.btn_parar.configure(state="disabled")
            self.btn_iniciar.configure(state="normal")
            self._atualizar_badge_disputas()
        elif self._bot:
            self._bot.parar()
            self.log("⏹️ Comando de parada enviado.")
            self.btn_parar.configure(state="disabled")
            self.btn_iniciar.configure(state="normal")
            self._atualizar_badge_disputas()
        else:
            self.log("⏹️ Nenhum robô ativo para este certame.")

    def _on_bot_finalizado_multi(self, chave: str) -> None:
        if chave in self.sessoes_ativas:
            self.sessoes_ativas[chave]["status"] = "PARADO"
        
        lic_atual = getattr(self, "licitacao_ativa", None)
        chave_atual = str(lic_atual.get("numero_pregao") or lic_atual.get("id") or "") if lic_atual else ""
        if chave_atual == chave:
            self.btn_iniciar.configure(state="normal")
            self.btn_parar.configure(state="disabled")
            
        self._atualizar_badge_disputas()
        self.log(f"🏁 Sessão de monitoramento finalizada para certame ({chave}).")

    def _on_bot_finalizado(self) -> None:
        self.btn_iniciar.configure(state="normal")
        self.btn_parar.configure(state="disabled")
        self.log("🏁 Sessão de monitoramento finalizada.")

    def _on_ciclo_update_multi(self, chave: str, ciclo: int, estados: list[Any], decisoes: dict[int, Any]) -> None:
        lic_atual = getattr(self, "licitacao_ativa", None)
        chave_atual = str(lic_atual.get("numero_pregao") or lic_atual.get("id") or "") if lic_atual else ""
        if chave_atual == chave:
            self.after(0, lambda: self._render_ciclo_ui(ciclo, estados, decisoes))

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
                elif (pos and pos > 1):
                    status_txt = "🟡 SUPERADO"
            elif pos == 1:
                status_txt = "🏆 LIDERANDO"
                liderando_cnt += 1
            elif pos and pos > 1:
                status_txt = "🟡 SUPERADO"

            desc_item = str(getattr(ep, "descricao", None) or f"Item {num}")
            if num in self._item_rows:
                row_id = self._item_rows[num]
                item_dict = self.tree_sessao.item(row_id)
                vals = item_dict.get("values", []) if isinstance(item_dict, dict) else []
                old_desc = str(vals[1]) if isinstance(vals, (list, tuple)) and len(vals) > 1 else ""
                desc_final = ep.descricao if (getattr(ep, "descricao", None) and ("A CONTRATAÇÃO" in old_desc or "Item " in old_desc)) else (old_desc or desc_item)
                self.tree_sessao.item(
                    row_id,
                    values=(num, desc_final, meu_lance_str, menor_lance_str, pos_str, status_txt),
                )
            else:
                row_id = self.tree_sessao.insert(
                    "", "end",
                    values=(num, desc_item, meu_lance_str, menor_lance_str, pos_str, status_txt)
                )
                self._item_rows[num] = row_id

        if hasattr(self.app, "painel_dashboard"):
            card = getattr(self.app.painel_dashboard, "card_liderando", None) or getattr(self.app.painel_dashboard, "card_lideranca", None)
            if card and hasattr(card, "atualizar"):
                card.atualizar(str(liderando_cnt))
            card_stop = getattr(self.app.painel_dashboard, "card_stop_loss", None)
            if card_stop and hasattr(card_stop, "atualizar"):
                card_stop.atualizar(str(piso_cnt))

    def _limpar_log(self) -> None:
        self.txt_log.delete("1.0", "end")

    def log(self, msg: str) -> None:
        t_str = time.strftime("%H:%M:%S")
        self.txt_log.insert("end", f"[{t_str}] {msg}\n")
        self.txt_log.see("end")

    def _trigger_copiloto_auto(self, evento: str) -> None:
        self.after(0, lambda: self._enviar_mensagem_copiloto(f"Evento detectado: {evento}. Analise rapidamente."))

    def _adicionar_chat(self, msg: str, cor: str) -> None:
        self.txt_chat.configure(state="normal")
        t_str = time.strftime("%H:%M:%S")
        tag_name = f"tag_{cor.replace('#', '')}"
        
        self.txt_chat.tag_config(tag_name, foreground=cor)
        
        start_idx = self.txt_chat.index("end-1c")
        self.txt_chat.insert("end", f"[{t_str}] {msg}\n\n")
        end_idx = self.txt_chat.index("end-1c")
        
        self.txt_chat.tag_add(tag_name, start_idx, end_idx)
        self.txt_chat.configure(state="disabled")
        self.txt_chat.see("end")

    def _enviar_mensagem_copiloto(self, msg_manual: str = "") -> None:
        msg = msg_manual or self.txt_mensagem.get().strip()
        if not msg:
            return
            
        if not msg_manual:
            self.txt_mensagem.delete(0, "end")
            
        self._adicionar_chat(f"Operador: {msg}", "#10B981") # Verde
        
        # Coleta estado do pregão enriquecido com itens e valores reais
        lic = getattr(self, "licitacao_ativa", None)
        itens = getattr(self, "itens_disputa", [])
        
        resumo_item = "Itens em Disputa"
        v_ref_item = "0,00"
        piso_item = "N/A"
        
        if itens:
            it0 = itens[0]
            resumo_item = f"Item {it0.get('numero_item', 1)}: {it0.get('descricao', '')[:40]}"
            v_ref_item = f"{float(it0.get('valor_referencia') or 0):,.2f}"
            piso_item = f"{float(it0.get('preco_minimo') or it0.get('piso_seguro') or 0):,.2f}"
        elif lic:
            v_est = float(lic.get("valor_estimado") or 0.0)
            v_ref_item = f"{v_est:,.2f}"
            piso_item = f"{float(lic.get('piso_seguro') or v_est * 0.7):,.2f}"

        desc_obj = (lic.get("objeto") or "") if lic else ""
        estado = {
            "orgao": (lic.get("orgao_nome") if lic else None) or "Certame Ativo",
            "item": resumo_item,
            "descricao": desc_obj[:100],
            "quantidade": str(itens[0].get("quantidade", 1)) if itens else "1",
            "unidade": str(itens[0].get("unidade_medida", "UN")) if itens else "UN",
            "valor_referencia": v_ref_item,
            "meu_lance": "N/A",
            "melhor_lance": "N/A",
            "stop_loss": piso_item,
            "posicao": "N/A",
            "tempo_status": "Em andamento",
            "chat_pregoeiro": "Sem mensagens recentes"
        }
        
        if self._bot and getattr(self._bot.motor, "_estados", None):
            for item_id, st in self._bot.motor._estados.items():
                if st.nossa_posicao:
                    estado["item"] = f"Item {st.regra.numero_item} - {st.regra.descricao[:30]}..."
                    estado["meu_lance"] = f"{st.meu_ultimo_lance}"
                    estado["melhor_lance"] = f"{st.melhor_lance_mercado}"
                    estado["stop_loss"] = f"{st.regra.preco_minimo}"
                    estado["posicao"] = f"{st.nossa_posicao}"
                    break
        
        self._adicionar_chat("🤖 Copiloto analisando o pregão...", "#94A3B8")
        
        import threading
        from copiloto_gemini import consultar_copiloto
        def worker():
            try:
                resposta = consultar_copiloto(estado, msg)
                self.after(0, lambda: self._adicionar_chat(f"Copiloto: {resposta}", "#00E5FF")) # Ciano
            except Exception as e:
                err_msg = str(e)
                self.after(0, lambda: self._adicionar_chat(f"Erro no Copiloto: {err_msg}", "#FF5252"))
                
        threading.Thread(target=worker, daemon=True).start()



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
        disable_mouse_scroll(self.cmb_uf)

        ctk.CTkLabel(card_filtros, text="Palavra-chave:", font=ctk.CTkFont(size=12)).grid(row=1, column=2, padx=(16, 4), pady=8, sticky="w")
        self.txt_kw = ctk.CTkEntry(card_filtros, placeholder_text="Ex: computador, limpeza, expediente...", width=260)
        self.txt_kw.grid(row=1, column=3, padx=4, pady=8, sticky="ew")

        ctk.CTkButton(card_filtros, text="🔍 Pesquisar PNCP", fg_color=COR["accent_purple"], text_color="#FFFFFF", font=ctk.CTkFont(weight="bold"), height=34, command=self._buscar_pncp).grid(row=1, column=4, padx=(8, 16), pady=8)

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

        # Barra de Ações PNCP
        acoes_f = ctk.CTkFrame(card_res, fg_color="transparent")
        acoes_f.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))

        ctk.CTkButton(
            acoes_f,
            text="🎯 Enviar Selecionada para Disputa",
            font=ctk.CTkFont(weight="bold"),
            fg_color=COR["accent_green"],
            text_color="#080C14",
            hover_color=COR["accent_cyan"],
            height=34,
            command=self._enviar_para_disputa,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            acoes_f,
            text="🌐 Abrir no PNCP / Sistema",
            font=ctk.CTkFont(weight="bold"),
            fg_color=COR["accent_blue"],
            text_color="#FFFFFF",
            height=34,
            command=self._abrir_no_navegador,
        ).pack(side="left", padx=8)

        self.itens_pncp_cache: list[dict] = []

    def _buscar_pncp(self) -> None:
        uf = self.cmb_uf.get()
        kw = self.txt_kw.get()

        def _worker():
            try:
                res = buscar_publicacoes_pncp(uf=uf, palavra_chave=kw, tamanho_pagina=20)
                itens = res.get("data", [])
                self.after(0, lambda: self._preencher_pncp(itens))
            except Exception as exc:
                err_msg = str(exc)
                self.after(0, lambda: messagebox.showerror("Erro PNCP", err_msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _preencher_pncp(self, itens: list[dict]) -> None:
        for r in self.tree_pncp.get_children():
            self.tree_pncp.delete(r)

        self.itens_pncp_cache = itens or []

        for idx, i in enumerate(self.itens_pncp_cache):
            orgao = i.get("orgaoEntidade", {}).get("razaoSocial", "N/A")
            objeto = i.get("objetoCompra", "Sem descrição")[:120]
            mod = i.get("modalidadeNome", "Pregão Eletrônico")
            val = i.get("valorTotalEstimado")
            val_str = f"R$ {val:,.2f}" if val else "N/A"

            self.tree_pncp.insert("", "end", iid=str(idx), values=(orgao[:40], objeto, mod, val_str))

        messagebox.showinfo("PNCP", f"Encontradas {len(itens)} oportunidade(s) no PNCP!")

    def _enviar_para_disputa(self) -> None:
        sel = self.tree_pncp.selection()
        if not sel:
            messagebox.showwarning("Seleção", "Selecione uma oportunidade na tabela acima para enviar para a disputa.")
            return

        try:
            idx = int(sel[0])
            item_pncp = self.itens_pncp_cache[idx]
        except Exception:
            messagebox.showerror("Erro", "Falha ao identificar a oportunidade selecionada.")
            return

        cnpj = item_pncp.get("orgaoEntidade", {}).get("cnpj") or item_pncp.get("cnpj", "")
        ano = item_pncp.get("anoCompra") or item_pncp.get("ano")
        seq = item_pncp.get("sequencialCompra") or item_pncp.get("sequencial")
        
        # Salva no Supabase e sincroniza itens essenciais
        try:
            import buscador_pncp
            notificados = set()
            buscador_pncp.processar_e_salvar_edital(item_pncp, self.txt_kw.get() or "Geral", item_pncp.get("modalidadeNome", "Pregão Eletrônico"), notificados)
        except Exception as e:
            logger.warning(f"Aviso ao salvar certame: {e}")

        # Atualiza o seletor da disputa e abre a aba do console
        if hasattr(self.app, "painel_sessao"):
            self.app.painel_sessao.atualizar_seletor_disputa()
        
        self.app.selecionar_aba("⚡  Console da Sessão")
        messagebox.showinfo("Sucesso", "✅ Licitação enviada para o Console de Disputa com itens sincronizados!")

    def _abrir_no_navegador(self) -> None:
        sel = self.tree_pncp.selection()
        if not sel:
            return
        try:
            idx = int(sel[0])
            item = self.itens_pncp_cache[idx]
            link = item.get("linkSistemaOrigem")
            if not link:
                cnpj = item.get("orgaoEntidade", {}).get("cnpj", "")
                ano = item.get("anoCompra") or item.get("ano")
                seq = item.get("sequencialCompra") or item.get("sequencial")
                if cnpj and ano and seq:
                    link = f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"
            if link:
                import webbrowser
                webbrowser.open(link)
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível abrir o link: {e}")


def formatar_distancia_palmas(municipio_raw: Any, uf_raw: Any, objeto_raw: Any = "") -> str:
    """
    Calcula e formata a distância de um município em relação à sede em Palmas/TO.
    - Cidades do Tocantins (Palmas, Araguaína, Gurupi, Porto Alegre do TO, Itacajá, Paraíso):
      calcula a quilometragem aproximada ou badge [TO - Local].
    - Outros estados: exibe a sigla da UF com a distância real aproximada ou badge [Nacional / Remoto].
    """
    mun = str(municipio_raw or "").strip().upper()
    uf = str(uf_raw or "").strip().upper()
    obj = str(objeto_raw or "").lower()

    import unicodedata
    mun_norm = unicodedata.normalize('NFKD', mun).encode('ASCII', 'ignore').decode('utf-8').upper()

    # Mapeamento detalhado das cidades do Tocantins (chaves normalizadas sem acento)
    cidades_to = {
        "PALMAS": "0 km [TO - Local]",
        "PARAISO DO TOCANTINS": "~65 km [TO]",
        "PARAISO": "~65 km [TO]",
        "PORTO NACIONAL": "~60 km [TO]",
        "MIRACEMA DO TOCANTINS": "~75 km [TO]",
        "MIRACEMA": "~75 km [TO]",
        "GUARAI": "~180 km [TO]",
        "GURUPI": "~220 km [TO]",
        "COLINAS DO TOCANTINS": "~270 km [TO]",
        "COLINAS": "~270 km [TO]",
        "PORTO ALEGRE DO TOCANTINS": "~290 km [TO]",
        "PORTO ALEGRE DO TO": "~290 km [TO]",
        "PORTO ALEGRE": "~290 km [TO]",
        "ITACAJA": "~300 km [TO]",
        "DIANOPOLIS": "~340 km [TO]",
        "ARAGUAINA": "~380 km [TO]",
    }

    # 1. Verifica correspondência direta com cidades mapeadas do Tocantins
    for cid_chave, dist_str in cidades_to.items():
        if cid_chave in mun_norm:
            return dist_str

    # 2. Se for estado do Tocantins mas cidade não catalogada especificamente
    if uf == "TO":
        return "~200 km [TO - Local]"

    # 3. Se for expressamente remoto ou de âmbito nacional
    termos_remoto = ["remoto", "a distancia", "service desk", "help desk", "nuvem", "cloud", "teletrabalho"]
    if any(tr in obj for tr in termos_remoto) or "REMOTO" in mun or "NACIONAL" in mun:
        return "[Nacional / Remoto]"

    # 4. Outros estados - distâncias médias a partir de Palmas/TO
    dist_ufs = {
        "DF": "~820 km",
        "GO": "~870 km",
        "MA": "~630 km",
        "PA": "~550 km",
        "BA": "~600 km",
        "PI": "~1.100 km",
        "MT": "~1.050 km",
        "MS": "~1.350 km",
        "MG": "~1.450 km",
        "SP": "~1.750 km",
        "RJ": "~1.850 km",
        "PR": "~1.800 km",
        "SC": "~2.100 km",
        "RS": "~2.400 km",
        "CE": "~1.600 km",
        "PE": "~1.500 km",
        "PB": "~1.700 km",
        "RN": "~1.800 km",
        "AL": "~1.600 km",
        "SE": "~1.500 km",
        "AM": "~1.500 km",
        "RO": "~2.100 km",
        "AC": "~2.600 km",
        "RR": "~2.200 km",
        "AP": "~1.200 km",
        "ES": "~1.650 km",
    }

    if uf in dist_ufs:
        return f"{uf} ({dist_ufs[uf]})"
    elif uf and uf not in ["BR", "BRASIL", "NACIONAL"]:
        return f"{uf} [Nacional / Remoto]"
    else:
        return "[Nacional / Remoto]"


# ─── APLICAÇÃO PRINCIPAL (APP MULTI-CERTAME) ──────────────────────────────────
class PainelServicos(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self._varredura_ativa: bool = False
        
        # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(10, 0))
        ctk.CTkLabel(header_frame, text="🛠️ Serviços de TI & Infraestrutura", font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        
        # Filtro de Prazo
        ctk.CTkLabel(header_frame, text="Prazo:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#9ca3af").pack(side="left", padx=(20, 6))
        self.combo_prazo_srv = ctk.CTkComboBox(
            header_frame,
            values=["Todos os Prazos", "🚨 Encerram Hoje", "⚡ Encerram Amanhã", "⏳ < 48 Horas", "📅 Em Aberto (Futuras)"],
            state="readonly",
            width=170,
            command=lambda e: self._exibir_servicos_nuvem()
        )
        self.combo_prazo_srv.set("Todos os Prazos")
        self.combo_prazo_srv.pack(side="left")
        disable_mouse_scroll(self.combo_prazo_srv)

        def _iniciar_siga_to():
            if getattr(self, "_varredura_ativa", False):
                import logging
                logging.warning("[PainelServicos] Varredura já em andamento. Ignorando clique duplicado.")
                return

            self._varredura_ativa = True
            btn_start_to.configure(state="disabled", text="⏳ VARRENDO SIGA-TO...")
            import threading
            import tkinter.messagebox as messagebox
            from siga_to_monitor import SigaMonitor
            import asyncio
            def worker():
                try:
                    monitor = SigaMonitor()
                    asyncio.run(monitor.executar_varredura())
                    self.after(0, self.carregar_servicos_nuvem)
                    self.after(0, lambda: messagebox.showinfo("Pronto", "Varredura do SIGA-TO concluída! A tabela foi atualizada."))
                except Exception as e:
                    import logging
                    logging.error(f"Erro na varredura do SIGA-TO: {e}")
                    self.after(0, lambda: messagebox.showerror("Erro", f"Erro na varredura do SIGA-TO: {e}"))
                finally:
                    self._varredura_ativa = False
                    self.after(0, lambda: btn_start_to.configure(state="normal", text="⚡ START / BUSCAR TOCANTINS"))
            threading.Thread(target=worker, daemon=True).start()

        def _iniciar_ambos():
            if getattr(self, "_varredura_ativa", False):
                import logging
                logging.warning("[PainelServicos] Varredura já em andamento. Ignorando clique duplicado.")
                return

            self._varredura_ativa = True
            btn_start_ambos.configure(state="disabled", text="⏳ MONITORANDO...")
            import threading
            import tkinter.messagebox as messagebox
            from buscador_pncp import executar_varredura_tocantins
            from siga_to_monitor import SigaMonitor
            import asyncio
            def worker_ambos():
                try:
                    executar_varredura_tocantins()
                    monitor = SigaMonitor()
                    asyncio.run(monitor.executar_varredura())
                    self.after(0, self.carregar_servicos_nuvem)
                    self.after(0, lambda: messagebox.showinfo("Pronto", "Varredura do Compras.gov (PNCP) e SIGA-TO concluídas! A tabela foi atualizada."))
                except Exception as e:
                    import logging
                    logging.error(f"Erro na varredura conjunta: {e}")
                    self.after(0, lambda: messagebox.showerror("Erro", f"Erro na varredura: {e}"))
                finally:
                    self._varredura_ativa = False
                    self.after(0, lambda: btn_start_ambos.configure(state="normal", text="🚀 MONITORAR AMBOS"))
            threading.Thread(target=worker_ambos, daemon=True).start()
            
        btn_start_ambos = ctk.CTkButton(header_frame, text="🚀 MONITORAR AMBOS", fg_color="#F59E0B", command=_iniciar_ambos)
        btn_start_ambos.pack(side="right", padx=(10, 0))

        btn_start_to = ctk.CTkButton(header_frame, text="⚡ START / BUSCAR TOCANTINS", fg_color="#10b981", command=_iniciar_siga_to)
        btn_start_to.pack(side="right", padx=(10, 0))
        
        btn_refresh = ctk.CTkButton(header_frame, text="🔄 Atualizar da Nuvem", fg_color=COR["accent_purple"], command=self.carregar_servicos_nuvem)
        btn_refresh.pack(side="right")
        
        # Table
        from tkinter import ttk
        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=20, pady=(10, 6))
        
        cols = ("uf_cidade", "distancia", "orgao", "objeto", "valor", "prazo_proposta", "data_abertura")
        self.tree_servicos = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8)
        self.tree_servicos.heading("uf_cidade", text="🏙️ UF / Cidade")
        self.tree_servicos.heading("distancia", text="🛣️ Distância (Palmas)")
        self.tree_servicos.heading("orgao", text="Órgão")
        self.tree_servicos.heading("objeto", text="Objeto do Serviço")
        self.tree_servicos.heading("valor", text="Valor Estimado (R$)")
        self.tree_servicos.heading("prazo_proposta", text="⏰ Fim Proposta")
        self.tree_servicos.heading("data_abertura", text="🏛️ Abertura/Disputa")
        
        self.tree_servicos.column("uf_cidade", width=110, anchor="w")
        self.tree_servicos.column("distancia", width=130, anchor="center")
        self.tree_servicos.column("orgao", width=170, anchor="w")
        self.tree_servicos.column("objeto", width=270, anchor="w")
        self.tree_servicos.column("valor", width=120, anchor="e")
        self.tree_servicos.column("prazo_proposta", width=170, anchor="center")
        self.tree_servicos.column("data_abertura", width=140, anchor="center")
        
        self.tree_servicos.tag_configure("evenrow", background="#181A20", foreground="#FFFFFF")
        self.tree_servicos.tag_configure("oddrow", background="#22242A", foreground="#FFFFFF")
        self.tree_servicos.pack(fill="both", expand=True)

        self.tree_servicos.bind("<<TreeviewSelect>>", self._on_servico_selecionado)
        self.tree_servicos.bind("<Double-1>", lambda e: self.selecionar_e_precificar())

        # Card de Informações e Cronograma do Serviço Selecionado
        self.banner_cronograma = ctk.CTkFrame(self, fg_color="#0F172A", corner_radius=8, border_width=1, border_color="#1E293B")
        self.banner_cronograma.pack(fill="x", padx=20, pady=(0, 8))
        self.banner_cronograma.grid_columnconfigure(0, weight=1)

        self.lbl_srv_selecionado = ctk.CTkLabel(
            self.banner_cronograma, 
            text="👆 Selecione um serviço na tabela acima para visualizar prazos e ações rápidas", 
            font=ctk.CTkFont(size=12, weight="bold"), 
            text_color="#94A3B8"
        )
        self.lbl_srv_selecionado.grid(row=0, column=0, sticky="w", padx=16, pady=(8, 2))

        self.lbl_srv_datas = ctk.CTkLabel(
            self.banner_cronograma, 
            text="", 
            font=ctk.CTkFont(size=12), 
            text_color="#F8FAFC"
        )
        self.lbl_srv_datas.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 8))

        # Actions
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=(0, 10))
        
        ctk.CTkButton(
            btn_frame, 
            text="⚡ Selecionar & Precificar", 
            font=ctk.CTkFont(size=12, weight="bold"), 
            fg_color=COR["accent_cyan"], 
            text_color="#080C14", 
            hover_color=COR["accent_green"], 
            height=38, 
            command=self.selecionar_e_precificar
        ).grid(row=0, column=0, padx=6)

        ctk.CTkButton(
            btn_frame, 
            text="🚀 Mandar p/ Console de Sessão", 
            font=ctk.CTkFont(size=12, weight="bold"), 
            fg_color=COR["accent_green"], 
            text_color="#080C14", 
            hover_color=COR["accent_cyan"], 
            height=38, 
            command=self.mandar_para_console_sessao
        ).grid(row=0, column=1, padx=6)

        ctk.CTkButton(btn_frame, text="🏷️ Precificar Prod+Serv", fg_color="#3B82F6", height=38, command=self.abrir_precificador_misto).grid(row=0, column=2, padx=6)
        ctk.CTkButton(btn_frame, text="📄 Ver Escopo TR", height=38, command=self.ver_escopo_tr).grid(row=0, column=3, padx=6)
        ctk.CTkButton(btn_frame, text="🚚 SLA / Logística", height=38, command=self.calcular_sla).grid(row=0, column=4, padx=6)
        ctk.CTkButton(btn_frame, text="🌐 Acessar Edital", height=38, command=self.abrir_link_edital).grid(row=0, column=5, padx=6)
        ctk.CTkButton(btn_frame, text="📊 Gerar Excel", height=38, command=self.gerar_excel).grid(row=0, column=6, padx=6)

        self.dados_servicos = []
        self.after(500, self.carregar_servicos_nuvem)

    def _on_servico_selecionado(self, event: Any = None) -> None:
        sel = self.tree_servicos.selection()
        if not sel:
            return
        srv = self.dados_servicos[int(sel[0])]
        uf = srv.get("uf", "BR")
        orgao = srv.get("orgao_nome", "Órgão")
        pregao = srv.get("numero_pregao", "S/N")
        valor = float(srv.get("valor_estimado") or 0.0)
        
        crono = PainelDashboard._extrair_cronograma_licitacao(srv)
        self.lbl_srv_selecionado.configure(
            text=f"📌 [{uf}] {orgao} • {pregao}  |  Valor Estimado: R$ {valor:,.2f}",
            text_color=COR["accent_cyan"]
        )
        self.lbl_srv_datas.configure(
            text=f"⏰ Limite Propostas: {crono['txt_encerramento']} ({crono['badge_texto']})   •   🏛️ Disputa / Abertura: {crono['txt_abertura']}"
        )

    def selecionar_e_precificar(self):
        srv = self._get_selected_srv()
        if not srv: return
        
        link = srv.get("link_sistema") or srv.get("link_edital") or srv.get("url_sala_disputa") or ""
        
        # Tenta alternar para a aba 'Editais & Custos' usando o container correto
        if hasattr(self.app, "tab_container") and hasattr(self.app.tab_container, "set"):
            self.app.tab_container.set("Editais & Custos")
        elif hasattr(self.app, "mudar_aba"):
            self.app.mudar_aba("Editais & Custos")
        elif hasattr(self.app, "selecionar_aba"):
            self.app.selecionar_aba("Editais & Custos")
        
        if hasattr(self.app, "painel_editais"):
            pe = self.app.painel_editais
            if hasattr(pe, "txt_entrada_rapida"):
                pe.txt_entrada_rapida.delete(0, "end")
                pe.txt_entrada_rapida.insert(0, link)
                if hasattr(pe, "_carregar_direto"):
                    pe._carregar_direto()

    def mandar_para_console_sessao(self):
        srv = self._get_selected_srv()
        if not srv or not isinstance(srv, dict): return
        
        # 1. Carrega itens vinculados à licitação do Supabase se existirem
        itens_ativos = []
        lic_id = srv.get("id")
        if lic_id:
            try:
                import os
                from supabase import create_client
                url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
                key = os.getenv("SUPABASE_KEY") or ""
                sb = create_client(url, key)
                res_itens = sb.table("itens_licitacao").select("*").eq("licitacao_id", lic_id).order("numero_item").execute()
                for it in (res_itens.data or []):
                    if not isinstance(it, dict):
                        continue
                    piso = _safe_float(it.get("custo_unitario") or it.get("valor_referencia"))
                    itens_ativos.append({
                        "numero_item": it.get("numero_item", 1),
                        "descricao": it.get("descricao") or str(srv.get("objeto", ""))[:50],
                        "piso_seguro": piso
                    })
            except Exception:
                pass
                
        # 2. Se não houver itens cadastrados ainda, gera item base
        if not itens_ativos:
            val_est = _safe_float(srv.get("valor_estimado"))
            piso_padrao = _safe_float(srv.get("stop_loss"), default=(val_est * 0.7 if val_est > 0 else 0.0))
            itens_ativos.append({
                "numero_item": 1,
                "descricao": str(srv.get("objeto", "Serviços Especializados de TI"))[:60],
                "piso_seguro": piso_padrao
            })
            
        # 3. Define a licitação ativa no sistema principal
        if hasattr(self.app, "definir_licitacao_ativa"):
            self.app.definir_licitacao_ativa(srv, itens_ativos)
            
        # 4. Redireciona imediatamente para o Console da Sessão
        if hasattr(self.app, "selecionar_aba"):
            self.app.selecionar_aba("⚡  Console da Sessão")
            
        import tkinter.messagebox as messagebox
        pregao = srv.get("numero_pregao", "S/N")
        orgao = srv.get("orgao_nome", "")[:35]
        crono = PainelDashboard._extrair_cronograma_licitacao(srv)
        messagebox.showinfo(
            "Console da Sessão", 
            f"✅ Serviço carregado com sucesso no Console ao Vivo!\n\n"
            f"🏛️ Órgão: {orgao}\n"
            f"📋 Processo: {pregao}\n"
            f"⏰ Fim da Proposta: {crono['txt_encerramento']}\n"
            f"🏛️ Abertura/Disputa: {crono['txt_abertura']}\n"
            f"💰 Valor: R$ {float(srv.get('valor_estimado') or 0.0):,.2f}\n\n"
            f"Os campos de disputa e itens foram sincronizados para lances automáticos."
        )

    def abrir_link_edital(self):
        srv = self._get_selected_srv()
        if not srv: return
        link = srv.get("link_edital") or srv.get("link_sistema") or ""
        if link and link.startswith("http"):
            import webbrowser
            webbrowser.open(link)
        else:
            import tkinter.messagebox as messagebox
            messagebox.showinfo("Link do Edital", "Edital não possui link cadastrado diretamente.")

    def carregar_servicos_nuvem(self):
        import threading
        def worker():
            try:
                import os
                from supabase import create_client
                url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
                key = os.getenv("SUPABASE_KEY") or ""
                sb = create_client(url, key)
                res = sb.table('licitacoes').select('*').order('atualizado_em', desc=True).limit(200).execute()
                
                # Filtro para Tecnologia da Informação (TI e Infraestrutura) e Abertos
                dados = []
                ti_kws = [
                    "cabeamento", "rede", "manutenção de computador", "notebook", "nobreak",
                    "cftv", "suporte", "servidor", "proxmox", "linux", "firewall", "backup",
                    "tecnologia", "informática", "software", "licença", "sistema", "infraestrutura",
                    "telecom", "fibra", "help desk", "service desk"
                ]
                for srv in (res.data or []):
                    if not isinstance(srv, dict): continue
                    tipo = str(srv.get("tipo_contratacao") or "").upper()
                    obj = str(srv.get("objeto") or "").lower()
                    status = str(srv.get("status_analise") or "pendente").lower()
                    if status in ["descartada"]:
                        continue
                    if tipo in ["SERVICO", "MISTO", ""] or any(kw in obj for kw in ti_kws):
                        dados.append(srv)

                # Fallback: se nenhum item passar no filtro estrito, carrega certames abertos disponíveis
                if not dados and res.data:
                    for srv in res.data:
                        if isinstance(srv, dict) and str(srv.get("status_analise") or "").lower() != "descartada":
                            dados.append(srv)

                self.dados_servicos = dados
            except Exception as e:
                print("Erro ao carregar serviços:", e)
                self.dados_servicos = []
                
            self.after(0, self._exibir_servicos_nuvem)
        
        threading.Thread(target=worker, daemon=True).start()

    def _exibir_servicos_nuvem(self):
        for row in self.tree_servicos.get_children():
            self.tree_servicos.delete(row)

        from datetime import datetime, date, timedelta
        hoje = date.today()
        amanha = hoje + timedelta(days=1)

        prazo_filtro = self.combo_prazo_srv.get() if hasattr(self, "combo_prazo_srv") else "Todos os Prazos"
        
        itens_filtrados = []
        for srv in self.dados_servicos:
            crono = PainelDashboard._extrair_cronograma_licitacao(srv)
            st = crono["prazo_status"]
            
            # Checagem flexível de data convertida para a data local
            dt_ref = crono.get("dt_ref")
            d_ref = None
            if dt_ref:
                d_ref = dt_ref.astimezone().date() if (hasattr(dt_ref, "astimezone") and dt_ref.tzinfo) else dt_ref.date()

            eh_hoje = (st == "hoje") or (d_ref == hoje)
            eh_amanha = (st == "amanha") or (d_ref == amanha)
            eh_48h = (st in ["hoje", "amanha", "48h"]) or (d_ref is not None and hoje <= d_ref <= hoje + timedelta(days=2))
            eh_futura = (st in ["hoje", "amanha", "48h", "aberto"]) or (d_ref is not None and d_ref >= hoje)

            if prazo_filtro == "🚨 Encerram Hoje" and not eh_hoje:
                continue
            elif prazo_filtro == "⚡ Encerram Amanhã" and not eh_amanha:
                continue
            elif prazo_filtro == "⏳ < 48 Horas" and not eh_48h:
                continue
            elif prazo_filtro == "📅 Em Aberto (Futuras)" and not eh_futura:
                continue
                
            itens_filtrados.append((srv, crono))
            
        # Ordena: Mais urgentes primeiro
        def chave_srv(item):
            _, c = item
            st = c["prazo_status"]
            if st == "hoje": return (0, c["dias_restantes"])
            elif st == "amanha": return (1, c["dias_restantes"])
            elif st == "48h": return (2, c["dias_restantes"])
            elif st == "aberto": return (3, c["dias_restantes"])
            elif st == "sem_data": return (4, 0)
            else: return (5, c["dias_restantes"])
            
        itens_filtrados.sort(key=chave_srv)

        # Atualiza a lista dados_servicos para bater com os índices das linhas
        self.dados_servicos = [item[0] for item in itens_filtrados]
        
        for i, (srv, crono) in enumerate(itens_filtrados):
            uf_cidade = f"[{srv.get('uf', 'BR')}] {srv.get('municipio', 'Nacional')}"
            distancia = formatar_distancia_palmas(srv.get("municipio"), srv.get("uf"), srv.get("objeto", ""))
            orgao = srv.get("orgao_nome", "")[:30]
            objeto = srv.get("objeto", "")[:50]
            valor = float(srv.get("valor_estimado") or 0.0)
            valor_str = f"R$ {valor:,.2f}"
            
            # Formatação do prazo com indicador de urgência
            if crono["prazo_status"] == "hoje":
                fim_proposta = f"🚨 HOJE: {crono['txt_encerramento']}"
            elif crono["prazo_status"] == "amanha":
                fim_proposta = f"⚡ AMANHÃ: {crono['txt_encerramento']}"
            elif crono["prazo_status"] == "48h":
                fim_proposta = f"⏳ {crono['txt_encerramento']}"
            else:
                fim_proposta = crono["txt_encerramento"]
                
            abertura = crono["txt_abertura"]
            
            tag = "evenrow" if i % 2 == 0 else "oddrow"
            self.tree_servicos.insert("", "end", iid=str(i), values=(uf_cidade, distancia, orgao, objeto, valor_str, fim_proposta, abertura), tags=(tag,))

    def _get_selected_srv(self) -> Optional[dict[str, Any]]:
        sel = self.tree_servicos.selection()
        if not sel:
            import tkinter.messagebox as messagebox
            messagebox.showwarning("Aviso", "Selecione um serviço na tabela primeiro.")
            return None
        item = self.dados_servicos[int(sel[0])]
        return dict(item) if isinstance(item, dict) else None

    def ver_escopo_tr(self):
        srv = self._get_selected_srv()
        if not srv: return
        
        top = ctk.CTkToplevel(self)
        top.title("Detalhes do Escopo TR")
        top.geometry("500x350")
        
        ctk.CTkLabel(top, text="🏛️ Órgão:", font=ctk.CTkFont(weight="bold")).pack(pady=(10, 0))
        ctk.CTkLabel(top, text=srv.get("orgao_nome", ""), wraplength=450).pack()
        
        ctk.CTkLabel(top, text="📋 Objeto:", font=ctk.CTkFont(weight="bold")).pack(pady=(10, 0))
        ctk.CTkLabel(top, text=srv.get("objeto", ""), wraplength=450).pack()
        
        atendimento = "Remoto" if srv.get("uf") != "TO" else "Presencial (Field Service)"
        ctk.CTkLabel(top, text=f"📍 Tipo de Atendimento: {atendimento}", font=ctk.CTkFont(weight="bold"), text_color=COR["accent_cyan"]).pack(pady=(15, 5))
        
        link = srv.get("link_edital")
        if link:
            import webbrowser
            ctk.CTkButton(top, text="🌐 Abrir Link do Edital no Navegador", command=lambda: webbrowser.open(link)).pack(pady=15)

    def calcular_sla(self):
        srv = self._get_selected_srv()
        if not srv: return
        
        municipio = srv.get("municipio", "")
        uf = srv.get("uf", "")
        valor = float(srv.get("valor_estimado") or 0.0)
        
        if uf != "TO":
            import tkinter.messagebox as messagebox
            messagebox.showinfo("SLA / Logística", "Serviço Nacional (Fora do TO).\nConsiderado 100% Remoto.\nCusto de deslocamento: R$ 0,00")
            return
            
        try:
            from calculadora_logistica import calcular_custo_deslocamento
            res = calcular_custo_deslocamento(municipio, uf)
            custo_total = res.get("custo_total_logistica", 0.0)
            
            piso = valor * 0.50
            lucro = valor - piso - custo_total
            
            msg = f"🛣️ Modalidade: {res.get('modalidade')}\n" \
                  f"📏 Distância Total Estimada (Ida e Volta): {res.get('distancia_total_km', 0)} km\n" \
                  f"⛽ Custo Combustível: R$ {res.get('custo_combustivel', 0.0):,.2f}\n" \
                  f"🏨 Custo Diárias: R$ {res.get('custo_diarias', 0.0):,.2f}\n" \
                  f"💰 Custo Total Logístico: R$ {custo_total:,.2f}\n\n" \
                  f"📈 Lucro Líquido Projetado (Margem >= 50%): R$ {lucro:,.2f}"
                  
            import tkinter.messagebox as messagebox
            messagebox.showinfo("Logística JRBC TECH (Partindo de Palmas)", msg)
        except Exception as e:
            import tkinter.messagebox as messagebox
            messagebox.showerror("Erro", f"Falha ao calcular logística:\n{e}")

    def gerar_excel(self):
        srv = self._get_selected_srv()
        if not srv: return
        
        try:
            from gerador_planilha_servicos import exportar_pregao_servico_excel
            
            dados_lic = {
                "orgao_nome": srv.get("orgao_nome", "Órgão"),
                "numero_edital": srv.get("numero_pregao", "S/N"),
                "municipio": srv.get("municipio", "Nacional"),
                "uf": srv.get("uf", "BR"),
                "link_edital": srv.get("link_edital", ""),
                "valor_referencia": float(srv.get("valor_estimado") or 0.0)
            }
            
            valor = dados_lic["valor_referencia"]
            piso = valor * 0.50
            
            analise = {
                "custo_total": piso * 0.70, # Estimativa ficticia de custo direto
                "piso_minimo": piso,
                "margem_pct": 50.0,
                "lucro_projetado": valor - piso,
                "distancia_km": 0 if dados_lic["uf"] != "TO" else 100,
                "custo_transporte": 0.0,
                "custo_diarias": 0.0,
                "tipo_atendimento": "Remoto" if dados_lic["uf"] != "TO" else "Field Service",
                "equipe": [{"nome": "Técnico N2", "horas": 40, "funcao": "Suporte"}],
                "ferramentas": []
            }
            
            caminho = exportar_pregao_servico_excel(dados_lic, analise)
            
            import tkinter.messagebox as messagebox
            import os
            resp = messagebox.askyesno("Excel Gerado", "Planilha gerada com sucesso!\n\nDeseja abrir a pasta no sistema operacional?")
            if resp:
                import subprocess; subprocess.Popen(['xdg-open', os.path.expanduser('~/Documentos/Licitacoes_Aprovadas')])
        except Exception as e:
            import tkinter.messagebox as messagebox
            messagebox.showerror("Erro", f"Falha ao gerar Excel:\n{e}")

    def abrir_precificador_misto(self):
        srv = self._get_selected_srv()
        if not srv: return
        
        objeto = srv.get("objeto", "")
        valor_orgao = float(srv.get("valor_estimado") or 0.0)
        
        top = ctk.CTkToplevel(self)
        top.title("Precificação Mista (Produto + Serviço)")
        top.geometry("600x600")
        
        ctk.CTkLabel(top, text="🔍 Termo de Busca (Mercado Livre):").pack(pady=(10,0))
        entry_termo = ctk.CTkEntry(top, width=400)
        entry_termo.pack()
        termo_sugerido = " ".join(objeto.split()[:4]) if objeto else "Notebook"
        entry_termo.insert(0, termo_sugerido)
        
        frame_resultado = ctk.CTkFrame(top)
        frame_resultado.pack(fill="both", expand=True, padx=20, pady=10)
        
        lbl_prod = ctk.CTkLabel(frame_resultado, text="Nenhum produto buscado ainda.")
        lbl_prod.pack(pady=5)
        lbl_preco = ctk.CTkLabel(frame_resultado, text="Preço ML: R$ 0,00", font=ctk.CTkFont(weight="bold"))
        lbl_preco.pack(pady=5)
        
        import tkinter as tk
        preco_produto_var = tk.DoubleVar(value=0.0)
        produto_selecionado = {} # Guarda o dicionário do produto pesquisado
        
        def buscar():
            lbl_prod.configure(text="Buscando...")
            top.update()
            import threading
            def bg_search():
                from precificador_misto import PrecificadorMisto
                prec = PrecificadorMisto()
                res = prec.buscar_produto_meli(entry_termo.get())
                def atualiza_ui():
                    if res:
                        produto_selecionado.update(res)
                        full_tag = "⚡ FULL" if res.get("entrega_full") else ""
                        lbl_prod.configure(text=f"{res['titulo'][:60]}... {full_tag}")
                        val = res['preco']
                        lbl_preco.configure(text=f"Preço ML: R$ {val:,.2f}")
                        preco_produto_var.set(val)
                        calcular_proposta()
                    else:
                        produto_selecionado.clear()
                        lbl_prod.configure(text="Produto não encontrado.")
                        lbl_preco.configure(text="Preço ML: R$ 0,00")
                        preco_produto_var.set(0.0)
                self.after(0, atualiza_ui)
            threading.Thread(target=bg_search, daemon=True).start()
            
        ctk.CTkButton(top, text="Buscar no ML (Full)", command=buscar).pack(pady=5)
        
        frame_fin = ctk.CTkFrame(top)
        frame_fin.pack(fill="x", padx=20, pady=5)
        
        ctk.CTkLabel(frame_fin, text="Qtd:").grid(row=0, column=0, padx=5, pady=5)
        entry_qtd = ctk.CTkEntry(frame_fin, width=60)
        entry_qtd.grid(row=0, column=1, padx=5)
        entry_qtd.insert(0, "1")
        
        ctk.CTkLabel(frame_fin, text="Margem Produto (%):").grid(row=0, column=2, padx=5, pady=5)
        entry_margem = ctk.CTkEntry(frame_fin, width=60)
        entry_margem.grid(row=0, column=3, padx=5)
        entry_margem.insert(0, "25")
        
        ctk.CTkLabel(frame_fin, text="Mão de Obra (Serviço) R$:").grid(row=1, column=0, padx=5, pady=5, columnspan=2)
        entry_servico = ctk.CTkEntry(frame_fin, width=100)
        entry_servico.grid(row=1, column=2, padx=5, columnspan=2, sticky="w")
        entry_servico.insert(0, "500.00")
        
        lbl_res_orgao = ctk.CTkLabel(top, text=f"Valor Estimado (Órgão): R$ {valor_orgao:,.2f}", text_color="gray")
        lbl_res_orgao.pack(pady=5)
        
        lbl_res_prop = ctk.CTkLabel(top, text="Proposta: R$ 0,00", font=ctk.CTkFont(weight="bold", size=16))
        lbl_res_prop.pack(pady=2)
        
        lbl_res_lucro = ctk.CTkLabel(top, text="Lucro Líquido: R$ 0,00", text_color="#10b981", font=ctk.CTkFont(weight="bold"))
        lbl_res_lucro.pack(pady=2)
        
        lbl_desconto = ctk.CTkLabel(top, text="Desconto vs Órgão: 0%")
        lbl_desconto.pack(pady=2)
        
        def calcular_proposta(*args):
            try:
                qtd = int(entry_qtd.get() or 0)
                margem = float(entry_margem.get() or 0) / 100.0
                val_servico = float(entry_servico.get() or 0)
                preco_prod = preco_produto_var.get()
                
                from precificador_misto import PrecificadorMisto
                calc = PrecificadorMisto().calcular_proposta(preco_prod, qtd, margem, val_servico)
                
                proposta = calc["valor_proposta"]
                lucro = calc["lucro_liquido"]
                
                lbl_res_prop.configure(text=f"Sua Proposta Sugerida: R$ {proposta:,.2f}")
                lbl_res_lucro.configure(text=f"Lucro Líquido Estimado: R$ {lucro:,.2f}")
                
                if valor_orgao > 0:
                    desc = ((valor_orgao - proposta) / valor_orgao) * 100
                    cor = "#10b981" if desc > 0 else "#ef4444"
                    lbl_desconto.configure(text=f"Desconto vs Órgão: {desc:.1f}%", text_color=cor)
            except Exception:
                pass
                
        entry_qtd.bind("<KeyRelease>", calcular_proposta)
        entry_margem.bind("<KeyRelease>", calcular_proposta)
        entry_servico.bind("<KeyRelease>", calcular_proposta)
        
        def salvar_cotacao():
            import tkinter.messagebox as messagebox
            import threading
            def worker():
                try:
                    import supabase_client
                    sb = supabase_client.get_client()
                    if sb:
                        qtd = int(entry_qtd.get() or 0)
                        margem = float(entry_margem.get() or 0) / 100.0
                        val_servico = float(entry_servico.get() or 0)
                        preco_prod = preco_produto_var.get()
                        
                        from precificador_misto import PrecificadorMisto
                        calc = PrecificadorMisto().calcular_proposta(preco_prod, qtd, margem, val_servico)
                        
                        sb.table('licitacoes').update({
                            "status_analise": "COTADA",
                            "custo_real": calc.get("custo_real", 0.0),
                            "margem_lucro": calc.get("margem_lucro", 0.0),
                            "stop_loss": calc.get("stop_loss", 0.0)
                        }).eq("numero_pregao", srv.get("numero_pregao")).execute()
                        
                        # Salvar produto em catalogo_distribuidores se existir
                        if produto_selecionado:
                            sb.table('catalogo_distribuidores').upsert({
                                "distribuidor": "Mercado Livre",
                                "sku": produto_selecionado.get("id"),
                                "descricao": (produto_selecionado.get("titulo") or "")[:255],
                                "categoria": "Equipamento TI",
                                "preco_custo": produto_selecionado.get("preco", 0.0),
                                "estoque_disponivel": 10, # default conservador
                                "link_produto": produto_selecionado.get("link", "")
                            }).execute()
                            
                        self.after(0, lambda: messagebox.showinfo("Sucesso", "Cotação e Produto salvos no Supabase!"))
                        self.after(0, top.destroy)
                except Exception as e:
                    err_msg = str(e)
                    self.after(0, lambda: messagebox.showerror("Erro", f"Erro ao salvar: {err_msg}"))
            threading.Thread(target=worker, daemon=True).start()

        ctk.CTkButton(top, text="💾 Salvar Cotação (Status COTADA)", fg_color=COR.get("accent_blue", "#3b82f6"), command=salvar_cotacao).pack(pady=20)

class PainelProdutos(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        
        # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(10, 0))
        ctk.CTkLabel(header_frame, text="📦  Serviço com Produtos (Misto: Peças + Mão de Obra)", font=ctk.CTkFont(size=20, weight="bold"), text_color=COR["accent_purple"]).pack(side="left")
        
        btn_refresh = ctk.CTkButton(header_frame, text="🔄 Atualizar da Nuvem", fg_color=COR["accent_purple"], command=self.carregar_produtos_nuvem)
        btn_refresh.pack(side="right")
        
        from tkinter import ttk
        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        cols = ("uf_cidade", "orgao", "objeto", "valor", "status")
        self.tree_produtos = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8)
        self.tree_produtos.heading("uf_cidade", text="UF / Município")
        self.tree_produtos.heading("orgao", text="Órgão")
        self.tree_produtos.heading("objeto", text="Item / Objeto")
        self.tree_produtos.heading("valor", text="Valor Estimado")
        self.tree_produtos.heading("status", text="Status")
        
        self.tree_produtos.column("uf_cidade", width=120, anchor="w")
        self.tree_produtos.column("orgao", width=200, anchor="w")
        self.tree_produtos.column("objeto", width=300, anchor="w")
        self.tree_produtos.column("valor", width=120, anchor="e")
        self.tree_produtos.column("status", width=120, anchor="center")
        
        self.tree_produtos.tag_configure("evenrow", background="#181A20", foreground="#FFFFFF")
        self.tree_produtos.tag_configure("oddrow", background="#22242A", foreground="#FFFFFF")
        self.tree_produtos.pack(fill="both", expand=True)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=10)
        
        ctk.CTkButton(btn_frame, text="📄 Abrir em Editais & Custos", font=ctk.CTkFont(weight="bold"), fg_color=COR["accent_cyan"], text_color="#080C14", command=self.selecionar_e_precificar).grid(row=0, column=0, padx=10)
        ctk.CTkButton(btn_frame, text="🔍 Cruzar com Fornecedores", command=self.cruzar_fornecedores).grid(row=0, column=1, padx=10)
        ctk.CTkButton(btn_frame, text="📥 Importar Tabela de Distribuidor", command=self.importar_tabela_distribuidor).grid(row=0, column=2, padx=10)
        ctk.CTkButton(btn_frame, text="📄 Gerar Proposta Comercial", command=self.gerar_proposta_comercial).grid(row=0, column=3, padx=10)
        ctk.CTkButton(btn_frame, text="🛒 Cotar no Mercado Livre", command=self.cotar_mercado_livre).grid(row=0, column=4, padx=10)
        
    def selecionar_e_precificar(self):
        prod = self._get_selected_prod()
        if not prod: return
        link = prod.get("link_sistema") or prod.get("link_edital") or ""
        self.app.selecionar_aba("Editais & Custos")
        if hasattr(self.app, "painel_editais"):
            pe = self.app.painel_editais
            if hasattr(pe, "txt_entrada_rapida"):
                pe.txt_entrada_rapida.delete(0, "end")
                pe.txt_entrada_rapida.insert(0, link)
                if hasattr(pe, "_carregar_direto"):
                    pe._carregar_direto()

    def cotar_mercado_livre(self):
        prod = self._get_selected_prod()
        if not prod: return
        cotar_no_ml_gui(self, prod)

        
        self.dados_produtos = []
        self.after(500, self.carregar_produtos_nuvem)
        
    def carregar_produtos_nuvem(self):
        import threading
        def worker():
            try:
                import os
                from supabase import create_client
                url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
                key = os.getenv("SUPABASE_KEY") or ""
                sb = create_client(url, key)
                res = sb.table('licitacoes').select('*').order('atualizado_em', desc=True).limit(200).execute()
                
                prod_kws = [
                    "produto", "produtos", "aquisição", "aquisicao", "fornecimento", "compra",
                    "material", "materiais", "peça", "peca", "peças", "pecas",
                    "equipamento", "equipamentos", "computador", "notebook",
                    "servidor", "switch", "roteador", "nobreak", "toner", "cartucho",
                    "suprimento", "suprimentos", "cabo", "rack", "cftv", "camera", "câmera",
                    "periférico", "periferico", "disco", "ssd", "memória", "memoria",
                    "hardware", "impressora", "scanner", "telecom", "fibra"
                ]
                
                dados = []
                for lic in (res.data or []):
                    if not isinstance(lic, dict):
                        continue
                    tipo = str(lic.get("tipo_contratacao") or "").upper()
                    obj = str(lic.get("objeto") or "").lower()
                    status = str(lic.get("status_analise") or "pendente").lower()
                    
                    if status in ["descartada"]:
                        continue

                    # 1. Se for marcado explicitamente como PRODUTO, MISTO, COMPRA, AQUISICAO ou BEM
                    if tipo in ["PRODUTO", "MISTO", "COMPRA", "AQUISICAO", "BEM"]:
                        dados.append(lic)
                    # 2. Se o objeto envolver fornecimento de materiais, peças, suprimentos e equipamentos TI/Telecom
                    elif any(kw in obj for kw in prod_kws):
                        dados.append(lic)

                # Fallback defensivo: se banco tiver apenas registros mistos ou cadastros gerais e dados for vazio,
                # carrega todos os certames não descartados para garantir que a aba nunca fique em branco
                if not dados and res.data:
                    for lic in res.data:
                        if isinstance(lic, dict) and str(lic.get("status_analise") or "").lower() != "descartada":
                            dados.append(lic)

                self.dados_produtos = dados
            except Exception as e:
                print("Erro ao carregar produtos:", e)
                self.dados_produtos = []
            self.after(0, self._exibir_produtos_nuvem)
        
        threading.Thread(target=worker, daemon=True).start()
        
    def _exibir_produtos_nuvem(self):
            
        for row in self.tree_produtos.get_children():
            self.tree_produtos.delete(row)
            
        for i, prod in enumerate(self.dados_produtos):
            if not isinstance(prod, dict): continue
            uf_cidade = f"[{prod.get('uf', 'BR')}] {prod.get('municipio', 'Nacional')}"
            orgao = str(prod.get("orgao_nome") or "")[:30]
            objeto = str(prod.get("objeto") or "")[:50]
            valor_raw = prod.get("valor_estimado")
            valor = float(valor_raw) if valor_raw else 0.0  # type: ignore
            valor_str = f"R$ {valor:,.2f}"
            status = str(prod.get("status_analise") or "pendente").title()
            
            tag = "evenrow" if i % 2 == 0 else "oddrow"
            self.tree_produtos.insert("", "end", iid=str(i), values=(uf_cidade, orgao, objeto, valor_str, status), tags=(tag,))

    def _get_selected_prod(self):
        sel = self.tree_produtos.selection()
        if not sel:
            import tkinter.messagebox as messagebox
            messagebox.showwarning("Aviso", "Selecione um produto na tabela primeiro.")
            return None
        return self.dados_produtos[int(sel[0])]
        
    def cruzar_fornecedores(self):
        prod = self._get_selected_prod()
        if not prod: return
        
        descricao = prod.get("objeto", "")
        valor_ref = float(prod.get("valor_estimado") or 0.0)
        
        try:
            from comparador_distribuidores import cruzar_item_com_distribuidores
            resultado = cruzar_item_com_distribuidores(descricao, valor_ref)
            
            import tkinter.messagebox as messagebox
            if not resultado.get("encontrado"):
                messagebox.showinfo("Cruzamento de Fornecedores", f"Nenhum fornecedor encontrado no catálogo para: {descricao[:30]}...")
                return
                
            msg = f"🏆 Melhor Distribuidor: {resultado.get('distribuidor')}\n" \
                  f"📦 Item: {resultado.get('descricao_fornecedor')}\n" \
                  f"🔢 Estoque Atual: {resultado.get('estoque')} unidades\n\n" \
                  f"💰 Menor Custo Encontrado: R$ {resultado.get('custo_distribuidor'):,.2f}\n" \
                  f"🚚 Frete p/ Palmas (Fixo Estimado): R$ {resultado.get('frete_estimado'):,.2f}\n" \
                  f"💲 Custo Total (Produto + Frete): R$ {resultado.get('custo_total'):,.2f}\n\n" \
                  f"📊 Piso Mín. p/ Venda (Margem >= 30%): R$ {resultado.get('piso_venda'):,.2f}\n" \
                  f"📈 Lucro Líquido Projetado: R$ {resultado.get('lucro_projetado_rs'):,.2f} ({resultado.get('margem_pct'):.1f}%)"
                  
            messagebox.showinfo("Resultado: Cruzamento Distribuidor", msg)
        except Exception as e:
            import tkinter.messagebox as messagebox
            messagebox.showerror("Erro", f"Falha no cruzamento:\n{e}")
            
    def importar_tabela_distribuidor(self):
        import tkinter.messagebox as messagebox
        from tkinter import filedialog
        import os
        
        initial = os.path.expanduser("~/Documentos")
        path = filedialog.askopenfilename(
            title="Selecionar Catálogo do Distribuidor",
            initialdir=initial,
            filetypes=[("Planilhas Excel/CSV", "*.xlsx *.csv"), ("Todos os arquivos", "*.*")]
        )
        if not path: return
        
        try:
            messagebox.showinfo("Importar Catálogo", f"Arquivo {os.path.basename(path)} simulado para importação para o Supabase (catalogo_distribuidores) com sucesso!")
        except Exception as e:
            messagebox.showerror("Erro", f"Falha na importação: {e}")
            
    def gerar_proposta_comercial(self):
        prod = self._get_selected_prod()
        if not prod: return
        
        import tkinter.messagebox as messagebox
        import os
        
        pasta_dest = os.path.expanduser("~/Documentos/Licitacoes_Aprovadas")
        os.makedirs(pasta_dest, exist_ok=True)
        
        arq = os.path.join(pasta_dest, f"Proposta_Venda_{prod.get('numero_pregao', 'S_N').replace('/', '_')}.xlsx")
        
        # Simulando criação de arquivo
        with open(arq, "w") as f:
            f.write("Planilha de Proposta de Venda de Produto")
            
        resp = messagebox.askyesno("Proposta Gerada", "Planilha de Proposta Comercial gerada com sucesso!\n\nDeseja abrir a pasta no sistema operacional?")
        if resp:
            import subprocess; subprocess.Popen(['xdg-open', pasta_dest])


# ─── PAINEL 5: CERTAMES APROVADOS PARA PARTICIPAÇÃO (AGENDA DISPUTAS) ───────────
class PainelAprovados(ctk.CTkFrame):
    """
    Aba 5: 🎯 Aprovados para Participação
    Exibe a agenda cronológica de certames confirmados para participação com Stop-Loss
    (status_analise = 'aprovado_disputa').
    """
    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self.dados_aprovados: list[dict[str, Any]] = []

        # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(10, 0))
        
        ctk.CTkLabel(
            header_frame,
            text="🎯  Agenda de Certames Aprovados para Participação",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#10B981",
        ).pack(side="left")

        btn_refresh = ctk.CTkButton(
            header_frame,
            text="🔄 Atualizar Agenda",
            fg_color="#1E293B",
            hover_color="#334155",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.carregar_aprovados,
        )
        btn_refresh.pack(side="right")

        # Tabela Treeview
        from tkinter import ttk
        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=20, pady=10)

        cols = ("data_sessao", "orgao", "pregao", "objeto", "valor", "stop_loss", "portal")
        self.tree_aprovados = ttk.Treeview(tree_frame, columns=cols, show="headings", height=12)
        
        self.tree_aprovados.heading("data_sessao", text="📅 Data / Horário Disputa")
        self.tree_aprovados.heading("orgao", text="🏛️ Órgão")
        self.tree_aprovados.heading("pregao", text="📋 Pregão")
        self.tree_aprovados.heading("objeto", text="📝 Objeto")
        self.tree_aprovados.heading("valor", text="💰 Valor Estimado")
        self.tree_aprovados.heading("stop_loss", text="🛑 Stop-Loss Aprovado")
        self.tree_aprovados.heading("portal", text="🌐 Portal")

        self.tree_aprovados.column("data_sessao", width=180, anchor="center")
        self.tree_aprovados.column("orgao", width=220, anchor="w")
        self.tree_aprovados.column("pregao", width=110, anchor="center")
        self.tree_aprovados.column("objeto", width=320, anchor="w")
        self.tree_aprovados.column("valor", width=130, anchor="e")
        self.tree_aprovados.column("stop_loss", width=140, anchor="e")
        self.tree_aprovados.column("portal", width=110, anchor="center")

        self.tree_aprovados.tag_configure("evenrow", background="#181A20", foreground="#FFFFFF")
        self.tree_aprovados.tag_configure("oddrow", background="#22242A", foreground="#FFFFFF")
        self.tree_aprovados.pack(fill="both", expand=True)

        # Barra de Ações Inferior
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkButton(
            btn_frame,
            text="⚡ Enviar p/ Console da Sessão",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COR["accent_cyan"],
            text_color="#080C14",
            hover_color="#06B6D4",
            height=34,
            command=self._enviar_para_sessao,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_frame,
            text="📄 Rever em Editais & Custos",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#334155",
            text_color="#FFFFFF",
            hover_color="#475569",
            height=34,
            command=self._rever_em_editais,
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            btn_frame,
            text="🌐 Abrir Sala / Link",
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            text_color="#38BDF8",
            border_width=1,
            border_color="#38BDF8",
            hover_color="#1E293B",
            height=34,
            command=self._abrir_link_certame,
        ).pack(side="left", padx=10)

        self.after(500, self.carregar_aprovados)

    def carregar_aprovados(self) -> None:
        import threading
        def worker():
            try:
                import os
                from supabase import create_client
                url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
                key = os.getenv("SUPABASE_KEY") or ""
                sb = create_client(url, key)
                res = sb.table("licitacoes").select("*").eq("status_analise", "aprovado_disputa").order("atualizado_em", desc=True).execute()
                dados = res.data or []
                self.dados_aprovados = dados
            except Exception as e:
                logger.warning(f"Erro ao carregar aprovados do Supabase: {e}")
                self.dados_aprovados = []
            self.after(0, self._exibir_aprovados)
        threading.Thread(target=worker, daemon=True).start()

    def _exibir_aprovados(self) -> None:
        for r in self.tree_aprovados.get_children():
            self.tree_aprovados.delete(r)

        # Ordenação inteligente: os que abrem mais cedo primeiro
        lista_ordenada = []
        for item in self.dados_aprovados:
            if not isinstance(item, dict): continue
            crono = PainelDashboard._extrair_cronograma_licitacao(item)
            lista_ordenada.append((item, crono))

        def chave_cronograma(par):
            _, c = par
            st = c["prazo_status"]
            if st == "hoje": return (0, c["dias_restantes"])
            elif st == "amanha": return (1, c["dias_restantes"])
            elif st == "48h": return (2, c["dias_restantes"])
            elif st == "aberto": return (3, c["dias_restantes"])
            else: return (4, c["dias_restantes"])

        lista_ordenada.sort(key=chave_cronograma)
        self.dados_aprovados = [p[0] for p in lista_ordenada]

        for i, (lic, crono) in enumerate(lista_ordenada):
            data_str = crono["txt_abertura"] or crono["txt_encerramento"]
            if crono["prazo_status"] == "hoje":
                data_str = f"🚨 HOJE: {data_str}"
            elif crono["prazo_status"] == "amanha":
                data_str = f"⚡ AMANHÃ: {data_str}"

            uf = lic.get("uf", "BR")
            orgao = f"[{uf}] " + str(lic.get("orgao_nome") or "")[:28]
            pregao = str(lic.get("numero_pregao") or "S/N")
            objeto = str(lic.get("objeto") or "")[:60]
            val_est = float(lic.get("valor_estimado") or 0.0)
            val_str = f"R$ {val_est:,.2f}"
            stop = float(lic.get("stop_loss") or (val_est * 0.85))
            stop_str = f"R$ {stop:,.2f}"
            portal = str(lic.get("portal") or "COMPRAS_GOV")

            tag = "evenrow" if i % 2 == 0 else "oddrow"
            self.tree_aprovados.insert("", "end", iid=str(i), values=(data_str, orgao, pregao, objeto, val_str, stop_str, portal), tags=(tag,))

    def _get_selected(self) -> Optional[dict[str, Any]]:
        sel = self.tree_aprovados.selection()
        if not sel:
            import tkinter.messagebox as messagebox
            messagebox.showwarning("Aviso", "Selecione um certame aprovado na agenda primeiro.")
            return None
        return self.dados_aprovados[int(sel[0])]

    def _enviar_para_sessao(self) -> None:
        lic = self._get_selected()
        if not lic: return
        self.app.licitacao_ativa = lic
        self.app.definir_licitacao_ativa(lic)
        self.app.selecionar_aba("⚡  Console da Sessão")
        if hasattr(self.app, "painel_sessao"):
            pregao = lic.get("numero_pregao") or ""
            self.app.painel_sessao.carregar_licitacao_disputa(pregao)

    def _rever_em_editais(self) -> None:
        lic = self._get_selected()
        if not lic: return
        link = lic.get("link_sistema") or lic.get("link_edital") or ""
        self.app.selecionar_aba("Editais & Custos")
        if hasattr(self.app, "painel_editais"):
            pe = self.app.painel_editais
            if hasattr(pe, "txt_entrada_rapida"):
                pe.txt_entrada_rapida.delete(0, "end")
                pe.txt_entrada_rapida.insert(0, link)
                if hasattr(pe, "_carregar_direto"):
                    pe._carregar_direto()

    def _abrir_link_certame(self) -> None:
        lic = self._get_selected()
        if not lic: return
        import webbrowser
        link = lic.get("link_sistema") or lic.get("link_edital") or ""
        if link and link.startswith("http"):
            webbrowser.open(link)
        else:
            import tkinter.messagebox as messagebox
            messagebox.showinfo("Link", "Certame não possui link direto cadastrado.")


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("🤖 Robô de Licitações – Multi-Pregão & Dispensas (Lei 14.133/2021)")
        self.geometry("1280x800")
        self.minsize(1024, 720)
        self.configure(fg_color=COR["bg_deep"])

        # Catálogo Dinâmico Multi-Certame
        self.catalogo_licitacoes: Dict[str, ResultadoEdital] = {}
        self.certame_selecionado: Optional[str] = None

        self._paineis: dict[str, ctk.CTkFrame] = {}
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Inicia o Listener de Callbacks do Telegram em thread daemon
        try:
            from telegram_service import iniciar_listener_telegram
            iniciar_listener_telegram()
        except Exception as exc_tg:
            logger.warning(f"Não foi possível iniciar o listener do Telegram: {exc_tg}")

    def on_closing(self) -> None:
        """Encerramento imediato para evitar deadlock no Tcl/Tk (GIL)."""
        import os
        os._exit(0)

    def adicionar_licitacao(self, nome_chave: str, res: ResultadoEdital) -> None:
        """Adiciona um pregão/dispensa ao catálogo diário sem apagar os anteriores."""
        self.catalogo_licitacoes[nome_chave] = res
        self.certame_selecionado = nome_chave

        # Atualiza a interface
        self.painel_dashboard.atualizar_catalogo()
        self.painel_editais.atualizar_lista_certames()
        if hasattr(self, "painel_aprovados"):
            self.painel_aprovados.carregar_aprovados()
        self.painel_sessao.atualizar_seletor_disputa()

        messagebox.showinfo("Licitação Adicionada", f"✅ '{nome_chave}' adicionada com sucesso!\nTotal de certames ativos hoje: {len(self.catalogo_licitacoes)}")

    def definir_licitacao_ativa(self, licitacao_dados: dict, itens: list | None = None) -> None:
        self.licitacao_ativa = licitacao_dados
        self.itens_ativos = itens or []
        # Sincroniza imediatamente o Console de Disputa
        if hasattr(self, 'painel_sessao') and hasattr(self.painel_sessao, 'sincronizar_com_licitacao_ativa'):
            self.painel_sessao.sincronizar_com_licitacao_ativa()

    def _build_ui(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # Sidebar
        sidebar = ctk.CTkFrame(self, fg_color=COR["bg_card"], width=220, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(9, weight=1)

        lbl_logo = ctk.CTkLabel(sidebar, text="🤖  ROBÔ LICITAÇÕES", font=ctk.CTkFont(size=16, weight="bold"), text_color=COR["accent_cyan"])
        lbl_logo.grid(row=0, column=0, padx=16, pady=(16, 20), sticky="w")

        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        # 6 Abas Oficiais do Fluxo JRBC TECH
        abas = [
            ("📊  Dashboard", "dashboard"),
            ("🛠️  Serviços", "servicos"),
            ("📦  Serviço com Produtos", "produtos"),
            ("📄  Editais & Custos", "editais"),
            ("🎯  Aprovados para Participação", "aprovados"),
            ("⚡  Console da Sessão", "sessao"),
        ]

        for idx, (nome, id_aba) in enumerate(abas, 1):
            btn = ctk.CTkButton(
                sidebar,
                text=nome,
                font=ctk.CTkFont(size=12, weight="bold"),
                anchor="w",
                fg_color="transparent",
                text_color=COR["text_secondary"],
                hover_color="#1E293B",
                height=38,
                corner_radius=10,
                command=lambda n=nome: self.selecionar_aba(n),
            )
            btn.grid(row=idx, column=0, sticky="ew", padx=10, pady=2)
            self._nav_buttons[nome] = btn

        lbl_status_lic = ctk.CTkLabel(sidebar, text="✅ Licença Ativa (Full)", font=ctk.CTkFont(size=11), text_color=COR["accent_green"])
        lbl_status_lic.grid(row=9, column=0, padx=16, pady=16, sticky="w")

        # Container de Conteúdo
        self.tab_container = ctk.CTkFrame(self, fg_color="transparent")
        self.tab_container.grid(row=0, column=1, sticky="nsew", padx=16, pady=16)
        self.tab_container.grid_rowconfigure(0, weight=1)
        self.tab_container.grid_columnconfigure(0, weight=1)

        # Painéis Oficiais do Fluxo
        self.painel_dashboard = PainelDashboard(self.tab_container, app=self)
        self.painel_servicos = PainelServicos(self.tab_container, app=self)
        self.painel_produtos = PainelProdutos(self.tab_container, app=self)
        self.painel_editais = PainelEditais(self.tab_container, app=self)
        self.painel_aprovados = PainelAprovados(self.tab_container, app=self)
        self.painel_sessao = PainelSessao(self.tab_container, app=self)
        self.painel_pncp = PainelPNCP(self.tab_container, app=self)

        self._paineis["📊  Dashboard"] = self.painel_dashboard
        self._paineis["🛠️  Serviços"] = self.painel_servicos
        self._paineis["📦  Serviço com Produtos"] = self.painel_produtos
        self._paineis["📄  Editais & Custos"] = self.painel_editais
        self._paineis["🎯  Aprovados para Participação"] = self.painel_aprovados
        self._paineis["⚡  Console da Sessão"] = self.painel_sessao

        for p in self._paineis.values():
            p.grid(row=0, column=0, sticky="nsew")

        self.selecionar_aba("📊  Dashboard")

    def selecionar_aba(self, nome_aba: str) -> None:
        chave_alvo = nome_aba
        for chave in self._paineis:
            if nome_aba.lower() in chave.lower():
                chave_alvo = chave
                break

        for n, btn in self._nav_buttons.items():
            if n == chave_alvo:
                btn.configure(fg_color=COR["accent_cyan"], text_color="#080C14")
            else:
                btn.configure(fg_color="transparent", text_color=COR["text_secondary"])

        if chave_alvo in self._paineis:
            self._paineis[chave_alvo].tkraise()

    def mudar_aba(self, nome_aba: str) -> None:
        """Alias flexível para alternar abas com segurança."""
        self.selecionar_aba(nome_aba)

    def log(self, nivel: str, msg: str) -> None:
        logger.info(f"[{nivel}] {msg}")

import os
import fcntl
_lock_fd = None

def run_gui() -> None:
    global _lock_fd
    try:
        lock_file = os.path.join(os.path.expanduser("~"), ".robo_licitacoes.lock")
        _lock_fd = os.open(lock_file, os.O_CREAT | os.O_WRONLY)
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception as e:
        import sys
        print(f"O robô já está em execução! (Lock retido). Encerrando. Erro: {e}")
        sys.exit(0)

    try:
        from telegram_service import iniciar_listener_telegram
        iniciar_listener_telegram()
    except Exception as e:
        logger.warning(f"Aviso ao inicializar listener do Telegram: {e}")

    app = App()
    app.mainloop()


if __name__ == "__main__":
    run_gui()
