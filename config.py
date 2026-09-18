"""
config.py – Gerenciamento de configurações do Robô de Licitações JRBC TECH.

Carrega e valida as configurações do sistema via Pydantic e variáveis de ambiente (.env).
"""

from typing import List, Union
import os
import json
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

# Carrega variáveis de ambiente do arquivo .env
load_dotenv()


def _parse_list(val: Union[str, List[str]]) -> List[str]:
    """Converte string separada por vírgula ou JSON em lista de strings."""
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return []
        if val_str.startswith("[") and val_str.endswith("]"):
            try:
                parsed = json.loads(val_str)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except json.JSONDecodeError:
                pass
        return [x.strip() for x in val_str.split(",") if x.strip()]
    return []


class Config(BaseModel):
    """
    Modelo de configurações globais do Robô de Licitações.
    """

    # Estados de interesse padrão
    estados_interesse: List[str] = Field(
        default_factory=lambda: ["TO", "GO"],
        description="Estados (UF) de interesse para monitoramento de contratações",
    )

    # Modalidades de contratação
    modalidades: List[str] = Field(
        default_factory=lambda: [
            "Dispensa Eletrônica (Art. 75, II)",
            "Pregão Eletrônico",
        ],
        description="Modalidades de contratação observadas",
    )

    # Palavras-chave de TI da JRBC TECH
    palavras_chave_ti: List[str] = Field(
        default_factory=lambda: [
            "suporte",
            "manutencao",
            "computador",
            "rede",
            "cabeamento",
            "servidor",
            "licenciamento",
            "software",
            "saas",
            "ti",
            "telecom",
        ],
        description="Palavras-chave de TI para filtragem dos objetos de licitação",
    )

    # Configurações de API e Conexão
    pncp_base_url: str = Field(
        default="https://pncp.gov.br/pncp-api/v1/",
        description="URL base da API pública do PNCP",
    )

    dias_publicacao_recuo: int = Field(
        default=7,
        description="Período em dias para buscar publicações recentes",
    )

    http_timeout: float = Field(
        default=15.0,
        description="Timeout em segundos para requisições HTTP",
    )

    max_retries: int = Field(
        default=3,
        description="Número máximo de tentativas em caso de erro ou rate limit (429)",
    )

    @field_validator("estados_interesse", "modalidades", "palavras_chave_ti", mode="before")
    @classmethod
    def validar_listas(cls, val: Union[str, List[str]]) -> List[str]:
        return _parse_list(val)

    @classmethod
    def carregar(cls) -> "Config":
        """Carrega e retorna a instância de configuração considerando o arquivo .env."""
        env_dict = {}

        if os.getenv("ESTADOS_INTERESSE"):
            env_dict["estados_interesse"] = os.getenv("ESTADOS_INTERESSE")
        if os.getenv("MODALIDADES"):
            env_dict["modalidades"] = os.getenv("MODALIDADES")
        if os.getenv("PALAVRAS_CHAVE_TI"):
            env_dict["palavras_chave_ti"] = os.getenv("PALAVRAS_CHAVE_TI")
        if os.getenv("PNCP_BASE_URL"):
            env_dict["pncp_base_url"] = os.getenv("PNCP_BASE_URL")
        if os.getenv("DIAS_PUBLICACAO_RECUO"):
            try:
                env_dict["dias_publicacao_recuo"] = int(os.getenv("DIAS_PUBLICACAO_RECUO", "7"))
            except ValueError:
                pass
        if os.getenv("HTTP_TIMEOUT"):
            try:
                env_dict["http_timeout"] = float(os.getenv("HTTP_TIMEOUT", "15.0"))
            except ValueError:
                pass
        if os.getenv("MAX_RETRIES"):
            try:
                env_dict["max_retries"] = int(os.getenv("MAX_RETRIES", "3"))
            except ValueError:
                pass

        return cls(**env_dict)


# Instância global
config = Config.carregar()
