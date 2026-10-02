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
    intervalo_minimo: Optional[float] = None
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
                return req.status == 200
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
                        self._browser.close()
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
        Localiza a aba ativa do navegador onde está a sala de disputa do Compras.gov.br.
        Permite que o operador navegue livremente pelas abas sem que o robô perca o foco.
        """
        if not self._context:
            return fallback_page

        try:
            pages = [p for p in self._context.pages if not p.is_closed()]
            if not pages:
                return fallback_page

            # 1. Procura aba cuja URL contenha 'disputa'
            for p in reversed(pages):
                try:
                    if "disputa" in p.url.lower():
                        return p
                except Exception:
                    pass

            # 2. Procura aba do Comprasnet / Serpro / Gov.br
            for p in reversed(pages):
                try:
                    u = p.url.lower()
                    if any(k in u for k in ("comprasnet", "serpro", "gov.br", "acesso")):
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
                            self.motor._estados[estado_pag.numero_item].regra.intervalo_minimo = estado_pag.intervalo_minimo

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
        Lê o estado atual de todos os itens na página do pregão via JavaScript nativo
        e seletores flexíveis para Compras.gov.br / Serpro Comprasnet Web (SPA Angular).
        """
        estados: list[EstadoPagina] = []
        if not page or (hasattr(page, "is_closed") and page.is_closed()):
            return estados

        try:
            js_script = """
            () => {
                const resultado = {};
                const url = window.location.href || "";
                const bodyText = document.body ? document.body.innerText : "";
                const isLogin = /gov\\.br\\/acesso|sso|login|autenticacao|contas\\.acesso|acesso-nao-autorizado/i.test(url) || 
                                /informe seu cpf|entrar com gov\\.br|identifique-se no gov\\.br/i.test(bodyText);
                const isEncerrado = /disputa encerrada|sessão encerrada|pregao encerrado/i.test(bodyText);

                function parseMoeda(str) {
                    if (!str) return null;
                    const limpo = str.replace(/[^0-9.,]/g, '').trim();
                    if (!limpo) return null;
                    let numStr = limpo;
                    if (numStr.includes(',') && numStr.includes('.')) {
                        numStr = numStr.replace(/\\./g, '').replace(',', '.');
                    } else if (numStr.includes(',')) {
                        numStr = numStr.replace(',', '.');
                    }
                    const val = parseFloat(numStr);
                    return isNaN(val) ? null : val;
                }

                // 1. Procura blocos/cards de itens no DOM
                const blocos = Array.from(document.querySelectorAll(
                    'app-card-item, app-item-disputa, app-item, mat-card, .card, .br-card, .item-card, [class*="item-disputa"], [class*="card-item"], [class*="item-lista"], tr, [role="row"], .list-group-item, div[id*="item"], div[class*="item"]'
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
                                  txt.match(/^(\d+)\s+L\b/m) ||
                                  txt.match(/\b(\d+)\s*[-–]\s*(?:servi[çc]o|produto|fornecimento|aquisi[çc][ãa]o|licenciamento)/i);
                        if (m) {
                            numItem = parseInt(m[1], 10);
                        }
                    }

                    if (!numItem || isNaN(numItem) || numItem <= 0 || numItem > 9999) return;

                    if (!resultado[numItem]) {
                        resultado[numItem] = {
                            numero_item: numItem,
                            melhor_lance: null,
                            nossa_posicao: null,
                            nosso_ultimo_lance: null,
                            intervalo_minimo: null,
                        };
                    }

                    const mMelhor = txt.match(/(?:melhor\s+valor|melhor\s+lance|menor\s+lance|menor\s+valor|1[º°]|primeiro|l[íi]der|vencedor)[\s\S]*?R\$\s*([\d.,]+)/i) ||
                                    txt.match(/(?:valor|proposta|lance)\s*(?:atual|vencedor)\s*[:\-–]?[\s\S]*?R\$\s*([\d.,]+)/i) ||
                                    txt.match(/R\$\s*([\d.,]+)/i);
                    if (mMelhor && resultado[numItem].melhor_lance === null) {
                        resultado[numItem].melhor_lance = parseMoeda(mMelhor[1]);
                    }

                    const mMeu = txt.match(/(?:meu\s+valor|seu\s+valor|meu\s+lance|seu\s+lance|último\s+lance|nosso\s+lance)[\s\S]*?R\$\s*([\d.,]+)/i);
                    if (mMeu && resultado[numItem].nosso_ultimo_lance === null) {
                        resultado[numItem].nosso_ultimo_lance = parseMoeda(mMeu[1]);
                    }

                    const mInt = txt.match(/intervalo\s+mínimo[\s\S]*?R\$\s*([\d.,]+)/i);
                    if (mInt && resultado[numItem].intervalo_minimo === null) {
                        resultado[numItem].intervalo_minimo = parseMoeda(mInt[1]);
                    }

                    const mPos = txt.match(/(?:sua\s+posi[çc][ãa]o|posi[çc][ãa]o\s*atual|posi[çc][ãa]o)\s*[:\-–]?\s*(\d+)[º°]?/i) ||
                                 txt.match(/\b(\d+)[º°]\b/);
                    if (mPos && resultado[numItem].nossa_posicao === null) {
                        const p = parseInt(mPos[1], 10);
                        if (!isNaN(p) && p > 0 && p < 200) {
                            resultado[numItem].nossa_posicao = p;
                        }
                    }

                    // Se não encontrou posição explícita, infere comparando os valores
                    if (resultado[numItem].nossa_posicao === null) {
                        const mel = resultado[numItem].melhor_lance;
                        const meu = resultado[numItem].nosso_ultimo_lance;
                        if (mel !== null && meu !== null) {
                            resultado[numItem].nossa_posicao = (meu <= mel) ? 1 : 2;
                        }
                    }
                });

                // 2. Fallback de regex global no texto
                if (Object.keys(resultado).length === 0 && bodyText) {
                    const regexGlobal = /(?:item|lote)\s*(?:n[º°])?\s*[:\-–]?\s*(\d+)[\s\S]{1,250}?R\$\s*([\d.,]+)/gi;
                    let match;
                    while ((match = regexGlobal.exec(bodyText)) !== null) {
                        const n = parseInt(match[1], 10);
                        const v = parseMoeda(match[2]);
                        if (n > 0 && v !== null && !resultado[n]) {
                            resultado[n] = {
                                numero_item: n,
                                melhor_lance: v,
                                nossa_posicao: null,
                                nosso_ultimo_lance: null,
                                intervalo_minimo: null,
                            };
                        }
                    }
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

        itens_lidos = {it["numero_item"]: it for it in dados_dom.get("itens", [])}
        is_login = dados_dom.get("is_login", False)
        is_encerrado = dados_dom.get("is_encerrado", False)

        for num_item in self.motor._estados:
            if num_item in itens_lidos:
                d = itens_lidos[num_item]
                estados.append(EstadoPagina(
                    numero_item=num_item,
                    melhor_lance_mercado=d.get("melhor_lance"),
                    nossa_posicao=d.get("nossa_posicao"),
                    nosso_ultimo_lance=d.get("nosso_ultimo_lance"),
                    intervalo_minimo=d.get("intervalo_minimo"),
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
                    pregao_encerrado=is_encerrado,
                    erro_leitura=msg_aviso,
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
        if not self.motor.validar_lance_pre_envio(num_item, valor):
            self._log(
                "CRITICAL",
                f"Lance BLOQUEADO pela validação pré-envio | "
                f"Item {num_item} | Valor R$ {valor:.2f}",
            )
            return

        if valor is None or valor <= 0:
            self._log("ERROR", f"Valor de lance inválido: {valor}")
            return

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
                
                const blocos = Array.from(document.querySelectorAll('app-card-item, app-item-disputa, mat-card, .card, .br-card, .item-card, [class*="item-disputa"], [class*="card-item"], [class*="item-lista"], tr, [role="row"], div'));
                
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
                    const input = targetBlock.querySelector('input');
                    if (input) {{
                        input.focus();
                        input.value = valorFormatado;
                        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        input.dispatchEvent(new Event('change', {{ bubbles: true }}));
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

            # Confirmação de modal (se houver)
            modal_selectors = [
                "dialog button:has-text('Confirmar')",
                "dialog button:has-text('Sim')",
                ".modal button:has-text('Confirmar')",
                ".modal button:has-text('Sim')",
                "button:has-text('Confirmar')",
                "button:has-text('Sim')",
            ]
            for m_sel in modal_selectors:
                try:
                    modal_btn = page.wait_for_selector(m_sel, timeout=1500)
                    if modal_btn and modal_btn.is_visible():
                        modal_btn.click()
                        break
                except Exception:
                    pass

            self._log(
                "INFO",
                f"✅ Lance enviado e confirmado no portal | Item {numero_item} | R$ {valor:.2f}",
            )
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

