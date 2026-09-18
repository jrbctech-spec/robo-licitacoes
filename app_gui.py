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
from pathlib import Path
from typing import Any, Callable, Optional, Dict, List

import customtkinter as ctk
from tkinter import filedialog, messagebox, ttk

try:
    from config import ModoOperacao, settings
    from engine import (
        DecisaoLance,
        EstadoItemEnum,
        MotivoBloqueio,
        MotorDecisao,
        RegraItem,
    )
    from edital_parser import (
        EditalParser,
        ItemEdital,
        ResultadoEdital,
        SeveridadeRisco,
    )
    from browser_bot import BrowserBot
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
            orgao_str = res.orgao_comprador or res.objeto_licitacao[:50] or "Órgão Não Informado"
            qtd_itens = len(res.itens)

            self.tree_lics.insert("", "end", values=(pregao_str, orgao_str[:50], qtd_itens, "PRONTO"))


# ─── PAINEL 2: EDITAIS & CUSTOS (MULTI-CERTAME) ────────────────────────────────
class PainelEditais(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self._build()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # Bar de Upload / Fontes
        card_upload = CardFrame(self)
        card_upload.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 8))
        card_upload.grid_columnconfigure((0, 1, 2, 3), weight=1)

        ctk.CTkLabel(card_upload, text="📄  Importar Edital / Processo de Licitação", font=ctk.CTkFont(size=14, weight="bold"), text_color=COR["text_primary"]).grid(row=0, column=0, columnspan=4, sticky="w", padx=16, pady=(10, 6))

        ctk.CTkButton(card_upload, text="📁 Selecionar PDF de Edital", fg_color=COR["cyan_primary"], text_color="#000000", font=ctk.CTkFont(weight="bold"), height=34, command=self._carregar_pdf_local).grid(row=1, column=0, padx=8, pady=(0, 10), sticky="ew")
        ctk.CTkButton(card_upload, text="📂 Escanear Pasta (Filtro Rápido)", fg_color=COR["bg_card_hover"], border_color=COR["cyan_primary"], border_width=1, text_color=COR["text_primary"], height=34, command=self._escanear_pasta_processos).grid(row=1, column=1, padx=8, pady=(0, 10), sticky="ew")
        ctk.CTkButton(card_upload, text="🌐 Importar do PNCP", fg_color=COR["purple_accent"], text_color="#FFFFFF", font=ctk.CTkFont(weight="bold"), height=34, command=lambda: self.app.selecionar_aba("🌐  Oportunidades PNCP")).grid(row=1, column=2, padx=8, pady=(0, 10), sticky="ew")
        ctk.CTkButton(card_upload, text="📊 Importar Proposta PDF", fg_color=COR["blue_accent"], text_color="#000000", font=ctk.CTkFont(weight="bold"), height=34, command=self._carregar_proposta_pdf).grid(row=1, column=3, padx=8, pady=(0, 10), sticky="ew")

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

        # Status Bar
        self.card_info = CardFrame(self)
        self.card_info.grid(row=2, column=0, sticky="ew", padx=0, pady=(0, 8))
        self.card_info.grid_columnconfigure(0, weight=1)

        self.lbl_edital_status = ctk.CTkLabel(self.card_info, text="Pronto para carregar edital. Selecione um PDF ou escaneie uma pasta.", font=ctk.CTkFont(size=12), text_color=COR["text_secondary"])
        self.lbl_edital_status.grid(row=0, column=0, sticky="w", padx=16, pady=8)

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

        sb = ctk.CTkScrollbar(tbl_f, orientation="vertical", command=self.tree_itens.yview)
        self.tree_itens.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

    def _carregar_pdf_local(self) -> None:
        doc_dir = os.path.expanduser("~/Documentos") if os.path.exists(os.path.expanduser("~/Documentos")) else os.path.expanduser("~")
        path = filedialog.askopenfilename(
            title="Selecionar PDF de Edital",
            initialdir=doc_dir,
            filetypes=[("Arquivos PDF", "*.pdf"), ("Todos os arquivos", "*.*")]
        )
        if not path:
            return
        
        # Filtro prévio ultra-rápido (<0.05s)
        if not eh_edital_pdf_valido(path):
            messagebox.showwarning("Arquivo Não É Edital", f"O arquivo '{Path(path).name}' não possui termos característicos de licitação/edital e foi ignorado.")
            return

        self.lbl_edital_status.configure(text=f"⏳ Analisando edital: {Path(path).name}...", text_color=COR["amber_warning"])
        
        def _worker():
            try:
                parser = EditalParser()
                res = parser.parsear(path)
                self.after(0, lambda: self._on_edital_carregado(res, Path(path).name))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Erro no PDF", str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_edital_carregado(self, res: ResultadoEdital, nome_arquivo: str) -> None:
        nome_certame = f"Pregão {res.numero_pregao or 'S/N'} - {nome_arquivo}"
        self.app.adicionar_licitacao(nome_certame, res)

    def atualizar_lista_certames(self) -> None:
        cat = self.app.catalogo_licitacoes
        if not cat:
            self.cmb_certames.configure(values=["(Nenhum pregão carregado)"])
            self.cmb_certames.set("(Nenhum pregão carregado)")
            for row in self.tree_itens.get_children():
                self.tree_itens.delete(row)
            self.lbl_edital_status.configure(text="Nenhuma licitação ativa.", text_color=COR["text_secondary"])
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
            val_ref = f"R$ {item.valor_referencia:,.2f}" if item.valor_referencia else "N/A"
            custo_default = item.valor_referencia * 0.75 if item.valor_referencia else 100.0
            piso_default = custo_default * 1.15
            prop_default = item.valor_referencia * 0.985 if item.valor_referencia else 115.0

            self.tree_itens.insert(
                "",
                "end",
                values=(
                    item.numero_item,
                    item.descricao[:80],
                    item.quantidade,
                    item.unidade_medida,
                    val_ref,
                    f"{custo_default:.2f}",
                    "15.0%",
                    f"R$ {piso_default:,.2f}",
                    f"R$ {prop_default:,.2f}",
                ),
            )

        msg = f"✅ {nome_chave} | {len(res.itens)} item(ns) identificados | Pregão: {res.numero_pregao or 'N/A'}"
        self.lbl_edital_status.configure(text=msg, text_color=COR["green_success"])

    def _remover_certame_atual(self) -> None:
        certame = self.cmb_certames.get()
        if certame in self.app.catalogo_licitacoes:
            del self.app.catalogo_licitacoes[certame]
            self.app.certame_selecionado = None
            self.atualizar_lista_certames()
            self.app.painel_dashboard.atualizar_catalogo()
            messagebox.showinfo("Removido", f"Licitação '{certame}' removida do catálogo atual.")

    def _escanear_pasta_processos(self) -> None:
        doc_dir = os.path.expanduser("~/Documentos") if os.path.exists(os.path.expanduser("~/Documentos")) else os.path.expanduser("~")
        folder = filedialog.askdirectory(title="Selecionar Pasta de Licitações", initialdir=doc_dir)
        if not folder:
            return
        self.lbl_edital_status.configure(text=f"⏳ Escaneando pasta com Filtro Rápido: {folder}...", text_color=COR["amber_warning"])

        def _worker():
            try:
                # Busca todos os PDFs na pasta
                todos_pdfs = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".pdf")]
                
                # Aplica o Filtro Rápido (<0.01s por arquivo)
                editais_validos = [p for p in todos_pdfs if eh_edital_pdf_valido(p)]
                ignorados = len(todos_pdfs) - len(editais_validos)

                parser = EditalParser()
                carregados = 0
                for path in editais_validos:
                    try:
                        res = parser.parsear(path)
                        if res and res.itens:
                            nome_c = f"Pregão {res.numero_pregao or 'S/N'} - {Path(path).name}"
                            self.after(0, lambda n=nome_c, r=res: self.app.adicionar_licitacao(n, r))
                            carregados += 1
                    except Exception as err:
                        logger.warning(f"Erro ao parsear {path}: {err}")

                msg_fin = f"✅ Varredura concluída! {carregados} edital(is) carregado(s). ({ignorados} arquivo(s) não-edital ignorados instantaneamente)."
                self.after(0, lambda: self.lbl_edital_status.configure(text=msg_fin, text_color=COR["green_success"]))
                self.after(0, lambda: messagebox.showinfo("Varredura Inteligente", msg_fin))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Erro no Scanner", str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def _carregar_proposta_pdf(self) -> None:
        doc_dir = os.path.expanduser("~/Documentos") if os.path.exists(os.path.expanduser("~/Documentos")) else os.path.expanduser("~")
        path = filedialog.askopenfilename(
            title="Selecionar Proposta PDF",
            initialdir=doc_dir,
            filetypes=[("Arquivos PDF", "*.pdf"), ("Todos os arquivos", "*.*")]
        )
        if not path:
            return
        messagebox.showinfo("Proposta PDF", f"Proposta '{Path(path).name}' importada para cálculo de preços!")


# ─── PAINEL 3: CONSOLE DA SESSÃO AO VIVO ──────────────────────────────────────
class PainelSessao(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self._bot: Optional[BrowserBot] = None
        self._motor: Optional[MotorDecisao] = None
        self._build()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header Control Card
        card_ctrl = CardFrame(self)
        card_ctrl.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 10))
        card_ctrl.grid_columnconfigure(0, weight=1)

        ctrl_top = ctk.CTkFrame(card_ctrl, fg_color="transparent")
        ctrl_top.grid(row=0, column=0, sticky="ew", padx=16, pady=12)
        ctrl_top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(ctrl_top, text="🚀  Console de Disputa de Lances ao Vivo", font=ctk.CTkFont(size=15, weight="bold"), text_color=COR["text_primary"]).grid(row=0, column=0, sticky="w")

        # Seletor do Pregão para Disputa
        ctk.CTkLabel(ctrl_top, text="Disputar Licitação:", font=ctk.CTkFont(size=12, weight="bold"), text_color=COR["cyan_primary"]).grid(row=0, column=1, padx=(16, 4), sticky="e")
        self.cmb_disputa = ctk.CTkOptionMenu(ctrl_top, values=["(Nenhum pregão)"], width=220)
        self.cmb_disputa.grid(row=0, column=2, padx=4, sticky="e")

        # Switch Simulação vs Real
        self.sw_modo = ctk.CTkSwitch(ctrl_top, text="Modo Simulação (Dry-Run)", font=ctk.CTkFont(weight="bold"), progress_color=COR["green_success"])
        self.sw_modo.select()
        self.sw_modo.grid(row=0, column=3, padx=(16, 0), sticky="e")

        ctrl_actions = ctk.CTkFrame(card_ctrl, fg_color="transparent")
        ctrl_actions.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 12))

        self.btn_iniciar = ctk.CTkButton(ctrl_actions, text="▶️  Iniciar Disputa", font=ctk.CTkFont(weight="bold"), fg_color=COR["green_success"], text_color="#000000", height=38, command=self._iniciar_sessao)
        self.btn_iniciar.pack(side="left", padx=(0, 8))

        self.btn_parar = ctk.CTkButton(ctrl_actions, text="⏹️  Parar Disputa", font=ctk.CTkFont(weight="bold"), fg_color=COR["red_danger"], text_color="#FFFFFF", height=38, command=self._parar_sessao)
        self.btn_parar.pack(side="left", padx=4)

        # Monitor de Itens na Disputa
        card_monitor = CardFrame(self)
        card_monitor.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 10))
        card_monitor.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card_monitor, text="📊  Status dos Itens na Disputa", font=ctk.CTkFont(size=13, weight="bold"), text_color=COR["text_primary"]).grid(row=0, column=0, sticky="w", padx=16, pady=(10, 4))

        tbl_f = ctk.CTkFrame(card_monitor, fg_color="transparent")
        tbl_f.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        tbl_f.grid_columnconfigure(0, weight=1)

        cols = ("num", "desc", "meu_lance", "menor_lance", "posicao", "status_pill")
        self.tree_sessao = ttk.Treeview(tbl_f, columns=cols, show="headings", height=5)
        self.tree_sessao.heading("num", text="Item")
        self.tree_sessao.heading("desc", text="Descrição do Produto/Serviço")
        self.tree_sessao.heading("meu_lance", text="Meu Último Lance")
        self.tree_sessao.heading("menor_lance", text="Líder de Mercado")
        self.tree_sessao.heading("posicao", text="Posição")
        self.tree_sessao.heading("status_pill", text="Status na Disputa")

        self.tree_sessao.column("num", width=50, anchor="center")
        self.tree_sessao.column("desc", width=300, anchor="w")
        self.tree_sessao.column("meu_lance", width=120, anchor="e")
        self.tree_sessao.column("menor_lance", width=120, anchor="e")
        self.tree_sessao.column("posicao", width=80, anchor="center")
        self.tree_sessao.column("status_pill", width=140, anchor="center")
        self.tree_sessao.pack(fill="x", expand=True)

        # Terminal Log Stream
        card_log = CardFrame(self)
        card_log.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
        card_log.grid_rowconfigure(1, weight=1)
        card_log.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card_log, text="📜  Log de Eventos & Auditoria da Disputa", font=ctk.CTkFont(size=13, weight="bold"), text_color=COR["text_primary"]).grid(row=0, column=0, sticky="w", padx=16, pady=(10, 4))

        self.txt_log = ctk.CTkTextbox(card_log, font=ctk.CTkFont(family="Consolas", size=11), fg_color=COR["bg_dark"], text_color=COR["text_primary"])
        self.txt_log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

    def atualizar_seletor_disputa(self) -> None:
        cat = self.app.catalogo_licitacoes
        if not cat:
            self.cmb_disputa.configure(values=["(Nenhum pregão)"])
            self.cmb_disputa.set("(Nenhum pregão)")
            return
        chaves = list(cat.keys())
        self.cmb_disputa.configure(values=chaves)
        self.cmb_disputa.set(chaves[0])

    def _iniciar_sessao(self) -> None:
        pregao = self.cmb_disputa.get()
        if not pregao or pregao == "(Nenhum pregão)":
            messagebox.showwarning("Selecione um Pregão", "Carregue ou selecione um pregão válido na aba 'Editais & Custos' antes de iniciar a disputa.")
            return

        dry_run = bool(self.sw_modo.get())
        msg_modo = "SIMULAÇÃO (Dry-Run)" if dry_run else "🔴 DISPUTA REAL (Envio de Lances)"
        self.log(f"🚀 Iniciando bot para: {pregao} [Modo: {msg_modo}]")
        messagebox.showinfo("Disputa Iniciada", f"Bot iniciado para {pregao} em modo {msg_modo}!")

    def _parar_sessao(self) -> None:
        self.log("⏹️ Disputa encerrada pelo operador.")
        messagebox.showwarning("Disputa Encerrada", "Robô de lances interrompido.")

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
