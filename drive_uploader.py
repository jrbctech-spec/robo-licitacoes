"""
drive_uploader.py – Gerenciador de organização e upload de pastas de certames em nuvem / local.

Estrutura pastas organizadas por certame no padrão:
  UASG_MODALIDADE_NUMERO (ex: "UASG_928758_PREGAO_000274_2026")

Armazena:
  - Editais e anexos oficiais baixados do PNCP/Portal
  - Propostas comerciais geradas e planilhas de custos
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DriveUploader:
    """
    Gerenciador de armazenamento em estrutura de pastas organizadas por certame.
    """

    def __init__(self, base_dir: str | Path = "processos_nuvem") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def obter_ou_criar_pasta_certame(
        self,
        numero_uasg: str,
        modalidade: str,
        numero_pregao: str,
    ) -> Path:
        """
        Cria e retorna a pasta padronizada para o certame: UASG_MODALIDADE_NUMERO.
        """
        uasg_limpo = str(numero_uasg).strip().replace("/", "_").replace("\\", "_")
        mod_limpa = str(modalidade).strip().upper().replace(" ", "_").replace("Ç", "C").replace("Ã", "A")
        if "DISPENSA" in mod_limpa:
            mod_tag = "DISPENSA"
        elif "PREGAO" in mod_limpa:
            mod_tag = "PREGAO"
        else:
            mod_tag = mod_limpa[:10]

        num_limpo = str(numero_pregao).strip().replace("/", "_").replace("\\", "_")

        nome_pasta = f"UASG_{uasg_limpo}_{mod_tag}_{num_limpo}"
        pasta = self.base_dir / nome_pasta
        pasta.mkdir(parents=True, exist_ok=True)

        # Subpastas internas organizadas
        (pasta / "editais_anexos").mkdir(exist_ok=True)
        (pasta / "propostas_geradas").mkdir(exist_ok=True)
        (pasta / "documentos_habilite").mkdir(exist_ok=True)

        return pasta

    def salvar_arquivo_certame(
        self,
        numero_uasg: str,
        modalidade: str,
        numero_pregao: str,
        caminho_arquivo: str | Path,
        subcategoria: str = "editais_anexos",
    ) -> Path:
        """
        Copia um arquivo para a pasta correspondente do certame.
        """
        pasta_certame = self.obter_ou_criar_pasta_certame(numero_uasg, modalidade, numero_pregao)
        destino_sub = pasta_certame / subcategoria
        destino_sub.mkdir(parents=True, exist_ok=True)

        origem = Path(caminho_arquivo)
        if not origem.exists():
            raise FileNotFoundError(f"Arquivo não encontrado para salvar: {origem}")

        destino = destino_sub / origem.name
        shutil.copy2(origem, destino)
        logger.info(f"📁 Arquivo salvo na pasta do certame: {destino}")
        return destino


def organizar_pasta_certame(
    numero_uasg: str,
    modalidade: str,
    numero_pregao: str,
    arquivos: list[str | Path],
    base_dir: str | Path = "processos_nuvem",
) -> Path:
    """
    Função utilitária para organizar lista de arquivos na pasta do certame.
    """
    uploader = DriveUploader(base_dir=base_dir)
    pasta = uploader.obter_ou_criar_pasta_certame(numero_uasg, modalidade, numero_pregao)
    for arq in arquivos:
        try:
            uploader.salvar_arquivo_certame(numero_uasg, modalidade, numero_pregao, arq)
        except Exception as exc:
            logger.warning(f"Erro ao salvar arquivo '{arq}' na pasta do certame: {exc}")

    return pasta
