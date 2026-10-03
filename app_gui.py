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
    # Pega da variável de ambiente OU usa a URL padrão oficial da JRBC TECH
    SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://szphoxostihnpcovyzvx.supabase.co"
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or ""
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
        ItemEdital,
        ResultadoEdital,
        SeveridadeRisco,
    )
    from mercadolivre_gui import cotar_no_ml_gui
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
    # Fundos e Superfícies
    "bg_deep": "#080C14",       # Fundo principal ultra-dark com tom azulado profundo
    "bg_panel": "#0F172A",      # Painéis laterais e containers (Slate 900)
    "bg_card": "#1E293B",       # Superfície dos cards de oportunidades (Slate 800)
    "bg_input": "#0F172A",      # Campos de digitação e filtros
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

class SearchableSelectPopup(ctk.CTkToplevel):
    def __init__(self, master, values, on_select_callback, **kwargs):
        super().__init__(master, **kwargs)
        self.title("Selecionar Opção")
        self.geometry("600x450")
        self.transient(master)
        self.grab_set()
        
        self.values = values
        self.on_select_callback = on_select_callback
        
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        
        self.entry_search = ctk.CTkEntry(self, placeholder_text="🔎 Digite para filtrar (ex: TO, Palmas, Pregão)...")
        self.entry_search.grid(row=0, column=0, padx=16, pady=16, sticky="ew")
        self.entry_search.bind("<KeyRelease>", self._on_search)
        
        self.scroll_frame = ctk.CTkScrollableFrame(self)
        self.scroll_frame.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="nsew")
        
        self.buttons = []
        self._populate(self.values)
        self.entry_search.focus()
        
    def _populate(self, items):
        for btn in self.buttons:
            btn.destroy()
        self.buttons.clear()
        
        for item in items:
            btn = ctk.CTkButton(
                self.scroll_frame, 
                text=item, 
                anchor="w", 
                fg_color="transparent", 
                text_color=COR["text_primary"],
                hover_color=COR["bg_panel"],
                command=lambda val=item: self._on_select(val)
            )
            btn.pack(fill="x", pady=2)
            btn.bind("<MouseWheel>", lambda e: self.scroll_frame._parent_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
            btn.bind("<Button-4>", lambda e: self.scroll_frame._parent_canvas.yview_scroll(-1, "units"))
            btn.bind("<Button-5>", lambda e: self.scroll_frame._parent_canvas.yview_scroll(1, "units"))
            self.buttons.append(btn)
            
    def _on_search(self, event=None):
        query = self.entry_search.get().lower()
        if not query:
            filtered = self.values
        else:
            filtered = [v for v in self.values if query in v.lower()]
        self._populate(filtered)
        
    def _on_select(self, value):
        self.on_select_callback(value)
        self.destroy()

class SearchableDropdown(ctk.CTkFrame):
    def __init__(self, master, values=None, command=None, width=200, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.values = values or []
        self.command = command
        self.current_value = ""
        self._values = self.values
        
        self.btn = ctk.CTkButton(
            self, 
            text="Selecione...", 
            width=width,
            anchor="w",
            fg_color="#1F2937",
            text_color="#F8FAFC",
            border_width=1,
            border_color="#374151",
            hover_color="#374151",
            command=self._open_popup
        )
        self.btn.pack(fill="both", expand=True)
        
    def configure(self, values=None, **kwargs):
        if values is not None:
            self.values = values
            self._values = values
            if not self.current_value and values:
                self.set(values[0])
        
    def set(self, value):
        self.current_value = value
        self.btn.configure(text=value[:70] + ("..." if len(value) > 70 else ""))
        
    def get(self):
        return self.current_value
        
    def _open_popup(self):
        if not self.values or self.values == ["(Carregando...)"] or self.values == ["(Nenhum pregão carregado)"] or self.values == ["(Nenhum pregão)"]:
            return
        popup = SearchableSelectPopup(self.winfo_toplevel(), self.values, self._on_select)
        
    def _on_select(self, value):
        self.set(value)
        if self.command:
            self.command(value)


# ─── PAINEL 1: DASHBOARD ──────────────────────────────────────────────────────
class PainelDashboard(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self._build()
        self.carregar_dados_dashboard()
        self.verificar_status_chrome()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

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
        main_content.grid(row=2, column=0, sticky="nsew")
        main_content.grid_columnconfigure(0, weight=1)
        main_content.grid_columnconfigure(1, weight=0, minsize=240)
        main_content.grid_rowconfigure(0, weight=1)

        card_lics = CardFrame(main_content)
        card_lics.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        card_lics.grid_rowconfigure(1, weight=1)
        card_lics.grid_columnconfigure(0, weight=1)

        self._criar_barra_filtros(card_lics)

        tbl_frame = ctk.CTkFrame(card_lics, fg_color="transparent")
        tbl_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
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

    def _criar_barra_filtros(self, parent_frame: Any) -> None:
        frame_filtros = ctk.CTkFrame(parent_frame, fg_color="#111827")
        frame_filtros.grid(row=0, column=0, sticky="ew", padx=12, pady=(0, 10))

        ctk.CTkLabel(frame_filtros, text="Modalidade:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#9ca3af").pack(side="left", padx=(10, 4), pady=6)
        self.combo_modalidade = ctk.CTkComboBox(
            frame_filtros, 
            values=["Todas", "Dispensa Eletrônica", "Pregão Eletrônico"], 
            state="readonly", 
            width=160,
            command=lambda e: self.aplicar_filtros()
        )
        self.combo_modalidade.set("Todas")
        self.combo_modalidade.pack(side="left", padx=(0, 15), pady=6)

        ctk.CTkLabel(frame_filtros, text="UF / Região:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#9ca3af").pack(side="left", padx=(0, 4), pady=6)
        self.combo_uf = ctk.CTkComboBox(
            frame_filtros, 
            values=["TODOS", "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"],
            state="readonly", 
            width=260,
            command=lambda e: self.aplicar_filtros()
        )
        self.combo_uf.set("TODOS")
        self.combo_uf.pack(side="left", padx=(0, 15), pady=6)

        ctk.CTkLabel(frame_filtros, text="Status:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#9ca3af").pack(side="left", padx=(0, 4), pady=6)
        self.combo_status = ctk.CTkComboBox(
            frame_filtros, 
            values=["Ativas (Pendente + Favorita)", "Apenas Favoritas", "Pendentes", "Descartadas", "Todas"], 
            state="readonly", 
            width=200,
            command=lambda e: self.aplicar_filtros()
        )
        self.combo_status.set("Ativas (Pendente + Favorita)")
        self.combo_status.pack(side="left", padx=(0, 15), pady=6)
        
        disable_mouse_scroll(self.combo_modalidade)
        disable_mouse_scroll(self.combo_uf)
        disable_mouse_scroll(self.combo_status)

        btn_recarregar = ctk.CTkButton(
            frame_filtros, 
            text="🔄 Atualizar", 
            command=self.carregar_dados_dashboard,
            fg_color="#374151", 
            text_color="#f9fafb", 
            font=ctk.CTkFont(size=11, weight="bold"), 
            width=100
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

    def aplicar_filtros(self) -> None:
        if not hasattr(self, "todas_licitacoes"):
            return
            
        mod_selecionada = self.combo_modalidade.get()
        uf_selecionada = self.combo_uf.get()
        status_selecionado = self.combo_status.get()

        for widget in self.scroll_lics.winfo_children():
            widget.destroy()

        uf_vizinhos = ["TO", "GO", "PA", "MA", "BA", "MT", "PI"]
        uf_norte = ["TO", "PA", "AP", "AM", "RO", "RR", "AC"]

        total_hoje = 0
        participando = 0
        row_idx = 0

        for lic in self.todas_licitacoes:
            modalidade_nome = lic.get("modalidade") or lic.get("tipo_processo") or ""  # type: ignore
            uf = (lic.get("uf") or "").upper().strip()  # type: ignore
            status = (lic.get("status_analise") or "PENDENTE").upper().strip()  # type: ignore
            objeto = (lic.get("objeto") or "Sem descrição")  # type: ignore

            if mod_selecionada != "Todas" and mod_selecionada.lower() not in modalidade_nome.lower():
                continue

            if uf_selecionada != "TODOS" and uf != uf_selecionada:
                continue

            if status_selecionado == "Ativas (Pendente + Favorita)" and status not in ["PENDENTE", "FAVORITA"]:
                continue
            elif status_selecionado == "Apenas Favoritas" and status != "FAVORITA":
                continue
            elif status_selecionado == "Pendentes" and status != "PENDENTE":
                continue
            elif status_selecionado == "Descartadas" and status != "DESCARTADA":
                continue

            sigla = "PE" if "Pregão" in modalidade_nome else ("Dispensa" if "Dispensa" in modalidade_nome else modalidade_nome)
            numero = lic.get("numero_pregao", "S/N")  # type: ignore
            uasg = lic.get("numero_uasg", "S/ UASG")  # type: ignore
            pregao_formatado = f"{sigla} {numero} (UASG {uasg})"
            
            valor = float(lic.get('valor_estimado') or 0.0)  # type: ignore
            
            # CUIDADO COM PALAVRAS DE TI
            ti_kws = ["tecnologia", "informática", "software", "computador", "rede", "suporte", "cabeamento", "notebook", "nobreak"]
            is_ti = any(kw in objeto.lower() for kw in ti_kws)

            # --- Criação do Card ---
            card = ctk.CTkFrame(self.scroll_lics, fg_color=COR["bg_card"], corner_radius=10, border_width=1, border_color=COR["border"])
            card.pack(fill="x", pady=6, padx=4)
            card.grid_columnconfigure(1, weight=1)
            
            # Linha 0: Badges
            badges_frame = ctk.CTkFrame(card, fg_color="transparent")
            badges_frame.grid(row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(12, 4))
            
            # Badge Tipo
            tipo_bg = COR["accent_cyan"] if is_ti else COR["accent_purple"]
            tipo_text = " 🛠️ SERVIÇO " if is_ti else " 📦 PRODUTO "
            ctk.CTkLabel(badges_frame, text=tipo_text, fg_color=tipo_bg, text_color="#080C14", corner_radius=5, font=ctk.CTkFont(size=10, weight="bold")).pack(side="left", padx=(0, 6))
            
            # Badge Modalidade
            mod_bg = COR["border"]
            mod_text = f" {sigla.upper()} "
            ctk.CTkLabel(badges_frame, text=mod_text, fg_color=mod_bg, text_color=COR["text_primary"], corner_radius=5, font=ctk.CTkFont(size=10, weight="bold")).pack(side="left", padx=(0, 6))

            # Prazo
            import datetime
            prazo = " ⏳ PRAZO: EM BREVE "
            if lic.get("data_abertura"):  # type: ignore
                try:
                    dt = datetime.datetime.fromisoformat(lic.get("data_abertura").replace("Z", "+00:00"))  # type: ignore
                    diff = dt - datetime.datetime.now(datetime.timezone.utc)
                    if diff.total_seconds() > 0 and diff.total_seconds() < 48 * 3600:
                        ctk.CTkLabel(badges_frame, text=" ⏳ < 48H ", fg_color=COR["accent_amber"], text_color="#080C14", corner_radius=5, font=ctk.CTkFont(size=10, weight="bold")).pack(side="left", padx=(0, 6))
                except:
                    pass

            # Linhas de Texto
            orgao = f"[{uf}] {lic.get('orgao_nome', '')[:45]}"  # type: ignore
            ctk.CTkLabel(card, text=orgao, font=ctk.CTkFont(size=14, weight="bold"), text_color=COR["text_primary"]).grid(row=1, column=0, columnspan=2, sticky="w", padx=16)
            ctk.CTkLabel(card, text=objeto[:100] + ("..." if len(objeto) > 100 else ""), font=ctk.CTkFont(size=12), text_color=COR["text_secondary"]).grid(row=2, column=0, columnspan=2, sticky="w", padx=16, pady=(2, 12))

            # Lado Direito (Valores e Ações)
            val_str = f"R$ {valor:,.2f}"
            ctk.CTkLabel(card, text=val_str, font=ctk.CTkFont(size=18, weight="bold"), text_color=COR["accent_green"]).grid(row=1, column=2, sticky="e", padx=16, rowspan=2)

            link = lic.get("link_edital", "")  # type: ignore
            btn = ctk.CTkButton(card, text="Acessar Processo", font=ctk.CTkFont(weight="bold"), fg_color=COR["accent_green"], text_color="#080C14", hover_color=COR["accent_cyan"], corner_radius=20, width=120)
            if link:
                import webbrowser
                btn.configure(command=lambda l=link: webbrowser.open(l))
            btn.grid(row=2, column=2, sticky="e", padx=16, pady=(0, 12))
            
            total_hoje += 1
            if status.lower() in ["proposta_enviada", "em_disputa", "ativo", "favorita"]:
                participando += 1
                
        self.card_radar_pncp.atualizar(str(total_hoje))
        self.card_servicos_ti.atualizar(str(participando))

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

    def carregar_dados_dashboard(self) -> None:
        """Busca licitações salvas no Supabase e popula a tabela no Dashboard (Assíncrono)."""
        import threading
        import cache_manager
        
        # 1. Carrega do cache instantaneamente na MainThread
        cache = cache_manager.carregar_cache()
        if "todas_licitacoes" in cache:
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
                    
                self.todas_licitacoes = resposta.data or []
                
                # 2. Salva o novo cache
                cache_manager.salvar_cache("todas_licitacoes", self.todas_licitacoes)
                
                self.after(0, self.aplicar_filtros)
                logging.info(f"[GUI] {len(self.todas_licitacoes)} licitações carregadas com sucesso do Supabase!")
                
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
        self.grid_rowconfigure(2, weight=1)

        # 1. Seletor de Certame na Nuvem
        card_seletor = CardFrame(self)
        card_seletor.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 8))
        card_seletor.grid_columnconfigure(1, weight=1)

        import tkinter as tk
        ctk.CTkLabel(card_seletor, text="☁️  Selecione a Licitação na Nuvem:", font=ctk.CTkFont(size=13, weight="bold"), text_color=COR["accent_cyan"]).grid(row=0, column=0, padx=(16, 8), pady=10, sticky="w")
        
        self.cmb_certames = SearchableDropdown(
            card_seletor,
            values=["(Carregando...)"],
            command=self._on_certame_selecionado,
            width=300
        )
        self.cmb_certames.grid(row=0, column=1, padx=8, pady=10, sticky="ew")

        btn_remover = ctk.CTkButton(card_seletor, text="🔄 Atualizar Nuvem", width=120, fg_color=COR["accent_purple"], text_color="#FFFFFF", command=self.atualizar_lista_certames)
        btn_remover.grid(row=0, column=2, padx=(4, 16), pady=10)

        # 2. Barra de Regras de Lances
        card_regras = CardFrame(self)
        card_regras.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 8))
        
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

        btn_salvar_regra = ctk.CTkButton(card_regras, text="💾 Salvar Regra no Supabase", fg_color=COR["accent_green"], text_color="#000000", command=self._salvar_regras_supabase)
        btn_salvar_regra.pack(side="right", padx=(8, 16), pady=10)

        # 3. Tabela de Itens & Stop Loss
        card_itens = CardFrame(self)
        card_itens.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
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
                self.dados_nuvem = res.data
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
            self._on_certame_selecionado(lista_chaves[0])

    def _on_certame_selecionado(self, escolha: str) -> None:
        idx = self.cmb_certames._values.index(escolha) if escolha in self.cmb_certames._values else -1
        if idx >= 0 and hasattr(self, "dados_nuvem"):
            lic = self.dados_nuvem[idx]
            self.licitacao_ativa = lic
            
            # Extrair CNPJ, Ano, Sequencial e carregar itens reais do PNCP
            def worker():
                link = lic.get("link_sistema", "")  # type: ignore
                if "pncp.gov.br" in link:
                    try:
                        from pncp_client import extrair_params_pncp, carregar_certame_pncp
                        cnpj, ano, seq = extrair_params_pncp(link)
                        if cnpj and ano and seq:
                            self.after(0, lambda: self.tree_itens.delete(*self.tree_itens.get_children()))
                            self.after(0, lambda: self.tree_itens.insert("", "end", values=("⏳", "Buscando itens no PNCP...", "", "", "", "", "", "")))
                            
                            resultado = carregar_certame_pncp(link)
                            self.after(0, lambda: self._exibir_itens_pncp(lic, resultado))
                            return
                    except Exception as e:
                        import logging
                        logging.error(f"Erro ao buscar PNCP: {e}")
                
                # Fallback se falhar ou não for PNCP
                self.after(0, lambda: self._exibir_itens(lic))  # type: ignore

            import threading
            threading.Thread(target=worker, daemon=True).start()

    def _exibir_itens_pncp(self, lic, resultado):
        for row in self.tree_itens.get_children():
            self.tree_itens.delete(row)
        
        if not resultado.itens:
            self._exibir_itens(lic)
            return

        for item in resultado.itens:
            valor = item.valor_referencia or 0.0
            custo_real = item.custo_real or valor * 0.5
            piso = item.piso_preco or valor * 0.7
            margem = ((valor - piso) / valor * 100) if valor > 0 else 0.0

            val_ref_str = f"R$ {valor:,.2f}"
            custo_str = f"R$ {custo_real:,.2f}"
            piso_str = f"R$ {piso:,.2f}"
            margem_str = f"{margem:.1f}%"

            self.tree_itens.insert(
                "",
                "end",
                iid=f"{lic.get('id')}_{item.numero_item}",  # type: ignore
                values=(
                    str(item.numero_item),
                    item.descricao[:100],
                    str(item.quantidade),
                    item.unidade_medida,
                    val_ref_str,
                    custo_str,
                    margem_str,
                    piso_str,
                ),
                tags=("evenrow",)
            )

    def _exibir_itens(self, lic: dict) -> None:
        for row in self.tree_itens.get_children():
            self.tree_itens.delete(row)
            
        valor = float(lic.get("valor_estimado") or 0.0)  # type: ignore
        custo_real = float(lic.get("custo_real") or valor * 0.5)  # type: ignore
        piso = float(lic.get("piso_seguro") or valor * 0.7)  # type: ignore
        margem = ((valor - piso) / valor * 100) if valor > 0 else 0.0

        val_ref_str = f"R$ {valor:,.2f}"
        custo_str = f"R$ {custo_real:,.2f}"
        piso_str = f"R$ {piso:,.2f}"
        margem_str = f"{margem:.1f}%"

        self.tree_itens.insert(
            "",
            "end",
            iid=lic.get("id"),  # type: ignore
            values=(
                "1",
                lic.get("objeto", "")[:100],  # type: ignore
                "1",
                "UN",
                val_ref_str,
                custo_str,
                margem_str,
                piso_str,
            ),
            tags=("evenrow",)
        )
        
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
                
                from supabase import create_client
                url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
                key = os.getenv("SUPABASE_KEY") or ""
                sb = create_client(url, key)
                sb.table("licitacoes").update({"custo_real": novo_custo_f, "piso_seguro": novo_piso}).eq("id", lic_id).execute()
                
                # Refresh table
                self.atualizar_lista_certames()
                import tkinter.messagebox as messagebox
                messagebox.showinfo("Atualizado", "Custo e Stop-Loss recalcuados e salvos!")
            except Exception as e:
                import tkinter.messagebox as messagebox
                messagebox.showerror("Erro", f"Falha:\\n{e}")

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
            text_color=COR["accent_cyan"],
        ).grid(row=0, column=1, padx=(16, 4), sticky="e")

        self.cmb_disputa = SearchableDropdown(
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
            progress_color=COR["accent_green"],
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
            placeholder_text="https://sso.acesso.gov.br/login?client_id=comprasnet.gov.br",
            font=ctk.CTkFont(size=11),
        )
        self.txt_url.insert(0, "https://sso.acesso.gov.br/login?client_id=comprasnet.gov.br")
        self.txt_url.grid(row=0, column=1, padx=6, sticky="ew")

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
        self.atualizar_tabela_inicial()

    def atualizar_seletor_disputa(self) -> None:
        import os
        try:
            from supabase import create_client
            url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
            key = os.getenv("SUPABASE_KEY") or ""
            sb = create_client(url, key)
            res = sb.table("licitacoes").select("*").order("atualizado_em", desc=True).execute()
            self.dados_nuvem = res.data
        except Exception as e:
            self.dados_nuvem = []

        if not self.dados_nuvem:
            self.cmb_disputa.configure(values=["(Nenhum pregão carregado)"])
            self.cmb_disputa.set("(Nenhum pregão carregado)")
            self.atualizar_tabela_inicial()
            return

        lista_chaves = []
        for lic in self.dados_nuvem:
            uf = lic.get("uf", "")  # type: ignore
            orgao = lic.get("orgao_nome", "")[:20]  # type: ignore
            pregao = lic.get("numero_pregao", "")  # type: ignore
            texto = f"[{uf}] {orgao} - {pregao}"
            lista_chaves.append(texto)
            
        self.cmb_disputa.configure(values=lista_chaves)
        if lista_chaves:
            self.cmb_disputa.set(lista_chaves[0])
            self.atualizar_tabela_inicial()

    def atualizar_tabela_inicial(self) -> None:
        """Preenche a tabela da disputa com os itens do certame selecionado em estado aguardando."""
        for r in self.tree_sessao.get_children():
            self.tree_sessao.delete(r)
        self._item_rows.clear()

        escolha = self.cmb_disputa.get()
        if not escolha or escolha.startswith("(Nenhum"):
            return

        idx = self.cmb_disputa._values.index(escolha) if escolha in self.cmb_disputa._values else -1
        if idx < 0 or not hasattr(self, "dados_nuvem"):
            return

        lic = self.dados_nuvem[idx]

        # Formata o numero do processo para a URL
        uasg = str(lic.get("numero_uasg", "")).zfill(6)  # type: ignore
        num_raw = str(lic.get("numero_pregao", ""))  # type: ignore
        num = num_raw.split("/")[0].zfill(5) if "/" in num_raw else num_raw.zfill(5)
        ano = num_raw.split("/")[1] if "/" in num_raw else "2024"
        mod_cod = "05" if "preg" in (lic.get("modalidade") or "").lower() else "06"  # type: ignore
        numero_compra = f"{uasg}{mod_cod}{num}{ano}"

        url_sug = f"https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/fornecedor/compras?compra={numero_compra}"
        if hasattr(self, "txt_url"):
            self.txt_url.delete(0, "end")
            self.txt_url.insert(0, url_sug)

        # Preenche com os dados salvos no Supabase
        valor = float(lic.get("valor_estimado") or 0.0)  # type: ignore
        piso = float(lic.get("piso_seguro") or valor * 0.7)  # type: ignore
        v_ref_str = f"R$ {valor:,.2f}" if valor else "—"
        piso_str = f"R$ {piso:,.2f}"
        
        row_id = self.tree_sessao.insert(
            "",
            "end",
            values=(
                1,
                lic.get("objeto", "")[:80],  # type: ignore
                "—",
                v_ref_str,
                "—",
                f"⏳ AGUARDANDO (Stop: {piso_str})",
            ),
        )
        self._item_rows[1] = row_id

    def _iniciar_sessao(self) -> None:
        escolha = self.cmb_disputa.get()
        if not escolha or escolha.startswith("(Nenhum"):
            messagebox.showwarning(
                "Selecione um Pregão",
                "Carregue ou selecione um pregão válido na nuvem antes de iniciar a disputa.",
            )
            return

        idx = self.cmb_disputa._values.index(escolha) if escolha in self.cmb_disputa._values else -1
        if idx < 0 or not hasattr(self, "dados_nuvem"):
            return

        lic = self.dados_nuvem[idx]

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

        regras: list[RegraItem] = []
        valor = float(lic.get("valor_estimado") or 100.0)  # type: ignore
        custo_base = float(lic.get("custo_real") or valor * 0.6)  # type: ignore
        piso_calc = float(lic.get("piso_seguro") or valor * 0.7)  # type: ignore
        preco_ini = float(lic.get("valor_estimado") or piso_calc * 1.3)  # type: ignore

        regras.append(
            RegraItem(
                item_id=str(uuid.uuid4()),
                numero_item=1,
                descricao=lic.get("objeto", ""),  # type: ignore
                custo_unitario=custo_base,
                margem_minima_percent=10.0,
                preco_minimo=piso_calc,
                preco_inicial_proposta=preco_ini,
                intervalo_minimo=float(lic.get("intervalo_minimo") or 0.01),  # type: ignore
                tipo_intervalo=TipoIntervalo.MONETARIO,
                valor_referencia=valor,
                licitacao_id=lic.get("numero_pregao", "0"),  # type: ignore
            )
        )

        sessao_id = str(uuid.uuid4())
        url_alvo = self.txt_url.get().strip() or "https://sso.acesso.gov.br/login?client_id=comprasnet.gov.br"

        try:
            self._motor = MotorDecisao(dry_run=dry_run)
            self._motor.carregar_regras(regras)
            self._motor.iniciar_sessao()

            self._bot = BrowserBot(
                motor=self._motor,
                sessao_id=sessao_id,
                licitacao_id=lic.get("numero_pregao", ""),  # type: ignore
                callback_ciclo=self._on_ciclo_update,
            )
            self._bot.callback_copiloto = self._trigger_copiloto_auto
        except Exception as exc:
            messagebox.showerror("Erro ao Configurar Robô", f"Falha ao inicializar motor: {exc}")
            return

        self.btn_iniciar.configure(state="disabled")
        self.btn_parar.configure(state="normal")

        msg_modo = "🛡️ SIMULAÇÃO (Dry-Run)" if dry_run else "🔴 DISPUTA REAL (Envio de Lances)"
        self.log(f"🚀 Iniciando robô para '{escolha}' | {msg_modo}")
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
        url = self.txt_url.get().strip() or "https://sso.acesso.gov.br/login?client_id=comprasnet.gov.br"
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
                elif (pos and pos > 1):
                    status_txt = "🟡 SUPERADO"

            if num in self._item_rows:
                row_id = self._item_rows[num]
                desc_atual = self.tree_sessao.item(row_id)["values"][1]  # type: ignore
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
        
        # Coleta estado do pregao
        estado = {
            "orgao": self.cmb_disputa.get() if self.cmb_disputa.get() != "(Nenhum pregão)" else "Desconhecido",
            "item": "Itens atuais",
            "meu_lance": "N/A",
            "melhor_lance": "N/A",
            "stop_loss": "N/A",
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
                self.after(0, lambda: self._adicionar_chat(f"Erro no Copiloto: {e}", "#FF5252"))
                
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
class PainelServicos(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        
        # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(10, 0))
        ctk.CTkLabel(header_frame, text="🛠️ Serviços", font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        
        def _iniciar_siga_to():
            import threading
            import tkinter.messagebox as messagebox
            from siga_to_monitor import SigaMonitor
            import asyncio
            def worker():
                monitor = SigaMonitor()
                asyncio.run(monitor.executar_varredura())
                self.after(0, self.carregar_servicos_nuvem)
                self.after(0, lambda: messagebox.showinfo("Pronto", "Varredura do SIGA-TO concluída! A tabela foi atualizada."))
            threading.Thread(target=worker, daemon=True).start()

        def _iniciar_ambos():
            import threading
            import tkinter.messagebox as messagebox
            from buscador_pncp import executar_varredura_tocantins
            from siga_to_monitor import SigaMonitor
            import asyncio
            def worker_ambos():
                executar_varredura_tocantins()
                monitor = SigaMonitor()
                asyncio.run(monitor.executar_varredura())
                self.after(0, self.carregar_servicos_nuvem)
                self.after(0, lambda: messagebox.showinfo("Pronto", "Varredura do Compras.gov (PNCP) e SIGA-TO concluídas! A tabela foi atualizada."))
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
        tree_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        cols = ("uf_cidade", "distancia", "orgao", "objeto", "valor", "data_abertura")
        self.tree_servicos = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8)
        self.tree_servicos.heading("uf_cidade", text="🏙️ UF / Cidade")
        self.tree_servicos.heading("distancia", text="🛣️ Distância (Palmas)")
        self.tree_servicos.heading("orgao", text="Órgão")
        self.tree_servicos.heading("objeto", text="Objeto do Serviço")
        self.tree_servicos.heading("valor", text="Valor Estimado (R$)")
        self.tree_servicos.heading("data_abertura", text="Data Abertura")
        
        self.tree_servicos.column("uf_cidade", width=120, anchor="w")
        self.tree_servicos.column("distancia", width=140, anchor="center")
        self.tree_servicos.column("orgao", width=180, anchor="w")
        self.tree_servicos.column("objeto", width=300, anchor="w")
        self.tree_servicos.column("valor", width=120, anchor="e")
        self.tree_servicos.column("data_abertura", width=120, anchor="center")
        
        self.tree_servicos.tag_configure("evenrow", background="#181A20", foreground="#FFFFFF")
        self.tree_servicos.tag_configure("oddrow", background="#22242A", foreground="#FFFFFF")
        self.tree_servicos.pack(fill="both", expand=True)

        # Actions
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=10)
        
        ctk.CTkButton(btn_frame, text="📄 Ver Escopo TR", command=self.ver_escopo_tr).grid(row=0, column=0, padx=10)
        ctk.CTkButton(btn_frame, text="🚚 Calcular SLA/Logística", command=self.calcular_sla).grid(row=0, column=1, padx=10)
        ctk.CTkButton(btn_frame, text="📊 Gerar Excel", command=self.gerar_excel).grid(row=0, column=2, padx=10)

        self.dados_servicos = []
        # Wait slightly to load after initialization
        self.after(500, self.carregar_servicos_nuvem)

    def carregar_servicos_nuvem(self):
        import threading
        def worker():
            try:
                import os
                from supabase import create_client
                url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
                key = os.getenv("SUPABASE_KEY") or ""
                sb = create_client(url, key)
                res = sb.table('licitacoes').select('*').eq('tipo_contratacao', 'SERVICO').order('atualizado_em', desc=True).execute()
                
                # Filtro para Tecnologia da Informação (TI e Infraestrutura) e Abertos
                dados = []
                ti_kws = ["cabeamento", "rede", "manutenção de computador", "notebook", "nobreak", "cftv", "suporte", "servidor", "proxmox", "linux", "firewall", "backup", "tecnologia", "informática"]
                for srv in res.data:
                    if not isinstance(srv, dict): continue
                    obj = str(srv.get("objeto") or "").lower()
                    status = str(srv.get("status_analise") or "pendente").lower()
                    if status not in ["encerrada", "descartada"] and any(kw in obj for kw in ti_kws):
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
            
        for i, srv in enumerate(self.dados_servicos):
            uf_cidade = f"[{srv.get('uf', 'BR')}] {srv.get('municipio', 'Nacional')}"
            distancia = "0 km (Remoto)" if srv.get("uf") != "TO" else "Local (TO)"
            orgao = srv.get("orgao_nome", "")[:30]
            objeto = srv.get("objeto", "")[:50]
            valor = float(srv.get("valor_estimado") or 0.0)
            valor_str = f"R$ {valor:,.2f}"
            data_raw = srv.get("data_abertura") or ""
            abertura = str(data_raw)[:10] if data_raw else "N/D"
            
            tag = "evenrow" if i % 2 == 0 else "oddrow"
            self.tree_servicos.insert("", "end", iid=str(i), values=(uf_cidade, distancia, orgao, objeto, valor_str, abertura), tags=(tag,))

    def _get_selected_srv(self):
        sel = self.tree_servicos.selection()
        if not sel:
            import tkinter.messagebox as messagebox
            messagebox.showwarning("Aviso", "Selecione um serviço na tabela primeiro.")
            return None
        return self.dados_servicos[int(sel[0])]

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

class PainelProdutos(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        
        # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(10, 0))
        ctk.CTkLabel(header_frame, text="📦 Venda de Produtos", font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        
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
        
        ctk.CTkButton(btn_frame, text="🔍 Cruzar com Fornecedores", command=self.cruzar_fornecedores).grid(row=0, column=0, padx=10)
        ctk.CTkButton(btn_frame, text="📥 Importar Tabela de Distribuidor", command=self.importar_tabela_distribuidor).grid(row=0, column=1, padx=10)
        ctk.CTkButton(btn_frame, text="📄 Gerar Proposta Comercial", command=self.gerar_proposta_comercial).grid(row=0, column=2, padx=10)
        ctk.CTkButton(btn_frame, text="🛒 Cotar no Mercado Livre", command=self.cotar_mercado_livre).grid(row=0, column=3, padx=10)
        
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
                res = sb.table('licitacoes').select('*').eq('tipo_contratacao', 'PRODUTO').order('atualizado_em', desc=True).execute()
                self.dados_produtos = res.data
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

    def on_closing(self) -> None:
        self.destroy()
        import os
        os._exit(0)

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

        lbl_logo = ctk.CTkLabel(sidebar, text="🤖  ROBÔ LICITAÇÕES", font=ctk.CTkFont(size=16, weight="bold"), text_color=COR["accent_cyan"])
        lbl_logo.grid(row=0, column=0, padx=16, pady=(16, 20), sticky="w")

        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        abas = [
            ("📊  Dashboard", "dashboard"),
            ("🛠️  Serviços", "servicos"),
            ("📦  Venda de Produtos", "produtos"),
            ("📄  Editais & Custos", "editais"),
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

        # Painéis
        self.painel_dashboard = PainelDashboard(self.tab_container, app=self)
        self.painel_servicos = PainelServicos(self.tab_container, app=self)
        self.painel_produtos = PainelProdutos(self.tab_container, app=self)
        self.painel_editais = PainelEditais(self.tab_container, app=self)
        self.painel_sessao = PainelSessao(self.tab_container, app=self)
        self.painel_pncp = PainelPNCP(self.tab_container, app=self)

        self._paineis["📊  Dashboard"] = self.painel_dashboard
        self._paineis["🛠️  Serviços"] = self.painel_servicos
        self._paineis["📦  Venda de Produtos"] = self.painel_produtos
        self._paineis["📄  Editais & Custos"] = self.painel_editais
        self._paineis["⚡  Console da Sessão"] = self.painel_sessao

        for p in self._paineis.values():
            p.grid(row=0, column=0, sticky="nsew")

        self.selecionar_aba("📊  Dashboard")

    def selecionar_aba(self, nome_aba: str) -> None:
        for n, btn in self._nav_buttons.items():
            if n == nome_aba:
                btn.configure(fg_color=COR["accent_cyan"], text_color="#080C14")
            else:
                btn.configure(fg_color="transparent", text_color=COR["text_secondary"])

        if nome_aba in self._paineis:
            self._paineis[nome_aba].tkraise()

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
    except Exception:
        import tkinter.messagebox as messagebox
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning("Aviso", "O robô já está em execução!")
        root.destroy()
        import sys
        sys.exit(0)

    app = App()
    app.mainloop()


if __name__ == "__main__":
    run_gui()
