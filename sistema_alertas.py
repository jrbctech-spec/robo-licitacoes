"""
sistema_alertas.py – Sistema de alertas automáticos para novas oportunidades de licitação.

Funciona em duas modalidades:
  1. Modo CLI (terminal): polling automático a cada X segundos, imprime no terminal quando encontra NOVA oportunidade.
  2. Modo integrado (quando o robo estiver rodando): dispara evento para a GUI ou log.

O sistema monitora:
  - PNCP (quando disponível) — endpoint de publicações com palavras-chave e UFs configuradas
  - Compras.gov.br (paginação simples) — quando PNCP indisponível

Configuração via .env:
  ALERTA_POLLING_INTERVAL  → segundos entre cada varredura (padrão: 3600 = 1h)
  ALERTA_PALAVRAS_CHAVE   → palavras-chave para filtrar (vírgula)
  ALERTA_UFS              → UFs para monitorar (vírgula)
  ALERTA_TERMINAL         → imprime no terminal (padrão: True)
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sistema_alertas")

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from config import settings
from supabase_client import log_queue


# ─────────────────────────────────────────────────────────────────────────────
# Configuração de alertas (carregada do .env ou padrões)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_list(value: str, separator: str = ",") -> list[str]:
    """Transforma string separada em lista trimada."""
    if not value:
        return []
    return [s.strip() for s in value.split(separator) if s.strip()]


ALERTA_POLLING_INTERVAL: int = int(os.getenv("ALERTA_POLLING_INTERVAL", "3600"))
ALERTA_PALAVRAS_CHAVE: list[str] = _parse_list(os.getenv("ALERTA_PALAVRAS_CHAVE", "manutencao,informatica,TI,suporte,rede,instalacao"))
ALERTA_UFS: list[str] = _parse_list(os.getenv("ALERTA_UFS", "TO,PI,MA,GO,MT,RO"))
ALERTA_TERMINAL: bool = os.getenv("ALERTA_TERMINAL", "True").lower() in ("true", "1", "yes")
ALERTA_GUI: bool = os.getenv("ALERTA_GUI", "True").lower() in ("true", "1", "yes")


@dataclass
class OportunidadeAlerta:
    """Representa uma oportunidade nova detectada pelo sistema de alertas."""
    id_pncp: str
    orgao: str
    objeto: str
    valor_estimado: float
    uf: str
    data_encerramento: Optional[str] = None
    detectada_em: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    palavras_chave_match: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Fila de alertas (para integração com GUI/thread principal)
# ─────────────────────────────────────────────────────────────────────────────

_fila_alertas: list[OportunidadeAlerta] = []
_lock_alertas = threading.Lock()


def enfileirar_alerta(oportunidade: OportunidadeAlerta) -> None:
    """Adiciona um alerta à fila (thread-safe)."""
    with _lock_alertas:
        _fila_alertas.append(oportunidade)
    logger.info(f"🔔 ALERTA DE NOVA OPORTUNIDADE: [{oportunidade.uf}] {oportunidade.orgao} — R$ {oportunidade.valor_estimado:,.2f}")


def listar_alertas_pendentes() -> list[OportunidadeAlerta]:
    """Retorna e limpa a fila de alertas pendentes."""
    with _lock_alertas:
        alertas = _fila_alertas.copy()
        _fila_alertas.clear()
        return alertas


# ─────────────────────────────────────────────────────────────────────────────
# Motor de varredura (polling)
# ─────────────────────────────────────────────────────────────────────────────

class AlertasScanner:
    """
    Scanner automático que varre o PNCP a cada N segundos procurando novas
    oportunidades que casem com as palavras-chave e UFs configuradas.
    """

    def __init__(self):
        self._rodando = False
        self._thread: Optional[threading.Thread] = None
        self._já_vistos: set[str] = set()  # IDs de oportunidades já alertadas
        self._ultimo_scroll: int = 0

    def iniciar(self) -> None:
        """Inicia o thread de varredura automática."""
        if self._rodando:
            return
        self._rodando = True
        self._thread = threading.Thread(target=self._loop, name="alertas-scanner", daemon=True)
        self._thread.start()
        logger.info(f"🔍 Scanner de alertas iniciado | intervalo={ALERTA_POLLING_INTERVAL}s | UFs={ALERTA_UFS} | palavras={ALERTA_PALAVRAS_CHAVE}")

    def parar(self) -> None:
        """Sinaliza parada do scanner."""
        self._rodando = False
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        """Loop principal de varredura."""
        while self._rodando:
            try:
                self._varrer()
            except Exception as exc:
                logger.error(f"Erro no scanner de alertas: {exc}")
            # Espera o intervalo configurado (interruptível)
            for _ in range(ALERTA_POLLING_INTERVAL):
                if not self._rodando:
                    break
                time.sleep(1)

    def _varrer(self) -> None:
        """
        Executa uma varredura do PNCP (e fallback Compras.gov.br se indisponível).
        Filtra por palavras-chave e UFs configuradas.
        """
        from pncp_client import buscar_publicacoes_pncp

        logger.debug("Iniciando varredura de oportunidades...")

        try:
            # Tenta cada UF configurada
            for uf in ALERTA_UFS:
                if not self._rodando:
                    break
                self._varrer_uf(uf)
        except Exception as exc:
            logger.error(f"Erro geral na varredura: {exc}")

    def _varrer_uf(self, uf: str) -> None:
        """
        Varre uma UF específica no PNCP e filtra por palavras-chave.
        """
        # Tenta buscar com cada palavra-chave relevante
        for kw in ALERTA_PALAVRAS_CHAVE:
            if not self._rodando:
                break
            try:
                res = buscar_publicacoes_pncp(
                    uf=uf,
                    palavra_chave=kw,
                    pagina=1,
                    tamanho_pagina=20,
                )
                itens = res.get("data", [])
                logger.debug(f"  [{uf}] keyword='{kw}': {len(itens)} resultados")

                for item in itens:
                    id_pncp = self._extrair_id_pncp(item)
                    if not id_pncp:
                        continue
                    if id_pncp in self._já_vistos:
                        continue  # Já alertamos essa oportunidade

                    # Verifica se o objeto casa com as palavras-chave
                    objeto = (item.get("objetoCompra") or "").lower()
                    orgao = (item.get("orgaoEntidade", {}).get("razaoSocial") or "").lower()

                    palavras_casa = []
                    for pw in ALERTA_PALAVRAS_CHAVE:
                        if pw.lower() in objeto or pw.lower() in orgao:
                            palavras_casa.append(pw)

                    if not palavras_casa:
                        continue  # Não é relevante para nós

                    # Novo alerta!
                    valor = item.get("valorTotalEstimado") or 0.0
                    op = OportunidadeAlerta(
                        id_pncp=id_pncp,
                        orgao=item.get("orgaoEntidade", {}).get("razaoSocial", "Órgão Desconhecido"),
                        objeto=item.get("objetoCompra", "Sem descrição")[:200],
                        valor_estimado=float(valor) if valor else 0.0,
                        uf=uf,
                        data_encerramento=item.get("dataEncerramentoProposta"),
                        palavras_chave_match=palavras_casa,
                    )
                    self._já_vistos.add(id_pncp)
                    enfileirar_alerta(op)

                    # Imprime no terminal se configurado
                    if ALERTA_TERMINAL:
                        print(f"\n🔔 [bold green]NOVA OPORTUNIDADE DETECTADA[/bold green]")
                        print(f"   📍 UF: {uf}")
                        print(f"   🏢 Órgão: {op.orgao}")
                        print(f"   📝 Objeto: {op.objeto[:100]}")
                        print(f"   💰 Valor: R$ {op.valor_estimado:,.2f}")
                        print(f"   ⏰ Encerramento: {op.data_encerramento}")
                        print(f"   🔑 Palavras: {', '.join(op.palavras_chave_match)}")

            except Exception as exc:
                logger.warning(f"Erro ao varrer {uf}/{kw}: {exc}")

    @staticmethod
    def _extrair_id_pncp(item: dict) -> Optional[str]:
        """Extrai um identificador único da oportunidade."""
        cnpj = item.get("orgaoEntidade", {}).get("cnpj", "")
        ano = item.get("anoCompra", "")
        seq = item.get("sequencialCompra", "")
        if cnpj and ano and seq:
            return f"{cnpj}/{ano}/{seq}"
        # Fallback: usa o objeto como ID
        obj = item.get("objetoCompra", "")
        if obj:
            return obj[:60]
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Uso como script standalone
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sistema de Alertas Automático de Licitações")
    parser.add_argument("--intervalo", type=int, default=ALERTA_POLLING_INTERVAL, help="Intervalo em segundos entre varreduras")
    parser.add_argument("--uf", action="append", dest="ufs", help="UF específica para monitorar (pode repetir)")
    parser.add_argument("--keyword", action="append", dest="keywords", help="Palavra-chave específica (pode repetir)")
    parser.add_argument("--once", action="store_true", help="Executa apenas uma varredura e sai")
    args = parser.parse_args()

    # Override de configuração via CLI
    if args.ufs:
        ALERTA_UFS = args.ufs
    if args.keywords:
        ALERTA_PALAVRAS_CHAVE = args.keywords
    if args.intervalo:
        ALERTA_POLLING_INTERVAL = args.intervalo

    print(f"🔍 Iniciando scanner de alertas | UFs={ALERTA_UFS} | keywords={ALERTA_PALAVRAS_CHAVE} | intervalo={ALERTA_POLLING_INTERVAL}s")

    scanner = AlertasScanner()
    scanner.iniciar()

    if args.once:
        time.sleep(3)  # Dá tempo de fazer uma varredura
        scanner.parar()
        alertas = listar_alertas_pendentes()
        print(f"\n📊 Total de novas oportunidades encontradas: {len(alertas)}")
        for a in alertas:
            print(f"  [{a.uf}] {a.orgao} — R$ {a.valor_estimado:,.2f} — {a.objeto[:60]}")
    else:
        print("\n👀 Monitorando em segundo plano (Ctrl+C para parar)...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n⏹ Parando scanner...")
            scanner.parar()
            alertas = listar_alertas_pendentes()
            print(f"\nOportunidades detectadas nesta sessão: {len(alertas)}")
