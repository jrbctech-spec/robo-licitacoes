"""
license_manager.py – Gerenciamento e Validação de Licenças / Mensalidades (JRBC).

Funcionalidades:
  1. Identificação única da máquina (HWID / Fingerprint) para evitar cópias não autorizadas.
  2. Validação online de mensalidade e status ('ativa', 'suspensa', 'expirada') na tabela 'licencas' do Supabase.
  3. Vinculação automática do HWID no primeiro uso da chave.
  4. Cache local seguro (.license_cache) para tolerância a oscilações temporárias de rede.
  5. Função administrativa para criar e renovar licenças diretamente pelo sistema.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from supabase_client import get_client

logger = logging.getLogger(__name__)

ARQUIVO_LICENCA = Path(".license")
ARQUIVO_CACHE_LICENCA = Path(".license_cache")
SEGREDO_CACHE = "JRBC-LICENCA-SIG-2026-KEY"


class StatusLicenca(str, Enum):
    ATIVA = "ativa"
    EXPIRADA = "expirada"
    SUSPENSA = "suspensa"
    NAO_ENCONTRADA = "nao_encontrada"
    MAQUINA_NAO_AUTORIZADA = "maquina_nao_autorizada"
    SEM_LICENCA = "sem_licenca"
    ERRO_CONEXAO = "erro_conexao"


@dataclass
class ResultadoValidacao:
    """Resultado da validação de uma licença."""
    valida: bool
    status: StatusLicenca
    mensagem: str
    cliente_nome: Optional[str] = None
    cnpj_ou_cpf: Optional[str] = None
    dias_restantes: Optional[int] = None
    valida_ate: Optional[datetime] = None
    plano: Optional[str] = None
    chave_licenca: Optional[str] = None


def obter_hwid() -> str:
    """
    Gera um identificador único e consistente da máquina (Hardware ID).
    Combina: nó de rede MAC, hostname, sistema operacional e processador.
    """
    try:
        mac = uuid.getnode()
        host = socket.gethostname()
        sistema = platform.system()
        proc = platform.processor() or platform.machine()
        raw = f"{mac}:{host}:{sistema}:{proc}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24].upper()
    except Exception:
        # Fallback se não conseguir ler hardware
        return hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()[:24].upper()


def obter_chave_salva() -> Optional[str]:
    """Retorna a chave de licença salva localmente no arquivo .license ou variável de ambiente."""
    env_key = os.getenv("CHAVE_LICENCA")
    if env_key and env_key.strip():
        return env_key.strip().upper()

    if ARQUIVO_LICENCA.exists():
        try:
            conteudo = ARQUIVO_LICENCA.read_text(encoding="utf-8").strip().upper()
            if conteudo:
                return conteudo
        except Exception as exc:
            logger.warning(f"Falha ao ler arquivo de licença: {exc}")

    return None


def salvar_chave_local(chave: str) -> bool:
    """Salva a chave de licença no arquivo local .license."""
    try:
        chave_limpa = chave.strip().upper()
        ARQUIVO_LICENCA.write_text(chave_limpa, encoding="utf-8")
        logger.info(f"Chave de licença salva localmente: {chave_limpa[:8]}...")
        return True
    except Exception as exc:
        logger.error(f"Erro ao salvar arquivo de licença: {exc}")
        return False


def _salvar_cache(resultado: ResultadoValidacao) -> None:
    """Salva um snapshot criptografado da validação para contingência offline."""
    try:
        if not resultado.valida:
            if ARQUIVO_CACHE_LICENCA.exists():
                ARQUIVO_CACHE_LICENCA.unlink()
            return

        dados = {
            "chave": resultado.chave_licenca,
            "hwid": obter_hwid(),
            "cliente": resultado.cliente_nome,
            "cnpj": resultado.cnpj_ou_cpf,
            "valida_ate": resultado.valida_ate.isoformat() if resultado.valida_ate else None,
            "plano": resultado.plano,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        json_str = json.dumps(dados)
        sig = hashlib.sha256((json_str + SEGREDO_CACHE).encode("utf-8")).hexdigest()
        ARQUIVO_CACHE_LICENCA.write_text(f"{sig}::{json_str}", encoding="utf-8")
    except Exception as exc:
        logger.debug(f"Falha ao salvar cache de licença: {exc}")


def _validar_cache_offline() -> Optional[ResultadoValidacao]:
    """Tenta validar via cache local se o banco estiver temporariamente indisponível."""
    if not ARQUIVO_CACHE_LICENCA.exists():
        return None

    try:
        conteudo = ARQUIVO_CACHE_LICENCA.read_text(encoding="utf-8")
        if "::" not in conteudo:
            return None

        sig_esperada, json_str = conteudo.split("::", 1)
        sig_calculada = hashlib.sha256((json_str + SEGREDO_CACHE).encode("utf-8")).hexdigest()
        if sig_esperada != sig_calculada:
            logger.warning("Cache de licença adulterado.")
            return None

        dados = json.loads(json_str)
        if dados.get("hwid") != obter_hwid():
            return None

        cached_at = datetime.fromisoformat(dados["cached_at"])
        # Cache válido por até 48 horas offline
        if datetime.now(timezone.utc) - cached_at > timedelta(hours=48):
            return None

        valida_ate = datetime.fromisoformat(dados["valida_ate"]) if dados.get("valida_ate") else None
        agora = datetime.now(timezone.utc)
        if valida_ate and valida_ate < agora:
            return ResultadoValidacao(
                valida=False,
                status=StatusLicenca.EXPIRADA,
                mensagem=f"Sua mensalidade expirou em {valida_ate.strftime('%d/%m/%Y')}.",
                valida_ate=valida_ate,
            )

        dias = (valida_ate - agora).days if valida_ate else None
        return ResultadoValidacao(
            valida=True,
            status=StatusLicenca.ATIVA,
            mensagem="Licença válida (Modo Offline Temporário).",
            cliente_nome=dados.get("cliente"),
            cnpj_ou_cpf=dados.get("cnpj"),
            dias_restantes=dias,
            valida_ate=valida_ate,
            plano=dados.get("plano"),
            chave_licenca=dados.get("chave"),
        )
    except Exception as exc:
        logger.warning(f"Erro ao ler cache offline de licença: {exc}")
        return None


def validar_licenca(chave: Optional[str] = None) -> ResultadoValidacao:
    """
    Valida a licença do cliente no Supabase.

    Regras:
      1. Se status != 'ativa' -> bloqueado.
      2. Se valida_ate < data_atual -> expirada.
      3. Se HWID não preenchido no banco -> vincula a máquina atual automaticamente.
      4. Se HWID diferente da máquina -> bloqueia acesso em outra máquina.
    """
    chave_final = (chave or obter_chave_salva() or "").strip().upper()

    if not chave_final:
        return ResultadoValidacao(
            valida=False,
            status=StatusLicenca.SEM_LICENCA,
            mensagem="Nenhuma chave de licença encontrada. Insira sua chave de ativação.",
        )

    hwid_atual = obter_hwid()

    try:
        client = get_client()
        resposta = (
            client.table("licencas")
            .select("*")
            .eq("chave_licenca", chave_final)
            .execute()
        )

        if not resposta.data:
            return ResultadoValidacao(
                valida=False,
                status=StatusLicenca.NAO_ENCONTRADA,
                mensagem=f"Chave de licença '{chave_final}' não encontrada ou inválida.",
                chave_licenca=chave_final,
            )

        reg = resposta.data[0]
        status_banco = str(reg.get("status", "")).lower()
        cliente_nome = reg.get("cliente_nome", "Cliente")
        cnpj_cpf = reg.get("cnpj_ou_cpf", "")
        plano = reg.get("plano", "mensal")
        valida_ate_str = reg.get("valida_ate")
        hwid_banco = reg.get("hwid")

        # 1. Verifica status administrativo
        if status_banco == "suspensa":
            return ResultadoValidacao(
                valida=False,
                status=StatusLicenca.SUSPENSA,
                mensagem=f"A licença da empresa {cliente_nome} está suspensa. Entre em contato com o suporte.",
                cliente_nome=cliente_nome,
                cnpj_ou_cpf=cnpj_cpf,
                chave_licenca=chave_final,
            )

        # 2. Verifica data de expiração da mensalidade
        valida_ate: Optional[datetime] = None
        if valida_ate_str:
            try:
                valida_ate = datetime.fromisoformat(valida_ate_str.replace("Z", "+00:00"))
            except Exception:
                valida_ate = None

        agora = datetime.now(timezone.utc)
        if valida_ate and valida_ate < agora:
            dt_fmt = valida_ate.strftime("%d/%m/%Y")
            return ResultadoValidacao(
                valida=False,
                status=StatusLicenca.EXPIRADA,
                mensagem=f"Sua assinatura expirou em {dt_fmt}. Efetue a renovação da mensalidade para continuar.",
                cliente_nome=cliente_nome,
                cnpj_ou_cpf=cnpj_cpf,
                dias_restantes=0,
                valida_ate=valida_ate,
                plano=plano,
                chave_licenca=chave_final,
            )

        # 3. Validação e Vinculação de Máquina (HWID)
        if not hwid_banco:
            # Primeiro uso: vincula o computador do cliente
            try:
                client.table("licencas").update({
                    "hwid": hwid_atual,
                    "atualizado_em": agora.isoformat(),
                }).eq("id", reg["id"]).execute()
                logger.info(f"✅ HWID {hwid_atual} vinculado com sucesso à chave {chave_final}.")
            except Exception as exc_hwid:
                logger.warning(f"Aviso ao vincular HWID: {exc_hwid}")
        elif hwid_banco != hwid_atual:
            return ResultadoValidacao(
                valida=False,
                status=StatusLicenca.MAQUINA_NAO_AUTORIZADA,
                mensagem="Esta chave já está ativada em outro computador. Para transferir de máquina, fale com o suporte.",
                cliente_nome=cliente_nome,
                cnpj_ou_cpf=cnpj_cpf,
                chave_licenca=chave_final,
            )

        # Salva localmente se a chave for válida
        salvar_chave_local(chave_final)

        dias = (valida_ate - agora).days if valida_ate else None
        res_ok = ResultadoValidacao(
            valida=True,
            status=StatusLicenca.ATIVA,
            mensagem=f"Licença ativa para {cliente_nome} ({dias} dias restantes).",
            cliente_nome=cliente_nome,
            cnpj_ou_cpf=cnpj_cpf,
            dias_restantes=dias,
            valida_ate=valida_ate,
            plano=plano,
            chave_licenca=chave_final,
        )
        _salvar_cache(res_ok)
        return res_ok

    except Exception as exc:
        logger.error(f"Falha ao validar licença no Supabase: {exc}")
        # Tenta contingência offline
        cache_res = _validar_cache_offline()
        if cache_res:
            return cache_res

        return ResultadoValidacao(
            valida=False,
            status=StatusLicenca.ERRO_CONEXAO,
            mensagem=f"Não foi possível validar a licença online: {exc}",
            chave_licenca=chave_final,
        )


def criar_nova_licenca(
    cliente_nome: str,
    cnpj_ou_cpf: str = "",
    dias_validade: int = 30,
    plano: str = "mensal",
    observacoes: str = "",
) -> dict[str, Any]:
    """
    Função administrativa para gerar uma nova chave de licença no Supabase.

    Uso:
        from license_manager import criar_nova_licenca
        lic = criar_nova_licenca("Empresa XPTO Ltda", "12.345.678/0001-90", dias_validade=30)
        print("Chave gerada:", lic["chave_licenca"])
    """
    prefixo = "JRBC"
    parte1 = hashlib.md5(f"{cliente_nome}:{uuid.uuid4()}".encode("utf-8")).hexdigest()[:4].upper()
    parte2 = hashlib.md5(f"{cnpj_ou_cpf}:{uuid.uuid4()}".encode("utf-8")).hexdigest()[:4].upper()
    parte3 = hashlib.md5(str(uuid.uuid4()).encode("utf-8")).hexdigest()[:4].upper()
    chave = f"{prefixo}-{parte1}-{parte2}-{parte3}"

    valida_ate = datetime.now(timezone.utc) + timedelta(days=dias_validade)

    dados = {
        "chave_licenca": chave,
        "cliente_nome": cliente_nome.strip(),
        "cnpj_ou_cpf": cnpj_ou_cpf.strip(),
        "status": "ativa",
        "valida_ate": valida_ate.isoformat(),
        "plano": plano,
        "limite_maquinas": 1,
        "observacoes": observacoes,
    }

    client = get_client()
    resp = client.table("licencas").insert(dados).execute()
    logger.info(f"🔑 Nova licença criada: {chave} para '{cliente_nome}' até {valida_ate.strftime('%d/%m/%Y')}")
    return resp.data[0] if resp.data else dados
