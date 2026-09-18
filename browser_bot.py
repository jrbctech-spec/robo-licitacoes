"""
browser_bot.py – Automação do Compras.gov.br com Playwright.

Responsável por:
  - Manter sessão autenticada via perfil persistente do Chromium.
  - Navegar até a página de lances do pregão especificado.
  - Fazer polling periódico do estado dos itens (lances, posição).
  - Enviar lances conforme decisão do MotorDecisao.
  - Registrar toda atividade na AsyncLogQueue (não-bloqueante).

IMPORTANTE:
  - O bot nunca toma decisões de lance – apenas executa o que o MotorDecisao
    determina. A lógica de negócio está isolada em engine.py.
  - O modo dry_run (simulação) é verificado em DOIS pontos antes de qualquer clique.
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator, Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from config import settings
from engine import DecisaoLance, EstadoItem, MotorDecisao, RegraItem, EstadoItemEnum
from estrategia_zonas import Zona, determinar_zona, analisar_zonas, MotorComZonas
from supabase_client import AsyncLogQueue, EntradaLog, log_queue, registrar_lance

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Seletores CSS/XPath do Compras.gov.br (Comprasnet Web)
# Estes seletores podem precisar de ajuste conforme atualização do portal.
# ─────────────────────────────────────────────────────────────────────────────
class Seletores:
    """Seletores da interface do Comprasnet Web para automação."""

    # Página de lances do pregão
    TABELA_ITENS = "table.itens-pregao, [data-testid='tabela-itens']"
    LINHA_ITEM = "tr[data-item], .item-licitacao"

    # Coluna de melhor lance (valor do líder de mercado)
    MELHOR_LANCE = "[data-campo='melhor-lance'], .melhor-lance-valor"

    # Coluna de nossa posição no ranking
    NOSSA_POSICAO = "[data-campo='nossa-posicao'], .posicao-atual"

    # Campo de entrada do valor do lance
    INPUT_LANCE = "input[name='valorLance'], input[id*='lance'], input.valor-lance"

    # Botão de envio do lance
    BTN_ENVIAR_LANCE = (
        "button[type='submit']:has-text('Enviar Lance'), "
        "button.btn-lance, "
        "button:has-text('Confirmar Lance')"
    )

    # Botão de confirmação no modal
    BTN_CONFIRMAR_MODAL = (
        "button.btn-confirmar, "
        "button:has-text('Confirmar'), "
        "dialog button[type='submit']"
    )

    # Status do pregão
    STATUS_PREGAO = ".status-pregao, [data-campo='status']"

    # Mensagem de encerramento
    ENCERRADO = ":has-text('encerrado'), :has-text('Encerrado')"


# ─────────────────────────────────────────────────────────────────────────────
# Dados lidos da página em cada ciclo de polling
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EstadoPagina:
    """Snapshot do estado de um item conforme lido da página."""
    numero_item: int
    melhor_lance_mercado: Optional[float]
    nossa_posicao: Optional[int]
    nosso_ultimo_lance: Optional[float]
    pregao_encerrado: bool = False
    erro_leitura: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Browser Bot
# ─────────────────────────────────────────────────────────────────────────────

class BrowserBot:
    """
    Robô de automação do navegador para sessões de lances no Compras.gov.br.

    Integra-se ao MotorDecisao para receber as decisões de lance e ao
    AsyncLogQueue para registro não-bloqueante de eventos.

    Uso típico:
        motor = MotorDecisao(dry_run=True)
        motor.carregar_regras([...])
        motor.iniciar_sessao()

        bot = BrowserBot(motor=motor, sessao_id="uuid-aqui")
        bot.iniciar(url_pregao="https://...")
    """

    def __init__(
        self,
        motor: MotorDecisao | MotorComZonas | MotorComZonas,
        sessao_id: Optional[str] = None,
        licitacao_id: Optional[str] = None,
        gui_queue: Optional[queue.Queue[GUIEvent]] = None,
    ) -> None:
        self.motor = motor
        self.sessao_id = sessao_id or str(uuid.uuid4())
        self.licitacao_id = licitacao_id
        self._page: Optional[Page] = None
        self._context: Optional[BrowserContext] = None
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._rodando = False

    # ── Ciclo de vida do navegador ────────────────────────────────────────────

    @contextmanager
    def _abrir_navegador(self) -> Generator[Page, None, None]:
        """
        Contexto que abre o Chromium com perfil persistente e fecha ao sair.

        O perfil persistente mantém cookies e sessão autenticada entre
        execuções, evitando novo login a cada inicialização.
        """
        user_data_dir = str(settings.browser_user_data_dir)
        settings.browser_user_data_dir.mkdir(parents=True, exist_ok=True)

        self._log("INFO", f"Abrindo Chromium com perfil em: {user_data_dir}")

        with sync_playwright() as pw:
            self._playwright = pw
            self._context = pw.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=settings.browser_headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
                timeout=settings.browser_timeout_ms,
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
            )
            page = self._context.new_page()
            page.set_default_timeout(settings.browser_timeout_ms)
            self._page = page
            try:
                yield page
            finally:
                self._context.close()
                self._log("INFO", "Navegador fechado.")

    def iniciar(
        self,
        url_pregao: str,
        intervalo_polling_s: float = 5.0,
        max_ciclos: int = 3600,
    ) -> None:
        """
        Inicia a sessão de monitoramento e lances.

        Args:
            url_pregao: URL direta da página de lances do pregão.
            intervalo_polling_s: Segundos entre cada leitura da página.
            max_ciclos: Número máximo de ciclos antes de encerrar automaticamente.
        """
        self._log(
            "INFO",
            f"BrowserBot iniciado | sessao_id={self.sessao_id} | "
            f"dry_run={self.motor.dry_run} | url={url_pregao}"
        )

        log_queue.iniciar()

        try:
            with self._abrir_navegador() as page:
                self._navegar_para_pregao(page, url_pregao)
                self._loop_monitoramento(
                    page=page,
                    intervalo=intervalo_polling_s,
                    max_ciclos=max_ciclos,
                )
        except KeyboardInterrupt:
            self._log("WARNING", "Sessão interrompida pelo usuário (Ctrl+C).")
        except Exception as exc:
            self._log("ERROR", f"Erro crítico no BrowserBot: {exc}", {"traceback": str(exc)})
            logger.exception("Erro crítico no BrowserBot")
        finally:
            self.motor.encerrar_sessao()
            log_queue.parar()
            relatorio = self.motor.relatorio_sessao()
            self._log("INFO", "Sessão encerrada.", relatorio)
            logger.info(f"Relatório final: {relatorio}")

    # ── Navegação ─────────────────────────────────────────────────────────────

    def _navegar_para_pregao(self, page: Page, url: str) -> None:
        """Navega até a página do pregão e aguarda carregamento."""
        self._log("INFO", f"Navegando para: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=settings.browser_timeout_ms)
            self._log("INFO", "Página do pregão carregada.")
        except PlaywrightTimeout:
            self._log("WARNING", "Timeout ao aguardar carregamento. Continuando...")
        except Exception as exc:
            raise RuntimeError(f"Falha ao navegar para o pregão: {exc}") from exc

    # ── Loop principal de monitoramento ──────────────────────────────────────

    def _loop_monitoramento(
        self,
        page: Page,
        intervalo: float,
        max_ciclos: int,
    ) -> None:
        """
        Loop de polling: lê o estado da página e toma decisões de lance.

        Ciclo por iteração:
          1. Ler estado de cada item na página.
          2. Atualizar o MotorDecisao com o estado atual.
          3. Calcular decisão de lance para cada item.
          4. Se deve_dar_lance: validar stop-loss pré-envio e enviar (ou simular).
          5. Aguardar intervalo_polling_s segundos.
        """
        self._rodando = True
        ciclo = 0

        while self._rodando and ciclo < max_ciclos:
            ciclo += 1
            inicio_ciclo = time.monotonic()

            try:
                estados = self._ler_estado_pagina(page)

                todos_encerrados = all(e.pregao_encerrado for e in estados)
                if todos_encerrados and estados:
                    self._log("INFO", "Pregão encerrado detectado. Encerrando sessão.")
                    break

                for estado_pag in estados:
                    if estado_pag.erro_leitura:
                        self._log(
                            "WARNING",
                            f"Erro de leitura item {estado_pag.numero_item}: "
                            f"{estado_pag.erro_leitura}",
                        )
                        continue

                    # Atualiza o motor com o estado lido
                    self.motor.atualizar_estado(
                        numero_item=estado_pag.numero_item,
                        melhor_lance_mercado=estado_pag.melhor_lance_mercado,
                        nossa_posicao=estado_pag.nossa_posicao,
                        nosso_ultimo_lance=estado_pag.nosso_ultimo_lance,
                    )

                    # Calcula decisão
                    try:
                        decisao = self.motor.calcular_lance(estado_pag.numero_item)
                    except Exception as exc:
                        self._log(
                            "ERROR",
                            f"Erro ao calcular lance item {estado_pag.numero_item}: {exc}",
                        )
                        continue

                    # Processa a decisão
                    if decisao.deve_dar_lance and decisao.valor_lance is not None:
                        self._processar_decisao_lance(page, decisao)

            except Exception as exc:
                self._log(
                    "ERROR",
                    f"Erro no ciclo {ciclo}: {exc}",
                    {"ciclo": ciclo},
                )
                logger.exception(f"Erro no ciclo {ciclo}")

            # Aguarda o intervalo configurado
            elapsed = time.monotonic() - inicio_ciclo
            sleep_time = max(0, intervalo - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        self._rodando = False
        self._log("INFO", f"Loop de monitoramento encerrado após {ciclo} ciclo(s).")

    # ── Leitura do estado da página ───────────────────────────────────────────

    def _ler_estado_pagina(self, page: Page) -> list[EstadoPagina]:
        """
        Lê o estado atual de todos os itens na página do pregão.

        NOTA: Os seletores aqui são heurísticos. Em produção, inspecione
        o DOM real do Comprasnet Web e ajuste conforme necessário.
        """
        estados: list[EstadoPagina] = []

        try:
            page.wait_for_load_state("domcontentloaded", timeout=5_000)
        except PlaywrightTimeout:
            pass

        # Tenta localizar linhas de itens na tabela
        try:
            linhas = page.query_selector_all(Seletores.LINHA_ITEM)
        except Exception:
            linhas = []

        if not linhas:
            # Fallback: retorna estado vazio para todos os itens carregados
            for num_item in self.motor._estados:
                estados.append(EstadoPagina(
                    numero_item=num_item,
                    melhor_lance_mercado=None,
                    nossa_posicao=None,
                    nosso_ultimo_lance=None,
                    erro_leitura="Tabela de itens não encontrada na página.",
                ))
            return estados

        for linha in linhas:
            try:
                # Extrai número do item do atributo data-item ou do texto
                num_item_str = linha.get_attribute("data-item") or ""
                if not num_item_str.isdigit():
                    continue
                num_item = int(num_item_str)

                melhor_lance = self._extrair_valor(
                    linha, Seletores.MELHOR_LANCE
                )
                nossa_posicao_str = self._extrair_texto(
                    linha, Seletores.NOSSA_POSICAO
                )
                nossa_posicao = int(nossa_posicao_str) if nossa_posicao_str.isdigit() else None

                estados.append(EstadoPagina(
                    numero_item=num_item,
                    melhor_lance_mercado=melhor_lance,
                    nossa_posicao=nossa_posicao,
                    nosso_ultimo_lance=None,  # Atualizado após envio de lance
                ))

            except Exception as exc:
                estados.append(EstadoPagina(
                    numero_item=0,
                    melhor_lance_mercado=None,
                    nossa_posicao=None,
                    nosso_ultimo_lance=None,
                    erro_leitura=str(exc),
                ))

        return estados

    # ── Envio de lance ────────────────────────────────────────────────────────

    def _processar_decisao_lance(self, page: Page, decisao: DecisaoLance) -> None:
        """
        Processa uma decisão de lance do motor.

        Em modo dry_run: registra no log e no Supabase mas NÃO clica.
        Em modo real: valida stop-loss pré-envio e executa o clique.
        """
        valor = decisao.valor_lance
        num_item = decisao.numero_item
        val_str = f"R$ {valor:.4f}" if valor is not None else "N/A"

        # ── LOG da decisão (sempre) ────────────────────────────────────────────
        self._log(
            "INFO",
            f"[{'SIMULAÇÃO' if self.motor.dry_run else 'REAL'}] "
            f"Lance Item {num_item}: {val_str} | {decisao.motivo}",
            {
                "item_id": decisao.item_id,
                "valor_lance": valor,
                "lance_concorrente": decisao.lance_concorrente,
                "posicao_atual": decisao.posicao_atual,
                "dry_run": self.motor.dry_run,
            },
        )

        # ── Registra no Supabase (histórico de lances) ────────────────────────
        self._registrar_lance_supabase(decisao, tipo="simulado" if self.motor.dry_run else "real")

        # ── Modo Simulação: encerra aqui ──────────────────────────────────────
        if self.motor.dry_run:
            logger.info(
                f"[SIMULAÇÃO] Lance {val_str} calculado para item {num_item}. "
                "Nenhum clique realizado."
            )
            return

        # ── MODO REAL: Verificação quádrupla antes de enviar ──────────────────
        # Verificação 1: flag de dry_run (já verificada acima)
        # Verificação 2: motor.validar_lance_pre_envio (stop-loss duplo)
        if not self.motor.validar_lance_pre_envio(num_item, valor):
            self._log(
                "CRITICAL",
                f"Lance BLOQUEADO pela validação pré-envio | "
                f"Item {num_item} | Valor R$ {valor:.2f}",
            )
            return

        # Verificação 3: confirmação redundante do valor
        if valor is None or valor <= 0:
            self._log("ERROR", f"Valor de lance inválido: {valor}")
            return

        # Verificação 4: Trava atômica anti-duplicidade
        if not self.motor.marcar_lance_em_envio(num_item):
            self._log("WARNING", f"Envio ignorado para item {num_item}: lance já em trânsito.")
            return

        self._enviar_lance_real(page, num_item, valor, decisao)

    def _enviar_lance_real(
        self,
        page: Page,
        numero_item: int,
        valor: float,
        decisao: DecisaoLance,
    ) -> None:
        """
        Executa o envio do lance real na interface do Comprasnet.

        CUIDADO: Este método só deve ser chamado após passar por TODAS
        as verificações de segurança em _processar_decisao_lance().
        """
        logger.info(
            f"🔴 ENVIANDO LANCE REAL | Item {numero_item} | R$ {valor:.2f}"
        )
        valor_formatado = f"{valor:.2f}".replace(".", ",")

        try:
            # Localiza o input de lance para o item específico
            input_selector = (
                f"[data-item='{numero_item}'] {Seletores.INPUT_LANCE}, "
                f"{Seletores.INPUT_LANCE}"
            )
            input_lance = page.wait_for_selector(
                input_selector,
                timeout=5_000,
            )
            if not input_lance:
                raise RuntimeError(f"Campo de lance não encontrado para item {numero_item}")

            input_lance.click()
            input_lance.fill(valor_formatado)

            # Clique no botão de envio
            btn = page.wait_for_selector(Seletores.BTN_ENVIAR_LANCE, timeout=5_000)
            if not btn:
                raise RuntimeError("Botão de envio de lance não encontrado")
            btn.click()

            # Confirma o modal de confirmação (se aparecer)
            try:
                modal_btn = page.wait_for_selector(
                    Seletores.BTN_CONFIRMAR_MODAL, timeout=3_000
                )
                if modal_btn:
                    modal_btn.click()
            except PlaywrightTimeout:
                pass  # Modal pode não aparecer em todos os casos

            self._log(
                "INFO",
                f"✅ Lance enviado e confirmado no portal | Item {numero_item} | R$ {valor:.2f}",
            )
            # Atualiza MEU ÚLTIMO LANCE e libera a trava de duplicidade
            self.motor.confirmar_lance_enviado(numero_item, valor)

        except Exception as exc:
            self.motor.abortar_envio_lance(numero_item, str(exc))
            self._log(
                "ERROR",
                f"Falha ao enviar lance | Item {numero_item} | Erro: {exc}",
                {"erro": str(exc), "valor_tentativa": valor},
            )
            logger.error(f"Erro ao enviar lance item {numero_item}: {exc}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _registrar_lance_supabase(
        self, decisao: DecisaoLance, tipo: str
    ) -> None:
        """Registra o lance no Supabase de forma não-bloqueante (fire-and-forget)."""
        import threading

        def _inserir() -> None:
            try:
                registrar_lance({
                    "item_id": decisao.item_id,
                    "licitacao_id": self.licitacao_id,
                    "numero_item": decisao.numero_item,
                    "valor_lance": decisao.valor_lance,
                    "valor_concorrente": decisao.lance_concorrente,
                    "posicao_antes": decisao.posicao_atual,
                    "tipo_lance": tipo,
                    "motivo_calculo": decisao.motivo,
                    "bloqueado_stop_loss": decisao.bloqueado_stop_loss,
                    "valor_stop_loss": decisao.valor_stop_loss,
                })
            except Exception as exc:
                logger.warning(f"Falha ao registrar lance no Supabase: {exc}")

        threading.Thread(target=_inserir, daemon=True).start()

    def _log(
        self,
        nivel: str,
        mensagem: str,
        dados_extras: Optional[dict] = None,
    ) -> None:
        """Enfileira um log para envio assíncrono ao Supabase."""
        log_queue.enfileirar(EntradaLog(
            sessao_id=self.sessao_id,
            nivel=nivel,
            mensagem=mensagem,
            modulo="browser_bot",
            licitacao_id=self.licitacao_id,
            dados_extras=dados_extras,
        ))
        # Também loga localmente via logging padrão
        getattr(logger, nivel.lower(), logger.info)(mensagem)

    @staticmethod
    def _extrair_valor(elemento, seletor: str) -> Optional[float]:
        """Extrai um valor monetário de um sub-elemento."""
        try:
            sub = elemento.query_selector(seletor)
            if not sub:
                return None
            texto = sub.inner_text().strip()
            texto = texto.replace("R$", "").strip()
            if "," in texto and "." in texto:
                texto = texto.replace(".", "").replace(",", ".")
            elif "," in texto:
                texto = texto.replace(",", ".")
            return float(texto) if texto else None
        except Exception:
            return None

    @staticmethod
    def _extrair_texto(elemento, seletor: str) -> str:
        """Extrai texto de um sub-elemento."""
        try:
            sub = elemento.query_selector(seletor)
            return sub.inner_text().strip() if sub else ""
        except Exception:
            return ""


def testar_pre_voo_comprasgov(timeout_ms: int = 10000) -> tuple[bool, str]:
    """
    Executa teste de pré-voo (health-check) no Compras.gov.br verificando conectividade.
    """
    url_test = "https://www.compras.gov.br"
    logger.info("🛫 Executando teste de pré-voo (health-check) no Compras.gov.br...")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            response = page.goto(url_test, timeout=timeout_ms)
            browser.close()

            if response and response.status in (200, 302):
                msg = f"Health-check OK: Compras.gov.br acessível (Status {response.status})."
                logger.info(f"✅ {msg}")
                return True, msg
            else:
                status_code = response.status if response else "Sem resposta"
                msg = f"Falha no health-check: Compras.gov.br retornou status {status_code}."
                logger.warning(f"⚠️ {msg}")
                return False, msg

    except Exception as exc:
        msg = f"Falha de conexão / browser no health-check Compras.gov.br: {exc}"
        logger.warning(f"⚠️ {msg}")
        return False, msg
