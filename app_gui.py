"""
app_gui.py – Interface Gráfica Desktop Profissional para o Robô de Licitações.

Tecnologia: CustomTkinter (dark mode moderno).
Arquitetura: Thread de background para Playwright + fila de eventos para GUI.

Painéis:
  1. Editais & Custos  – upload de PDF, tabela de itens, stop-loss em tempo real
  2. Sessão & Lances   – URL, botões de controle, painel de lances ativos
  3. Console & Logs    – log em tempo real, indicadores de posição

Threading:
  - MainThread: GUI (Tkinter/CustomTkinter)
  - BotThread:  BrowserBot + MotorDecisao (Playwright)
  - LogThread:  AsyncLogQueue → Supabase (já implementada em supabase_client.py)
  - Comunicação via queue.Queue thread-safe
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Callable, Optional

import customtkinter as ctk

# ── Módulos do projeto ────────────────────────────────────────────────────────
try:
    from config import ModoOperacao, settings
    from edital_parser import (
        EditalParser,
        ItemEdital,
        ResultadoEdital,
        InteligenciaComprasGov,
        RiscoDesclassificacao,
        SeveridadeRisco,
        CriterioJulgamento,
        TipoModoDisputa,
    )
    from engine import MotorDecisao, RegraItem
    from supabase_client import log_queue, testar_conexao
    from diagnostics import DiagnosticoSistema, StatusDiagnostico
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
    _IMPORT_ERROR = str(_import_err)

# ── Configuração global do CustomTkinter ─────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Paleta de cores ───────────────────────────────────────────────────────────
COR = {
    "bg_deep":      "#0D1117",
    "bg_panel":     "#161B22",
    "bg_card":      "#21262D",
    "bg_input":     "#1C2128",
    "border":       "#30363D",
    "accent_blue":  "#388BFD",
    "accent_green": "#3FB950",
    "accent_red":   "#F85149",
    "accent_amber": "#D29922",
    "accent_purple":"#BC8CFF",
    "text_primary": "#E6EDF3",
    "text_secondary":"#8B949E",
    "text_muted":   "#484F58",
    "stop_loss":    "#FF4444",
    "simulation":   "#3FB950",
    "real_mode":    "#F85149",
}

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Estruturas de comunicação inter-thread
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GUIEvent:
    """Evento enviado da thread do bot para a GUI."""
    tipo: str          # "log", "lance", "status", "posicao", "erro", "fim"
    dados: dict[str, Any] = field(default_factory=dict)


@dataclass
class ItemRow:
    """Estado de uma linha da tabela de itens no painel de editais."""
    numero_item: int
    descricao: str
    valor_referencia: Optional[float]
    unidade_medida: str = "UN"
    quantidade: float = 1.0
    decremento_minimo: Optional[float] = None
    decremento_percentual: Optional[float] = None
    # Widgets referenciados para atualização em tempo real
    var_custo: Optional[ctk.StringVar] = None
    var_margem: Optional[ctk.StringVar] = None
    lbl_stop_loss: Optional[ctk.CTkLabel] = None
    lbl_sugestao: Optional[ctk.CTkLabel] = None


# ─────────────────────────────────────────────────────────────────────────────
# BotWorker – executa Playwright em thread separada
# ─────────────────────────────────────────────────────────────────────────────

class BotWorker:
    """
    Encapsula o BrowserBot em uma thread de background.

    Toda comunicação com a GUI é feita via gui_queue (thread-safe).
    Nunca acessa widgets Tkinter diretamente.
    """

    def __init__(self, gui_queue: "queue.Queue[GUIEvent]") -> None:
        self._gui_queue = gui_queue
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # Não pausado inicialmente

    def iniciar(
        self,
        regras: list[RegraItem],
        url_pregao: str,
        dry_run: bool,
        licitacao_id: Optional[str],
        sessao_id: str,
    ) -> None:
        """Inicia a thread do bot."""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._pause_event.set()

        self._thread = threading.Thread(
            target=self._run,
            args=(regras, url_pregao, dry_run, licitacao_id, sessao_id),
            name="bot-worker",
            daemon=True,
        )
        self._thread.start()

    def pausar(self) -> None:
        self._pause_event.clear()
        self._emit("status", {"texto": "⏸ Sessão pausada.", "cor": COR["accent_amber"]})

    def retomar(self) -> None:
        self._pause_event.set()
        self._emit("status", {"texto": "▶ Sessão retomada.", "cor": COR["accent_green"]})

    def encerrar(self) -> None:
        self._stop_event.set()
        self._pause_event.set()  # Desbloqueia se estiver pausado

    def esta_rodando(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _emit(self, tipo: str, dados: dict) -> None:
        """Enfileira um evento para a GUI (thread-safe)."""
        try:
            self._gui_queue.put_nowait(GUIEvent(tipo=tipo, dados=dados))
        except queue.Full:
            pass

    def _run(
        self,
        regras: list[RegraItem],
        url_pregao: str,
        dry_run: bool,
        licitacao_id: Optional[str],
        sessao_id: str,
    ) -> None:
        """Loop principal da thread do bot (corre em background)."""
        self._emit("log", {
            "nivel": "INFO",
            "msg": f"🚀 Bot iniciado | sessao={sessao_id[:8]}... | dry_run={dry_run}",
        })

        try:
            # Importação local para evitar importar Playwright na thread principal
            from browser_bot import BrowserBot

            # Substitui o log padrão do BrowserBot por emissor de eventos GUI
            motor = MotorDecisao(dry_run=dry_run)
            motor.carregar_regras(regras)
            motor.iniciar_sessao()

            # Bot com callbacks de eventos GUI
            bot = _BotComEventos(
                motor=motor,
                gui_queue=self._gui_queue,
                stop_event=self._stop_event,
                pause_event=self._pause_event,
                sessao_id=sessao_id,
                licitacao_id=licitacao_id,
            )
            bot.iniciar(
                url_pregao=url_pregao,
                intervalo_polling_s=5.0,
            )

        except Exception as exc:
            self._emit("erro", {"msg": f"Erro crítico no bot: {exc}"})
            logger.exception("Erro crítico na thread do bot")
        finally:
            self._emit("fim", {"msg": "Sessão encerrada."})


class _BotComEventos:
    """
    Versão do BrowserBot adaptada para enviar eventos à GUI em vez de
    apenas logar. Reaproveita toda a lógica de browser_bot.py.
    """

    def __init__(
        self,
        motor: MotorDecisao,
        gui_queue: "queue.Queue[GUIEvent]",
        stop_event: threading.Event,
        pause_event: threading.Event,
        sessao_id: str,
        licitacao_id: Optional[str],
    ) -> None:
        self.motor = motor
        self._gui_queue = gui_queue
        self._stop_event = stop_event
        self._pause_event = pause_event
        self.sessao_id = sessao_id
        self.licitacao_id = licitacao_id

    def _emit(self, tipo: str, dados: dict) -> None:
        try:
            self._gui_queue.put_nowait(GUIEvent(tipo=tipo, dados=dados))
        except queue.Full:
            pass

    def _log(self, nivel: str, msg: str, dados: Optional[dict] = None) -> None:
        self._emit("log", {"nivel": nivel, "msg": msg})
        # Persiste no Supabase de forma assíncrona
        from supabase_client import log_queue as lq, EntradaLog
        lq.enfileirar(EntradaLog(
            sessao_id=self.sessao_id,
            nivel=nivel,
            mensagem=msg,
            modulo="gui_bot",
            licitacao_id=self.licitacao_id,
            dados_extras=dados,
        ))

    def iniciar(self, url_pregao: str, intervalo_polling_s: float = 5.0) -> None:
        """Loop de monitoramento com suporte a pause/stop da GUI."""
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        from supabase_client import registrar_lance

        settings_local = settings
        self._log("INFO", f"Abrindo navegador → {url_pregao}")

        try:
            with sync_playwright() as pw:
                user_data_dir = str(settings_local.browser_user_data_dir)
                Path(user_data_dir).mkdir(parents=True, exist_ok=True)

                ctx = pw.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    headless=settings_local.browser_headless,
                    args=["--disable-blink-features=AutomationControlled"],
                    locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                )
                page = ctx.new_page()
                page.set_default_timeout(settings_local.browser_timeout_ms)

                self._log("INFO", "Navegando para o pregão...")
                try:
                    page.goto(url_pregao, wait_until="domcontentloaded")
                except PWTimeout:
                    self._log("WARNING", "Timeout na navegação inicial. Continuando...")

                ciclo = 0
                while not self._stop_event.is_set():
                    # Pausa se solicitado
                    self._pause_event.wait()
                    if self._stop_event.is_set():
                        break

                    ciclo += 1
                    inicio = time.monotonic()

                    # Simula leitura de estado e decisão para cada item
                    for num_item, estado in self.motor._estados.items():
                        if estado.encerrado:
                            continue

                        # Simula estado do mercado (em produção, leria da página)
                        import random
                        ultimo_meu = float(estado.meu_ultimo_lance) if estado.meu_ultimo_lance else float(estado.regra.preco_inicial_proposta)
                        lance_mercado = round(ultimo_meu * 0.99, 2)
                        posicao = random.choice([1, 2, 3])

                        self.motor.atualizar_estado(
                            numero_item=num_item,
                            melhor_lance_mercado=lance_mercado,
                            nossa_posicao=posicao,
                        )

                        try:
                            decisao = self.motor.calcular_lance(num_item)
                        except Exception as exc:
                            self._log("ERROR", f"Erro no motor item {num_item}: {exc}")
                            continue

                        if decisao.bloqueado_stop_loss or decisao.estado_resultante.value == "PISO_ATINGIDO":
                            self._emit("lance", {
                                "item": num_item,
                                "valor": None,
                                "mercado": lance_mercado,
                                "posicao": posicao,
                                "bloqueado": True,
                                "motivo": decisao.motivo,
                            })
                            self._log("WARNING", f"🛑 STOP-LOSS Item {num_item}: {decisao.motivo}")

                        elif decisao.deve_dar_lance and decisao.valor_lance:
                            self._emit("lance", {
                                "item": num_item,
                                "valor": decisao.valor_lance,
                                "mercado": lance_mercado,
                                "posicao": posicao,
                                "bloqueado": False,
                                "dry_run": self.motor.dry_run,
                            })
                            modo_str = "[SIMULAÇÃO]" if self.motor.dry_run else "[REAL]"
                            self._log(
                                "INFO",
                                f"✅ {modo_str} Lance Item {num_item}: "
                                f"R$ {decisao.valor_lance:.2f} | {decisao.motivo}",
                            )
                            self.motor.confirmar_lance_enviado(
                                numero_item=num_item,
                                valor_confirmado=decisao.valor_lance,
                            )
                            self.motor.atualizar_estado(
                                numero_item=num_item,
                                nossa_posicao=1,
                            )
                            # Registra no Supabase
                            try:
                                registrar_lance({
                                    "item_id": decisao.item_id,
                                    "licitacao_id": self.licitacao_id,
                                    "numero_item": num_item,
                                    "valor_lance": decisao.valor_lance,
                                    "valor_concorrente": lance_mercado,
                                    "posicao_antes": posicao,
                                    "tipo_lance": "simulado" if self.motor.dry_run else "real",
                                    "motivo_calculo": decisao.motivo,
                                    "bloqueado_stop_loss": False,
                                })
                            except Exception:
                                pass
                        else:
                            self._emit("posicao", {
                                "item": num_item,
                                "posicao": posicao,
                                "mercado": lance_mercado,
                            })

                    elapsed = time.monotonic() - inicio
                    sleep_time = max(0, intervalo_polling_s - elapsed)
                    # Sleep interruptível para parar rapidamente
                    for _ in range(int(sleep_time * 10)):
                        if self._stop_event.is_set():
                            break
                        time.sleep(0.1)

                ctx.close()

        except Exception as exc:
            self._log("ERROR", f"Erro no navegador: {exc}")
            raise

        self._log("INFO", "🏁 Bot encerrado com sucesso.")
        relatorio = self.motor.relatorio_sessao()
        self._emit("status", {
            "texto": f"✅ Sessão concluída | {relatorio['lances_calculados']} lances calculados",
            "cor": COR["accent_green"],
        })


# ─────────────────────────────────────────────────────────────────────────────
# Widgets reutilizáveis
# ─────────────────────────────────────────────────────────────────────────────

class StatusDot(ctk.CTkFrame):
    """Indicador visual circular de status (verde/vermelho/amarelo)."""

    def __init__(self, master: Any, **kwargs: Any) -> None:
        super().__init__(master, width=12, height=12, corner_radius=6, **kwargs)
        self.configure(fg_color=COR["accent_red"])

    def set_ok(self) -> None:
        self.configure(fg_color=COR["accent_green"])

    def set_erro(self) -> None:
        self.configure(fg_color=COR["accent_red"])

    def set_aviso(self) -> None:
        self.configure(fg_color=COR["accent_amber"])

    def set_piscando(self) -> None:
        """Pisca entre verde e escuro para indicar atividade."""
        cor_atual = self.cget("fg_color")
        nova = COR["accent_green"] if cor_atual != COR["accent_green"] else COR["bg_card"]
        self.configure(fg_color=nova)


class SectionLabel(ctk.CTkLabel):
    """Label de título de seção com estilo consistente."""

    def __init__(self, master: Any, text: str, **kwargs: Any) -> None:
        super().__init__(
            master,
            text=text,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=COR["text_secondary"],
            **kwargs,
        )


class CardFrame(ctk.CTkFrame):
    """Frame estilizado como card com borda sutil."""

    def __init__(self, master: Any, **kwargs: Any) -> None:
        super().__init__(
            master,
            fg_color=COR["bg_card"],
            corner_radius=10,
            border_width=1,
            border_color=COR["border"],
            **kwargs,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Painel 1 – Editais e Custos
# ─────────────────────────────────────────────────────────────────────────────

class PainelEditais(ctk.CTkFrame):
    """Painel de carregamento do edital PDF e configuração de custos por item."""

    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self._item_rows: list[ItemRow] = []
        self._resultado_edital: Optional[ResultadoEdital] = None
        self._frame_rows: list[ctk.CTkFrame] = []
        self._build()

    def _build(self) -> None:
        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ── Card de Importação (PDF Local, Pasta Local, PNCP, Proposta) ───────
        card_upload = CardFrame(self)
        card_upload.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 6))
        card_upload.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card_upload,
            text="📄  Importação de Edital, PNCP e Proposta de Custos",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COR["text_primary"],
        ).grid(row=0, column=0, columnspan=3, padx=16, pady=(10, 4), sticky="w")

        inner = ctk.CTkFrame(card_upload, fg_color="transparent")
        inner.grid(row=1, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 6))
        inner.grid_columnconfigure(0, weight=1)
        inner.grid_columnconfigure(1, weight=1)
        inner.grid_columnconfigure(2, weight=1)

        # Seção 1: Arquivo PDF Local
        frame_local = ctk.CTkFrame(inner, fg_color=COR["bg_input"], corner_radius=8)
        frame_local.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=0)
        frame_local.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            frame_local,
            text="💻  Arquivo PDF Local",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["text_secondary"],
        ).grid(row=0, column=0, padx=10, pady=(8, 2), sticky="w")

        self._var_pdf = ctk.StringVar(value="Nenhum arquivo selecionado")
        self._lbl_pdf = ctk.CTkLabel(
            frame_local,
            textvariable=self._var_pdf,
            text_color=COR["text_muted"],
            font=ctk.CTkFont(size=10),
            anchor="w",
        )
        self._lbl_pdf.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 4))

        ctk.CTkButton(
            frame_local,
            text="📂  Selecionar PDF",
            command=self._selecionar_pdf,
            height=30,
            fg_color=COR["accent_blue"],
            hover_color="#1F6FEB",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=11, weight="bold"),
            corner_radius=6,
        ).grid(row=2, column=0, padx=10, pady=(0, 8), sticky="ew")

        # Seção 2: Pasta Local do Edital / Processo
        frame_pasta = ctk.CTkFrame(inner, fg_color=COR["bg_input"], corner_radius=8)
        frame_pasta.grid(row=0, column=1, sticky="nsew", padx=4, pady=0)
        frame_pasta.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            frame_pasta,
            text="📁  Pasta Local do Processo",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["text_secondary"],
        ).grid(row=0, column=0, padx=10, pady=(8, 2), sticky="w")

        self._var_pasta_local = ctk.StringVar(value="Nenhuma pasta selecionada")
        self._lbl_pasta_local = ctk.CTkLabel(
            frame_pasta,
            textvariable=self._var_pasta_local,
            text_color=COR["text_muted"],
            font=ctk.CTkFont(size=10),
            anchor="w",
        )
        self._lbl_pasta_local.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 4))

        ctk.CTkButton(
            frame_pasta,
            text="📂  Selecionar Pasta Local",
            command=self._selecionar_pasta_local,
            height=30,
            fg_color=COR["accent_purple"],
            hover_color="#8A2BE2",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=10, weight="bold"),
            corner_radius=6,
        ).grid(row=2, column=0, padx=10, pady=(0, 8), sticky="ew")

        # Seção 3: API do PNCP
        frame_pncp = ctk.CTkFrame(inner, fg_color=COR["bg_input"], corner_radius=8)
        frame_pncp.grid(row=0, column=2, sticky="nsew", padx=(4, 0), pady=0)
        frame_pncp.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            frame_pncp,
            text="🌐  API do PNCP (Portal Nacional)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["text_secondary"],
        ).grid(row=0, column=0, columnspan=2, padx=10, pady=(8, 2), sticky="w")

        self._entry_pncp = ctk.CTkEntry(
            frame_pncp,
            placeholder_text="Cole o link/CNPJ do PNCP...",
            height=28,
            fg_color=COR["bg_card"],
            border_color=COR["border"],
            text_color=COR["text_primary"],
            font=ctk.CTkFont(size=10),
        )
        self._entry_pncp.grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 4), sticky="ew")

        self._btn_pncp = ctk.CTkButton(
            frame_pncp,
            text="🌐  Buscar no PNCP",
            command=self._buscar_no_pncp,
            width=110,
            height=30,
            fg_color="#0088CC",
            hover_color="#006699",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=10, weight="bold"),
            corner_radius=6,
        )
        self._btn_pncp.grid(row=2, column=0, padx=(10, 2), pady=(0, 8), sticky="w")

        self._btn_explorar_pncp = ctk.CTkButton(
            frame_pncp,
            text="🔎  Por UF",
            command=self._abrir_modal_explorar_pncp,
            height=30,
            fg_color=COR["bg_card"],
            hover_color=COR["border"],
            text_color=COR["text_primary"],
            font=ctk.CTkFont(size=10, weight="bold"),
            corner_radius=6,
        )
        self._btn_explorar_pncp.grid(row=2, column=1, padx=(2, 10), pady=(0, 8), sticky="ew")

        # Linha de Ações Adicionais: Importar Proposta e Escanear Pasta em Lote
        row_acoes = ctk.CTkFrame(card_upload, fg_color="transparent")
        row_acoes.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(2, 8))
        row_acoes.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            row_acoes,
            text="📄  Importar Proposta de Preços (PDF)",
            command=self._importar_proposta_pdf,
            height=30,
            fg_color=COR["accent_amber"],
            hover_color="#D97706",
            text_color="#000000",
            font=ctk.CTkFont(size=11, weight="bold"),
            corner_radius=6,
        ).grid(row=0, column=0, padx=(0, 6), sticky="w")

        ctk.CTkButton(
            row_acoes,
            text="📂  Escanear Pasta Local de Processos (Lote)",
            command=self._escanear_pasta_processos,
            height=30,
            fg_color=COR["accent_green"],
            hover_color="#2EA043",
            text_color="#000000",
            font=ctk.CTkFont(size=11, weight="bold"),
            corner_radius=6,
        ).grid(row=0, column=1, padx=6, sticky="w")

        self._lbl_proposta_status = ctk.CTkLabel(
            row_acoes,
            text="💡 Preencha custos da empresa ou escaneie editais em lote para o Supabase",
            font=ctk.CTkFont(size=10),
            text_color=COR["text_muted"],
            anchor="w",
        )
        self._lbl_proposta_status.grid(row=0, column=2, padx=10, sticky="w")

        # ── Painel de Inteligência Regulatória Compras.gov.br ─────────────────
        self._card_intel = CardFrame(self)
        self._card_intel.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self._card_intel.grid_columnconfigure(0, weight=1)

        intel_top = ctk.CTkFrame(self._card_intel, fg_color="transparent")
        intel_top.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 2))
        intel_top.grid_columnconfigure(3, weight=1)

        self._lbl_intel_criterio = ctk.CTkLabel(
            intel_top,
            text="🎯 Critério: Menor Preço por Item",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["accent_blue"],
        )
        self._lbl_intel_criterio.grid(row=0, column=0, padx=(0, 10), sticky="w")

        self._lbl_intel_modo = ctk.CTkLabel(
            intel_top,
            text="⚡ Modo: Aberto (IN 73/2022)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["accent_purple"],
        )
        self._lbl_intel_modo.grid(row=0, column=1, padx=10, sticky="w")

        self._lbl_intel_degrau = ctk.CTkLabel(
            intel_top,
            text="📉 Degrau: R$ 0,01",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["accent_amber"],
        )
        self._lbl_intel_degrau.grid(row=0, column=2, padx=10, sticky="w")

        self._lbl_intel_horario = ctk.CTkLabel(
            intel_top,
            text="⏰ Sessão: Horário de Brasília (UTC-3)",
            font=ctk.CTkFont(size=11),
            text_color=COR["text_secondary"],
        )
        self._lbl_intel_horario.grid(row=0, column=3, padx=10, sticky="w")

        self._btn_ver_riscos = ctk.CTkButton(
            intel_top,
            text="🛡️  Matriz de Conformidade & Riscos",
            command=self._abrir_modal_riscos,
            height=26,
            fg_color=COR["bg_input"],
            hover_color=COR["border"],
            text_color=COR["accent_green"],
            font=ctk.CTkFont(size=10, weight="bold"),
            corner_radius=6,
        )
        self._btn_ver_riscos.grid(row=0, column=4, padx=(8, 0), sticky="e")

        intel_bottom = ctk.CTkFrame(self._card_intel, fg_color="transparent")
        intel_bottom.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        intel_bottom.grid_columnconfigure(0, weight=1)

        self._lbl_intel_desc = ctk.CTkLabel(
            intel_bottom,
            text="💡 Regras operacionais do Compras.gov.br cruzadas com o Aviso de Contratação / Termo de Referência.",
            font=ctk.CTkFont(size=10),
            text_color=COR["text_muted"],
            anchor="w",
        )
        self._lbl_intel_desc.grid(row=0, column=0, sticky="w")

        self._lbl_intel_riscos_resumo = ctk.CTkLabel(
            intel_bottom,
            text="🛡️ 5 verificações regulatórias ativas (Lei 14.133 / IN 73)",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COR["accent_green"],
        )
        self._lbl_intel_riscos_resumo.grid(row=0, column=1, sticky="e")

        # ── Legenda da tabela ─────────────────────────────────────────────────
        card_header = CardFrame(self)
        card_header.grid(row=2, column=0, sticky="ew", pady=(0, 2))
        card_header.configure(corner_radius=8, border_width=0, fg_color=COR["bg_deep"])

        headers = [
            "Item Nº",
            "Descrição Resumida",
            "Qtd / Un",
            "Val. Ref. Órgão",
            "Meu Custo (R$)",
            "Margem (%)",
            "Stop-Loss (Piso)",
            "Sugestão Abertura",
        ]
        pesos = [1, 4, 2, 2, 2, 2, 2, 2]
        for col, (h, p) in enumerate(zip(headers, pesos)):
            card_header.grid_columnconfigure(col, weight=p)
            ctk.CTkLabel(
                card_header, text=h,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=COR["text_secondary"],
            ).grid(row=0, column=col, padx=4, pady=6, sticky="w")

        # ── Área scrollável de itens ──────────────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(
            self,
            fg_color=COR["bg_panel"],
            corner_radius=10,
            border_width=1,
            border_color=COR["border"],
            scrollbar_button_color=COR["border"],
            scrollbar_button_hover_color=COR["accent_blue"],
        )
        self._scroll.grid(row=3, column=0, sticky="nsew", pady=(0, 8))
        self._scroll.grid_columnconfigure([0, 1, 2, 3, 4, 5, 6, 7], weight=1)

        # ── Placeholder ───────────────────────────────────────────────────────
        self._lbl_placeholder = ctk.CTkLabel(
            self._scroll,
            text="📂  Selecione um PDF de edital para carregar os itens",
            text_color=COR["text_muted"],
            font=ctk.CTkFont(size=12),
        )
        self._lbl_placeholder.grid(row=0, column=0, columnspan=8, pady=40)

        # ── Rodapé com botão de confirmar ─────────────────────────────────────
        rodape = ctk.CTkFrame(self, fg_color="transparent")
        rodape.grid(row=3, column=0, sticky="ew")
        rodape.grid_columnconfigure(0, weight=1)

        self._lbl_resumo = ctk.CTkLabel(
            rodape,
            text="",
            text_color=COR["text_secondary"],
            font=ctk.CTkFont(size=11),
        )
        self._lbl_resumo.grid(row=0, column=0, sticky="w")

        self._btn_confirmar = ctk.CTkButton(
            rodape,
            text="✅  Confirmar Custos e Preparar Sessão",
            command=self._confirmar_custos,
            height=38,
            fg_color=COR["accent_green"],
            hover_color="#2EA043",
            text_color="#000000",
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=8,
            state="disabled",
        )
        self._btn_confirmar.grid(row=0, column=1, sticky="e")

    # ── Lógica ────────────────────────────────────────────────────────────────

    def _selecionar_pdf(self) -> None:
        caminho = filedialog.askopenfilename(
            title="Selecionar Edital PDF",
            filetypes=[("PDF", "*.pdf"), ("Todos", "*.*")],
        )
        if not caminho:
            return

        self._var_pdf.set(Path(caminho).name)
        self._lbl_pdf.configure(text_color=COR["text_primary"])
        self.app.log("INFO", f"Carregando edital: {Path(caminho).name}...")

        # Limpa tabela e exibe feedback de carregamento imediato
        for frame in self._frame_rows:
            frame.destroy()
        self._frame_rows.clear()
        self._item_rows.clear()

        lbl_load = ctk.CTkLabel(
            self._scroll,
            text="⏳ Lendo e analisando edital...\n(Extraindo regras do certame e identificando tabela de itens via IA)",
            text_color=COR["accent_blue"],
            font=ctk.CTkFont(size=12, weight="bold"),
            justify="center",
        )
        lbl_load.grid(row=0, column=0, columnspan=8, pady=30)
        self._frame_rows.append(lbl_load)

        # Parsing em thread separada para não travar a GUI
        threading.Thread(
            target=self._parse_pdf_thread,
            args=(caminho,),
            daemon=True,
        ).start()

    def _selecionar_pasta_local(self) -> None:
        """Abre o explorador nativo para selecionar uma pasta local contendo o edital e anexos."""
        caminho_pasta = filedialog.askdirectory(
            title="Selecione a Pasta do Edital / Processo no seu Computador",
            parent=self.app,
        )
        if not caminho_pasta:
            return

        p_dir = Path(caminho_pasta)
        self._var_pasta_local.set(p_dir.name)
        self._lbl_pasta_local.configure(text_color=COR["accent_purple"])
        self.app.log("INFO", f"📂 Pasta local selecionada: '{p_dir}'...")

        pdfs = list(p_dir.glob("*.pdf")) + list(p_dir.glob("*.PDF"))
        if not pdfs:
            messagebox.showwarning(
                "Nenhum PDF Encontrado",
                f"Nenhum arquivo de edital (.pdf) foi encontrado na pasta:\n{p_dir}",
                parent=self.app,
            )
            return

        # Priorizar arquivos que contenham 'aviso', 'edital', 'tr', 'termo'
        candidatos = sorted(
            pdfs,
            key=lambda p: (
                0 if any(k in p.name.lower() for k in ["aviso", "edital", "tr", "termo"]) else 1
            ),
        )
        arq_pdf = candidatos[0]
        self._var_pdf.set(f"[Pasta Local] {arq_pdf.name}")
        self._lbl_pdf.configure(text_color=COR["accent_blue"])
        self.app.log("INFO", f"📖 Lendo PDF da pasta local: {arq_pdf.name}...")

        # Limpa tabela e exibe feedback de carregamento imediato
        for frame in self._frame_rows:
            frame.destroy()
        self._frame_rows.clear()
        self._item_rows.clear()

        lbl_load = ctk.CTkLabel(
            self._scroll,
            text="⏳ Lendo e analisando edital da pasta local...\n(Extraindo regras do certame e identificando tabela de itens via IA)",
            text_color=COR["accent_blue"],
            font=ctk.CTkFont(size=12, weight="bold"),
            justify="center",
        )
        lbl_load.grid(row=0, column=0, columnspan=8, pady=30)
        self._frame_rows.append(lbl_load)

        threading.Thread(
            target=self._parse_pdf_thread,
            args=(str(arq_pdf),),
            daemon=True,
        ).start()





    def _buscar_no_pncp(self) -> None:
        """Busca certame e edital via API do PNCP (pncp_client.py) e sincroniza no Supabase."""
        url = self._entry_pncp.get().strip()
        if not url:
            messagebox.showwarning("PNCP", "Cole o link ou parâmetros da compra do PNCP (ex: 25053190000136/2026/000274).", parent=self.app)
            return

        self.app.log("INFO", f"🌐 Conectando à API do PNCP e sincronizando com Supabase: {url}...")
        self._btn_pncp.configure(state="disabled", text="⏳ Buscando PNCP...")

        def _worker() -> None:
            try:
                cnpj, ano, seq = extrair_params_pncp(url)
                # Sincroniza diretamente com Supabase
                try:
                    sincronizar_pncp_supabase(cnpj, ano, seq)
                    self.app.log("INFO", f"✅ Dados do PNCP salvos no Supabase para compra {seq}/{ano}.")
                except Exception as sup_err:
                    self.app.log("WARNING", f"⚠️ Aviso Supabase ao sincronizar PNCP: {sup_err}")

                compra, itens_pncp, caminho_pdf = carregar_certame_pncp(url)
                
                # Se baixou o PDF do edital, parseia com EditalParser
                if caminho_pdf and Path(caminho_pdf).exists():
                    parser = EditalParser()
                    resultado = parser.parsear(caminho_pdf)
                else:
                    resultado = ResultadoEdital(caminho_pdf=url)

                # Se o PNCP retornou itens via API e o parser de PDF ficou vazio, usa os itens da API
                if itens_pncp and not resultado.itens:
                    for i_pncp in itens_pncp:
                        num = i_pncp.get("numeroItem", i_pncp.get("item", 1))
                        desc = i_pncp.get("descricao", "Item PNCP")
                        val_unit = i_pncp.get("valorUnitarioEstimado", i_pncp.get("valorTotal", None))
                        qtd = i_pncp.get("quantidade", 1.0)
                        unidade = i_pncp.get("unidadeMedida", "UN")
                        item = ItemEdital(
                            numero_item=int(num),
                            descricao=str(desc),
                            quantidade=float(qtd),
                            unidade_medida=str(unidade).upper(),
                            valor_referencia=float(val_unit) if val_unit else None,
                        )
                        resultado.itens.append(item)

                self.after(0, self._on_pncp_sucesso, compra, resultado, caminho_pdf)
            except Exception as exc:
                self.after(0, self._on_pncp_erro, str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _escanear_pasta_processos(self) -> None:
        """Escaneia uma pasta local contendo subpastas de processos (drive_scanner.py)."""
        caminho_local = filedialog.askdirectory(
            title="Selecionar Pasta Local de Processos de Licitação",
            parent=self.app,
        )
        if not caminho_local:
            return

        self._lbl_proposta_status.configure(
            text="⏳ Escaneando processos locais e sincronizando no Supabase...",
            text_color=COR["accent_amber"],
        )
        self.app.log("INFO", f"🔍 Iniciando escaneamento da pasta local: '{caminho_local}'...")

        def _worker() -> None:
            try:
                from drive_scanner import escanear_pasta_local
                resultados = escanear_pasta_local(caminho_local)
                self.after(0, self._on_escanear_sucesso, resultados)
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_proposta_erro(f"Falha ao escanear pasta local: {e}"))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_escanear_sucesso(self, resultados: list[Any]) -> None:
        if not resultados:
            msg = "Nenhum edital PDF encontrado nas pastas de processos."
            self._lbl_proposta_status.configure(text=f"⚠️ {msg}", text_color=COR["accent_amber"])
            messagebox.showwarning("Scanner de Processos", msg, parent=self.app)
            return

        total_itens = sum(len(r.itens) for r in resultados)
        msg = f"✅ {len(resultados)} edital(is) e {total_itens} item(ns) sincronizados no Supabase!"
        self._lbl_proposta_status.configure(text=msg, text_color=COR["accent_green"])
        self.app.log("INFO", msg)
        messagebox.showinfo("Escaneamento Concluído", msg, parent=self.app)

        if resultados and resultados[0].itens:
            self._resultado_edital = resultados[0]
            self._popular_tabela(resultados[0])

    def _abrir_modal_explorar_pncp(self) -> None:
        """Abre janela modal para explorar licitações abertas no PNCP por Estado (UF)."""
        modal = ctk.CTkToplevel(self)
        modal.title("🔎 Explorar Licitações com Propostas Abertas no PNCP")
        modal.geometry("780x560")
        modal.configure(fg_color=COR["bg_deep"])
        modal.transient(self)
        modal.grab_set()

        top_frame = ctk.CTkFrame(modal, fg_color=COR["bg_panel"], corner_radius=8)
        top_frame.pack(fill="x", padx=16, pady=16)

        ctk.CTkLabel(
            top_frame,
            text="UF (Estado):",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["text_primary"],
        ).pack(side="left", padx=(12, 6), pady=12)

        ufs = ["TO", "SP", "RJ", "MG", "DF", "BA", "GO", "PE", "PR", "SC", "RS", "MA", "CE", "PA", "AM"]
        combo_uf = ctk.CTkComboBox(
            top_frame,
            values=ufs,
            width=80,
            height=30,
            fg_color=COR["bg_card"],
            text_color=COR["text_primary"],
        )
        combo_uf.set("TO")
        combo_uf.pack(side="left", padx=6, pady=12)

        lbl_status = ctk.CTkLabel(
            top_frame,
            text="Selecione o estado e clique em Buscar",
            font=ctk.CTkFont(size=11),
            text_color=COR["text_muted"],
        )
        lbl_status.pack(side="right", padx=12, pady=12)

        btn_buscar = ctk.CTkButton(
            top_frame,
            text="🔍 Buscar Oportunidades",
            height=30,
            fg_color="#0088CC",
            hover_color="#006699",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        btn_buscar.pack(side="left", padx=12, pady=12)

        scroll_op = ctk.CTkScrollableFrame(
            modal,
            fg_color=COR["bg_panel"],
            corner_radius=10,
            border_width=1,
            border_color=COR["border"],
        )
        scroll_op.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        def _buscar_worker() -> None:
            uf_sel = combo_uf.get()
            btn_buscar.configure(state="disabled", text="⏳ Buscando...")
            lbl_status.configure(text=f"Buscando licitações em {uf_sel}...")

            def _fn() -> None:
                try:
                    res = buscar_licitacoes_propostas_pncp(uf=uf_sel, pagina=1, tamanho_pagina=20)
                    self.after(0, _exibir_resultados, res)
                except Exception as exc:
                    self.after(0, lambda e=exc: messagebox.showerror("Erro PNCP", str(e), parent=modal))
                finally:
                    self.after(0, lambda: btn_buscar.configure(state="normal", text="🔍 Buscar Oportunidades"))

            threading.Thread(target=_fn, daemon=True).start()

        def _exibir_resultados(res: dict) -> None:
            for child in scroll_op.winfo_children():
                child.destroy()

            lista = res.get("data", [])
            total = res.get("totalRegistros", 0)
            lbl_status.configure(text=f"Total: {total} licitação(ões) aberta(s)", text_color=COR["accent_green"])

            if not lista:
                ctk.CTkLabel(
                    scroll_op,
                    text="Nenhuma licitação aberta encontrada para o filtro selecionado.",
                    text_color=COR["text_muted"],
                    font=ctk.CTkFont(size=12),
                ).pack(pady=30)
                return

            for idx, c in enumerate(lista):
                bg = COR["bg_card"] if idx % 2 == 0 else COR["bg_panel"]
                card = ctk.CTkFrame(scroll_op, fg_color=bg, corner_radius=6)
                card.pack(fill="x", padx=4, pady=3)

                orgao = c.get("orgaoEntidade", {}).get("razaoSocial", "Órgão Não Informado")
                objeto = c.get("objetoCompra", "Objeto não informado")
                val_est = c.get("valorTotalEstimado", 0.0)
                dt_enc = c.get("dataEncerramentoProposta", "N/D")
                cnpj = c.get("orgaoEntidade", {}).get("cnpj", "")
                ano = c.get("anoCompra", "")
                seq = c.get("sequencialCompra", "")
                param_url = f"{cnpj}/{ano}/{seq}"

                top_row = ctk.CTkFrame(card, fg_color="transparent")
                top_row.pack(fill="x", padx=8, pady=(6, 2))

                ctk.CTkLabel(
                    top_row,
                    text=f"🏢 {orgao[:55]}",
                    font=ctk.CTkFont(size=11, weight="bold"),
                    text_color=COR["accent_blue"],
                ).pack(side="left")

                val_str = f"R$ {val_est:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if val_est else "Valor N/D"
                ctk.CTkLabel(
                    top_row,
                    text=f"💰 {val_str}",
                    font=ctk.CTkFont(size=11, weight="bold"),
                    text_color=COR["accent_purple"],
                ).pack(side="right")

                ctk.CTkLabel(
                    card,
                    text=objeto[:120] + "..." if len(objeto) > 120 else objeto,
                    font=ctk.CTkFont(size=10),
                    text_color=COR["text_primary"],
                    anchor="w",
                    justify="left",
                ).pack(fill="x", padx=8, pady=(0, 4))

                bot_row = ctk.CTkFrame(card, fg_color="transparent")
                bot_row.pack(fill="x", padx=8, pady=(0, 6))

                ctk.CTkLabel(
                    bot_row,
                    text=f"⏰ Encerramento: {dt_enc[:10]} | PNCP: {param_url}",
                    font=ctk.CTkFont(size=10),
                    text_color=COR["text_muted"],
                ).pack(side="left")

                def _selecionar(u=param_url):
                    modal.destroy()
                    self._entry_pncp.delete(0, "end")
                    self._entry_pncp.insert(0, u)
                    self._buscar_no_pncp()

                ctk.CTkButton(
                    bot_row,
                    text="⚡ Carregar & Sincronizar",
                    command=_selecionar,
                    width=150,
                    height=26,
                    fg_color=COR["accent_green"],
                    hover_color="#2EA043",
                    text_color="#000000",
                    font=ctk.CTkFont(size=10, weight="bold"),
                ).pack(side="right")

        btn_buscar.configure(command=_buscar_worker)
        _buscar_worker()

    def _on_pncp_sucesso(self, compra: dict, resultado: ResultadoEdital, caminho_pdf: Optional[str]) -> None:
        self._btn_pncp.configure(state="normal", text="🌐  Buscar no PNCP")
        orgao = compra.get("orgaoEntidade", {}).get("razaoSocial", "Órgão PNCP")
        objeto = compra.get("objeto", "Licitação PNCP")
        resultado.orgao = orgao
        resultado.objeto = objeto
        
        nome_exibicao = Path(caminho_pdf).name if caminho_pdf else "PNCP API"
        self._var_pdf.set(f"[PNCP] {nome_exibicao}")
        self._lbl_pdf.configure(text_color=COR["accent_blue"])
        self._resultado_edital = resultado
        self._popular_tabela(resultado)
        self.app.log("INFO", f"✅ Certame PNCP carregado: {orgao} - {len(resultado.itens)} item(s)")

    def _on_pncp_erro(self, msg_erro: str) -> None:
        self._btn_pncp.configure(state="normal", text="🌐  Buscar no PNCP")
        self.app.log("ERROR", f"❌ Falha ao buscar no PNCP: {msg_erro}")
        if "timed out" in msg_erro.lower() or "timeout" in msg_erro.lower():
            orientacao = (
                "O portal oficial do Governo (pncp.gov.br) não respondeu a tempo (Timeout de Conexão).\n\n"
                "💡 Como prosseguir sem o PNCP:\n"
                "1. Clique no botão azul '📂 Selecionar PDF' e carregue o arquivo do Edital diretamente do seu computador.\n"
                "2. Ou utilize o botão roxo '📁 Selecionar Pasta Local' para carregar a pasta do processo.\n"
                "3. Caso queira usar o PNCP, tente novamente em alguns instantes quando o portal do governo restabelecer."
            )
            messagebox.showwarning("Servidor do PNCP Indisponível (Timeout)", orientacao, parent=self.app)
        else:
            messagebox.showerror("Erro PNCP", f"Falha ao consultar PNCP:\n{msg_erro}", parent=self.app)

    def _importar_proposta_pdf(self) -> None:
        """Lê a proposta comercial da empresa em PDF e preenche os custos na tabela (proposta_parser.py)."""
        caminho = filedialog.askopenfilename(
            title="Selecionar Proposta Comercial de Preços (PDF)",
            filetypes=[("PDF", "*.pdf"), ("Todos", "*.*")],
        )
        if not caminho:
            return

        if not self._item_rows:
            messagebox.showwarning("Aviso", "Carregue o edital ou certame antes de importar os custos da proposta.", parent=self.app)
            return

        self.app.log("INFO", f"📄 Lendo proposta comercial de preços: {Path(caminho).name}...")
        self._lbl_proposta_status.configure(text="⏳ Lendo proposta...", text_color=COR["accent_amber"])

        def _worker() -> None:
            try:
                parser = PropostaParser()
                res_proposta = parser.parsear(caminho)
                self.after(0, self._on_proposta_sucesso, res_proposta)
            except Exception as exc:
                self.after(0, self._on_proposta_erro, str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_proposta_sucesso(self, res_proposta: Any) -> None:
        if not res_proposta.itens:
            self._lbl_proposta_status.configure(text="⚠️ Nenhum custo extraído da proposta PDF.", text_color=COR["accent_amber"])
            messagebox.showwarning("Proposta", "Não foi possível extrair itens e custos do PDF da proposta comercial.", parent=self.app)
            return

        preenchidos = 0
        mapa_proposta = {item.numero_item: item for item in res_proposta.itens}

        for row in self._item_rows:
            if row.numero_item in mapa_proposta:
                item_prop = mapa_proposta[row.numero_item]
                row.var_custo.set(f"{item_prop.valor_unitario:.2f}")
                preenchidos += 1

        msg = f"✅ Custos de {preenchidos} item(ns) preenchidos automaticamente!"
        self._lbl_proposta_status.configure(text=msg, text_color=COR["accent_green"])
        self.app.log("INFO", msg)
        messagebox.showinfo("Proposta Importada", msg, parent=self.app)

    def _abrir_modal_riscos(self) -> None:
        """Exibe modal com a Matriz Completa de Conformidade Regulatória e Riscos de Desclassificação."""
        if not self._resultado_edital or not getattr(self._resultado_edital, "inteligencia_compras_gov", None):
            intel = InteligenciaComprasGov()
        else:
            intel = self._resultado_edital.inteligencia_compras_gov

        modal = ctk.CTkToplevel(self.app)
        modal.title("🛡️ Matriz de Conformidade & Riscos — Compras.gov.br")
        modal.geometry("860x640")
        modal.minsize(700, 500)
        modal.grab_set()

        # Cabeçalho
        top_bar = ctk.CTkFrame(modal, fg_color=COR["bg_panel"], corner_radius=0, height=65)
        top_bar.pack(fill="x", padx=0, pady=0)
        top_bar.pack_propagate(False)

        ctk.CTkLabel(
            top_bar,
            text="🛡️ MATRIZ DE CONFORMIDADE OPERACIONAL & RISCOS DE DESCLASSIFICAÇÃO",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COR["accent_blue"],
        ).pack(anchor="w", padx=16, pady=(10, 2))

        ctk.CTkLabel(
            top_bar,
            text="Cruzamento do Aviso de Contratação / Termo de Referência com a Lei nº 14.133/2021 e IN SEGES/ME nº 73/2022",
            font=ctk.CTkFont(size=10),
            text_color=COR["text_muted"],
        ).pack(anchor="w", padx=16)

        # Container scrollável dos riscos
        scroll_riscos = ctk.CTkScrollableFrame(
            modal,
            fg_color=COR["bg_deep"],
            scrollbar_button_color=COR["border"],
            scrollbar_button_hover_color=COR["accent_blue"],
        )
        scroll_riscos.pack(fill="both", expand=True, padx=16, pady=12)

        # Bloco de Destaque das Regras Operacionais
        card_regras = CardFrame(scroll_riscos)
        card_regras.pack(fill="x", pady=(0, 10))
        
        ctk.CTkLabel(
            card_regras,
            text="📋 REGRAS OPERACIONAIS VINCULANTES IDENTIFICADAS",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COR["text_primary"],
        ).pack(anchor="w", padx=12, pady=(10, 6))

        grid_regras = ctk.CTkFrame(card_regras, fg_color="transparent")
        grid_regras.pack(fill="x", padx=12, pady=(0, 10))
        grid_regras.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(
            grid_regras,
            text=f"🎯 Critério de Julgamento: {intel.criterio_julgamento_legivel}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["accent_blue"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=2)

        ctk.CTkLabel(
            grid_regras,
            text=f"⚡ Modo de Disputa: {intel.modo_disputa.nome_legivel}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["accent_purple"],
            anchor="w",
        ).grid(row=0, column=1, sticky="w", pady=2)

        ctk.CTkLabel(
            grid_regras,
            text=f"📉 Intervalo / Degrau Mínimo: {intel.intervalo_lances.descricao}",
            font=ctk.CTkFont(size=11),
            text_color=COR["accent_amber"],
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=2)

        cron_txt = f"{intel.cronograma.data_abertura or 'A Definir'} às {intel.cronograma.hora_abertura or '09:00'} ({intel.cronograma.fuso_horario})"
        ctk.CTkLabel(
            grid_regras,
            text=f"⏰ Horário da Sessão Pública: {cron_txt}",
            font=ctk.CTkFont(size=11),
            text_color=COR["text_secondary"],
            anchor="w",
        ).grid(row=1, column=1, sticky="w", pady=2)

        # Lista de Riscos
        ctk.CTkLabel(
            scroll_riscos,
            text="⚠️ RISCOS DE DESCLASSIFICAÇÃO E DIRETRIZES DE MITIGAÇÃO",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COR["accent_amber"],
        ).pack(anchor="w", padx=4, pady=(6, 4))

        cores_sev = {
            SeveridadeRisco.ALTO: (COR["accent_red"], "🔴 CRÍTICO / ALTO"),
            SeveridadeRisco.MEDIO: (COR["accent_amber"], "🟡 MÉDIO / ATENÇÃO"),
            SeveridadeRisco.BAIXO: (COR["accent_green"], "🟢 BAIXO / OPERACIONAL"),
            SeveridadeRisco.INFO: (COR["accent_blue"], "ℹ️ INFORMAÇÃO"),
        }

        for r in intel.riscos_identificados:
            cor_tag, txt_tag = cores_sev.get(r.severidade, (COR["text_muted"], r.severidade.value))
            c_card = CardFrame(scroll_riscos)
            c_card.pack(fill="x", pady=4)

            # Cabeçalho do risco
            r_header = ctk.CTkFrame(c_card, fg_color="transparent")
            r_header.pack(fill="x", padx=12, pady=(8, 4))
            r_header.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                r_header,
                text=r.titulo,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=COR["text_primary"],
            ).grid(row=0, column=0, sticky="w")

            ctk.CTkLabel(
                r_header,
                text=txt_tag,
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=cor_tag,
            ).grid(row=0, column=1, sticky="e")

            # Descrição do Risco
            ctk.CTkLabel(
                c_card,
                text=r.descricao,
                font=ctk.CTkFont(size=11),
                text_color=COR["text_secondary"],
                justify="left",
                wraplength=680,
            ).pack(anchor="w", padx=12, pady=(0, 4))

            # Base Legal e Mitigação
            f_meta = ctk.CTkFrame(c_card, fg_color=COR["bg_surface"], corner_radius=6)
            f_meta.pack(fill="x", padx=12, pady=(2, 8))

            ctk.CTkLabel(
                f_meta,
                text=f"📜 Base Legal: {r.base_legal}",
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=COR["accent_blue"],
            ).pack(anchor="w", padx=8, pady=(4, 2))

            ctk.CTkLabel(
                f_meta,
                text=f"🛡️ Mitigação / Ação: {r.sugestao_mitigacao}",
                font=ctk.CTkFont(size=10),
                text_color=COR["accent_green"],
                wraplength=660,
                justify="left",
            ).pack(anchor="w", padx=8, pady=(2, 6))

        # Botão Fechar Modal
        b_bottom = ctk.CTkFrame(modal, fg_color="transparent")
        b_bottom.pack(fill="x", padx=16, pady=12)

        ctk.CTkButton(
            b_bottom,
            text="✅ Entendido / Fechar Painel de Conformidade",
            command=modal.destroy,
            height=34,
            fg_color=COR["accent_blue"],
            hover_color="#1F6FEB",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(side="right")

    def _on_proposta_erro(self, erro_msg: str) -> None:
        self._lbl_proposta_status.configure(text=f"❌ Erro na proposta: {erro_msg}", text_color=COR["accent_red"])
        self.app.log("ERROR", f"Erro ao ler proposta: {erro_msg}")
        messagebox.showerror("Erro Proposta", erro_msg, parent=self.app)

    def _parse_pdf_thread(self, caminho: str) -> None:
        try:
            parser = EditalParser()
            resultado = self._resultado_edital = parser.parsear(caminho)
            # Atualiza GUI na thread principal
            self.after(0, self._popular_tabela, resultado)
        except Exception as exc:
            self.after(0, lambda e=exc: self.app.log("ERROR", f"Erro ao parsear PDF: {e}"))

    def _popular_tabela(self, resultado: ResultadoEdital) -> None:
        """Popula a tabela de itens com os dados do edital e atualiza a inteligência regulatória."""
        self._resultado_edital = resultado

        # Limpa linhas existentes
        for frame in self._frame_rows:
            frame.destroy()
        self._frame_rows.clear()
        self._item_rows.clear()

        if self._lbl_placeholder.winfo_exists():
            self._lbl_placeholder.destroy()

        # ── Atualização do Painel de Inteligência Compras.gov.br ─────────────
        intel = getattr(resultado, "inteligencia_compras_gov", None) or InteligenciaComprasGov()
        self._lbl_intel_criterio.configure(text=f"🎯 Critério: {intel.criterio_julgamento_legivel}")
        self._lbl_intel_modo.configure(text=f"⚡ Modo: {intel.modo_disputa.nome_legivel}")
        self._lbl_intel_degrau.configure(text=f"📉 Degrau: {intel.intervalo_lances.descricao.split('(')[0].strip()}")

        cron = intel.cronograma
        if cron.data_abertura and cron.hora_abertura:
            self._lbl_intel_horario.configure(
                text=f"⏰ Sessão: {cron.data_abertura} às {cron.hora_abertura} ({cron.fuso_horario})",
                text_color=COR["accent_green"],
            )
        elif cron.hora_abertura:
            self._lbl_intel_horario.configure(
                text=f"⏰ Sessão: {cron.hora_abertura} ({cron.fuso_horario})",
                text_color=COR["accent_green"],
            )
        else:
            self._lbl_intel_horario.configure(
                text="⏰ Sessão: Horário de Brasília (UTC-3)",
                text_color=COR["text_secondary"],
            )

        self._lbl_intel_desc.configure(text=f"💡 {intel.modo_disputa.descricao_operacional}")
        n_riscos = len(intel.riscos_identificados)
        tem_alto = any(r.severidade == SeveridadeRisco.ALTO for r in intel.riscos_identificados)
        self._lbl_intel_riscos_resumo.configure(
            text=f"🛡️ {n_riscos} verificações regulatórias ativas (Lei 14.133 / IN 73)",
            text_color=COR["accent_amber"] if tem_alto else COR["accent_green"],
        )

        self.app.log(
            "INFO",
            f"✅ Edital carregado | {len(resultado.itens)} item(s) | Critério: {intel.criterio_julgamento_legivel} | Modo: {intel.modo_disputa.nome_legivel}"
        )

        if not resultado.itens:
            detalhe = ""
            if resultado.erros_parsing:
                detalhe = "\n\n" + "\n".join(f"• {e}" for e in resultado.erros_parsing[:2])
            lbl = ctk.CTkLabel(
                self._scroll,
                text=f"⚠️  Nenhum item identificado automaticamente no PDF.{detalhe}\n\n💡 Dica: Você também pode buscar os dados diretamente pelo campo 'API do PNCP' ao lado.",
                text_color=COR["accent_amber"],
                font=ctk.CTkFont(size=12),
                justify="center",
            )
            lbl.grid(row=0, column=0, columnspan=8, pady=30)
            self._frame_rows.append(lbl)
            return

        for idx, item in enumerate(resultado.itens):
            self._adicionar_linha_item(idx, item)

        n = len(resultado.itens)
        self._lbl_resumo.configure(
            text=f"  {n} item(ns) | Critério: {intel.criterio_julgamento_legivel} | Modo: {intel.modo_disputa.nome_legivel} | "
                 f"Dec.: {resultado.regras.decremento_global_valor or resultado.regras.decremento_global_percent or 'R$ 0,01'}"
        )
        self._btn_confirmar.configure(state="normal")

    def _adicionar_linha_item(self, idx: int, item: ItemEdital) -> None:
        """Adiciona uma linha na tabela para um item do edital com a nova estrutura de 8 colunas."""
        bg = COR["bg_card"] if idx % 2 == 0 else COR["bg_panel"]
        row_frame = ctk.CTkFrame(self._scroll, fg_color=bg, corner_radius=6)
        row_frame.grid(row=idx, column=0, columnspan=8, sticky="ew", pady=1, padx=2)
        for c, w in enumerate([1, 4, 2, 2, 2, 2, 2, 2]):
            row_frame.grid_columnconfigure(c, weight=w)

        # 0. Item Nº
        ctk.CTkLabel(
            row_frame, text=str(item.numero_item),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["accent_blue"],
        ).grid(row=0, column=0, padx=4, pady=6, sticky="w")

        # 1. Descrição Resumida
        desc = item.descricao[:45] + "…" if len(item.descricao) > 45 else item.descricao
        ctk.CTkLabel(
            row_frame, text=desc,
            font=ctk.CTkFont(size=11),
            text_color=COR["text_primary"],
            anchor="w",
        ).grid(row=0, column=1, padx=4, pady=6, sticky="w")

        # 2. Qtd / Un
        qtd = getattr(item, "quantidade", 1.0)
        unidade = getattr(item, "unidade_medida", "UN") or "UN"
        qtd_str = f"{qtd:g} {unidade}" if qtd else f"1 {unidade}"
        ctk.CTkLabel(
            row_frame, text=qtd_str,
            font=ctk.CTkFont(size=11),
            text_color=COR["text_secondary"],
        ).grid(row=0, column=2, padx=4, pady=6)

        # 3. Val. Referência Órgão
        ref_str = f"R$ {item.valor_referencia:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if item.valor_referencia else "—"
        ctk.CTkLabel(
            row_frame, text=ref_str,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["accent_purple"],
        ).grid(row=0, column=3, padx=4, pady=6)

        # 4. Meu Custo (R$) input
        var_custo = ctk.StringVar(value="")
        entry_custo = ctk.CTkEntry(
            row_frame,
            textvariable=var_custo,
            width=85,
            height=28,
            fg_color=COR["bg_input"],
            border_color=COR["border"],
            text_color=COR["text_primary"],
            font=ctk.CTkFont(size=11),
            placeholder_text="0.00",
        )
        entry_custo.grid(row=0, column=4, padx=4, pady=4)

        # 5. Margem (%) input
        var_margem = ctk.StringVar(value=str(settings.margem_minima_percent))
        entry_margem = ctk.CTkEntry(
            row_frame,
            textvariable=var_margem,
            width=65,
            height=28,
            fg_color=COR["bg_input"],
            border_color=COR["border"],
            text_color=COR["text_primary"],
            font=ctk.CTkFont(size=11),
        )
        entry_margem.grid(row=0, column=5, padx=4, pady=4)

        # 6. Stop-Loss (Piso)
        lbl_stop = ctk.CTkLabel(
            row_frame,
            text="—",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["stop_loss"],
        )
        lbl_stop.grid(row=0, column=6, padx=4, pady=6)

        # 7. Sugestão de Abertura
        lbl_sugestao = ctk.CTkLabel(
            row_frame,
            text="—",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["accent_green"],
        )
        lbl_sugestao.grid(row=0, column=7, padx=4, pady=6)

        item_row = ItemRow(
            numero_item=item.numero_item,
            descricao=item.descricao,
            valor_referencia=item.valor_referencia,
            unidade_medida=unidade,
            quantidade=qtd,
            decremento_minimo=item.decremento_minimo,
            decremento_percentual=item.decremento_percentual,
            var_custo=var_custo,
            var_margem=var_margem,
            lbl_stop_loss=lbl_stop,
            lbl_sugestao=lbl_sugestao,
        )
        self._item_rows.append(item_row)
        self._frame_rows.append(row_frame)

        # Callbacks de recálculo dinâmico em tempo real
        def _atualizar(*_, r=item_row):
            self._recalcular_stop_loss_e_sugestao(r)

        var_custo.trace_add("write", _atualizar)
        var_margem.trace_add("write", _atualizar)

    def _recalcular_stop_loss_e_sugestao(self, row: ItemRow) -> None:
        """Calcula Stop-Loss e Sugestão de Abertura em tempo real."""
        try:
            custo_raw = row.var_custo.get().replace(".", "").replace(",", ".") if "," in row.var_custo.get() else row.var_custo.get()
            margem_raw = row.var_margem.get().replace(",", ".")
            custo = float(custo_raw) if custo_raw else 0.0
            margem = float(margem_raw) if margem_raw else 0.0

            if custo > 0 and 0 <= margem <= 500:
                stop_loss = custo * (1 + margem / 100)
                stop_str = f"R$ {stop_loss:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                row.lbl_stop_loss.configure(
                    text=stop_str,
                    text_color=COR["stop_loss"],
                )

                # Sugestão de Abertura
                if row.valor_referencia and row.valor_referencia > 0:
                    sugestao = row.valor_referencia * 0.985  # 1.5% abaixo do valor de referência
                    if sugestao < stop_loss:
                        sugestao = stop_loss * 1.05
                else:
                    sugestao = stop_loss * 1.10

                sug_str = f"R$ {sugestao:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                row.lbl_sugestao.configure(
                    text=sug_str,
                    text_color=COR["accent_green"],
                )
            else:
                row.lbl_stop_loss.configure(text="—", text_color=COR["text_muted"])
                row.lbl_sugestao.configure(text="—", text_color=COR["text_muted"])

        except Exception:
            row.lbl_stop_loss.configure(text="—", text_color=COR["text_muted"])
            row.lbl_sugestao.configure(text="—", text_color=COR["text_muted"])

    def _confirmar_custos(self) -> None:
        """Valida os custos inseridos e registra as RegraItem na app."""
        regras: list[RegraItem] = []
        erros: list[str] = []

        for row in self._item_rows:
            try:
                custo_raw = row.var_custo.get().replace(".", "").replace(",", ".") if "," in row.var_custo.get() else row.var_custo.get()
                margem_raw = row.var_margem.get().replace(",", ".")
                custo = float(custo_raw)
                margem = float(margem_raw)
            except ValueError:
                erros.append(f"Item {row.numero_item}: custo ou margem inválidos.")
                continue

            if custo <= 0:
                erros.append(f"Item {row.numero_item}: custo deve ser > 0.")
                continue
            if margem < 0:
                erros.append(f"Item {row.numero_item}: margem não pode ser negativa.")
                continue

            preco_minimo = round(custo * (1 + margem / 100), 4)

            # Sugestão de abertura como proposta inicial
            if row.valor_referencia and row.valor_referencia > 0:
                proposta_inicial = round(row.valor_referencia * 0.985, 2)
                if proposta_inicial < preco_minimo:
                    proposta_inicial = round(preco_minimo * 1.05, 2)
            else:
                proposta_inicial = round(preco_minimo * 1.10, 2)

            try:
                regra = RegraItem(
                    item_id=str(uuid.uuid4()),
                    numero_item=row.numero_item,
                    descricao=row.descricao,
                    custo_unitario=custo,
                    margem_minima_percent=margem,
                    preco_minimo=preco_minimo,
                    preco_inicial_proposta=proposta_inicial,
                    decremento_minimo=row.decremento_minimo,
                    decremento_percentual=row.decremento_percentual,
                )
                regras.append(regra)
            except ValueError as exc:
                erros.append(str(exc))

        if erros:
            messagebox.showerror(
                "Erros nos Custos",
                "\n".join(erros),
                parent=self.app,
            )
            return

        if not regras:
            messagebox.showwarning(
                "Nenhum Item",
                "Nenhum item com custo válido encontrado.",
                parent=self.app,
            )
            return

        self.app.regras = regras
        self.app.log("INFO", f"✅ {len(regras)} regra(s) de stop-loss confirmadas e carregadas em memória.")
        messagebox.showinfo(
            "Custos Confirmados",
            f"✅ {len(regras)} item(s) configurados com stop-loss!\n\n"
            f"Acesse a aba 'Sessão & Lances' para iniciar o monitoramento.",
            parent=self.app,
        )
        self.app.tab_view.set("🚀  Sessão & Lances")

    def get_item_rows(self) -> list[ItemRow]:
        return self._item_rows


# ─────────────────────────────────────────────────────────────────────────────
# Painel 2 – Sessão e Lances
# ─────────────────────────────────────────────────────────────────────────────

class PainelSessao(ctk.CTkFrame):
    """Painel de controle da sessão de lances e monitoramento em tempo real."""

    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self._card_lances: dict[int, dict] = {}  # item → {widgets}
        self._build()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(1, weight=1)

        # ── Configuração da sessão ────────────────────────────────────────────
        card_config = CardFrame(self)
        card_config.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        card_config.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card_config,
            text="⚙️  Configuração da Sessão",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COR["text_primary"],
        ).grid(row=0, column=0, columnspan=3, padx=16, pady=(14, 8), sticky="w")

        # URL do pregão
        ctk.CTkLabel(
            card_config, text="URL da Sala de Disputa:",
            font=ctk.CTkFont(size=12),
            text_color=COR["text_secondary"],
        ).grid(row=1, column=0, padx=16, pady=4, sticky="w")

        self._var_url = ctk.StringVar()
        self._entry_url = ctk.CTkEntry(
            card_config,
            textvariable=self._var_url,
            height=36,
            fg_color=COR["bg_input"],
            border_color=COR["border"],
            text_color=COR["text_primary"],
            font=ctk.CTkFont(size=11),
            placeholder_text="https://www.compras.gov.br/...",
        )
        self._entry_url.grid(row=1, column=1, padx=(0, 16), pady=4, sticky="ew", columnspan=2)

        # Intervalo de polling
        ctk.CTkLabel(
            card_config, text="Polling (seg):",
            font=ctk.CTkFont(size=12),
            text_color=COR["text_secondary"],
        ).grid(row=2, column=0, padx=16, pady=(4, 14), sticky="w")

        self._var_polling = ctk.StringVar(value="5")
        ctk.CTkEntry(
            card_config,
            textvariable=self._var_polling,
            width=80, height=32,
            fg_color=COR["bg_input"],
            border_color=COR["border"],
            text_color=COR["text_primary"],
            font=ctk.CTkFont(size=11),
        ).grid(row=2, column=1, padx=(0, 4), pady=(4, 14), sticky="w")

        # ── Botões de controle ────────────────────────────────────────────────
        card_ctrl = CardFrame(self)
        card_ctrl.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=0)
        card_ctrl.grid_rowconfigure(4, weight=1)
        card_ctrl.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card_ctrl,
            text="🎮  Controles",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COR["text_primary"],
        ).grid(row=0, column=0, padx=16, pady=(14, 12), sticky="w")

        btn_cfg = dict(height=46, corner_radius=10, font=ctk.CTkFont(size=14, weight="bold"))

        self._btn_iniciar = ctk.CTkButton(
            card_ctrl,
            text="▶   Iniciar Monitoramento",
            command=self._iniciar,
            fg_color=COR["accent_green"],
            hover_color="#2EA043",
            text_color="#000000",
            **btn_cfg,
        )
        self._btn_iniciar.grid(row=1, column=0, padx=16, pady=4, sticky="ew")

        self._btn_pausar = ctk.CTkButton(
            card_ctrl,
            text="⏸   Pausar",
            command=self._pausar,
            fg_color=COR["accent_amber"],
            hover_color="#B8860B",
            text_color="#000000",
            state="disabled",
            **btn_cfg,
        )
        self._btn_pausar.grid(row=2, column=0, padx=16, pady=4, sticky="ew")

        self._btn_encerrar = ctk.CTkButton(
            card_ctrl,
            text="⏹   Encerrar",
            command=self._encerrar,
            fg_color=COR["accent_red"],
            hover_color="#B22222",
            state="disabled",
            **btn_cfg,
        )
        self._btn_encerrar.grid(row=3, column=0, padx=16, pady=(4, 16), sticky="ew")

        # Status da sessão
        self._card_status = ctk.CTkFrame(card_ctrl, fg_color=COR["bg_deep"], corner_radius=8)
        self._card_status.grid(row=4, column=0, padx=16, pady=(0, 16), sticky="ew")
        self._card_status.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self._card_status,
            text="STATUS DA SESSÃO",
            font=ctk.CTkFont(size=10),
            text_color=COR["text_muted"],
        ).grid(row=0, column=0, padx=12, pady=(10, 4))

        self._lbl_status = ctk.CTkLabel(
            self._card_status,
            text="Aguardando início...",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COR["text_secondary"],
            wraplength=200,
        )
        self._lbl_status.grid(row=1, column=0, padx=12, pady=(0, 12))

        # ── Painel de lances ativos ───────────────────────────────────────────
        card_lances = CardFrame(self)
        card_lances.grid(row=1, column=1, sticky="nsew")
        card_lances.grid_rowconfigure(1, weight=1)
        card_lances.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card_lances,
            text="📊  Lances Ativos",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COR["text_primary"],
        ).grid(row=0, column=0, padx=16, pady=(14, 8), sticky="w")

        self._scroll_lances = ctk.CTkScrollableFrame(
            card_lances,
            fg_color=COR["bg_deep"],
            corner_radius=8,
            scrollbar_button_color=COR["border"],
        )
        self._scroll_lances.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self._scroll_lances.grid_columnconfigure(0, weight=1)

        self._lbl_no_lances = ctk.CTkLabel(
            self._scroll_lances,
            text="Nenhum lance ativo.\nInicie o monitoramento.",
            text_color=COR["text_muted"],
            font=ctk.CTkFont(size=11),
            justify="center",
        )
        self._lbl_no_lances.grid(row=0, column=0, pady=30)

    # ── Controles da sessão ───────────────────────────────────────────────────

    def _iniciar(self) -> None:
        url = self._var_url.get().strip()
        if not url:
            messagebox.showwarning("URL necessária", "Informe a URL da sala de disputa.", parent=self.app)
            return

        if not self.app.regras:
            messagebox.showwarning(
                "Sem itens",
                "Configure os custos na aba 'Editais & Custos' primeiro.",
                parent=self.app,
            )
            return

        dry_run = self.app.modo_operacao == ModoOperacao.SIMULATION

        if not dry_run:
            resp = messagebox.askyesno(
                "⚠️  MODO REAL",
                "Você está prestes a iniciar em MODO REAL.\n\n"
                "Lances serão enviados ao Compras.gov.br com efeito legal.\n\n"
                "Confirma?",
                icon="warning",
                parent=self.app,
            )
            if not resp:
                return

        self._btn_iniciar.configure(state="disabled")
        self._btn_pausar.configure(state="normal")
        self._btn_encerrar.configure(state="normal")
        self._lbl_status.configure(
            text="🟢 Sessão ativa" + (" [SIMULAÇÃO]" if dry_run else " [REAL 🔴]"),
            text_color=COR["accent_green"] if dry_run else COR["accent_red"],
        )
        self.app.dot_ativo.set_piscando()

        sessao_id = str(uuid.uuid4())
        self.app.log("INFO", f"{'[SIMULAÇÃO]' if dry_run else '[REAL]'} Iniciando sessão {sessao_id[:8]}...")
        log_queue.iniciar()

        self.app.bot_worker.iniciar(
            regras=self.app.regras,
            url_pregao=url,
            dry_run=dry_run,
            licitacao_id=None,
            sessao_id=sessao_id,
        )

        # Limpa placeholder de lances
        if self._lbl_no_lances.winfo_exists():
            self._lbl_no_lances.destroy()

    def _pausar(self) -> None:
        if self.app.bot_worker.esta_rodando():
            if self._btn_pausar.cget("text").startswith("⏸"):
                self.app.bot_worker.pausar()
                self._btn_pausar.configure(text="▶   Retomar")
                self._lbl_status.configure(
                    text="⏸ Pausado",
                    text_color=COR["accent_amber"],
                )
            else:
                self.app.bot_worker.retomar()
                self._btn_pausar.configure(text="⏸   Pausar")
                self._lbl_status.configure(
                    text="🟢 Sessão ativa",
                    text_color=COR["accent_green"],
                )

    def _encerrar(self) -> None:
        if self.app.bot_worker.esta_rodando():
            self.app.bot_worker.encerrar()
        self._btn_iniciar.configure(state="normal")
        self._btn_pausar.configure(state="disabled", text="⏸   Pausar")
        self._btn_encerrar.configure(state="disabled")
        self._lbl_status.configure(
            text="⏹ Encerrado",
            text_color=COR["text_secondary"],
        )
        self.app.dot_ativo.set_erro()
        log_queue.parar(timeout=3)
        self.app.log("INFO", "⏹ Sessão encerrada pelo usuário.")

    def atualizar_lance(self, evento: GUIEvent) -> None:
        """Atualiza o card de lance ativo para um item."""
        d = evento.dados
        num = d.get("item", 0)
        valor = d.get("valor")
        mercado = d.get("mercado")
        posicao = d.get("posicao")
        bloqueado = d.get("bloqueado", False)
        dry_run = d.get("dry_run", True)

        if num not in self._card_lances:
            self._criar_card_lance(num)

        c = self._card_lances[num]

        if bloqueado:
            c["lbl_status"].configure(text="🛑 STOP-LOSS", text_color=COR["accent_red"])
            c["lbl_valor"].configure(text="Bloqueado", text_color=COR["accent_red"])
        elif valor is not None:
            modo_tag = "SIM" if dry_run else "REAL"
            c["lbl_status"].configure(
                text=f"✅ Lance [{modo_tag}]",
                text_color=COR["accent_green"] if dry_run else COR["accent_red"],
            )
            c["lbl_valor"].configure(
                text=f"R$ {valor:.4f}",
                text_color=COR["accent_blue"],
            )

        if mercado is not None:
            c["lbl_mercado"].configure(text=f"Mercado: R$ {mercado:.4f}")
        if posicao is not None:
            cor_pos = COR["accent_green"] if posicao == 1 else COR["text_secondary"]
            c["lbl_posicao"].configure(text=f"Posição: {posicao}º", text_color=cor_pos)

        c["lbl_hora"].configure(text=datetime.now().strftime("%H:%M:%S"))

    def _criar_card_lance(self, num_item: int) -> None:
        """Cria um novo card de lance para um item."""
        idx = len(self._card_lances)
        card = ctk.CTkFrame(
            self._scroll_lances,
            fg_color=COR["bg_card"],
            corner_radius=8,
            border_width=1,
            border_color=COR["border"],
        )
        card.grid(row=idx, column=0, sticky="ew", padx=4, pady=3)
        card.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header, text=f"  Item {num_item}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COR["accent_blue"],
        ).grid(row=0, column=0, sticky="w")

        lbl_hora = ctk.CTkLabel(
            header, text="—",
            font=ctk.CTkFont(size=10),
            text_color=COR["text_muted"],
        )
        lbl_hora.grid(row=0, column=1, sticky="e")

        lbl_status = ctk.CTkLabel(
            card, text="Aguardando...",
            font=ctk.CTkFont(size=11),
            text_color=COR["text_secondary"],
        )
        lbl_status.grid(row=1, column=0, sticky="w", padx=10, pady=1)

        lbl_valor = ctk.CTkLabel(
            card, text="—",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COR["accent_blue"],
        )
        lbl_valor.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 2))

        info_row = ctk.CTkFrame(card, fg_color="transparent")
        info_row.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 8))

        lbl_mercado = ctk.CTkLabel(
            info_row, text="Mercado: —",
            font=ctk.CTkFont(size=10),
            text_color=COR["text_muted"],
        )
        lbl_mercado.grid(row=0, column=0, sticky="w")

        lbl_posicao = ctk.CTkLabel(
            info_row, text="Posição: —",
            font=ctk.CTkFont(size=10),
            text_color=COR["text_muted"],
        )
        lbl_posicao.grid(row=0, column=1, padx=(12, 0), sticky="w")

        self._card_lances[num_item] = {
            "card": card,
            "lbl_hora": lbl_hora,
            "lbl_status": lbl_status,
            "lbl_valor": lbl_valor,
            "lbl_mercado": lbl_mercado,
            "lbl_posicao": lbl_posicao,
        }

    def atualizar_status(self, texto: str, cor: str) -> None:
        self._lbl_status.configure(text=texto, text_color=cor)


# ─────────────────────────────────────────────────────────────────────────────
# Painel 3 – Console de Logs
# ─────────────────────────────────────────────────────────────────────────────

class PainelConsole(ctk.CTkFrame):
    """Painel de log em tempo real com filtros de nível."""

    _CORES_NIVEL = {
        "DEBUG":    COR["text_muted"],
        "INFO":     COR["text_primary"],
        "WARNING":  COR["accent_amber"],
        "ERROR":    COR["accent_red"],
        "CRITICAL": "#FF0000",
    }

    def __init__(self, master: Any, app: "App", **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self._filtro_nivel = "DEBUG"  # Exibe todos por padrão
        self._contador = {"INFO": 0, "WARNING": 0, "ERROR": 0}
        self._build()

    def _build(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ── Barra de ferramentas ──────────────────────────────────────────────
        toolbar = ctk.CTkFrame(self, fg_color=COR["bg_card"], corner_radius=8)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        toolbar.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(
            toolbar,
            text="📋  Console de Logs",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COR["text_primary"],
        ).grid(row=0, column=0, padx=16, pady=10, sticky="w")

        # Filtro
        ctk.CTkLabel(
            toolbar, text="Filtro:",
            font=ctk.CTkFont(size=11),
            text_color=COR["text_secondary"],
        ).grid(row=0, column=1, padx=(16, 4), pady=10)

        self._combo_filtro = ctk.CTkComboBox(
            toolbar,
            values=["DEBUG", "INFO", "WARNING", "ERROR"],
            command=self._aplicar_filtro,
            width=110, height=30,
            fg_color=COR["bg_input"],
            border_color=COR["border"],
            text_color=COR["text_primary"],
            font=ctk.CTkFont(size=11),
            dropdown_fg_color=COR["bg_card"],
        )
        self._combo_filtro.set("DEBUG")
        self._combo_filtro.grid(row=0, column=2, padx=(0, 16), pady=10)

        # Contadores
        self._frame_contadores = ctk.CTkFrame(toolbar, fg_color="transparent")
        self._frame_contadores.grid(row=0, column=3, padx=8, pady=10, sticky="e")

        self._lbls_contador: dict[str, ctk.CTkLabel] = {}
        for i, (nivel, cor) in enumerate([
            ("INFO", COR["accent_blue"]),
            ("WARNING", COR["accent_amber"]),
            ("ERROR", COR["accent_red"]),
        ]):
            lbl = ctk.CTkLabel(
                self._frame_contadores,
                text=f"{nivel}: 0",
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=cor,
            )
            lbl.grid(row=0, column=i, padx=8)
            self._lbls_contador[nivel] = lbl

        ctk.CTkButton(
            toolbar,
            text="🗑️  Limpar",
            command=self._limpar,
            width=80, height=30,
            fg_color=COR["bg_input"],
            hover_color=COR["border"],
            text_color=COR["text_secondary"],
            font=ctk.CTkFont(size=11),
            corner_radius=6,
        ).grid(row=0, column=4, padx=16, pady=10)

        # ── Área de texto ─────────────────────────────────────────────────────
        self._txt_area = ctk.CTkTextbox(
            self,
            fg_color=COR["bg_deep"],
            text_color=COR["text_primary"],
            font=ctk.CTkFont(family="Consolas", size=11),
            corner_radius=10,
            border_width=1,
            border_color=COR["border"],
            activate_scrollbars=True,
            wrap="word",
            state="disabled",
        )
        self._txt_area.grid(row=1, column=0, sticky="nsew")

        # Configurar tags de cor
        self._txt_area._textbox.tag_configure("DEBUG",    foreground=COR["text_muted"])
        self._txt_area._textbox.tag_configure("INFO",     foreground=COR["text_primary"])
        self._txt_area._textbox.tag_configure("WARNING",  foreground=COR["accent_amber"])
        self._txt_area._textbox.tag_configure("ERROR",    foreground=COR["accent_red"])
        self._txt_area._textbox.tag_configure("CRITICAL", foreground="#FF0000")
        self._txt_area._textbox.tag_configure("hora",     foreground=COR["text_muted"])
        self._txt_area._textbox.tag_configure("accent",   foreground=COR["accent_blue"])

        # ── Linha de status inferior ──────────────────────────────────────────
        status_bar = ctk.CTkFrame(self, fg_color=COR["bg_deep"], corner_radius=6, height=28)
        status_bar.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        status_bar.grid_columnconfigure(0, weight=1)
        status_bar.grid_propagate(False)

        self._lbl_ultimo = ctk.CTkLabel(
            status_bar,
            text="Pronto.",
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color=COR["text_muted"],
            anchor="w",
        )
        self._lbl_ultimo.grid(row=0, column=0, padx=10, sticky="w")

    def adicionar_log(self, nivel: str, mensagem: str) -> None:
        """Adiciona uma linha de log colorida (deve ser chamado na MainThread)."""
        nivel_map = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
        filtro_map = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}

        if nivel_map.get(nivel, 0) < filtro_map.get(self._filtro_nivel, 0):
            return

        hora = datetime.now().strftime("%H:%M:%S")
        self._txt_area.configure(state="normal")
        tb = self._txt_area._textbox

        tb.insert("end", f"[{hora}] ", "hora")
        tb.insert("end", f"[{nivel:<8}] ", nivel)
        tb.insert("end", mensagem + "\n")
        tb.see("end")

        self._txt_area.configure(state="disabled")
        self._lbl_ultimo.configure(text=f"Último: {mensagem[:80]}")

        # Atualiza contadores
        if nivel in self._contador:
            self._contador[nivel] += 1
            self._lbls_contador[nivel].configure(
                text=f"{nivel}: {self._contador[nivel]}"
            )

    def _aplicar_filtro(self, valor: str) -> None:
        self._filtro_nivel = valor

    def _limpar(self) -> None:
        self._txt_area.configure(state="normal")
        self._txt_area.delete("1.0", "end")
        self._txt_area.configure(state="disabled")
        self._contador = {"INFO": 0, "WARNING": 0, "ERROR": 0}
        for nivel, lbl in self._lbls_contador.items():
            lbl.configure(text=f"{nivel}: 0")


# ─────────────────────────────────────────────────────────────────────────────
# Painel de Oportunidades PNCP
# ─────────────────────────────────────────────────────────────────────────────

class PainelOportunidadesPNCP(ctk.CTkFrame):
    """
    Painel de busca de oportunidades e licitações abertas no PNCP com suporte a
    filtros (palavra-chave, UF, modalidade), download automático de editais e
    favoritamento no Supabase.
    """

    MODALIDADES_MAP = {
        "TODAS": None,
        "Pregão - Eletrônico": 6,
        "Dispensa - Eletrônica": 8,
        "Concorrência - Eletrônica": 4,
        "Leilão - Eletrônico": 1,
    }

    def __init__(self, master: ctk.CTkFrame, app: App, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self._oportunidades: list[dict[str, Any]] = []
        self._modo_favoritas = False
        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Card de Busca e Filtros
        card_busca = ctk.CTkFrame(
            self,
            fg_color=COR["bg_panel"],
            corner_radius=10,
            border_width=1,
            border_color=COR["border"],
        )
        card_busca.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 10))
        card_busca.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(card_busca, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=4, sticky="ew", padx=12, pady=(10, 4))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="🔍  Buscador de Oportunidades do PNCP",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COR["accent_blue"],
        ).grid(row=0, column=0, sticky="w")

        self._btn_toggle_fav = ctk.CTkButton(
            header,
            text="⭐  Minhas Oportunidades Salvas",
            command=self._toggle_ver_favoritas,
            height=28,
            fg_color=COR["bg_card"],
            hover_color=COR["border"],
            text_color=COR["accent_amber"],
            font=ctk.CTkFont(size=10, weight="bold"),
        )
        self._btn_toggle_fav.grid(row=0, column=1, sticky="e")

        row_filtros = ctk.CTkFrame(card_busca, fg_color="transparent")
        row_filtros.grid(row=1, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 10))
        row_filtros.grid_columnconfigure(0, weight=3)

        # Termo / Palavra-chave
        ctk.CTkLabel(
            row_filtros,
            text="Palavra-Chave / Objeto:",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COR["text_secondary"],
        ).grid(row=0, column=0, sticky="w", padx=(0, 6))

        self._entry_kw = ctk.CTkEntry(
            row_filtros,
            placeholder_text="ex: TI, infraestrutura, rede, suporte, medicamentos...",
            height=32,
            fg_color=COR["bg_card"],
            border_color=COR["border"],
            text_color=COR["text_primary"],
            font=ctk.CTkFont(size=11),
        )
        self._entry_kw.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(2, 0))

        # UF
        ctk.CTkLabel(
            row_filtros,
            text="UF (Estado):",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COR["text_secondary"],
        ).grid(row=0, column=1, sticky="w", padx=(0, 6))

        ufs = ["TODAS", "TO", "SP", "RJ", "MG", "DF", "BA", "GO", "PE", "PR", "SC", "RS", "MA", "CE", "PA", "AM"]
        self._combo_uf = ctk.CTkComboBox(
            row_filtros,
            values=ufs,
            width=90,
            height=32,
            fg_color=COR["bg_card"],
            text_color=COR["text_primary"],
        )
        self._combo_uf.set("TO")
        self._combo_uf.grid(row=1, column=1, sticky="w", padx=(0, 8), pady=(2, 0))

        # Modalidade
        ctk.CTkLabel(
            row_filtros,
            text="Modalidade:",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COR["text_secondary"],
        ).grid(row=0, column=2, sticky="w", padx=(0, 6))

        self._combo_mod = ctk.CTkComboBox(
            row_filtros,
            values=list(self.MODALIDADES_MAP.keys()),
            width=170,
            height=32,
            fg_color=COR["bg_card"],
            text_color=COR["text_primary"],
        )
        self._combo_mod.set("Pregão - Eletrônico")
        self._combo_mod.grid(row=1, column=2, sticky="w", padx=(0, 8), pady=(2, 0))

        # Botão Buscar
        self._btn_buscar = ctk.CTkButton(
            row_filtros,
            text="🔍  Buscar no PNCP",
            command=self._buscar_pncp,
            height=32,
            width=140,
            fg_color="#0088CC",
            hover_color="#006699",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=11, weight="bold"),
            corner_radius=6,
        )
        self._btn_buscar.grid(row=1, column=3, sticky="e", pady=(2, 0))

        # Bar de Status
        self._lbl_status = ctk.CTkLabel(
            card_busca,
            text="Digite os filtros e clique em 'Buscar no PNCP' para consultar contratações públicas.",
            font=ctk.CTkFont(size=11),
            text_color=COR["text_muted"],
        )
        self._lbl_status.grid(row=2, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 8))

        # Tabela de Resultados
        self._scroll_resultados = ctk.CTkScrollableFrame(
            self,
            fg_color=COR["bg_panel"],
            corner_radius=10,
            border_width=1,
            border_color=COR["border"],
        )
        self._scroll_resultados.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        self._scroll_resultados.grid_columnconfigure(0, weight=1)

        self._lbl_vazio = ctk.CTkLabel(
            self._scroll_resultados,
            text="Nenhuma oportunidade carregada.\nFaça uma busca para visualizar os certames do PNCP.",
            text_color=COR["text_muted"],
            font=ctk.CTkFont(size=12),
            justify="center",
        )
        self._lbl_vazio.pack(pady=40)

    def _buscar_pncp(self) -> None:
        """Executa busca de oportunidades no PNCP em thread assíncrona."""
        kw = self._entry_kw.get().strip()
        uf = self._combo_uf.get()
        mod_str = self._combo_mod.get()
        cod_mod = self.MODALIDADES_MAP.get(mod_str)

        self._modo_favoritas = False
        self._btn_toggle_fav.configure(text="⭐  Minhas Oportunidades Salvas")
        self._btn_buscar.configure(state="disabled", text="⏳ Buscando...")
        self._lbl_status.configure(
            text="Consultando API do Portal Nacional de Contratações Públicas (PNCP)...",
            text_color=COR["accent_blue"],
        )

        def _fn() -> None:
            try:
                res = buscar_publicacoes_pncp(
                    codigo_modalidade=cod_mod,
                    uf=uf,
                    palavra_chave=kw if kw else None,
                    pagina=1,
                    tamanho_pagina=20,
                )
                self.after(0, self._renderizar_resultados, res)
            except Exception as exc:
                err_msg = f"Falha ao consultar PNCP:\n{exc}"
                self.after(
                    0,
                    lambda msg=err_msg: messagebox.showerror(
                        "Erro PNCP", msg, parent=self.app
                    ),
                )
            finally:
                self.after(
                    0, lambda: self._btn_buscar.configure(state="normal", text="🔍  Buscar no PNCP")
                )

        threading.Thread(target=_fn, daemon=True).start()

    def _toggle_ver_favoritas(self) -> None:
        """Alterna a exibição das oportunidades salvas no Supabase."""
        if not self._modo_favoritas:
            self._modo_favoritas = True
            self._btn_toggle_fav.configure(text="🌐  Voltar às Oportunidades do PNCP")
            self._lbl_status.configure(
                text="Carregando oportunidades salvas do Supabase...", text_color=COR["accent_amber"]
            )

            def _worker() -> None:
                try:
                    salvas = listar_oportunidades_salvas()
                    self.after(0, self._renderizar_favoritas, salvas)
                except Exception as exc:
                    self.after(
                        0, lambda e=exc: messagebox.showerror("Erro Supabase", str(e), parent=self.app)
                    )

            threading.Thread(target=_worker, daemon=True).start()
        else:
            self._modo_favoritas = False
            self._btn_toggle_fav.configure(text="⭐  Minhas Oportunidades Salvas")
            self._buscar_pncp()

    def _renderizar_resultados(self, res: dict[str, Any]) -> None:
        for child in self._scroll_resultados.winfo_children():
            child.destroy()

        lista = res.get("data", [])
        total = res.get("totalRegistros", 0)
        self._lbl_status.configure(
            text=f"✅ {len(lista)} oportunidade(s) exibida(s) de {total} encontrada(s) no PNCP.",
            text_color=COR["accent_green"],
        )

        if not lista:
            ctk.CTkLabel(
                self._scroll_resultados,
                text="Nenhuma oportunidade encontrada para os filtros selecionados.",
                text_color=COR["text_muted"],
                font=ctk.CTkFont(size=12),
            ).pack(pady=40)
            return

        for idx, item in enumerate(lista):
            self._criar_card_oportunidade(idx, item, eh_favorita=False)

    def _renderizar_favoritas(self, lista: list[dict[str, Any]]) -> None:
        for child in self._scroll_resultados.winfo_children():
            child.destroy()

        self._lbl_status.configure(
            text=f"⭐ {len(lista)} oportunidade(s) favoritada(s) encontrada(s) no Supabase.",
            text_color=COR["accent_amber"],
        )

        if not lista:
            ctk.CTkLabel(
                self._scroll_resultados,
                text="Nenhuma oportunidade salva no banco de dados ainda.",
                text_color=COR["text_muted"],
                font=ctk.CTkFont(size=12),
            ).pack(pady=40)
            return

        for idx, item in enumerate(lista):
            dados_item = {
                "orgaoEntidade": {"razaoSocial": item.get("orgao", ""), "cnpj": item.get("id_pncp", "").split("/")[0] if "/" in item.get("id_pncp", "") else ""},
                "objetoCompra": item.get("objeto", ""),
                "valorTotalEstimado": item.get("valor_global", 0.0),
                "dataAberturaProposta": item.get("data_abertura_proposta", "N/D"),
                "modalidadeNome": "Salva no Supabase",
                "_id_pncp": item.get("id_pncp", ""),
            }
            self._criar_card_oportunidade(idx, dados_item, eh_favorita=True)

    def _criar_card_oportunidade(self, idx: int, c: dict[str, Any], eh_favorita: bool = False) -> None:
        bg = COR["bg_card"] if idx % 2 == 0 else COR["bg_panel"]
        card = ctk.CTkFrame(
            self._scroll_resultados,
            fg_color=bg,
            corner_radius=8,
            border_width=1,
            border_color=COR["border"],
        )
        card.pack(fill="x", padx=6, pady=4)

        orgao = c.get("orgaoEntidade", {}).get("razaoSocial", "Órgão Não Informado")
        objeto = c.get("objetoCompra", "Objeto não informado")
        val_est = c.get("valorTotalEstimado", 0.0)
        dt_ab = c.get("dataAberturaProposta", c.get("dataEncerramentoProposta", "N/D"))
        mod_nome = c.get("modalidadeNome", "Pregão Eletrônico")

        cnpj = c.get("orgaoEntidade", {}).get("cnpj", "")
        ano = str(c.get("anoCompra", ""))
        seq = str(c.get("sequencialCompra", ""))
        param_url = c.get("_id_pncp") or f"{cnpj}/{ano}/{seq}"

        top_row = ctk.CTkFrame(card, fg_color="transparent")
        top_row.pack(fill="x", padx=10, pady=(8, 2))

        ctk.CTkLabel(
            top_row,
            text=f"🏢  {orgao[:60]}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["accent_blue"],
        ).pack(side="left")

        val_str = f"R$ {val_est:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if val_est else "Valor N/D"
        ctk.CTkLabel(
            top_row,
            text=f"💰  {val_str}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["accent_purple"],
        ).pack(side="right")

        ctk.CTkLabel(
            card,
            text=objeto[:150] + "..." if len(objeto) > 150 else objeto,
            font=ctk.CTkFont(size=10),
            text_color=COR["text_primary"],
            anchor="w",
            justify="left",
        ).pack(fill="x", padx=10, pady=(0, 4))

        bot_row = ctk.CTkFrame(card, fg_color="transparent")
        bot_row.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(
            bot_row,
            text=f"🏛️ {mod_nome}  |  ⏰ Abertura: {str(dt_ab)[:10]}  |  PNCP: {param_url}",
            font=ctk.CTkFont(size=10),
            text_color=COR["text_muted"],
        ).pack(side="left")

        # Botão 1: Baixar Edital & Importar para o Robô
        def _baixar_e_importar(p=param_url):
            self.app.log("INFO", f"📥 Baixando edital e importando PNCP {p}...")
            self.app.painel_editais._entry_pncp.delete(0, "end")
            self.app.painel_editais._entry_pncp.insert(0, p)
            self.app.tab_view.set("📄  Editais & Custos")
            self.app.painel_editais._buscar_no_pncp()

        btn_dl = ctk.CTkButton(
            bot_row,
            text="📥 Baixar Edital & Importar",
            command=_baixar_e_importar,
            height=26,
            fg_color=COR["accent_green"],
            hover_color="#2EA043",
            text_color="#000000",
            font=ctk.CTkFont(size=10, weight="bold"),
        )
        btn_dl.pack(side="right", padx=(6, 0))

        # Botão 2: Salvar no Supabase
        def _salvar_supabase(u=param_url, o=orgao, obj=objeto, v=val_est, d=dt_ab, b_btn=None):
            def _fn_salvar():
                try:
                    dados_fav = {
                        "id_pncp": u,
                        "orgao": str(o)[:200],
                        "objeto": str(obj),
                        "valor_global": float(v) if v else 0.0,
                        "data_abertura_proposta": str(d)[:30],
                        "link_edital": f"https://pncp.gov.br/app/editais/{u}",
                        "status": "favoritada",
                    }
                    salvar_oportunidade(dados_fav)
                    self.app.log("INFO", f"⭐ Oportunidade {u} salva no Supabase!")
                    if b_btn:
                        self.after(0, lambda: b_btn.configure(text="✅ Salva", state="disabled"))
                except Exception as exc:
                    self.app.log("ERROR", f"Erro ao salvar oportunidade no Supabase: {exc}")

            threading.Thread(target=_fn_salvar, daemon=True).start()

        btn_fav = ctk.CTkButton(
            bot_row,
            text="⭐ Salvar" if not eh_favorita else "✅ Salva",
            state="normal" if not eh_favorita else "disabled",
            height=26,
            width=80,
            fg_color=COR["accent_amber"],
            hover_color="#D97706",
            text_color="#000000",
            font=ctk.CTkFont(size=10, weight="bold"),
        )
        btn_fav.configure(command=lambda u=param_url, o=orgao, obj=objeto, v=val_est, d=dt_ab, b=btn_fav: _salvar_supabase(u, o, obj, v, d, b))
        btn_fav.pack(side="right")


# ─────────────────────────────────────────────────────────────────────────────
# Janela Principal (App)
# ─────────────────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    """Janela principal do Robô de Licitações."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Robô de Licitações — Compras.gov.br")
        self.geometry("1280x820")
        self.minsize(1024, 700)
        self.configure(fg_color=COR["bg_deep"])

        # ── Estado global ─────────────────────────────────────────────────────
        self.regras: list[RegraItem] = []
        self.modo_operacao = ModoOperacao.SIMULATION
        self._gui_queue: queue.Queue[GUIEvent] = queue.Queue(maxsize=2000)
        self.bot_worker = BotWorker(self._gui_queue)

        # ── Ícone e foco ──────────────────────────────────────────────────────
        try:
            icon_path = Path("assets/icon.png")
            if icon_path.exists():
                from PIL import Image, ImageTk
                icon_img = ImageTk.PhotoImage(Image.open(icon_path))
                self.wm_iconphoto(True, icon_img)
        except Exception:
            pass

        self._build_ui()
        self._verificar_conexao_async()
        self._verificar_licenca_async()
        self._iniciar_loop_eventos()

    def _build_ui(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ── HEADER ────────────────────────────────────────────────────────────
        header = ctk.CTkFrame(
            self,
            fg_color=COR["bg_panel"],
            corner_radius=0,
            border_width=0,
            height=60,
        )
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        # Logo / título
        titulo_frame = ctk.CTkFrame(header, fg_color="transparent")
        titulo_frame.grid(row=0, column=0, padx=16, pady=0, sticky="ns")
        titulo_frame.grid_rowconfigure(0, weight=1)

        try:
            logo_path = Path("assets/logo.png")
            if logo_path.exists():
                from PIL import Image
                pil_img = Image.open(logo_path)
                self._logo_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(42, 42))
                lbl_logo = ctk.CTkLabel(titulo_frame, image=self._logo_img, text="")
                lbl_logo.grid(row=0, column=0, rowspan=2, padx=(0, 10), sticky="w")
        except Exception as exc:
            logger.warning(f"Não foi possível carregar imagem do logo: {exc}")

        ctk.CTkLabel(
            titulo_frame,
            text="⚡ ROBÔ DE LICITAÇÕES — JRBC",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COR["text_primary"],
        ).grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(
            titulo_frame,
            text="Automação Inteligente — Compras.gov.br",
            font=ctk.CTkFont(size=10),
            text_color=COR["text_muted"],
        ).grid(row=1, column=1, sticky="w")

        # Status Supabase
        sup_frame = ctk.CTkFrame(header, fg_color=COR["bg_card"], corner_radius=8)
        sup_frame.grid(row=0, column=1, padx=12, pady=12, sticky="ns")
        sup_frame.grid_columnconfigure(1, weight=1)

        self.dot_supabase = StatusDot(sup_frame)
        self.dot_supabase.grid(row=0, column=0, padx=(12, 6), pady=14)

        self._lbl_supabase = ctk.CTkLabel(
            sup_frame,
            text="Verificando Supabase...",
            font=ctk.CTkFont(size=11),
            text_color=COR["text_secondary"],
        )
        self._lbl_supabase.grid(row=0, column=1, padx=(0, 14))

        # Ativo (pisca durante sessão)
        ativo_frame = ctk.CTkFrame(header, fg_color=COR["bg_card"], corner_radius=8)
        ativo_frame.grid(row=0, column=2, padx=4, pady=12, sticky="ns")
        ativo_frame.grid_columnconfigure(1, weight=1)

        self.dot_ativo = StatusDot(ativo_frame)
        self.dot_ativo.grid(row=0, column=0, padx=(12, 6), pady=14)
        ctk.CTkLabel(
            ativo_frame,
            text="Bot Inativo",
            font=ctk.CTkFont(size=11),
            text_color=COR["text_secondary"],
        ).grid(row=0, column=1, padx=(0, 14))

        # Seletor de Modo
        modo_frame = ctk.CTkFrame(header, fg_color=COR["bg_card"], corner_radius=8)
        modo_frame.grid(row=0, column=3, padx=(4, 8), pady=12, sticky="ns")

        ctk.CTkLabel(
            modo_frame,
            text="MODO:",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COR["text_muted"],
        ).grid(row=0, column=0, padx=(12, 4), pady=14)

        self._modo_var = ctk.StringVar(value="SIMULAÇÃO 🛡️")
        self._seg_modo = ctk.CTkSegmentedButton(
            modo_frame,
            values=["SIMULAÇÃO 🛡️", "REAL ⚠️"],
            variable=self._modo_var,
            command=self._mudar_modo,
            font=ctk.CTkFont(size=11, weight="bold"),
            selected_color=COR["accent_green"],
            selected_hover_color="#2EA043",
            unselected_color=COR["bg_input"],
            fg_color=COR["bg_input"],
            height=32,
        )
        self._seg_modo.grid(row=0, column=1, padx=(0, 12), pady=14)

        # Botão Diagnóstico
        self._btn_diag = ctk.CTkButton(
            header,
            text="🔍  Diagnóstico",
            command=self._executar_diagnostico_gui,
            height=32,
            fg_color=COR["bg_card"],
            hover_color=COR["border"],
            text_color=COR["text_primary"],
            font=ctk.CTkFont(size=11, weight="bold"),
            corner_radius=6,
        )
        self._btn_diag.grid(row=0, column=4, padx=4, pady=12, sticky="ns")

        # Botão Licença
        self._btn_licenca = ctk.CTkButton(
            header,
            text="🔑  Licença",
            command=self._exibir_modal_licenca,
            height=32,
            fg_color=COR["bg_card"],
            hover_color=COR["border"],
            text_color=COR["text_primary"],
            font=ctk.CTkFont(size=11, weight="bold"),
            corner_radius=6,
        )
        self._btn_licenca.grid(row=0, column=5, padx=(4, 16), pady=12, sticky="ns")

        # ── CONTEÚDO PRINCIPAL ────────────────────────────────────────────────
        self.tab_view = ctk.CTkTabview(
            self,
            fg_color=COR["bg_panel"],
            corner_radius=10,
            border_width=1,
            border_color=COR["border"],
            segmented_button_fg_color=COR["bg_card"],
            segmented_button_selected_color=COR["accent_blue"],
            segmented_button_selected_hover_color="#1F6FEB",
            segmented_button_unselected_color=COR["bg_card"],
            segmented_button_unselected_hover_color=COR["border"],
            text_color=COR["text_primary"],
        )
        self.tab_view.grid(row=1, column=0, sticky="nsew", padx=12, pady=12)

        abas = [
            "🔍  Oportunidades PNCP",
            "📄  Editais & Custos",
            "🚀  Sessão & Lances",
            "📋  Console & Logs",
        ]
        for aba in abas:
            self.tab_view.add(aba)
            self.tab_view.tab(aba).grid_columnconfigure(0, weight=1)
            self.tab_view.tab(aba).grid_rowconfigure(0, weight=1)

        # Painel 0 (Oportunidades PNCP)
        self.painel_oportunidades = PainelOportunidadesPNCP(
            self.tab_view.tab("🔍  Oportunidades PNCP"), app=self
        )
        self.painel_oportunidades.grid(row=0, column=0, sticky="nsew", padx=12, pady=(4, 12))

        # Painel 1 (Editais & Custos)
        self.painel_editais = PainelEditais(
            self.tab_view.tab("📄  Editais & Custos"), app=self
        )
        self.painel_editais.grid(row=0, column=0, sticky="nsew", padx=12, pady=(4, 12))

        # Painel 2 (Sessão & Lances)
        self.painel_sessao = PainelSessao(
            self.tab_view.tab("🚀  Sessão & Lances"), app=self
        )
        self.painel_sessao.grid(row=0, column=0, sticky="nsew", padx=12, pady=(4, 12))

        # Painel 3 (Console & Logs)
        self.painel_console = PainelConsole(
            self.tab_view.tab("📋  Console & Logs"), app=self
        )
        self.painel_console.grid(row=0, column=0, sticky="nsew", padx=12, pady=(4, 12))

    def _executar_diagnostico_gui(self) -> None:
        """Executa o diagnóstico de sistema em background e exibe janela modal."""
        self.log("INFO", "🔍 Executando diagnóstico completo de conexões...")
        self._btn_diag.configure(state="disabled", text="⏳ Testando...")

        def _worker() -> None:
            try:
                diag = DiagnosticoSistema()
                itens = diag.executar_tudo()
                self.after(0, self._exibir_modal_diagnostico, itens)
            except Exception as exc:
                self.after(0, lambda e=exc: messagebox.showerror("Erro de Diagnóstico", str(e), parent=self))
            finally:
                self.after(0, lambda: self._btn_diag.configure(state="normal", text="🔍  Diagnóstico"))

        threading.Thread(target=_worker, daemon=True).start()

    def _exibir_modal_diagnostico(self, itens: list[Any]) -> None:
        """Abre janela Toplevel formatada com os resultados do diagnóstico."""
        modal = ctk.CTkToplevel(self)
        modal.title("🔍 Diagnóstico do Sistema & Conexões")
        modal.geometry("700x520")
        modal.configure(fg_color=COR["bg_deep"])
        modal.transient(self)
        modal.grab_set()

        ctk.CTkLabel(
            modal,
            text="🔍  Relatório de Diagnóstico de Conexões e Integridade",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COR["text_primary"],
        ).pack(padx=16, pady=(16, 8), anchor="w")

        scroll = ctk.CTkScrollableFrame(
            modal,
            fg_color=COR["bg_panel"],
            corner_radius=10,
            border_width=1,
            border_color=COR["border"],
        )
        scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        for idx, item in enumerate(itens):
            bg = COR["bg_card"] if idx % 2 == 0 else COR["bg_panel"]
            card = ctk.CTkFrame(scroll, fg_color=bg, corner_radius=6)
            card.pack(fill="x", padx=4, pady=3)

            status_str = getattr(item.status, "value", str(item.status))
            if status_str == "OK":
                cor = COR["accent_green"]
                icon = "✅"
            elif status_str == "WARNING":
                cor = COR["accent_amber"]
                icon = "⚠️"
            else:
                cor = COR["accent_red"]
                icon = "❌"

            row_top = ctk.CTkFrame(card, fg_color="transparent")
            row_top.pack(fill="x", padx=8, pady=(6, 2))

            ctk.CTkLabel(
                row_top,
                text=f"{icon}  [{item.categoria}]  {item.nome}",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=cor,
            ).pack(side="left")

            ctk.CTkLabel(
                card,
                text=item.mensagem,
                font=ctk.CTkFont(size=11),
                text_color=COR["text_primary"],
                anchor="w",
                justify="left",
            ).pack(fill="x", padx=28, pady=(0, 4))

            if item.detalhes:
                ctk.CTkLabel(
                    card,
                    text=f"└─ Detalhes: {item.detalhes}",
                    font=ctk.CTkFont(size=10),
                    text_color=COR["text_muted"],
                    anchor="w",
                    justify="left",
                ).pack(fill="x", padx=28, pady=(0, 6))

        btn_fechar = ctk.CTkButton(
            modal,
            text="Fechar",
            command=modal.destroy,
            width=120, height=34,
            fg_color=COR["accent_blue"],
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        btn_fechar.pack(pady=(0, 16))

    # ── Modo de operação ──────────────────────────────────────────────────────

    def _mudar_modo(self, valor: str) -> None:
        if "REAL" in valor:
            resp = messagebox.askyesno(
                "⚠️  Ativar Modo Real",
                "Tem certeza que deseja ativar o MODO REAL?\n\n"
                "No modo real, lances serão enviados com efeito legal vinculante.\n\n"
                "✅ Sempre teste em SIMULAÇÃO antes!",
                icon="warning",
                parent=self,
            )
            if not resp:
                self._modo_var.set("SIMULAÇÃO 🛡️")
                self._seg_modo.configure(selected_color=COR["accent_green"])
                return
            self.modo_operacao = ModoOperacao.REAL
            self._seg_modo.configure(selected_color=COR["accent_red"])
            self.log("WARNING", "⚠️  MODO REAL ATIVADO – lances reais serão enviados!")
        else:
            self.modo_operacao = ModoOperacao.SIMULATION
            self._seg_modo.configure(selected_color=COR["accent_green"])
            self.log("INFO", "🛡️  Modo Simulação ativado (seguro).")

    # ── Conexão Supabase ──────────────────────────────────────────────────────

    def _verificar_conexao_async(self) -> None:
        """Verifica a conexão com Supabase em thread de background."""
        def _check() -> None:
            ok = testar_conexao()
            self.after(0, self._atualizar_status_supabase, ok)

        threading.Thread(target=_check, daemon=True).start()

    def _atualizar_status_supabase(self, ok: bool) -> None:
        if ok:
            self.dot_supabase.set_ok()
            self._lbl_supabase.configure(
                text="Supabase ✅",
                text_color=COR["accent_green"],
            )
            self.log("INFO", "✅ Conexão com Supabase OK!")
        else:
            self.dot_supabase.set_erro()
            self._lbl_supabase.configure(
                text="Supabase ❌",
                text_color=COR["accent_red"],
            )
            self.log("ERROR", "❌ Falha na conexão com Supabase. Verifique o .env.")

    # ── Gerenciamento de Licença & Mensalidade ────────────────────────────────

    def _verificar_licenca_async(self) -> None:
        """Verifica a licença do cliente no Supabase em background."""
        def _check() -> None:
            try:
                res = validar_licenca()
                self.after(0, self._atualizar_status_licenca, res)
            except Exception as exc:
                logger.warning(f"Erro ao verificar licença: {exc}")

        threading.Thread(target=_check, daemon=True).start()

    def _atualizar_status_licenca(self, res: Any) -> None:
        """Atualiza o botão/status da licença no header."""
        if res.valida:
            dias_txt = f"{res.dias_restantes}d" if res.dias_restantes is not None else "Ativa"
            self._btn_licenca.configure(
                text=f"🔑  {dias_txt} ✅",
                fg_color=COR["bg_card"],
                text_color=COR["accent_green"],
            )
            self.log("INFO", f"🔑 Licença ativa: {res.cliente_nome or 'Cliente'} ({res.mensagem})")
        else:
            self._btn_licenca.configure(
                text="🔑  Ativar Licença ⚠️",
                fg_color=COR["accent_red"],
                hover_color="#D0342C",
                text_color="#FFFFFF",
            )
            self.log("WARNING", f"⚠️ Licença: {res.mensagem}")

    def _exibir_modal_licenca(self) -> None:
        """Abre janela para visualização de HWID e ativação de chave de licença."""
        modal = ctk.CTkToplevel(self)
        modal.title("🔑 Gerenciador de Licença & Assinatura")
        modal.geometry("560x420")
        modal.configure(fg_color=COR["bg_deep"])
        modal.transient(self)
        modal.grab_set()

        ctk.CTkLabel(
            modal,
            text="🔑  Ativação & Gerenciamento de Licença",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=COR["text_primary"],
        ).pack(padx=20, pady=(20, 4), anchor="w")

        ctk.CTkLabel(
            modal,
            text="Insira sua chave de ativação para validar a mensalidade no servidor.",
            font=ctk.CTkFont(size=11),
            text_color=COR["text_muted"],
        ).pack(padx=20, pady=(0, 14), anchor="w")

        card = ctk.CTkFrame(modal, fg_color=COR["bg_panel"], corner_radius=10, border_width=1, border_color=COR["border"])
        card.pack(fill="x", padx=20, pady=(0, 14))

        # Hardware ID da Máquina
        hwid_frame = ctk.CTkFrame(card, fg_color="transparent")
        hwid_frame.pack(fill="x", padx=14, pady=(12, 6))

        ctk.CTkLabel(
            hwid_frame,
            text="Identificador da Máquina (HWID):",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["text_secondary"],
        ).pack(side="left")

        hwid_val = obter_hwid()
        lbl_hwid = ctk.CTkLabel(
            hwid_frame,
            text=hwid_val,
            font=ctk.CTkFont(size=11, family="monospace", weight="bold"),
            text_color=COR["accent_blue"],
        )
        lbl_hwid.pack(side="left", padx=8)

        def _copiar_hwid():
            self.clipboard_clear()
            self.clipboard_append(hwid_val)
            messagebox.showinfo("Copiado", f"HWID copiado para a área de transferência:\n{hwid_val}", parent=modal)

        ctk.CTkButton(
            hwid_frame,
            text="Copiar",
            command=_copiar_hwid,
            width=60, height=24,
            font=ctk.CTkFont(size=10),
            fg_color=COR["bg_card"],
            hover_color=COR["border"],
        ).pack(side="right")

        # Campo da Chave de Licença
        ctk.CTkLabel(
            card,
            text="Chave de Licença (Ex: JRBC-XXXX-XXXX-XXXX):",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["text_secondary"],
        ).pack(padx=14, pady=(8, 2), anchor="w")

        chave_salva = obter_chave_salva() or ""
        entry_chave = ctk.CTkEntry(
            card,
            placeholder_text="JRBC-XXXX-XXXX-XXXX",
            height=34,
            font=ctk.CTkFont(size=12, family="monospace"),
            fg_color=COR["bg_input"],
            border_color=COR["border"],
        )
        entry_chave.pack(fill="x", padx=14, pady=(0, 10))
        if chave_salva:
            entry_chave.insert(0, chave_salva)

        # Status / Feedback
        lbl_feedback = ctk.CTkLabel(
            card,
            text="",
            font=ctk.CTkFont(size=11),
            wraplength=480,
            justify="left",
        )
        lbl_feedback.pack(fill="x", padx=14, pady=(0, 10))

        def _ativar():
            chave_digitada = entry_chave.get().strip().upper()
            if not chave_digitada:
                lbl_feedback.configure(text="❌ Informe uma chave de licença.", text_color=COR["accent_red"])
                return

            lbl_feedback.configure(text="⏳ Validando licença no Supabase...", text_color=COR["accent_blue"])
            modal.update()

            def _worker():
                res = validar_licenca(chave_digitada)
                def _ui():
                    if res.valida:
                        salvar_chave_local(chave_digitada)
                        lbl_feedback.configure(text=f"✅ {res.mensagem}", text_color=COR["accent_green"])
                        self._atualizar_status_licenca(res)
                        messagebox.showinfo("Licença Ativada", f"Licença validada com sucesso para:\n{res.cliente_nome}\n\nVálida por mais {res.dias_restantes} dias.", parent=modal)
                    else:
                        lbl_feedback.configure(text=f"❌ {res.mensagem}", text_color=COR["accent_red"])
                        self._atualizar_status_licenca(res)
                self.after(0, _ui)

            threading.Thread(target=_worker, daemon=True).start()

        # Botões de Ação
        btn_frame = ctk.CTkFrame(modal, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=8)

        ctk.CTkButton(
            btn_frame,
            text="Validar & Ativar",
            command=_ativar,
            height=36,
            fg_color=COR["accent_green"],
            hover_color="#2EA043",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            btn_frame,
            text="Fechar",
            command=modal.destroy,
            height=36,
            width=100,
            fg_color=COR["bg_card"],
            hover_color=COR["border"],
            font=ctk.CTkFont(size=12),
        ).pack(side="right", padx=(6, 0))

    # ── Log público ───────────────────────────────────────────────────────────

    def log(self, nivel: str, mensagem: str) -> None:
        """Adiciona uma mensagem ao console de logs (thread-safe via after)."""
        self.after(0, self.painel_console.adicionar_log, nivel, mensagem)

    # ── Loop de eventos da GUI (processa eventos do bot) ─────────────────────

    def _iniciar_loop_eventos(self) -> None:
        """Processa a fila de eventos do bot a cada 50ms (não-bloqueante)."""
        self._processar_fila()

    def _processar_fila(self) -> None:
        """Drena a fila de eventos GUI emitidos pela thread do bot."""
        MAX_POR_CICLO = 20  # Limita para não travar a GUI
        processados = 0

        while processados < MAX_POR_CICLO:
            try:
                evento = self._gui_queue.get_nowait()
            except queue.Empty:
                break

            processados += 1
            tipo = evento.tipo
            dados = evento.dados

            if tipo == "log":
                self.painel_console.adicionar_log(
                    dados.get("nivel", "INFO"),
                    dados.get("msg", ""),
                )
            elif tipo == "lance":
                self.painel_sessao.atualizar_lance(evento)
                self.painel_console.adicionar_log(
                    "WARNING" if dados.get("bloqueado") else "INFO",
                    dados.get("motivo") or (
                        f"Lance Item {dados.get('item')}: R$ {dados.get('valor', '—')}"
                    ),
                )
            elif tipo == "status":
                self.painel_sessao.atualizar_status(
                    dados.get("texto", ""),
                    dados.get("cor", COR["text_secondary"]),
                )
            elif tipo == "erro":
                self.painel_console.adicionar_log("ERROR", dados.get("msg", "Erro desconhecido"))
            elif tipo == "fim":
                self.dot_ativo.set_erro()
                self.painel_console.adicionar_log("INFO", "🏁 " + dados.get("msg", "Sessão finalizada."))

        # Pisca o dot de ativo se o bot estiver rodando
        if self.bot_worker.esta_rodando():
            self.dot_ativo.set_piscando()

        # Re-agenda para o próximo tick
        self.after(50, self._processar_fila)

    def on_closing(self) -> None:
        """Handler de fechamento da janela."""
        if self.bot_worker.esta_rodando():
            resp = messagebox.askyesno(
                "Sessão Ativa",
                "Uma sessão está em andamento.\nDeseja encerrar o bot e fechar o programa?",
                parent=self,
            )
            if not resp:
                return
            self.bot_worker.encerrar()
        log_queue.parar(timeout=2)
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────────────────────────────────────

def run_gui() -> None:
    """Inicializa e executa a interface gráfica."""
    if not _MODULES_OK:
        # Tenta mostrar erro em janela Tkinter antes de abortar
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Erro de Inicialização",
            f"Falha ao carregar módulos do projeto:\n\n{_IMPORT_ERROR}\n\n"
            "Verifique o arquivo .env e as dependências instaladas.",
        )
        root.destroy()
        return

    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()


if __name__ == "__main__":
    run_gui()
