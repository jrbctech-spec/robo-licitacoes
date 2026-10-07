"""
supabase_client.py – Conexão e operações com o Supabase.

Fornece:
  - Cliente singleton do Supabase com reconexão automática.
  - AsyncLogQueue: fila não-bloqueante para gravação de logs em background,
    garantindo latência zero durante decisões de lance.
  - Funções de alto nível para CRUD das tabelas do schema.
"""
from __future__ import annotations

import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from supabase import Client, create_client
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from config import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Cliente Supabase (Singleton thread-safe)
# ─────────────────────────────────────────────────────────────────────────────

_client_lock = threading.Lock()
_supabase_client: Optional[Client] = None


def get_client() -> Client:
    """
    Retorna o cliente Supabase singleton.
    Cria a conexão na primeira chamada (lazy initialization).

    Returns:
        Client: Instância do cliente Supabase.

    Raises:
        RuntimeError: Se a conexão não puder ser estabelecida.
    """
    global _supabase_client
    if _supabase_client is None:
        with _client_lock:
            if _supabase_client is None:
                try:
                    _supabase_client = create_client(
                        settings.supabase_url,
                        settings.supabase_key,
                    )
                    logger.info("Conexão com Supabase estabelecida com sucesso.")
                except Exception as exc:
                    raise RuntimeError(
                        f"Falha ao conectar ao Supabase: {exc}"
                    ) from exc
    return _supabase_client


get_supabase_client = get_client


def testar_conexao() -> bool:
    """
    Verifica se a conexão com o Supabase está funcional.

    Returns:
        True se a conexão for bem-sucedida, False caso contrário.
    """
    try:
        client = get_client()
        # Consulta simples para validar conectividade
        client.table("licitacoes").select("id").limit(1).execute()
        logger.info("✅ Conexão com Supabase verificada com sucesso.")
        return True
    except Exception as exc:
        logger.error(f"❌ Falha na verificação de conexão com Supabase: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Fila de Logs Assíncronos (não-bloqueante)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EntradaLog:
    """Estrutura de um registro de log para envio ao Supabase."""
    sessao_id: str
    nivel: str
    mensagem: str
    modulo: str = ""
    licitacao_id: Optional[str] = None
    dados_extras: Optional[dict[str, Any]] = None
    registrado_em: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class AsyncLogQueue:
    """
    Fila de logs assíncronos para gravação não-bloqueante no Supabase.

    Durante sessões de lances, o envio de logs ao banco NÃO deve adicionar
    latência à tomada de decisão. Esta fila desacopla a geração do log
    do envio à rede usando uma thread dedicada em background.

    Uso:
        log_queue = AsyncLogQueue()
        log_queue.iniciar()

        log_queue.enfileirar(EntradaLog(
            sessao_id="...",
            nivel="INFO",
            mensagem="Lance calculado: R$ 100,00",
        ))

        log_queue.parar()  # Aguarda envio de todos os logs pendentes
    """

    def __init__(self, max_size: int = 500) -> None:
        self._queue: queue.Queue[Optional[EntradaLog]] = queue.Queue(
            maxsize=max_size
        )
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False
        self._flush_event = threading.Event()

    def iniciar(self) -> None:
        """Inicia a thread worker de envio de logs em background."""
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="supabase-log-worker",
            daemon=True,
        )
        self._worker_thread.start()
        logger.debug("AsyncLogQueue iniciada.")

    def enfileirar(self, entrada: EntradaLog) -> None:
        """
        Adiciona um log à fila de envio (não-bloqueante).
        Se a fila estiver cheia, o log é descartado com aviso.
        """
        try:
            self._queue.put_nowait(entrada)
        except queue.Full:
            logger.warning(
                "⚠️  Fila de logs cheia – log descartado. "
                "Considere aumentar log_async_queue_size no .env."
            )

    def parar(self, timeout: float = 10.0) -> None:
        """
        Sinaliza o encerramento da fila e aguarda o envio de logs pendentes.

        Args:
            timeout: Tempo máximo de espera (segundos) para flush.
        """
        if not self._running:
            return
        self._running = False
        self._queue.put(None)  # Sentinela para encerrar a thread
        if self._worker_thread:
            self._worker_thread.join(timeout=timeout)
        logger.debug("AsyncLogQueue encerrada.")

    def _worker_loop(self) -> None:
        """Loop da thread worker: processa e envia logs em lotes."""
        BATCH_SIZE = 20
        BATCH_TIMEOUT = 2.0  # segundos

        batch: list[dict[str, Any]] = []

        while True:
            try:
                # Tenta coletar itens em lote
                try:
                    entrada = self._queue.get(timeout=BATCH_TIMEOUT)
                except queue.Empty:
                    # Timeout – envia lote parcial se houver algo
                    if batch:
                        self._enviar_lote(batch)
                        batch = []
                    continue

                if entrada is None:
                    # Sentinela: encerra após enviar o que sobrou
                    if batch:
                        self._enviar_lote(batch)
                    break

                batch.append(self._entrada_para_dict(entrada))

                if len(batch) >= BATCH_SIZE:
                    self._enviar_lote(batch)
                    batch = []

            except Exception as exc:
                logger.error(f"Erro no worker de logs: {exc}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(Exception),
        reraise=False,
    )
    def _enviar_lote(self, batch: list[dict[str, Any]]) -> None:
        """Envia um lote de logs ao Supabase com retry automático."""
        if not batch:
            return
        try:
            get_client().table("logs_sessao").insert(batch).execute()
            logger.debug(f"📤 {len(batch)} log(s) enviados ao Supabase.")
        except Exception as exc:
            logger.error(f"Falha ao enviar lote de logs ao Supabase: {exc}")
            raise

    @staticmethod
    def _entrada_para_dict(entrada: EntradaLog) -> dict[str, Any]:
        lic_uuid = None
        if entrada.licitacao_id:
            try:
                uuid.UUID(str(entrada.licitacao_id))
                lic_uuid = str(entrada.licitacao_id)
            except (ValueError, AttributeError):
                lic_uuid = None

        dados_extras = dict(entrada.dados_extras) if entrada.dados_extras else {}
        if entrada.licitacao_id and not lic_uuid:
            dados_extras["licitacao_ref"] = str(entrada.licitacao_id)

        return {
            "sessao_id": entrada.sessao_id,
            "nivel": entrada.nivel,
            "mensagem": entrada.mensagem,
            "modulo": entrada.modulo or None,
            "licitacao_id": lic_uuid,
            "dados_extras": dados_extras if dados_extras else None,
            "registrado_em": entrada.registrado_em,
        }


# Instância global da fila de logs
log_queue = AsyncLogQueue()


# ─────────────────────────────────────────────────────────────────────────────
# Operações de alto nível nas tabelas
# ─────────────────────────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type(Exception),
)
def inserir_licitacao(dados: dict[str, Any]) -> dict[str, Any]:
    """
    Insere ou atualiza uma licitação no Supabase (upsert).

    Args:
        dados: Dicionário com campos da tabela licitacoes.

    Returns:
        Registro inserido/atualizado como dicionário.
    """
    if not dados.get("numero_controle_pncp"):
        import uuid
        uasg = str(dados.get("numero_uasg") or "S_UASG").strip()
        preg = str(dados.get("numero_pregao") or "S_PREG").replace("/", "_").strip()
        dados["numero_controle_pncp"] = f"GEN_{uasg}_{preg}_{uuid.uuid4().hex[:8]}"
    conflict_col = "numero_controle_pncp"
    try:
        resposta = (
            get_client()
            .table("licitacoes")
            .upsert(dados, on_conflict=conflict_col)
            .execute()
        )
        return resposta.data[0] if resposta.data else {}
    except Exception as e:
        if "PGRST204" in str(e) and "portal" in dados:
            logger.warning("Coluna 'portal' ausente no banco de dados. Tentando inserir sem este campo (fallback).")
            dados_fallback = dados.copy()
            dados_fallback.pop("portal", None)
            resposta = (
                get_client()
                .table("licitacoes")
                .upsert(dados_fallback, on_conflict=conflict_col)
                .execute()
            )
            return resposta.data[0] if resposta.data else {}
        raise


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type(Exception),
)
def inserir_item(dados: dict[str, Any]) -> dict[str, Any]:
    """
    Insere ou atualiza um item de licitação no Supabase.

    Args:
        dados: Dicionário com campos da tabela itens_licitacao.

    Returns:
        Registro inserido/atualizado como dicionário.
    """
    resposta = (
        get_client()
        .table("itens_licitacao")
        .upsert(dados, on_conflict="licitacao_id,numero_item")
        .execute()
    )
    return resposta.data[0] if resposta.data else {}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type(Exception),
    reraise=False,
)
def registrar_lance(dados: dict[str, Any]) -> dict[str, Any]:
    """
    Registra um lance (simulado ou real) no histórico de lances.

    Args:
        dados: Dicionário com campos da tabela historico_lances.

    Returns:
        Registro inserido como dicionário.
    """
    dados_sanitizados = dict(dados)
    for k in ("licitacao_id", "item_id"):
        val = dados_sanitizados.get(k)
        if val is not None:
            try:
                uuid.UUID(str(val))
            except (ValueError, AttributeError):
                dados_sanitizados.pop(k, None)

    try:
        resposta = (
            get_client()
            .table("historico_lances")
            .insert(dados_sanitizados)
            .execute()
        )
        return resposta.data[0] if resposta.data else {}
    except Exception as exc:
        logger.warning(f"Falha ao registrar lance no Supabase: {exc}")
        return {}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type(Exception),
)
def atualizar_item(item_id: str, campos: dict[str, Any]) -> dict[str, Any]:
    """
    Atualiza campos de um item de licitação pelo seu ID.

    Args:
        item_id: UUID do item na tabela itens_licitacao.
        campos: Dicionário com campos a atualizar.

    Returns:
        Registro atualizado como dicionário.
    """
    resposta = (
        get_client()
        .table("itens_licitacao")
        .update(campos)
        .eq("id", item_id)
        .execute()
    )
    return resposta.data[0] if resposta.data else {}


def buscar_licitacao(numero_uasg: str, numero_pregao: str) -> Optional[dict[str, Any]]:
    """
    Busca uma licitação pelo número UASG e pregão.

    Args:
        numero_uasg: Código UASG do órgão.
        numero_pregao: Número do pregão eletrônico.

    Returns:
        Dicionário com os dados da licitação ou None se não encontrada.
    """
    try:
        resposta = (
            get_client()
            .table("licitacoes")
            .select("*, itens_licitacao(*)")
            .eq("numero_uasg", numero_uasg)
            .eq("numero_pregao", numero_pregao)
            .single()
            .execute()
        )
        return resposta.data
    except Exception:
        return None


_OPORTUNIDADES_MEMORIA_LOCAL: list[dict[str, Any]] = []


def salvar_oportunidade(dados: dict[str, Any]) -> dict[str, Any]:
    """
    Insere ou atualiza (upsert) uma oportunidade favoritada na tabela oportunidades_salvas.
    Se a tabela ainda não tiver sido criada no Supabase, salva em memória RAM local.
    """
    try:
        resposta = (
            get_client()
            .table("oportunidades_salvas")
            .upsert(dados, on_conflict="id_pncp")
            .execute()
        )
        logger.info(f"⭐ Oportunidade PNCP '{dados.get('id_pncp')}' favoritada no Supabase.")
        return resposta.data[0] if resposta.data else dados
    except Exception as exc:
        logger.warning(f"Tabela 'oportunidades_salvas' indisponível no Supabase (salvando localmente): {exc}")
        global _OPORTUNIDADES_MEMORIA_LOCAL
        _OPORTUNIDADES_MEMORIA_LOCAL = [o for o in _OPORTUNIDADES_MEMORIA_LOCAL if o.get("id_pncp") != dados.get("id_pncp")]
        _OPORTUNIDADES_MEMORIA_LOCAL.insert(0, dados)
        return dados


def listar_licitacoes(termo_busca: str = "", limite: int = 50) -> list[dict[str, Any]]:
    """
    Lista licitações cadastradas no Supabase com contagem de itens, com suporte a filtro.
    """
    try:
        query = (
            get_client()
            .table("licitacoes")
            .select("*, itens_licitacao(id, numero_item, descricao, quantidade, valor_referencia, custo_unitario, margem_minima_percent, preco_minimo)")
            .order("criado_em", desc=True)
            .limit(limite)
        )
        if termo_busca:
            termo = f"%{termo_busca}%"
            query = query.or_(f"orgao.ilike.{termo},objeto.ilike.{termo},numero_pregao.ilike.{termo},numero_uasg.ilike.{termo}")
        
        resposta = query.execute()
        return resposta.data or []
    except Exception as exc:
        logger.warning(f"Erro ao listar licitações do Supabase: {exc}")
        return []


def obter_licitacao_com_itens(licitacao_id: str) -> Optional[dict[str, Any]]:
    """
    Obtém uma licitação específica por ID com todos os seus itens associados.
    """
    try:
        resposta = (
            get_client()
            .table("licitacoes")
            .select("*, itens_licitacao(*)")
            .eq("id", licitacao_id)
            .single()
            .execute()
        )
        return resposta.data
    except Exception as exc:
        logger.warning(f"Erro ao obter licitação '{licitacao_id}' do Supabase: {exc}")
        return None



def listar_oportunidades_salvas() -> list[dict[str, Any]]:
    """Retorna a lista de todas as oportunidades salvas no Supabase ou localmente."""
    try:
        resposta = (
            get_client()
            .table("oportunidades_salvas")
            .select("*")
            .order("salvo_em", desc=True)
            .execute()
        )
        if resposta.data:
            return resposta.data
    except Exception as exc:
        logger.warning(f"Não foi possível listar oportunidades do Supabase (retornando locais): {exc}")

    return _OPORTUNIDADES_MEMORIA_LOCAL


def atualizar_status_licitacao(licitacao_id: str, status_analise: str, campos_extras: Optional[dict[str, Any]] = None) -> bool:
    """
    Padroniza e atualiza o status de fluxo de uma licitação na tabela licitacoes.
    Status suportados no fluxo JRBC TECH:
      - 'radar' / 'pendente': Oportunidades captadas no Dashboard
      - 'triado_servico': Oportunidades na aba Serviços (mão de obra pura)
      - 'triado_misto': Oportunidades na aba Serviço com Produtos (peças + serviços)
      - 'aprovado_disputa': Oportunidades precificadas com Stop-Loss e confirmadas para participação
      - 'concluido': Disputas finalizadas
    """
    try:
        dados: dict[str, Any] = {
            "status_analise": status_analise,
            "atualizado_em": datetime.now(timezone.utc).isoformat(),
        }
        if campos_extras:
            dados.update(campos_extras)
        client = get_client()
        # 1. Tenta atualizar por id primário
        try:
            res = client.table("licitacoes").update(dados).eq("id", licitacao_id).execute()
            if res.data:
                logger.info(f"✅ Licitação '{licitacao_id}' movida para status '{status_analise}'.")
                return True
        except Exception:
            pass

        # 2. Tenta por numero_controle_pncp
        try:
            res_pncp = client.table("licitacoes").update(dados).eq("numero_controle_pncp", licitacao_id).execute()
            if res_pncp.data:
                logger.info(f"✅ Licitação (PNCP '{licitacao_id}') movida para status '{status_analise}'.")
                return True
        except Exception:
            pass

        # 3. Tenta por numero_pregao
        try:
            res_pregao = client.table("licitacoes").update(dados).eq("numero_pregao", licitacao_id).execute()
            if res_pregao.data:
                logger.info(f"✅ Licitação (Pregão '{licitacao_id}') movida para status '{status_analise}'.")
                return True
        except Exception:
            pass

        logger.warning(f"Nenhum registro encontrado no Supabase para licitacao_id='{licitacao_id}'")
        return False
    except Exception as exc:
        logger.warning(f"Erro ao atualizar status da licitação '{licitacao_id}': {exc}")
        return False


def listar_licitacoes_por_status(status_analise: str, limite: int = 100) -> list[dict[str, Any]]:
    """
    Lista licitações filtradas pelo status do fluxo JRBC TECH.
    """
    try:
        res = (
            get_client()
            .table("licitacoes")
            .select("*, itens_licitacao(*)")
            .eq("status_analise", status_analise)
            .order("atualizado_em", desc=True)
            .limit(limite)
            .execute()
        )
        return res.data or []
    except Exception as exc:
        logger.warning(f"Erro ao listar licitações com status '{status_analise}': {exc}")
        return []


def aprovar_licitacao_disputa(
    licitacao_id: str,
    stop_loss_total: float,
    itens: Optional[list[dict[str, Any]]] = None,
    data_sessao: Optional[str] = None
) -> bool:
    """
    Grava a aprovação definitiva de um certame para disputa:
    - Atualiza status_analise = 'aprovado_disputa'
    - Grava stop_loss e margem_lucro no cabeçalho
    - Atualiza os stop_loss de cada item em itens_licitacao
    """
    try:
        client = get_client()
        dados_lic: dict[str, Any] = {
            "status_analise": "aprovado_disputa",
            "stop_loss": stop_loss_total,
            "atualizado_em": datetime.now(timezone.utc).isoformat(),
        }
        if data_sessao:
            dados_lic["data_hora_sessao"] = data_sessao

        client.table("licitacoes").update(dados_lic).eq("id", licitacao_id).execute()

        # Atualiza itens se fornecidos
        if itens:
            for it in itens:
                it_id = it.get("id")
                if not it_id:
                    continue
                preco_min = it.get("piso_preco") or it.get("stop_loss") or it.get("preco_minimo")
                custo_u = it.get("custo_real") or it.get("custo_unitario")
                margem = it.get("margem") or it.get("margem_minima_percent")
                upd_item: dict[str, Any] = {}
                if preco_min is not None:
                    upd_item["preco_minimo"] = float(preco_min)
                if custo_u is not None:
                    upd_item["custo_unitario"] = float(custo_u)
                if margem is not None:
                    upd_item["margem_minima_percent"] = float(margem)
                if upd_item:
                    try:
                        client.table("itens_licitacao").update(upd_item).eq("id", it_id).execute()
                    except Exception as exc_it:
                        logger.warning(f"Erro ao atualizar item {it_id}: {exc_it}")

        logger.info(f"🎯 Licitação '{licitacao_id}' APROVADA PARA DISPUTA (Stop-loss: R$ {stop_loss_total:,.2f})")
        return True
    except Exception as exc:
        logger.error(f"Erro ao aprovar licitação para disputa: {exc}")
        return False
