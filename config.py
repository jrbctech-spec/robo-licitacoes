"""
config.py – Gerenciamento de configurações com Pydantic Settings.

Carrega e valida todas as variáveis de ambiente necessárias para o robô.
Garante tipagem forte e valores padrão seguros.
"""
from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModoOperacao(str, Enum):
    """Modo de operação do robô de lances."""
    SIMULATION = "simulation"   # Apenas simula; não envia lances reais.
    REAL = "real"               # Envia lances reais no Compras.gov.br.


class Settings(BaseSettings):
    """
    Configurações globais do projeto carregadas a partir do arquivo .env.

    Uso:
        from config import settings
        print(settings.supabase_url)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Supabase ─────────────────────────────────────────────────────────────
    supabase_url: str = Field(
        ...,
        validation_alias=AliasChoices("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL"),
        description="URL do projeto Supabase (ex: https://xyz.supabase.co)",
    )
    supabase_key: str = Field(
        ...,
        validation_alias=AliasChoices(
            "SUPABASE_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
            "NEXT_PUBLIC_SUPABASE_ANON_KEY",
        ),
        description="Chave de API do Supabase (anon ou service_role).",
    )

    # ── Google Cloud & Gemini AI ──────────────────────────────────────────────
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        description="Chave de API do Google Gemini.",
    )
    gcp_project_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT"),
        description="ID do Projeto Google Cloud.",
    )
    gcp_region: str = Field(
        default="southamerica-east1",
        validation_alias=AliasChoices("GCP_REGION", "GOOGLE_CLOUD_REGION"),
        description="Região Padrão do GCP.",
    )

    # ── Modo de operação ──────────────────────────────────────────────────────
    modo_operacao: ModoOperacao = Field(
        default=ModoOperacao.SIMULATION,
        description=(
            "Modo de operação: 'simulation' (padrão seguro) ou 'real'. "
            "No modo simulation nenhum lance real é enviado."
        ),
    )

    # ── Regras financeiras ────────────────────────────────────────────────────
    margem_minima_percent: float = Field(
        default=5.0,
        ge=0.0,
        le=100.0,
        description="Margem mínima de lucro sobre o custo declarado (em %).",
    )

    # ── Navegador ────────────────────────────────────────────────────────────
    browser_user_data_dir: Path = Field(
        default=Path("./browser_profile"),
        description=(
            "Caminho para o perfil persistente do Chromium. "
            "Mantém a sessão autenticada entre execuções."
        ),
    )
    browser_timeout_ms: int = Field(
        default=30_000,
        ge=5_000,
        le=120_000,
        description="Timeout em milissegundos para operações do Playwright.",
    )
    browser_headless: bool = Field(
        default=False,
        description=(
            "Se True, o navegador roda sem interface gráfica. "
            "Deixe False para visualizar a automação."
        ),
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Nível de verbosidade dos logs.",
    )
    log_async_queue_size: int = Field(
        default=500,
        description="Tamanho máximo da fila de logs assíncronos para o Supabase.",
    )

    # ── URLs do Compras.gov.br ────────────────────────────────────────────────
    comprasnet_base_url: str = Field(
        default="https://www.compras.gov.br",
        description="URL base do portal Compras.gov.br.",
    )
    comprasnet_pregao_url: str = Field(
        default="https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/public/pregao",
        description="URL base para pregões no Comprasnet Web.",
    )

    @field_validator("supabase_url")
    @classmethod
    def validar_supabase_url(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("SUPABASE_URL deve começar com 'https://'.")
        if "supabase.co" not in v and "supabase.in" not in v:
            raise ValueError(
                "SUPABASE_URL não parece uma URL válida do Supabase. "
                "Verifique o arquivo .env."
            )
        return v.rstrip("/")

    @field_validator("supabase_key")
    @classmethod
    def validar_supabase_key(cls, v: str) -> str:
        if len(v) < 20:
            raise ValueError(
                "SUPABASE_KEY parece inválida (muito curta). "
                "Copie a chave completa do dashboard do Supabase."
            )
        return v

    @model_validator(mode="after")
    def avisar_modo_real(self) -> "Settings":
        """Emite aviso crítico quando o modo real está ativado."""
        if self.modo_operacao == ModoOperacao.REAL:
            import warnings
            warnings.warn(
                "\n" + "!" * 60 + "\n"
                "ATENÇÃO: MODO REAL ATIVADO – lances reais serão enviados!\n"
                "Certifique-se de que todos os custos e stop-losses estão corretos.\n"
                + "!" * 60,
                UserWarning,
                stacklevel=2,
            )
        return self

    @property
    def is_simulation(self) -> bool:
        """Retorna True se o robô está em modo de simulação (seguro)."""
        return self.modo_operacao == ModoOperacao.SIMULATION

    @property
    def is_real(self) -> bool:
        """Retorna True se o robô está em modo real (lances efetivos)."""
        return self.modo_operacao == ModoOperacao.REAL

    def calcular_preco_minimo(self, custo_unitario: float) -> float:
        """
        Calcula o preço mínimo (stop-loss) a partir do custo unitário
        usando a margem mínima configurada.

        Args:
            custo_unitario: Custo real do produto/serviço.

        Returns:
            Preço mínimo = custo * (1 + margem_minima / 100)
        """
        return round(custo_unitario * (1 + self.margem_minima_percent / 100), 4)


# Instância singleton das configurações.
# Importar com: from config import settings
try:
    settings = Settings()  # type: ignore[call-arg]
except Exception as exc:
    raise RuntimeError(
        f"Falha ao carregar configurações. Verifique o arquivo .env: {exc}"
    ) from exc
