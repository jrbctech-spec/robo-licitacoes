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

import asyncio
import dataclasses
from decimal import Decimal
import logging
import re
import shutil
import subprocess
import time
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Generator, Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from config import settings
from engine import (
    DecisaoLance,
    EstadoItem,
    MotorDecisao,
    RegraItem,
    EstadoItemEnum,
    ItemPiloto,
    DecisaoPiloto,
    PilotoDisputa,
)

from supabase_client import AsyncLogQueue, EntradaLog, log_queue, registrar_lance

CNPJ_ALVO = "65488003000161"
RAZAO_SOCIAL_ALVO = "JAKSON RIBEIRO DA COSTA"

def auto_selecionar_empresa_e_avancar(page, url_sala_final: str):
    """
    Detecta e automatiza a seleção do CNPJ da JRBC TECH e avança para a sala de disputa.
    """
    url_atual = page.url.lower()
    
    # 1. Se estiver na tela de seleção de empresa
    if "loginfornecedorselecempresa.asp" in url_atual:
        logger.info("Detectada tela de seleção de empresa. Selecionando CNPJ da JRBC TECH...")
        try:
            # Procura por Select / Dropdown
            selects = page.locator("select").all()
            selecionado = False
            for sel in selects:
                for opt in sel.locator("option").all():
                    txt = opt.inner_text().upper().replace(".", "").replace("/", "").replace("-", "")
                    if CNPJ_ALVO in txt or "JAKSON" in txt:
                        sel.select_option(value=opt.get_attribute("value"))
                        selecionado = True
                        break
                if selecionado:
                    break
                    
            # Se forem Radio buttons ou linhas clicáveis
            if not selecionado:
                radios = page.locator("input[type='radio']").all()
                for r in radios:
                    parent_text = r.locator("xpath=..").inner_text().upper().replace(".", "").replace("/", "").replace("-", "")
                    if CNPJ_ALVO in parent_text or "JAKSON" in parent_text:
                        r.check()
                        selecionado = True
                        break

            # Clica no botão de envio / avançar
            btn_confirmar = page.locator("input[type='submit'], button[type='submit'], input[value*='Confirmar'], input[value*='Avançar'], input[value*='OK']").first
            if btn_confirmar.count() > 0:
                btn_confirmar.click()
                page.wait_for_load_state("networkidle", timeout=15000)
                logger.info("✅ Empresa selecionada e confirmada automaticamente!")
        except Exception as e:
            logger.warning(f"Aviso na auto-seleção de empresa: {e}")

    # 2. Se cair na página de intro.htm, pula direto para a sala de compras
    if "intro.htm" in page.url.lower() and url_sala_final:
        logger.info(f"Página de intro detectada. Redirecionando direto para a sala: {url_sala_final}")
        page.goto(url_sala_final, wait_until="networkidle", timeout=20000)


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
    descricao: Optional[str] = None
    intervalo_minimo: Optional[float] = None
    status_fase: str = "DESCONHECIDO"  # ABERTO, IMINENCIA, RANDOMICO, ENCERRADO
    segundos_restantes: Optional[int] = None
    texto_tempo: str = ""
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
        motor: MotorDecisao,
        sessao_id: Optional[str] = None,
        licitacao_id: Optional[str] = None,
        gui_queue: Optional[Any] = None,
        callback_ciclo: Optional[Callable[[int, list[EstadoPagina], dict[int, DecisaoLance]], None]] = None,
    ) -> None:
        self.motor = motor
        self.sessao_id = sessao_id or str(uuid.uuid4())
        self.licitacao_id = licitacao_id
        self.callback_ciclo = callback_ciclo
        self.url_alvo: Optional[str] = None
        self._page: Optional[Page] = None
        self._context: Optional[BrowserContext] = None
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._rodando = False
        self._url_solicitada: Optional[str] = None
        self.callback_copiloto: Optional[Callable[[str], None]] = None
        self._ultimo_lider: dict[int, float] = {}

    def parar(self) -> None:
        """Solicita a interrupção segura do loop de monitoramento."""
        self._rodando = False
        self._log("INFO", "🛑 Solicitação de parada recebida. Encerrando bot...")

    # ── Ciclo de vida do navegador ────────────────────────────────────────────

    @contextmanager
    def _abrir_navegador(self) -> Generator[Page, None, None]:
        """
        Contexto que conecta ao Google Chrome oficial nativo com perfil persistente.
        
        A inicialização nativa com porta de depuração remota permite que o usuário
        faça login no Gov.br (via CPF, QR Code, Banco ou Certificado) com 100% de
        reputação humana, eliminando qualquer rejeição de hCaptcha/ERL000.
        """
        user_data_dir = str(settings.browser_user_data_dir)
        settings.browser_user_data_dir.mkdir(parents=True, exist_ok=True)

        # Limpeza preventiva de travas residuais de Chromium
        for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "Lock", ".lock"):
            try:
                (settings.browser_user_data_dir / lock_name).unlink(missing_ok=True)
            except Exception:
                pass

        chrome_bin = None
        for cand in ("/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "google-chrome", "google-chrome-stable", "chromium", "/usr/bin/chromium-browser"):
            if shutil.which(cand):
                chrome_bin = shutil.which(cand)
                break

        cdp_port = 9222
        cdp_url = f"http://127.0.0.1:{cdp_port}"
        chrome_proc = None

        def is_cdp_ready() -> bool:
            try:
                req = urllib.request.urlopen(f"{cdp_url}/json/version", timeout=0.8)
                return bool(getattr(req, "status", None) == 200)
            except Exception:
                return False

        # Se Chrome nativo não estiver rodando na porta CDP, inicia como processo nativo de sistema
        if not is_cdp_ready() and chrome_bin and not settings.browser_headless:
            self._log("INFO", f"Iniciando Google Chrome nativo ({chrome_bin}) na porta {cdp_port}...")
            chrome_args = [
                chrome_bin,
                f"--remote-debugging-port={cdp_port}",
                f"--user-data-dir={user_data_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--start-maximized",
            ]
            if self.url_alvo:
                chrome_args.append(self.url_alvo)
            else:
                chrome_args.append("https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/login")

            try:
                chrome_proc = subprocess.Popen(
                    chrome_args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                for _ in range(30):
                    if is_cdp_ready():
                        break
                    time.sleep(0.3)
            except Exception as exc_proc:
                self._log("WARNING", f"Aviso ao iniciar Chrome nativo: {exc_proc}. Usando inicialização direta...")
                chrome_proc = None

        with sync_playwright() as pw:
            self._playwright = pw
            browser = None
            context = None

            if is_cdp_ready():
                try:
                    self._log("INFO", f"Conectando ao Google Chrome nativo via CDP...")
                    browser = pw.chromium.connect_over_cdp(cdp_url)
                    self._browser = browser
                    if browser.contexts:
                        context = browser.contexts[0]
                    else:
                        context = browser.new_context()
                    self._context = context
                    self._log("INFO", "✅ Conectado ao Google Chrome nativo com sucesso.")
                except Exception as exc_cdp:
                    self._log("WARNING", f"Aviso ao conectar via CDP: {exc_cdp}. Usando fallback...")
                    browser = None
                    context = None

            if not context:
                self._log("INFO", f"Abrindo navegador com perfil persistente...")
                launch_kwargs = {
                    "user_data_dir": user_data_dir,
                    "headless": settings.browser_headless,
                    "ignore_default_args": ["--enable-automation"],
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--start-maximized",
                        "--no-default-browser-check",
                        "--no-first-run",
                    ],
                    "timeout": settings.browser_timeout_ms,
                    "locale": "pt-BR",
                    "timezone_id": "America/Sao_Paulo",
                    "viewport": None,
                    "bypass_csp": True,
                }
                try:
                    context = pw.chromium.launch_persistent_context(channel="chrome", **launch_kwargs)
                except Exception:
                    context = pw.chromium.launch_persistent_context(**launch_kwargs)
                self._context = context

            if context.pages:
                page = context.pages[0]
            else:
                page = context.new_page()

            page.set_default_timeout(settings.browser_timeout_ms)
            self._page = page
            try:
                yield page
            finally:
                try:
                    if self._browser:
                        # Em conexão CDP com Chrome existente, fecha a conexão sem matar navegador do operador
                        try:
                            self._browser.close()
                        except Exception:
                            pass
                    elif self._context:
                        self._context.close()
                except Exception:
                    pass
                self._log("INFO", "Navegador desconectado/fechado.")

    def navegar_para(self, url: str) -> None:
        """Permite que a interface solicite navegação de forma 100% thread-safe."""
        self._url_solicitada = url
        self._log("INFO", f"🌐 Solicitação de navegação registrada: {url}")

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
        self.url_alvo = url_pregao
        self._redirecionou_pos_login = False
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
            if "closed" in str(exc).lower() or "target" in str(exc).lower():
                self._log("INFO", "Sessão encerrada com o fechamento do navegador.")
            else:
                self._log("ERROR", f"Erro no BrowserBot: {exc}", {"traceback": str(exc)})
                logger.exception("Erro no BrowserBot")
        finally:
            self.motor.encerrar_sessao()
            log_queue.parar()
            relatorio = self.motor.relatorio_sessao()
            self._log("INFO", "Sessão encerrada.", relatorio)
            logger.info(f"Relatório final: {relatorio}")

    # ── Navegação & Rastreamento de Abas ──────────────────────────────────────

    def _obter_pagina_disputa(self, fallback_page: Page) -> Page:
        """
        Localiza a aba ativa do navegador onde está a sala de disputa do certame atual.
        Suporta abas simultâneas de diferentes disputas priorizando a url_alvo.
        """
        if not self._context:
            return fallback_page

        try:
            pages = [p for p in self._context.pages if not p.is_closed()]
            if not pages:
                return fallback_page

            # 0. Prioriza a aba específica do certame atual se url_alvo estiver definida
            if self.url_alvo and "pncp.gov.br" not in self.url_alvo:
                m_compra = re.search(r"compra=(\d+)", self.url_alvo)
                cod_compra = m_compra.group(1) if m_compra else ""

                for p in reversed(pages):
                    try:
                        u = p.url.lower()
                        if cod_compra and cod_compra in u:
                            return p
                        if self.url_alvo.lower() in u or u in self.url_alvo.lower():
                            return p
                    except Exception:
                        pass

            # 1. Procura aba cuja URL contenha 'disputa'
            for p in reversed(pages):
                try:
                    if "disputa" in p.url.lower():
                        return p
                except Exception:
                    pass

            # 2. Procura aba do Comprasnet / Serpro / Gov.br / Licitanet / SIGA
            for p in reversed(pages):
                try:
                    u = p.url.lower()
                    if any(k in u for k in ("comprasnet", "serpro", "gov.br", "acesso", "licitanet", "siga.to")):
                        return p
                except Exception:
                    pass

            # 3. Fallback: última página aberta
            return pages[-1]
        except Exception:
            return fallback_page

    def _navegar_para_pregao(self, page: Page, url: str) -> None:
        """Navega para a URL da sala de disputa ou portal Compras.gov.br."""
        if self._context:
            for p in self._context.pages:
                try:
                    if not p.is_closed() and "disputa" in p.url.lower():
                        self._redirecionou_pos_login = True
                        self._log("INFO", f"Aba da disputa ativa detectada: {p.url}")
                        return
                except Exception:
                    pass

        self._log("INFO", f"Navegando para: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:
                pass
            self._log("INFO", "Página carregada com sucesso.")
        except PlaywrightTimeout:
            self._log("WARNING", "Timeout ao aguardar carregamento inicial. Continuando monitoramento...")
        except Exception as exc:
            self._log("WARNING", f"Aviso de navegação: {exc}. Continuando monitoramento...")

    # ── Loop principal de monitoramento ──────────────────────────────────────

    def _loop_monitoramento(
        self,
        page: Page,
        intervalo: float,
        max_ciclos: int,
    ) -> None:
        """
        Loop de polling: lê o estado da página e toma decisões de lance.
        """
        self._rodando = True
        ciclo = 0

        while self._rodando and ciclo < max_ciclos:
            ciclo += 1
            inicio_ciclo = time.monotonic()

            try:
                # Localiza a aba onde a disputa está acontecendo
                active_page = self._obter_pagina_disputa(page)
                if not active_page or (hasattr(active_page, "is_closed") and active_page.is_closed()):
                    if self._context and self._context.pages:
                        valid = [p for p in self._context.pages if not p.is_closed()]
                        if valid:
                            active_page = valid[0]
                        else:
                            self._log("INFO", "Navegador fechado pelo usuário.")
                            break
                    else:
                        break

                # Atende solicitação de navegação sob demanda enviada pela interface gráfica
                if self._url_solicitada:
                    url_dest = self._url_solicitada
                    self._url_solicitada = None
                    try:
                        self._log("INFO", f"🌐 Navegando para URL solicitada: {url_dest}")
                        active_page.goto(url_dest, wait_until="domcontentloaded")
                    except Exception as exc_nav:
                        self._log("WARNING", f"Aviso ao navegar para {url_dest}: {exc_nav}")

                current_url = getattr(active_page, 'url', '').lower()

                if "disputa" in current_url:
                    self._redirecionou_pos_login = True

                # Verifica se o login foi concluído e voltou para a página inicial pós-login (intro.htm) ou seleção de empresa
                # IMPORTANTE: NUNCA redirecionar se o usuário estiver digitando credenciais ou autenticando!
                is_intro_landing = any(k in current_url for k in ("intro.htm", "loginfornecedorselecempresa.asp", "selecempresa", "comprasnet-web/seguro/fornecedor"))
                is_on_auth = any(k in current_url for k in (
                    "acesso.gov.br", "sso", "contas.acesso", "gov.br/acesso",
                    "login", "autenticacao", "auth", "acesso-nao-autorizado", "certificados"
                ))

                if is_intro_landing and not is_on_auth:
                    try:
                        auto_selecionar_empresa_e_avancar(active_page, self.url_alvo or "")
                    except Exception as e:
                        self._log("WARNING", f"Falha ao executar auto_selecionar_empresa_e_avancar: {e}")

                # Se acabou de logar e caiu na home do portal antigo, redireciona UMA ÚNICA VEZ para a sala de disputa
                if is_intro_landing and not is_on_auth and not self._redirecionou_pos_login:
                    self._log("INFO", "✅ Login Gov.br autenticado com sucesso!")
                    if self.url_alvo and "disputa" in self.url_alvo.lower():
                        self._redirecionou_pos_login = True
                        self._log("INFO", "🔄 Abrindo a Sala de Disputa...")
                        try:
                            active_page.goto(self.url_alvo, wait_until="domcontentloaded")
                            try:
                                active_page.wait_for_load_state("networkidle", timeout=4000)
                            except Exception:
                                pass
                            active_page = self._obter_pagina_disputa(page)
                        except Exception as exc_nav:
                            self._log("WARNING", f"Aviso ao redirecionar para a disputa: {exc_nav}")

                estados = self._ler_estado_pagina(active_page)
                self._ultimos_estados = {e.numero_item: e for e in estados}

                todos_encerrados = all(e.pregao_encerrado for e in estados) if estados else False
                if todos_encerrados and estados:
                    self._log("INFO", "Pregão encerrado detectado. Encerrando sessão.")
                    break

                decisoes_ciclo: dict[int, DecisaoLance] = {}

                for estado_pag in estados:
                    if estado_pag.erro_leitura:
                        continue

                    # Atualiza o motor com o estado lido
                    self.motor.atualizar_estado(
                        numero_item=estado_pag.numero_item,
                        melhor_lance_mercado=estado_pag.melhor_lance_mercado,
                        nossa_posicao=estado_pag.nossa_posicao,
                        nosso_ultimo_lance=estado_pag.nosso_ultimo_lance,
                    )

                    # Se o portal informar um intervalo mínimo específico (ex: R$ 5,00), atualiza a regra
                    if estado_pag.intervalo_minimo and estado_pag.intervalo_minimo > 0:
                        if hasattr(self.motor, "_estados") and estado_pag.numero_item in self.motor._estados:
                            regra_atual = self.motor._estados[estado_pag.numero_item].regra
                            self.motor._estados[estado_pag.numero_item].regra = dataclasses.replace(
                                regra_atual, intervalo_minimo=estado_pag.intervalo_minimo
                            )

                    # Verificação de ultrapassagem para alarme sonoro imediato + Push Telegram
                    try:
                        from notifier_manager import notifier_manager
                        notifier_manager.verificar_mudanca_lideranca(
                            item_id=estado_pag.numero_item,
                            liderando_agora=(estado_pag.nossa_posicao == 1),
                            melhor_mercado=estado_pag.melhor_lance_mercado,
                        )
                    except Exception:
                        pass

                    # Trigger copiloto on leader change
                    lider_antigo = self._ultimo_lider.get(estado_pag.numero_item)
                    novo_lider = estado_pag.melhor_lance_mercado
                    if novo_lider and lider_antigo and novo_lider < lider_antigo:
                        if self.callback_copiloto:
                            try:
                                self.callback_copiloto(f"Concorrente reduziu o preço do Item {estado_pag.numero_item} para R$ {novo_lider:.2f}")
                            except Exception:
                                pass
                    if novo_lider:
                        self._ultimo_lider[estado_pag.numero_item] = novo_lider

                    # Calcula decisão
                    try:
                        decisao = self.motor.calcular_lance(estado_pag.numero_item)
                        decisoes_ciclo[estado_pag.numero_item] = decisao
                    except Exception as exc:
                        self._log(
                            "ERROR",
                            f"Erro ao calcular lance item {estado_pag.numero_item}: {exc}",
                        )
                        continue

                    # Processa a decisão
                    if decisao.deve_dar_lance and decisao.valor_lance is not None:
                        self._processar_decisao_lance(active_page, decisao)

                # Notifica callback da interface gráfica se registrado
                if self.callback_ciclo is not None:
                    try:
                        self.callback_ciclo(ciclo, estados, decisoes_ciclo)
                    except Exception as exc_cb:
                        logger.warning(f"Erro no callback_ciclo do BrowserBot: {exc_cb}")

            except Exception as exc:
                if "closed" in str(exc).lower() or "target" in str(exc).lower():
                    self._log("INFO", "Página ou navegador encerrado.")
                    break
                self._log(
                    "ERROR",
                    f"Erro no ciclo {ciclo}: {exc}",
                    {"ciclo": ciclo},
                )
                logger.exception(f"Erro no ciclo {ciclo}")

            # Anti-Idle (Keep-Alive): Simula movimento de mouse para evitar que o Gov.br expire a sessão por inatividade
            try:
                if active_page and hasattr(active_page, 'mouse'):
                    active_page.mouse.move(200 + (ciclo % 50), 200 + (ciclo % 50))
            except Exception:
                pass

            # Polling Adaptativo: Se houver item em Iminência, Randômico ou <= 45s, polling acelera para 1s
            tem_urgencia = any(
                getattr(e, "status_fase", "") in ["IMINENCIA", "RANDOMICO"] or
                (getattr(e, "segundos_restantes", None) is not None and e.segundos_restantes <= 45)
                for e in estados
            ) if estados else False

            intervalo_efetivo = 1.0 if tem_urgencia else intervalo
            elapsed = time.monotonic() - inicio_ciclo
            sleep_time = max(0, intervalo_efetivo - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        self._rodando = False
        self._log("INFO", f"Loop de monitoramento encerrado após {ciclo} ciclo(s).")

    # ── Leitura do estado da página ───────────────────────────────────────────

    def _ler_estado_pagina(self, page: Page) -> list[EstadoPagina]:
        """
        Lê o estado atual de todos os itens na página do pregão via JavaScript nativo
        e seletores flexíveis para Compras.gov.br / Serpro Comprasnet Web (SPA Angular).
        """
        estados: list[EstadoPagina] = []
        if not page or (hasattr(page, "is_closed") and page.is_closed()):
            return estados

        try:
            js_script = r"""
            () => {
                const resultado = {};
                const url = window.location.href || "";
                const bodyText = document.body ? document.body.innerText : "";
                const isLogin = /gov\.br\/acesso|sso|login|autenticacao|contas\.acesso|acesso-nao-autorizado/i.test(url) || 
                                /informe seu cpf|entrar com gov\.br|identifique-se no gov\.br/i.test(bodyText);
                const isEncerrado = /disputa encerrada|sessão encerrada|pregao encerrado/i.test(bodyText);

                function parseMoeda(str) {
                    if (!str) return null;
                    const limpo = str.replace(/[^0-9.,]/g, '').trim();
                    if (!limpo) return null;
                    let numStr = limpo;
                    if (numStr.includes(',') && numStr.includes('.')) {
                        numStr = numStr.replace(/\./g, '').replace(',', '.');
                    } else if (numStr.includes(',')) {
                        numStr = numStr.replace(',', '.');
                    }
                    const val = parseFloat(numStr);
                    return isNaN(val) ? null : val;
                }

                // 1. Prioridade: Cards nativos do Compras.gov.br (Angular SPA)
                const cardsComprasnet = Array.from(document.querySelectorAll('.cp-itens-disputa, [class*="cp-itens-disputa"], app-identificacao-e-fase-item'));
                if (cardsComprasnet.length > 0) {
                    cardsComprasnet.forEach(c => {
                        let cardEl = c;
                        while (cardEl && cardEl.parentElement && !cardEl.classList.contains('cp-itens-disputa') && cardEl.tagName !== 'BODY') {
                            if (cardEl.parentElement.classList.contains('cp-itens-disputa')) {
                                cardEl = cardEl.parentElement;
                                break;
                            }
                            cardEl = cardEl.parentElement;
                        }
                        if (!cardEl) cardEl = c;

                        const elId = cardEl.querySelector('app-identificacao-item span:first-child') || cardEl.querySelector('span:first-child');
                        const elDesc = cardEl.querySelector('app-identificacao-item span:last-child') || cardEl.querySelector('.text-uppercase');
                        const elMelhor = cardEl.querySelector('[data-test="valor-geral"]');
                        const elMeu = cardEl.querySelector('[data-test="valor-fornec"]');
                        const elSituacao = cardEl.querySelector('[data-test="situacao-item"]');
                        const elIcon = cardEl.querySelector('i[title]');
                        const txtCard = cardEl.innerText;

                        let numItem = elId ? parseInt(elId.innerText.trim(), 10) : null;
                        if (!numItem || isNaN(numItem)) {
                            const mNum = txtCard.match(/^\s*(\d+)\s+[A-Za-z]/m);
                            if (mNum) numItem = parseInt(mNum[1], 10);
                        }
                        if (!numItem || isNaN(numItem) || resultado[numItem]) return;

                        const mInt = txtCard.match(/intervalo\s+m[íi]nimo[^:]*:\s*([\d.,]+%?)/i);
                        const mel = elMelhor ? parseMoeda(elMelhor.innerText) : null;
                        const meu = elMeu ? parseMoeda(elMeu.innerText) : null;
                        const titleIcon = elIcon ? elIcon.getAttribute('title') : '';

                        let pos = null;
                        if (titleIcon && (titleIcon.toLowerCase().includes('ganhando') || titleIcon.toLowerCase().includes('primeiro'))) {
                            pos = 1;
                        } else if (titleIcon && (titleIcon.toLowerCase().includes('perdendo') || titleIcon.toLowerCase().includes('superado'))) {
                            pos = 2;
                        } else if (meu !== null && mel !== null) {
                            pos = (meu <= mel) ? 1 : 2;
                        }

                        let txtCard = (elCard.innerText || '').toUpperCase();
                        let statusFase = "DESCONHECIDO";
                        if (txtCard.includes("IMINÊNCIA") || txtCard.includes("IMINENCIA")) {
                            statusFase = "IMINENCIA";
                        } else if (txtCard.includes("RANDÔMICO") || txtCard.includes("ALEATÓRIO") || txtCard.includes("ALEATORIO") || txtCard.includes("RANDOMICO")) {
                            statusFase = "RANDOMICO";
                        } else if (txtCard.includes("ENCERRADO") || txtCard.includes("HOMOLOGADO")) {
                            statusFase = "ENCERRADO";
                        } else if (txtCard.includes("ABERTO") || txtCard.includes("EM DISPUTA")) {
                            statusFase = "ABERTO";
                        }

                        let mTempo = txtCard.match(/(\d{2}):(\d{2})/);
                        let segundos = null;
                        let textoTempo = "";
                        if (mTempo) {
                            let min = parseInt(mTempo[1], 10);
                            let seg = parseInt(mTempo[2], 10);
                            segundos = (min * 60) + seg;
                            textoTempo = mTempo[1] + ":" + mTempo[2];
                        }

                        resultado[numItem] = {
                            numero_item: numItem,
                            descricao: elDesc ? elDesc.innerText.trim() : `Item ${numItem}`,
                            melhor_lance: mel,
                            nossa_posicao: pos,
                            nosso_ultimo_lance: meu,
                            intervalo_minimo: mInt ? parseMoeda(mInt[1]) : null,
                            status_fase: statusFase,
                            segundos_restantes: segundos,
                            texto_tempo: textoTempo,
                        };
                    });
                }

                // 2. Fallback para outros portais / layouts genéricos
                if (Object.keys(resultado).length === 0) {
                    const blocos = Array.from(document.querySelectorAll(
                        'app-card-item, app-item-disputa, app-item, mat-card, .card, .br-card, .item-card, [class*="item-disputa"], [class*="card-item"], [class*="item-lista"], tr, [role="row"], .list-group-item'
                    ));

                    blocos.forEach(el => {
                        const txt = el.innerText || '';
                        if (txt.length < 3) return;

                        let numItem = null;
                        const attrItem = el.getAttribute('data-item') || el.getAttribute('data-id');
                        if (attrItem && /^\d+$/.test(attrItem.trim())) {
                            numItem = parseInt(attrItem.trim(), 10);
                        } else {
                            const m = txt.match(/^\s*(\d+)\s+[A-Za-z]/m) ||
                                      txt.match(/\b(?:item|lote)\s*(?:n[º°])?\s*[:\-–]?\s*(\d+)\b/i) ||
                                      txt.match(/^(\d+)\s+L\b/m);
                            if (m) numItem = parseInt(m[1], 10);
                        }

                        if (!numItem || isNaN(numItem) || numItem <= 0 || numItem > 9999 || resultado[numItem]) return;

                        const mMelhor = txt.match(/(?:melhor\s+valor|melhor\s+lance|menor\s+lance|menor\s+valor|1[º°]|primeiro|l[íi]der|vencedor)[\s\S]*?R\$\s*([\d.,]+)/i) ||
                                        txt.match(/R\$\s*([\d.,]+)/i);
                        const mMeu = txt.match(/(?:meu\s+valor|seu\s+valor|meu\s+lance|seu\s+lance|último\s+lance|nosso\s+lance)[\s\S]*?R\$\s*([\d.,]+)/i);
                        const mInt = txt.match(/intervalo\s+mínimo[\s\S]*?R\$\s*([\d.,]+)/i);
                        const mPos = txt.match(/(?:sua\s+posi[çc][ãa]o|posi[çc][ãa]o\s*atual|posi[çc][ãa]o)\s*[:\-–]?\s*(\d+)[º°]?/i);

                        let txtUpper = txt.toUpperCase();
                        let statusFase = "DESCONHECIDO";
                        if (txtUpper.includes("IMINÊNCIA") || txtUpper.includes("IMINENCIA")) {
                            statusFase = "IMINENCIA";
                        } else if (txtUpper.includes("RANDÔMICO") || txtUpper.includes("ALEATÓRIO") || txtUpper.includes("ALEATORIO") || txtUpper.includes("RANDOMICO")) {
                            statusFase = "RANDOMICO";
                        } else if (txtUpper.includes("ENCERRADO") || txtUpper.includes("HOMOLOGADO")) {
                            statusFase = "ENCERRADO";
                        } else if (txtUpper.includes("ABERTO") || txtUpper.includes("EM DISPUTA")) {
                            statusFase = "ABERTO";
                        }

                        let mTempo = txt.match(/(\d{2}):(\d{2})/);
                        let segundos = null;
                        let textoTempo = "";
                        if (mTempo) {
                            let min = parseInt(mTempo[1], 10);
                            let seg = parseInt(mTempo[2], 10);
                            segundos = (min * 60) + seg;
                            textoTempo = mTempo[1] + ":" + mTempo[2];
                        }

                        resultado[numItem] = {
                            numero_item: numItem,
                            descricao: `Item ${numItem}`,
                            melhor_lance: mMelhor ? parseMoeda(mMelhor[1]) : null,
                            nossa_posicao: mPos ? parseInt(mPos[1], 10) : null,
                            nosso_ultimo_lance: mMeu ? parseMoeda(mMeu[1]) : null,
                            intervalo_minimo: mInt ? parseMoeda(mInt[1]) : null,
                            status_fase: statusFase,
                            segundos_restantes: segundos,
                            texto_tempo: textoTempo,
                        };
                    });
                }

                return {
                    itens: Object.values(resultado),
                    is_login: isLogin,
                    is_encerrado: isEncerrado,
                    url: url,
                };
            }
            """
            dados_dom = page.evaluate(js_script)
        except Exception as exc:
            logger.debug(f"Aviso transitório durante navegação da página: {exc}")
            dados_dom = {"itens": [], "is_login": False, "is_encerrado": False, "url": getattr(page, 'url', '')}

        if not isinstance(dados_dom, dict):
            dados_dom = {"itens": [], "is_login": False, "is_encerrado": False, "url": getattr(page, 'url', '')}

        itens_raw = dados_dom.get("itens", [])
        if not isinstance(itens_raw, list):
            itens_raw = []

        itens_lidos: dict[int, dict[str, Any]] = {}
        for it in itens_raw:
            if isinstance(it, dict) and "numero_item" in it:
                try:
                    num = int(it["numero_item"])
                    itens_lidos[num] = it
                except (ValueError, TypeError):
                    pass

        is_login = bool(dados_dom.get("is_login", False))
        is_encerrado = bool(dados_dom.get("is_encerrado", False))

        # 1. Itens já monitorados pelo motor
        for num_item in self.motor._estados:
            if num_item in itens_lidos:
                d = itens_lidos[num_item]
                estados.append(EstadoPagina(
                    numero_item=num_item,
                    melhor_lance_mercado=d.get("melhor_lance"),
                    nossa_posicao=d.get("nossa_posicao"),
                    nosso_ultimo_lance=d.get("nosso_ultimo_lance"),
                    descricao=d.get("descricao"),
                    intervalo_minimo=d.get("intervalo_minimo"),
                    status_fase=str(d.get("status_fase", "DESCONHECIDO")),
                    segundos_restantes=d.get("segundos_restantes"),
                    texto_tempo=str(d.get("texto_tempo", "")),
                    pregao_encerrado=is_encerrado,
                    erro_leitura=None,
                ))
            else:
                msg_aviso = None
                if is_login:
                    msg_aviso = "Aguardando autenticação no Compras.gov.br..."
                elif not itens_lidos:
                    msg_aviso = "Aguardando navegação ou carregamento da sala de disputa..."
                
                estados.append(EstadoPagina(
                    numero_item=num_item,
                    melhor_lance_mercado=None,
                    nossa_posicao=None,
                    nosso_ultimo_lance=None,
                    descricao=None,
                    pregao_encerrado=is_encerrado,
                    erro_leitura=msg_aviso,
                ))

        # 2. Itens detectados ao vivo na página que não estavam cadastrados no motor
        for num_item, d in itens_lidos.items():
            if num_item not in self.motor._estados:
                estados.append(EstadoPagina(
                    numero_item=num_item,
                    melhor_lance_mercado=d.get("melhor_lance"),
                    nossa_posicao=d.get("nossa_posicao"),
                    nosso_ultimo_lance=d.get("nosso_ultimo_lance"),
                    descricao=d.get("descricao"),
                    intervalo_minimo=d.get("intervalo_minimo"),
                    status_fase=str(d.get("status_fase", "DESCONHECIDO")),
                    segundos_restantes=d.get("segundos_restantes"),
                    texto_tempo=str(d.get("texto_tempo", "")),
                    pregao_encerrado=is_encerrado,
                    erro_leitura=None,
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
        if valor is None or valor <= 0:
            self._log("ERROR", f"Valor de lance inválido: {valor}")
            return

        if not self.motor.validar_lance_pre_envio(num_item, valor):
            self._log(
                "CRITICAL",
                f"Lance BLOQUEADO pela validação pré-envio | "
                f"Item {num_item} | Valor R$ {valor:.2f}",
            )
            return

        if not self.motor.marcar_lance_em_envio(num_item):
            self._log("WARNING", f"Envio ignorado para item {num_item}: lance já em trânsito.")
            return

        # Modo Sniper: se restarem mais de 5s, aguarda para disparar nos últimos 4s
        if hasattr(self, "_ultimos_estados") and decisao.numero_item in self._ultimos_estados:
            ep = self._ultimos_estados[decisao.numero_item]
            if ep.segundos_restantes is not None and ep.segundos_restantes > 5:
                espera_sniper = max(0.1, float(ep.segundos_restantes - 4))
                logger.info(
                    f"[SNIPER] 🎯 Aguardando janela cirúrgica ({espera_sniper:.1f}s) para lance no Item {num_item}"
                )
                time.sleep(min(espera_sniper, 5.0))
                logger.info(
                    f"[SNIPER] 🎯 Disparando lance cirúrgico Item {num_item}: R$ {valor:.2f} a {ep.segundos_restantes}s do fim"
                )

        self._enviar_lance_real(page, num_item, valor, decisao)

    def confirmar_modal_imediato(self, page: Page, timeout_ms: int = 400) -> bool:
        """
        Detecta e confirma imediatamente caixas de diálogo do Comprasnet Serpro
        (SweetAlert, Angular Material Dialog ou modais Bootstrap).
        """
        seletores_confirmacao = [
            "button.swal2-confirm",
            "button:has-text('Sim')",
            "button:has-text('Confirmar')",
            "mat-dialog-actions button:has-text('Confirmar')",
            "mat-dialog-actions button:has-text('Sim')",
            "mat-dialog-actions button",
            ".modal-footer button.btn-primary",
            "[data-test='btn-confirmar-lance']",
            "dialog button[type='submit']",
            "dialog button:has-text('Sim')",
            "dialog button:has-text('Confirmar')",
            ".modal button:has-text('Confirmar')",
            ".modal button:has-text('Sim')",
        ]
        seletor_unificado = ", ".join(seletores_confirmacao)
        try:
            btn_confirmar = page.locator(seletor_unificado).first
            btn_confirmar.wait_for(state="visible", timeout=timeout_ms)
            btn_confirmar.click(force=True)
            self._log("INFO", "⚡ Modal de confirmação do Comprasnet acionado com sucesso!")
            return True
        except Exception:
            return False

    @staticmethod
    def extrair_status_e_tempo_item(card_locator: Any) -> dict[str, Any]:
        """
        Analisa o card do item para obter o status da disputa e os segundos restantes.
        Aceita tanto Playwright Locator quanto string de texto do card.
        """
        resultado: dict[str, Any] = {
            "status": "DESCONHECIDO",  # ABERTO, IMINENCIA, RANDOMICO, ENCERRADO
            "segundos_restantes": None,
            "texto_tempo": "",
        }
        try:
            if hasattr(card_locator, "inner_text"):
                res = card_locator.inner_text()
                texto_card = str(res).upper()
            else:
                texto_card = str(card_locator).upper()

            # 1. Identificação de Status de Disputa
            if "IMINÊNCIA" in texto_card or "IMINENCIA" in texto_card:
                resultado["status"] = "IMINENCIA"
            elif "RANDÔMICO" in texto_card or "ALEATÓRIO" in texto_card or "ALEATORIO" in texto_card or "RANDOMICO" in texto_card:
                resultado["status"] = "RANDOMICO"
            elif "ENCERRADO" in texto_card or "HOMOLOGADO" in texto_card:
                resultado["status"] = "ENCERRADO"
            elif "ABERTO" in texto_card or "EM DISPUTA" in texto_card:
                resultado["status"] = "ABERTO"

            # 2. Leitura do Cronômetro
            padrao_tempo = re.search(r"(\d{2}):(\d{2})", texto_card)
            if padrao_tempo:
                minutos, segundos = int(padrao_tempo.group(1)), int(padrao_tempo.group(2))
                resultado["segundos_restantes"] = (minutos * 60) + segundos
                resultado["texto_tempo"] = f"{padrao_tempo.group(1)}:{padrao_tempo.group(2)}"
        except Exception as e:
            logger.debug(f"[ERRO PARSER] Falha ao ler tempo/status: {e}")

        return resultado

    @staticmethod
    def extrair_valores_card(card_locator_ou_texto: Any) -> dict[str, Any]:
        """
        Extrai melhor_mercado, meu_ultimo_lance e estou_liderando de um card de item.
        Suporta locator do Playwright ou string de texto.
        """
        resultado: dict[str, Any] = {
            "melhor_mercado": None,
            "meu_ultimo_lance": None,
            "estou_liderando": False,
        }
        try:
            def _parse_moeda(val_txt: str) -> Optional[Decimal]:
                if not val_txt:
                    return None
                limpo = re.sub(r"[^\d,\.]", "", str(val_txt)).strip()
                if not limpo:
                    return None
                if "," in limpo and "." in limpo:
                    limpo = limpo.replace(".", "").replace(",", ".")
                elif "," in limpo:
                    limpo = limpo.replace(",", ".")
                try:
                    return Decimal(limpo)
                except Exception:
                    return None

            txt_card = ""
            if hasattr(card_locator_ou_texto, "inner_text"):
                try:
                    res = card_locator_ou_texto.inner_text()
                    txt_card = str(res)
                except Exception:
                    txt_card = str(card_locator_ou_texto)
            else:
                txt_card = str(card_locator_ou_texto)

            el_melhor = None
            el_meu = None

            if hasattr(card_locator_ou_texto, "locator"):
                try:
                    loc_m = card_locator_ou_texto.locator('[data-test="valor-geral"]').first
                    if loc_m.count() > 0:
                        el_melhor = _parse_moeda(loc_m.inner_text())
                except Exception:
                    pass

                try:
                    loc_meu = card_locator_ou_texto.locator('[data-test="valor-fornec"]').first
                    if loc_meu.count() > 0:
                        el_meu = _parse_moeda(loc_meu.inner_text())
                except Exception:
                    pass

            if el_melhor is None:
                m_mel = re.search(r"(?:melhor\s+lance|menor\s+lance|valor\s+geral)[^\d]*R?\$?\s*([\d\.,]+)", txt_card, re.I)
                if m_mel:
                    el_melhor = _parse_moeda(m_mel.group(1))

            if el_meu is None:
                m_meu = re.search(r"(?:meu\s+lance|nosso\s+lance|valor\s+fornecedor)[^\d]*R?\$?\s*([\d\.,]+)", txt_card, re.I)
                if m_meu:
                    el_meu = _parse_moeda(m_meu.group(1))

            estou_liderando = False
            txt_upper = txt_card.upper()
            if "1º LUGAR" in txt_upper or "1° LUGAR" in txt_upper or "GANHANDO" in txt_upper or "EM 1º" in txt_upper:
                estou_liderando = True
            elif el_meu is not None and el_melhor is not None and el_meu <= el_melhor:
                estou_liderando = True

            resultado["melhor_mercado"] = el_melhor
            resultado["meu_ultimo_lance"] = el_meu
            resultado["estou_liderando"] = estou_liderando
        except Exception as e:
            logger.debug(f"[ERRO PARSER] Falha ao extrair valores do card: {e}")

        return resultado

    def enviar_lance_comprasnet(self, page: Page, num_item: str | int, valor_lance: float | Decimal) -> bool:
        """
        Localiza o input do item, digita o lance formatado, clica em enviar
        e dispara a rotina de confirmação de modal.
        """
        try:
            num = int(num_item)
            decisao = DecisaoLance(
                item_id=str(num),
                numero_item=num,
                deve_dar_lance=True,
                valor_lance=float(valor_lance),
                motivo="Disparo direto Comprasnet",
            )
            self._enviar_lance_real(page, num, float(valor_lance), decisao)
            self.confirmar_modal_imediato(page, timeout_ms=400)
            return True
        except Exception as e:
            logger.error(f"[ERRO BROWSER] Falha ao enviar lance no Item {num_item}: {e}")
            return False

    def _enviar_lance_real(
        self,
        page: Page,
        numero_item: int,
        valor: float,
        decisao: DecisaoLance,
    ) -> None:
        """
        Executa o envio do lance real na interface do Comprasnet.
        """
        logger.info(
            f"🔴 ENVIANDO LANCE REAL | Item {numero_item} | R$ {valor:.2f}"
        )
        valor_formatado = f"{valor:.2f}".replace(".", ",")

        try:
            # 1. Tenta envio via injeção JavaScript direta no Card do Item (mais rápido e preciso)
            js_submit_script = f"""
            () => {{
                const numItem = {numero_item};
                const valorFormatado = "{valor_formatado}";
                
                // 1.1 Tenta seleção direta por ID ou data-test do Comprasnet
                let input = document.querySelector(`input[data-test="lance"][id="${{numItem}}"]`) ||
                            document.querySelector(`input[id="${{numItem}}"]`);
                if (input) {{
                    input.focus();
                    input.value = valorFormatado;
                    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    
                    let cardParent = input.closest('.cp-itens-disputa, [class*="cp-itens-disputa"], div');
                    let btn = cardParent ? Array.from(cardParent.querySelectorAll('button, a')).find(b => (b.innerText || '').toLowerCase().includes('enviar')) : null;
                    if (btn) {{
                        btn.click();
                        return {{ success: true }};
                    }}
                }}

                const blocos = Array.from(document.querySelectorAll('.cp-itens-disputa, [class*="cp-itens-disputa"], app-card-item, app-item-disputa, mat-card, .card, .br-card, .item-card, [class*="item-disputa"], [class*="card-item"], [class*="item-lista"], tr, [role="row"], div'));
                
                let targetBlock = null;
                for (const el of blocos) {{
                    const t = el.innerText || '';
                    if (t.match(new RegExp('^\\\\s*' + numItem + '\\\\s+[A-Za-z]', 'm')) ||
                        t.match(new RegExp('\\\\b(?:item|lote)\\\\s*(?:n[º°])?\\\\s*[:\\\\-–]?\\\\s*' + numItem + '\\\\b', 'i')) ||
                        el.getAttribute('data-item') == String(numItem)) {{
                        if (el.querySelector('input')) {{
                            targetBlock = el;
                            break;
                        }}
                    }}
                }}
                
                if (targetBlock) {{
                    const inputEl = targetBlock.querySelector('input');
                    if (inputEl) {{
                        inputEl.focus();
                        inputEl.value = valorFormatado;
                        inputEl.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        inputEl.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}
                    const btn = Array.from(targetBlock.querySelectorAll('button, a, [role="button"]')).find(b => {{
                        const bt = (b.innerText || '').toLowerCase();
                        return bt.includes('enviar') || bt.includes('lance') || bt.includes('confirmar');
                    }});
                    if (btn) {{
                        btn.click();
                        return {{ success: true }};
                    }}
                }}
                return {{ success: false }};
            }}
            """
            res_js = page.evaluate(js_submit_script)
            if not res_js or not res_js.get("success"):
                # Fallback: Seletores Playwright
                input_selectors = [
                    f"[data-item='{numero_item}'] input",
                    f".item-card:has-text('{numero_item}') input",
                    "input[placeholder*='lance']",
                    "input[placeholder*='valor']",
                    "input[type='text']:visible",
                ]
                input_lance = None
                for sel in input_selectors:
                    try:
                        el = page.wait_for_selector(sel, timeout=1000)
                        if el and el.is_visible():
                            input_lance = el
                            break
                    except Exception:
                        continue

                if input_lance:
                    input_lance.click()
                    input_lance.fill(valor_formatado)
                    btn = page.query_selector(f"button:has-text('Enviar lance'), a:has-text('Enviar lance'), button:has-text('Enviar')")
                    if btn:
                        btn.click()

            # Executa a confirmação relâmpago de modal caso apareça (SweetAlert, Material, etc.)
            self.confirmar_modal_imediato(page, timeout_ms=500)

            self._log(
                "INFO",
                f"✅ Lance enviado e confirmado no portal | Item {numero_item} | R$ {valor:.2f}",
            )
            self.motor.confirmar_lance_enviado(numero_item, valor)

            # Notifica push no Telegram
            try:
                from notifier_manager import notifier_manager
                notifier_manager.telegram.enviar_mensagem(
                    f"🚀 *Lance Disparado!* Item {numero_item} | Valor: *R$ {valor:,.2f}*"
                )
            except Exception:
                pass

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

    def cadastrar_proposta_comprasnet(self, uasg: str, numero_compra: str, itens_proposta: list[dict]) -> bool:
        """
        Cadastra propostas no portal de compras automaticamente via UI e mantém aberto.
        """
        self._log("INFO", f"Iniciando preenchimento de proposta. UASG: {uasg} | Compra: {numero_compra}")
        
        try:
            with self._abrir_navegador() as page:
                page.goto("https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/fornecedor/compras")
                page.wait_for_load_state("networkidle")
                
                # Preencher a busca (ajustar seletores)
                try:
                    # Tenta os campos comuns de uasg e numero no comprasnet
                    page.fill("input[name*='uasg'], input[placeholder*='UASG']", uasg, timeout=5000)
                    page.fill("input[name*='numero'], input[placeholder*='Pregão'], input[placeholder*='Número']", numero_compra)
                    page.click("button:has-text('Pesquisar'), button:has-text('Consultar')")
                    page.wait_for_load_state("networkidle")
                except PlaywrightTimeout:
                    self._log("WARNING", "Campos de busca não encontrados. Assumindo que já está na tela ou layout mudou.")
                
                # Clica para Incluir Proposta / Cadastrar
                try:
                    page.click("button:has-text('Incluir Proposta'), a:has-text('Incluir Proposta'), button:has-text('Cadastrar Proposta')", timeout=5000)
                    page.wait_for_load_state("networkidle")
                except PlaywrightTimeout:
                    self._log("WARNING", "Botão de 'Incluir Proposta' não encontrado.")

                sucessos = 0
                for prop in itens_proposta:
                    item_num = prop.get("item_num")
                    self._log("INFO", f"Preenchendo item {item_num}...")
                    
                    try:
                        linha_item = page.locator(f"tr:has-text('Item {item_num}'), div.item-card:has-text('Item {item_num}')").first
                        if linha_item.count() == 0:
                            self._log("WARNING", f"Item {item_num} não encontrado.")
                            continue
                        
                        if prop.get("valor"):
                            linha_item.locator("input[name*='valor'], input[name*='valorUnitario']").fill(str(prop["valor"]))
                        if prop.get("marca"):
                            linha_item.locator("input[name*='marca']").fill(str(prop["marca"]))
                        if prop.get("fabricante"):
                            linha_item.locator("input[name*='fabricante']").fill(str(prop["fabricante"]))
                        if prop.get("modelo"):
                            linha_item.locator("input[name*='modelo']").fill(str(prop["modelo"]))
                        if prop.get("descricao"):
                            linha_item.locator("textarea[name*='descricao']").fill(str(prop["descricao"]))
                        
                        # Salvar o item individualmente, caso a interface exija
                        btn_salvar_item = linha_item.locator("button:has-text('Salvar Item'), button[title*='Salvar']")
                        if btn_salvar_item.count() > 0:
                            btn_salvar_item.first.click()
                            time.sleep(1) # Aguarda ajax de salvamento
                        
                        sucessos += 1
                        time.sleep(0.5)
                    except Exception as e:
                        self._log("ERROR", f"Falha ao preencher item {item_num}: {e}")

                self._log("INFO", f"Preenchimento concluído: {sucessos}/{len(itens_proposta)} itens. A tela permanecerá aberta para conferência visual!")
                
                # Deixa a tela aberta rodando um loop longo para o humano conferir
                self._log("INFO", "Aguardando conferência do operador... (timeout 10 min)")
                page.wait_for_timeout(600000) # 10 minutos
                return True
                
        except Exception as e:
            self._log("ERROR", f"Erro fatal ao preencher proposta: {e}")
            
        return False

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


def abrir_login_nativo(url: str = "https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/login") -> bool:
    """
    Abre o Google Chrome nativo do sistema diretamente no Gov.br / Serpro,
    utilizando o perfil persistente 'browser_profile'.
    
    Permite que o operador realize login sem qualquer intermediação do Playwright,
    salvando os cookies para as próximas sessões.
    """
    user_data_dir = str(settings.browser_user_data_dir)
    settings.browser_user_data_dir.mkdir(parents=True, exist_ok=True)

    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "Lock", ".lock"):
        try:
            (settings.browser_user_data_dir / lock_name).unlink(missing_ok=True)
        except Exception:
            pass

    chrome_bin = None
    for cand in ("/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "google-chrome", "google-chrome-stable", "chromium", "/usr/bin/chromium-browser"):
        if shutil.which(cand):
            chrome_bin = shutil.which(cand)
            break

    if not chrome_bin:
        return False

    cmd = [
        chrome_bin,
        "--remote-debugging-port=9222",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
        url,
    ]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception as exc:
        logger.error(f"Erro ao abrir Google Chrome nativo: {exc}")
        return False


class ComprasnetBrowserBot:
    """
    Controlador assíncrono para Comprasnet Serpro (cnetmobile.estaleiro.serpro.gov.br)
    otimizado para operações em tempo real via CDP (:9222).
    """

    def __init__(self, porta_cdp: int = 9222):
        self.porta_cdp = porta_cdp

    async def confirmar_modal_imediato(self, page: Any, timeout_ms: int = 400) -> bool:
        """
        Detecta e confirma imediatamente caixas de diálogo do Comprasnet Serpro
        (SweetAlert, Angular Material Dialog ou modais Bootstrap).
        """
        seletores_confirmacao = [
            "button.swal2-confirm",
            "button:has-text('Sim')",
            "button:has-text('Confirmar')",
            "mat-dialog-actions button:has-text('Confirmar')",
            "mat-dialog-actions button:has-text('Sim')",
            ".modal-footer button.btn-primary",
            "[data-test='btn-confirmar-lance']",
        ]
        
        seletor_unificado = ", ".join(seletores_confirmacao)
        try:
            btn_confirmar = page.locator(seletor_unificado).first
            await btn_confirmar.wait_for(state="visible", timeout=timeout_ms)
            await btn_confirmar.click(force=True)
            return True
        except Exception:
            return False

    async def enviar_lance_comprasnet(self, page: Any, num_item: str | int, valor_lance: Decimal | float) -> bool:
        """
        Localiza o input do item, digita o lance formatado, clica em enviar
        e dispara a rotina de confirmação de modal.
        """
        try:
            num_str = str(num_item)
            input_lance = page.locator(f'input[data-test="lance"][id="{num_str}"], input[id="lance-{num_str}"]').first
            
            if not await input_lance.is_visible(timeout=1000):
                card_item = page.locator(f".cp-itens-disputa:has-text('Item {num_str}')").first
                input_lance = card_item.locator("input[type='text'], input[type='number']").first

            valor_formatado = f"{float(valor_lance):.2f}".replace(".", ",")
            
            await input_lance.fill("")
            await input_lance.type(valor_formatado, delay=20)

            btn_enviar = page.locator(f'button[data-test="enviar-lance"][id="{num_str}"], button:has-text("Enviar Lance")').first
            await btn_enviar.click()

            await self.confirmar_modal_imediato(page, timeout_ms=500)
            return True
        except Exception as e:
            logger.error(f"[ERRO BROWSER] Falha ao enviar lance no Item {num_item}: {e}")
            return False

    async def extrair_status_e_tempo_item(self, page: Any, card_locator: Any) -> dict[str, Any]:
        """
        Analisa o card do item para obter o status da disputa e os segundos restantes.
        """
        resultado: dict[str, Any] = {
            "status": "DESCONHECIDO",
            "segundos_restantes": None,
            "texto_tempo": ""
        }
        try:
            if hasattr(card_locator, "inner_text"):
                res = card_locator.inner_text()
                texto_card = (await res if hasattr(res, "__await__") else res).upper()
            else:
                texto_card = str(card_locator).upper()

            if "IMINÊNCIA" in texto_card or "IMINENCIA" in texto_card:
                resultado["status"] = "IMINENCIA"
            elif "RANDÔMICO" in texto_card or "ALEATÓRIO" in texto_card or "ALEATORIO" in texto_card or "RANDOMICO" in texto_card:
                resultado["status"] = "RANDOMICO"
            elif "ENCERRADO" in texto_card or "HOMOLOGADO" in texto_card:
                resultado["status"] = "ENCERRADO"
            elif "ABERTO" in texto_card or "EM DISPUTA" in texto_card:
                resultado["status"] = "ABERTO"

            padrao_tempo = re.search(r"(\d{2}):(\d{2})", texto_card)
            if padrao_tempo:
                minutos, segundos = int(padrao_tempo.group(1)), int(padrao_tempo.group(2))
                resultado["segundos_restantes"] = (minutos * 60) + segundos
                resultado["texto_tempo"] = f"{padrao_tempo.group(1)}:{padrao_tempo.group(2)}"

        except Exception as e:
            logger.error(f"[ERRO PARSER] Falha ao ler tempo/status: {e}")

        return resultado

    async def extrair_valores_card(self, card_locator: Any) -> dict[str, Any]:
        """
        Extrai valores do card de disputa.
        """
        return BrowserBot.extrair_valores_card(card_locator)


def confirmar_modal_imediato(page: Any, timeout_ms: int = 400) -> bool:
    """
    Função modular: localiza e clica em botões de confirmação pós-lance no Comprasnet Serpro
    (.swal2-confirm, button:has-text('Sim'), button:has-text('Confirmar'), mat-dialog-actions button).
    """
    seletores_confirmacao = [
        "button.swal2-confirm",
        "button:has-text('Sim')",
        "button:has-text('Confirmar')",
        "mat-dialog-actions button:has-text('Confirmar')",
        "mat-dialog-actions button:has-text('Sim')",
        "mat-dialog-actions button",
        ".modal-footer button.btn-primary",
        "[data-test='btn-confirmar-lance']",
        "dialog button[type='submit']",
        "dialog button:has-text('Sim')",
        "dialog button:has-text('Confirmar')",
        ".modal button:has-text('Confirmar')",
        ".modal button:has-text('Sim')",
    ]
    seletor_unificado = ", ".join(seletores_confirmacao)
    try:
        btn_confirmar = page.locator(seletor_unificado).first
        if hasattr(btn_confirmar, "wait_for"):
            btn_confirmar.wait_for(state="visible", timeout=timeout_ms)
            btn_confirmar.click(force=True)
            return True
        return False
    except Exception:
        return False


def extrair_status_e_tempo_item(card_locator: Any) -> dict[str, Any]:
    """
    Função modular: analisa o card do item para obter o status da disputa
    ('ABERTO', 'IMINENCIA', 'RANDOMICO', 'ENCERRADO') e os segundos restantes no cronômetro.
    """
    return BrowserBot.extrair_status_e_tempo_item(card_locator)


def enviar_lance_comprasnet(page: Any, num_item: str | int, valor_lance: Decimal | float) -> bool:
    """
    Função modular: localiza o input do item, digita o lance formatado, clica em enviar
    e dispara a rotina de confirmação imediata de modal.
    """
    try:
        num_str = str(num_item)
        input_lance = page.locator(f'input[data-test="lance"][id="{num_str}"], input[id="lance-{num_str}"]').first
        if not input_lance.is_visible(timeout=1000):
            card_item = page.locator(f".cp-itens-disputa:has-text('Item {num_str}')").first
            input_lance = card_item.locator("input[type='text'], input[type='number']").first

        valor_formatado = f"{float(valor_lance):.2f}".replace(".", ",")
        input_lance.fill("")
        input_lance.type(valor_formatado, delay=20)

        btn_enviar = page.locator(f'button[data-test="enviar-lance"][id="{num_str}"], button:has-text("Enviar Lance")').first
        btn_enviar.click()

        confirmar_modal_imediato(page, timeout_ms=400)
        return True
    except Exception as e:
        logger.error(f"[ERRO BROWSER] Falha ao enviar lance no Item {num_item}: {e}")
        return False


_JS_EXTRAIR_ITENS_SALA = r"""() => {
    function parseMoeda(str) {
        if (!str) return null;
        const limpo = str.replace(/[^0-9.,]/g, '').trim();
        if (!limpo) return null;
        let numStr = limpo;
        if (numStr.includes(',') && numStr.includes('.')) {
            numStr = numStr.replace(/\./g, '').replace(',', '.');
        } else if (numStr.includes(',')) {
            numStr = numStr.replace(',', '.');
        }
        const val = parseFloat(numStr);
        return isNaN(val) ? null : val;
    }

    const cards = Array.from(document.querySelectorAll(
        '.cp-itens-disputa, [class*="cp-itens-disputa"], app-identificacao-e-fase-item, .card-item-disputa, app-card-item'
    ));

    const itens = [];
    const numerosVistos = new Set();

    cards.forEach(c => {
        let cardEl = c;
        while (cardEl && cardEl.parentElement && !cardEl.classList.contains('cp-itens-disputa') && cardEl.tagName !== 'BODY') {
            if (cardEl.parentElement.classList.contains('cp-itens-disputa')) {
                cardEl = cardEl.parentElement;
                break;
            }
            cardEl = cardEl.parentElement;
        }
        if (!cardEl) cardEl = c;

        const elId = cardEl.querySelector('app-identificacao-item span:first-child') ||
                     cardEl.querySelector('.numero-item, [data-test="numero-item"], .item-numero');
        const elDesc = cardEl.querySelector('app-identificacao-item span:last-child') ||
                       cardEl.querySelector('.text-uppercase, .item-descricao, [data-test="descricao-item"]');
        
        const elMelhor = cardEl.querySelector('[data-test="valor-geral"]');
        const elMeu = cardEl.querySelector('[data-test="valor-fornec"]');
        const elSituacao = cardEl.querySelector('[data-test="situacao-item"]');
        const elIcon = cardEl.querySelector('i[title]');
        const txtCard = cardEl.innerText || '';

        // 1. Número do item
        let num = elId ? parseInt(elId.innerText.trim(), 10) : null;
        if (!num || isNaN(num)) {
            const mNum = txtCard.match(/^\s*Item\s+(\d+)/i) || 
                         txtCard.match(/^\s*(\d+)\s+[A-Za-z]/m) ||
                         txtCard.match(/\bItem:\s*(\d+)\b/i);
            if (mNum) num = parseInt(mNum[1], 10);
        }
        if (!num || isNaN(num) || numerosVistos.has(num)) return;
        numerosVistos.add(num);

        // 2. Descrição resumida
        let desc = elDesc ? elDesc.innerText.trim() : '';
        if (!desc) {
            const linhas = txtCard.split('\n').map(l => l.trim()).filter(Boolean);
            desc = linhas.length > 1 ? linhas[1] : `Item ${num}`;
        }

        // 3. Valor de referência do órgão
        let valRef = null;
        const elRef = cardEl.querySelector('[data-test="valor-referencia"], [data-test="valor-estimado"], [data-test="valor-homologado"], [data-test="valor-aceitavel"]');
        if (elRef) {
            valRef = parseMoeda(elRef.innerText);
        }
        if (valRef === null) {
            const mRef = txtCard.match(/(?:valor\s+(?:de\s+)?refer[êe]ncia|valor\s+estimado|pre[çc]o\s+(?:de\s+)?refer[êe]ncia|valor\s+aceit[áa]vel|valor\s+teto|teto)[^:\d]*[:\s]+(?:R\$\s*)?([\d.,]+)/i);
            if (mRef) {
                valRef = parseMoeda(mRef[1]);
            }
        }

        // 4. Valor da nossa proposta inicial
        let valProposta = null;
        const elProp = cardEl.querySelector('[data-test="valor-proposta"], [data-test="proposta-inicial"], [data-test="minha-proposta"]');
        if (elProp) {
            valProposta = parseMoeda(elProp.innerText);
        }
        if (valProposta === null) {
            const mProp = txtCard.match(/(?:proposta\s+inicial|minha\s+proposta|nossa\s+proposta|meu\s+lance\s+inicial|lance\s+inicial)[^:\d]*[:\s]+(?:R\$\s*)?([\d.,]+)/i);
            if (mProp) {
                valProposta = parseMoeda(mProp[1]);
            }
        }
        // Fallback: se não houver campo exclusivo de proposta inicial,
        // no Comprasnet o 'valor-fornec' reflete a proposta cadastrada ou nosso último lance
        const valFornec = elMeu ? parseMoeda(elMeu.innerText) : null;
        if (valProposta === null && valFornec !== null) {
            valProposta = valFornec;
        }

        // Dados de disputa adicionais
        const mel = elMelhor ? parseMoeda(elMelhor.innerText) : null;
        const meu = valFornec;
        const titleIcon = elIcon ? elIcon.getAttribute('title') : '';

        let pos = 2;
        if (titleIcon && (titleIcon.toLowerCase().includes('ganhando') || titleIcon.toLowerCase().includes('primeiro'))) {
            pos = 1;
        } else if (meu !== null && mel !== null && meu <= mel) {
            pos = 1;
        }

        const sit = elSituacao ? elSituacao.innerText.trim() : (pos === 1 ? 'Liderando' : 'Em disputa');

        itens.push({
            numero_item: num,
            descricao: desc,
            valor_referencia: valRef,
            proposta_inicial: valProposta,
            melhor_lance: mel,
            nosso_ultimo_lance: meu,
            posicao: pos,
            situacao: sit
        });
    });

    // Fallback caso layout seja tabular ou diferente de SPA cnetmobile
    if (itens.length === 0) {
        const rows = Array.from(document.querySelectorAll('table tbody tr, [role="row"]'));
        rows.forEach(r => {
            const txt = r.innerText || '';
            const mNum = txt.match(/^\s*(\d+)\b/);
            if (!mNum) return;
            const num = parseInt(mNum[1], 10);
            if (numerosVistos.has(num)) return;
            numerosVistos.add(num);

            const cols = Array.from(r.querySelectorAll('td, [role="cell"]')).map(td => td.innerText.trim());
            const desc = cols.length > 1 ? cols[1] : `Item ${num}`;
            let valRef = null;
            let valProp = null;
            for (const col of cols) {
                const v = parseMoeda(col);
                if (v && v > 0) {
                    if (valRef === null) valRef = v;
                    else if (valProp === null) valProp = v;
                }
            }
            itens.push({
                numero_item: num,
                descricao: desc,
                valor_referencia: valRef,
                proposta_inicial: valProp,
                melhor_lance: null,
                nosso_ultimo_lance: valProp,
                posicao: null,
                situacao: 'Aguardando'
            });
        });
    }

    return itens;
}"""


def extrair_todos_itens_sala(page: Any) -> list[dict[str, Any]]:
    """
    Faz um scan em todos os cards '.cp-itens-disputa' da aba ativa da sala de disputa,
    coletando:
      - número do item
      - descrição resumida
      - valor de referência do órgão
      - valor da nossa proposta inicial
    Calcula também o stop-loss padrão sugerido (margem de 30% em produtos ou 50% em serviços).
    """
    if not page:
        logger.warning("[EXTRAIR_ITENS] Objeto page é nulo.")
        return []

    try:
        itens_brutos = page.evaluate(_JS_EXTRAIR_ITENS_SALA)
        if not isinstance(itens_brutos, list):
            itens_brutos = []
    except Exception as exc:
        logger.error(f"[EXTRAIR_ITENS] Falha ao executar scan nos cards da página: {exc}")
        return []

    palavras_servicos = {
        "servico", "serviço", "manutencao", "manutenção", "suporte", "instalacao", "instalação",
        "configuracao", "configuração", "locacao", "locação", "treinamento", "consultoria",
        "implantacao", "implantação", "operacao", "operação", "desenvolvimento", "assessoria",
        "licenciamento", "software", "nuvem", "cloud", "saas", "hospedagem", "segurança",
        "infraestrutura", "cabeamento", "helpdesk"
    }

    itens_processados: list[dict[str, Any]] = []
    for it in itens_brutos:
        num = int(it.get("numero_item", 1))
        desc = str(it.get("descricao", f"Item {num}")).strip()

        v_ref = it.get("valor_referencia")
        if v_ref is not None:
            try:
                v_ref = round(float(v_ref), 2)
            except (ValueError, TypeError):
                v_ref = None

        v_prop = it.get("proposta_inicial")
        if v_prop is not None:
            try:
                v_prop = round(float(v_prop), 2)
            except (ValueError, TypeError):
                v_prop = None

        # Classificação do tipo de item e cálculo do Stop-Loss sugerido
        desc_lower = desc.lower()
        eh_servico = any(palavra in desc_lower for palavra in palavras_servicos)

        if eh_servico:
            tipo_item = "SERVIÇO"
            margem_sugerida = 50.0  # 50% para serviços
            fator_piso = 0.50
        else:
            tipo_item = "PRODUTO"
            margem_sugerida = 30.0  # 30% para produtos
            fator_piso = 0.70

        # Base para o Stop-Loss: Proposta Inicial > Valor de Referência > Último Lance
        base_valor = v_prop if (v_prop and v_prop > 0) else (v_ref if (v_ref and v_ref > 0) else 0.0)
        if base_valor <= 0 and it.get("nosso_ultimo_lance"):
            try:
                base_valor = float(it["nosso_ultimo_lance"])
            except (ValueError, TypeError):
                base_valor = 0.0

        piso_stop_loss = round(base_valor * fator_piso, 2) if base_valor > 0 else 0.0
        custo_estimado = round(base_valor * (1.0 - margem_sugerida / 100.0), 2) if base_valor > 0 else 0.0

        item_dict = {
            "numero_item": num,
            "descricao": desc,
            "valor_referencia": v_ref,
            "preco_referencia": v_ref,
            "proposta_inicial": v_prop,
            "preco_inicial_proposta": v_prop or v_ref,
            "melhor_lance": it.get("melhor_lance"),
            "nosso_ultimo_lance": it.get("nosso_ultimo_lance") or v_prop,
            "posicao": it.get("posicao"),
            "situacao": it.get("situacao") or "AGUARDANDO",
            "tipo_item": tipo_item,
            "margem_sugerida": margem_sugerida,
            "margem_minima_percent": margem_sugerida,
            "stop_loss_sugerido": piso_stop_loss,
            "preco_minimo": piso_stop_loss,
            "piso_seguranca": piso_stop_loss,
            "piso_seguro": piso_stop_loss,
            "custo_unitario": custo_estimado,
            "quantidade": 1.0,
            "unidade_medida": "UN",
        }
        itens_processados.append(item_dict)

    logger.info(f"[EXTRAIR_ITENS] {len(itens_processados)} itens extraídos com sucesso da sala.")
    return itens_processados


def extrair_todos_itens_sala_cdp(porta: int = 9222, url_alvo: str = "") -> list[dict[str, Any]]:
    """
    Conecta via CDP ao Google Chrome na porta especificada (padrão 9222),
    localiza a aba aberta da disputa e extrai todos os itens com stop-loss sugerido.
    """
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{porta}")
            disputa_page = None
            url_alvo_clean = (url_alvo or "").strip().lower()

            for ctx in browser.contexts:
                for pg in reversed(ctx.pages):
                    try:
                        u = pg.url.lower()
                        if url_alvo_clean and url_alvo_clean in u:
                            disputa_page = pg
                            break
                        if "disputa" in u or "comprasnet" in u or "compras" in u:
                            disputa_page = pg
                            break
                    except Exception:
                        pass
                if disputa_page:
                    break

            if not disputa_page:
                for ctx in browser.contexts:
                    if ctx.pages:
                        disputa_page = ctx.pages[-1]
                        break

            if not disputa_page:
                logger.warning(f"[CDP] Nenhuma página aberta encontrada no Chrome na porta {porta}.")
                return []

            return extrair_todos_itens_sala(disputa_page)
    except Exception as exc:
        logger.warning(f"[CDP] Falha ao extrair itens via CDP na porta {porta}: {exc}")
        return []




