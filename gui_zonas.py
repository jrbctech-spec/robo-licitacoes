"""
PainelZonas – Painel da aba "📊 Zonas & Estratégia" do Robô de Licitações.

Exibe em tempo real:
  - Status das 4 zonas (Eliminatória, Competitiva, Preservação, Desistida)
  - Percentuais e limites configurados
  - Indicador visual de qual zona está ativa
  - Métricas de itens por zona
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import customtkinter as ctk

logger = logging.getLogger(__name__)

# Cores hardcode para evitar ciclo de importação com app_gui
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


class PainelZonas(ctk.CTkFrame):
    """Painel de visualização e controle da régua de zonas."""

    def __init__(self, master: Any, app: Any, **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self._zona_ativa: Optional[str] = None
        self._build()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Cabeçalho
        header = ctk.CTkFrame(self, fg_color=COR["bg_card"], corner_radius=10)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(0, 8))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="📊  Régua de Zonas — Estratégia de Lance",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COR["text_primary"],
        ).grid(row=0, column=0, padx=16, pady=12, sticky="w")

        ctk.CTkLabel(
            header,
            text="Zonas definem a estratégia de lance com base no percentual do stop-loss",
            font=ctk.CTkFont(size=10),
            text_color=COR["text_muted"],
        ).grid(row=1, column=0, padx=16, pady=(0, 8), sticky="w")

        # Indicador de zona ativa
        self._indicador_zona = ctk.CTkLabel(
            self,
            text="───  Aguarde início da sessão  ───",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["text_muted"],
            justify="center",
        )
        self._indicador_zona.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 12))

        # Cards das zonas
        card_zonas = ctk.CTkFrame(self, fg_color=COR["bg_panel"], corner_radius=10, border_width=1, border_color=COR["border"])
        card_zonas.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        card_zonas.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self._cards_zonas: dict[str, dict] = {}

        zonas_info = [
            ("eliminatoria", "🔴 Zona Eliminatória", "> 80% do stop-loss", "Agressivo", "#F85149"),
            ("competitiva", "🟡 Zona Competitiva", "60%–80% do stop-loss", "Padrão", "#D29922"),
            ("preservacao", "🟢 Zona Preservação", "< 60% do stop-loss", "Conservador", "#3FB950"),
            ("desistida", "⚫ Zona Desistida", "Abaixo do stop-loss", "Bloqueada", "#484F58"),
        ]

        for col, (chave, titulo, faixa, estrategia, cor) in enumerate(zonas_info):
            card = ctk.CTkFrame(card_zonas, fg_color=COR["bg_card"], corner_radius=8)
            card.grid(row=0, column=col, padx=4, pady=4, sticky="nsew")
            card.grid_columnconfigure(0, weight=1)
            card.grid_rowconfigure(3, weight=1)
            self._cards_zonas[chave] = {"card": card, "lbl_status": None}

            ctk.CTkLabel(
                card,
                text=titulo,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=cor,
            ).grid(row=0, column=0, padx=8, pady=(8, 2), sticky="w")

            ctk.CTkLabel(
                card,
                text=faixa,
                font=ctk.CTkFont(size=9),
                text_color=COR["text_muted"],
            ).grid(row=1, column=0, padx=8, pady=(0, 2), sticky="w")

            lbl_estrategia = ctk.CTkLabel(
                card,
                text=f"Estratégia: {estrategia}",
                font=ctk.CTkFont(size=9, weight="bold"),
                text_color=COR["text_secondary"],
            )
            lbl_estrategia.grid(row=2, column=0, padx=8, pady=(0, 4), sticky="w")

            # Indicador de atividade (pula)
            lbl_status = ctk.CTkLabel(
                card,
                text="⏸ Inativo",
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=COR["text_muted"],
            )
            lbl_status.grid(row=3, column=0, padx=8, pady=(4, 4), sticky="w")
            self._cards_zonas[chave]["lbl_status"] = lbl_status
            self._cards_zonas[chave]["lbl_estrategia"] = lbl_estrategia

        # Rodapé com status
        rodape = ctk.CTkFrame(self, fg_color=COR["bg_deep"], corner_radius=6)
        rodape.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 8))
        rodape.grid_columnconfigure(0, weight=1)

        self._lbl_zona_ativa = ctk.CTkLabel(
            rodape,
            text="Zona ativa: —",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COR["text_secondary"],
        )
        self._lbl_zona_ativa.grid(row=0, column=0, sticky="w", padx=8, pady=4)

    def atualizar_zona(self, evento: Any, estrategia_zonas: Any) -> None:
        """
        Atualiza o painel com a zona determinada pelo motor.
        evento: GUIEvent com dados do lance
        estrategia_zonas: instância de EstrategiaZonas
        """
        try:
            num = evento.dados.get("item", 0)
            valor = evento.dados.get("valor")
            mercado = evento.dados.get("mercado")
            bloqueado = evento.dados.get("bloqueado", False)

            if bloqueado or not valor:
                self._indicador_zona.configure(
                    text="🛑 STOP-LOSS ativado — lance bloqueado",
                    text_color=COR["accent_red"],
                )
                self._lbl_zona_ativa.configure(text="Zona ativa: Desistida (stop-loss)", text_color=COR["accent_red"])
                self._ativar_zona_card("desistida", COR["accent_red"], "🛑 BLOQUEADO")
                return

            # Determina zona do lance calculado
            if mercado and mercado > 0:
                pct_mercado = (valor / mercado) * 100 if mercado > 0 else 0
                zona_nome = "competitiva" if pct_mercado >= 60 else "preservacao" if pct_mercado > 0 else "desistida"
            else:
                zona_nome = "competitiva"  # padrão

            cor_zona = {
                "eliminatoria": COR["accent_red"],
                "competitiva": COR["accent_amber"],
                "preservacao": COR["accent_green"],
                "desistida": COR["text_muted"],
            }.get(zona_nome, COR["text_secondary"])

            self._indicador_zona.configure(
                text=f"⚡ Lance Item {num}: R$ {valor:.2f} → Zona {zona_nome.capitalize()}",
                text_color=cor_zona,
            )
            self._lbl_zona_ativa.configure(text=f"Zona ativa: {zona_nome.capitalize()} ({valor:.2f} / mercado {mercado or 0:.2f})", text_color=cor_zona)
            self._ativar_zona_card(zona_nome, cor_zona, f"✅ Lance: R$ {valor:.2f}")

        except Exception as e:
            logger.debug("Erro ao atualizar painel de zonas: %s", e)

    def _ativar_zona_card(self, chave: str, cor: str, status_txt: str) -> None:
        """Ativa o card de uma zona específica e desativa os outros."""
        for ch, widgets in self._cards_zonas.items():
            status_lbl = widgets.get("lbl_status")
            if status_lbl:
                if ch == chave:
                    status_lbl.configure(text=status_txt, text_color=cor)
                else:
                    status_lbl.configure(text="⏸ Inativo", text_color=COR["text_muted"])
