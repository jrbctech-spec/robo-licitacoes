"""
Módulo: disputa_manager.py
Gerenciamento de Salas de Disputa, Detecção de Abas Abertas via CDP e Suporte a Múltiplas Disputas Simultâneas.
JRBC TECH - CNPJ: 65.488.003/0001-61 - Palmas/TO
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import os
import re
import shutil
import subprocess
import urllib.request
from typing import Any, Optional

from config import settings

logger = logging.getLogger("DisputaManager")


def calcular_url_disputa_real(lic: dict[str, Any]) -> str:
    """
    Retorna a URL real e exata da sala de disputa ao vivo,
    evitando links meramente informativos de editais do PNCP.
    """
    # 1. Se já possui URL de sala explícita configurada (que não seja página geral do PNCP)
    url_sala = str(lic.get("url_sala_disputa") or "").strip()
    if url_sala and "pncp.gov.br" not in url_sala:
        return url_sala

    link_sis = str(lic.get("link_sistema") or lic.get("link_edital") or "").strip()

    # 2. Se for link de disputa direto
    if "disputa" in link_sis and "pncp.gov.br" not in link_sis:
        return link_sis

    # 3. Código de compra Comprasnet de 17 dígitos
    m_compra = re.search(r"compra=(\d{17})", link_sis) or re.search(r"\b(\d{17})\b", link_sis)
    if m_compra:
        return f"https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/fornecedor/disputa?compra={m_compra.group(1)}"

    # 4. Código numérico de UASG e Pregão no Compras.gov.br
    uasg = str(lic.get("numero_uasg") or "").strip()
    pregao = str(lic.get("numero_pregao") or "").strip()
    if uasg.isdigit() and len(uasg) <= 6 and pregao:
        uasg_pad = uasg.zfill(6)
        parts = pregao.split("/")
        num_pad = parts[0].zfill(5) if parts else "00001"
        ano = parts[1] if len(parts) > 1 else "2026"
        mod = "05" if "preg" in (lic.get("modalidade") or "pregao").lower() else "06"
        numero_compra = f"{uasg_pad}{mod}{num_pad}{ano}"
        return f"https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/fornecedor/disputa?compra={numero_compra}"

    # 5. Detecção por objeto / portal específico
    obj = str(lic.get("objeto") or "").lower()
    portal = str(lic.get("portal") or "").upper()
    orgao = str(lic.get("orgao_nome") or "").lower()

    if "licitanet" in obj or "licitanet" in orgao:
        return "https://licitanet.com.br/"

    if portal == "SIGA_TO" or "siga.to" in link_sis or "siga" in obj:
        return "https://siga.to.gov.br/"

    if "bll" in obj or "bllcompras" in link_sis:
        return "https://bllcompras.com/"

    if "portaldecompraspublicas" in link_sis or "compras publicas" in obj:
        return "https://www.portaldecompraspublicas.com.br/"

    # Fallback seguro para Compras.gov.br login / sala
    return "https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/login"


def verificar_cdp_ativo(porta: int = 9222) -> bool:
    """Verifica se o Google Chrome está respondendo na porta de depuração remota."""
    try:
        req = urllib.request.urlopen(f"http://127.0.0.1:{porta}/json/version", timeout=0.8)
        return bool(getattr(req, "status", None) == 200)
    except Exception:
        return False


def listar_abas_cdp(porta: int = 9222) -> list[dict[str, Any]]:
    """Lista todas as abas abertas no navegador conectado à porta 9222."""
    try:
        req = urllib.request.urlopen(f"http://127.0.0.1:{porta}/json", timeout=1.0)
        if req.status == 200:
            dados = json.loads(req.read().decode("utf-8"))
            # Filtra apenas páginas (ignora service workers e extensões)
            return [t for t in dados if t.get("type") == "page"]
    except Exception:
        pass
    return []


def detectar_aba_disputa_ativa(url_alvo: str = "", termo: str = "", porta: int = 9222) -> Optional[dict[str, Any]]:
    """
    Localiza se já existe uma aba aberta no Chrome correspondente à disputa atual.
    Procura por correspondência de URL, código de compra, 'disputa' ou portais de licitação.
    """
    abas = listar_abas_cdp(porta)
    if not abas:
        return None

    url_alvo_clean = (url_alvo or "").strip().lower()
    termo_clean = (termo or "").strip().lower()

    # 1. Correspondência exata ou parcial de URL
    if url_alvo_clean and "pncp.gov.br" not in url_alvo_clean:
        # Se tem código de compra
        m_compra = re.search(r"compra=(\d+)", url_alvo_clean)
        cod_compra = m_compra.group(1) if m_compra else ""

        for aba in abas:
            u = aba.get("url", "").lower()
            if cod_compra and cod_compra in u:
                return aba
            if url_alvo_clean in u or (len(url_alvo_clean) > 20 and u in url_alvo_clean):
                return aba

    # 2. Busca por termo ou número do pregão / órgão
    if termo_clean:
        for aba in abas:
            tit = aba.get("title", "").lower()
            u = aba.get("url", "").lower()
            if termo_clean in tit or termo_clean in u:
                return aba

    # 3. Procura qualquer aba que esteja em sala de disputa ao vivo
    for aba in abas:
        u = aba.get("url", "").lower()
        if "disputa" in u:
            return aba

    # 4. Procura aba do Compras.gov.br / Serpro / Licitanet autenticado
    for aba in abas:
        u = aba.get("url", "").lower()
        if any(portal in u for portal in ("comprasnet-web/seguro", "licitanet.com.br", "siga.to.gov.br")):
            return aba

    return None


def garantir_chrome_com_cdp(url: str = "", porta: int = 9222) -> bool:
    """
    Garante que o Google Chrome seja iniciado com suporte à porta de depuração (CDP),
    permitindo que o robô se conecte imediatamente a qualquer aba aberta.
    """
    if verificar_cdp_ativo(porta):
        # Já está rodando! Se passou URL, abre em nova aba
        if url:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{porta}/json/new?{url}", timeout=1.0)
            except Exception:
                pass
        return True

    # Inicia o processo nativo do Google Chrome
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
        logger.error("Nenhum binário do Google Chrome/Chromium encontrado no sistema.")
        return False

    url_inicial = url or "https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/seguro/login"
    cmd = [
        chrome_bin,
        f"--remote-debugging-port={porta}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
        url_inicial,
    ]

    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception as exc:
        logger.error(f"Erro ao abrir Google Chrome nativo: {exc}")
        return False


def extrair_dados_aba_disputa(porta: int = 9222, url_alvo: str = "") -> Optional[dict[str, Any]]:
    """
    Conecta via CDP à sessão do Chrome na porta especificada, localiza a aba de disputa
    e extrai os dados do certame (órgão, UASG, número, modalidade) e todos os itens com
    seus respectivos lances e status ao vivo.
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
                        if "disputa" in u:
                            disputa_page = pg
                            break
                    except Exception:
                        pass
                if disputa_page:
                    break

            if not disputa_page:
                return None

            dados = disputa_page.evaluate(r"""() => {
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

                const h = document.querySelector('app-cabecalho-disputa-fornecedor') || document.querySelector('main');
                const txtH = h ? h.innerText : '';
                const mMod = txtH.match(/(Dispensa\s+Eletr[ôo]nica|Preg[ãa]o\s+Eletr[ôo]nico|Concorr[êe]ncia)\s+N[°º]?\s*([\d\/]+)/i);
                const mUasg = txtH.match(/UASG\s*(\d+)\s*-\s*([^\n\r]+)/i);

                let uf = 'BR';
                const orgaoTexto = mUasg ? mUasg[2].trim() : '';
                const mUf = orgaoTexto.match(/\b([A-Z]{2})\b/) || txtH.match(/\bUF[:\s]+([A-Z]{2})\b/i);
                if (mUf) {
                    uf = mUf[1].toUpperCase();
                } else if (/CEARA|CEARÁ/i.test(orgaoTexto)) {
                    uf = 'CE';
                } else if (/TOCANTINS/i.test(orgaoTexto)) {
                    uf = 'TO';
                } else if (/SAO PAULO|SÃO PAULO/i.test(orgaoTexto)) {
                    uf = 'SP';
                }

                const certame = {
                    modalidade: mMod ? mMod[1] : 'Dispensa Eletrônica',
                    numero_pregao: mMod ? mMod[2] : '',
                    numero_uasg: mUasg ? mUasg[1] : '',
                    orgao_nome: orgaoTexto || 'Órgão Licitante',
                    uf: uf,
                    portal: 'COMPRAS_GOV',
                    link_sistema: window.location.href,
                    url_sala_disputa: window.location.href,
                };

                const cards = Array.from(document.querySelectorAll('.cp-itens-disputa, [class*="cp-itens-disputa"], app-identificacao-e-fase-item'));
                const itensProcessados = [];
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

                    const elId = cardEl.querySelector('app-identificacao-item span:first-child') || cardEl.querySelector('span:first-child');
                    const elDesc = cardEl.querySelector('app-identificacao-item span:last-child') || cardEl.querySelector('.text-uppercase');
                    const elMelhor = cardEl.querySelector('[data-test="valor-geral"]');
                    const elMeu = cardEl.querySelector('[data-test="valor-fornec"]');
                    const elSituacao = cardEl.querySelector('[data-test="situacao-item"]');
                    const elIcon = cardEl.querySelector('i[title]');
                    const txtCard = cardEl.innerText;

                    let num = elId ? parseInt(elId.innerText.trim(), 10) : null;
                    if (!num || isNaN(num)) {
                        const mNum = txtCard.match(/^\s*(\d+)\s+[A-Za-z]/m);
                        if (mNum) num = parseInt(mNum[1], 10);
                    }
                    if (!num || isNaN(num) || numerosVistos.has(num)) return;
                    numerosVistos.add(num);

                    const mInt = txtCard.match(/intervalo\s+m[íi]nimo[^:]*:\s*([\d.,]+%?)/i);
                    const mel = elMelhor ? parseMoeda(elMelhor.innerText) : null;
                    const meu = elMeu ? parseMoeda(elMeu.innerText) : null;
                    const titleIcon = elIcon ? elIcon.getAttribute('title') : '';

                    let pos = 2;
                    if (titleIcon && (titleIcon.toLowerCase().includes('ganhando') || titleIcon.toLowerCase().includes('primeiro'))) {
                        pos = 1;
                    } else if (meu !== null && mel !== null && meu <= mel) {
                        pos = 1;
                    }

                    let statusFase = 'DESCONHECIDO';
                    const uTxt = txtCard.toUpperCase();
                    if (uTxt.includes('IMINÊNCIA') || uTxt.includes('IMINENCIA')) {
                        statusFase = 'IMINENCIA';
                    } else if (uTxt.includes('RANDÔMICO') || uTxt.includes('RANDOMICO') || uTxt.includes('ALEATÓRIO') || uTxt.includes('ALEATORIO')) {
                        statusFase = 'RANDOMICO';
                    } else if (uTxt.includes('ENCERRADO') || uTxt.includes('HOMOLOGADO')) {
                        statusFase = 'ENCERRADO';
                    } else if (uTxt.includes('ABERTO') || uTxt.includes('EM DISPUTA')) {
                        statusFase = 'ABERTO';
                    }

                    let segundosRestantes = null;
                    let textoTempo = '';
                    const mTempo = txtCard.match(/(\d{2}):(\d{2})/);
                    if (mTempo) {
                        const mins = parseInt(mTempo[1], 10);
                        const segs = parseInt(mTempo[2], 10);
                        segundosRestantes = (mins * 60) + segs;
                        textoTempo = `${mTempo[1]}:${mTempo[2]}`;
                    }

                    itensProcessados.push({
                        numero_item: num,
                        descricao: elDesc ? elDesc.innerText.trim() : `Item ${num}`,
                        melhor_lance: mel,
                        nosso_ultimo_lance: meu,
                        situacao: elSituacao ? elSituacao.innerText.trim() : 'Em disputa',
                        posicao: pos,
                        intervalo: mInt ? mInt[1] : '0,50%',
                        status_fase: statusFase,
                        segundos_restantes: segundosRestantes,
                        texto_tempo: textoTempo,
                    });
                });

                return { certame, itens: itensProcessados };
            }""")
            return dados if isinstance(dados, dict) else None
    except Exception as exc:
        logger.warning(f"Não foi possível extrair dados ao vivo da aba CDP: {exc}")
        return None


class DisputaManager:
    """
    Gerenciador de Ciclo de Disputa em Tempo Real:
    - Polling Adaptativo: acelera o monitoramento quando a disputa esquenta (Iminência, Randômico, <=45s).
    - Modo Sniper: retém e calibra disparos cirúrgicos nos últimos segundos da janela de disputa.
    - Notificação e Alerta de Liderança integrados.
    """

    def __init__(self, browser_bot: Any, engine: Any, notifier: Any):
        self.bot = browser_bot
        self.engine = engine
        self.notifier = notifier
        self.itens_monitorados: dict[str, dict[str, Any]] = {}
        self.running = False

    async def ciclo_disputa_item(self, page: Any, card_item: Any, config_item: dict[str, Any]) -> float:
        """
        Executa a decisão em tempo real com polling adaptativo e timing sniper.
        """
        num_item = str(config_item["num_item"])
        stop_loss = Decimal(str(config_item["stop_loss"]))
        modo_sniper = config_item.get("modo_sniper", True)

        # 1. Extrai situação em tempo real
        dados_tempo = await self.bot.extrair_status_e_tempo_item(page, card_item)
        status_fase = dados_tempo.get("status", "DESCONHECIDO")
        segundos = dados_tempo.get("segundos_restantes")

        # 2. Polling Adaptativo: Define a próxima janela de checagem
        # Se estiver em Iminência, Randômico ou com menos de 45 segundos, acelera
        if status_fase in ["IMINENCIA", "RANDOMICO"] or (segundos is not None and segundos <= 45):
            intervalo_polling = 1.0  # Ultra-rápido (1 segundo)
        else:
            intervalo_polling = 4.0  # Econômico para poupar CPU

        # 3. Leitura dos lances do card
        # (melhor_lance_mercado, meu_ultimo_lance, estou_em_primeiro)
        dados_lances = await self.bot.extrair_valores_card(card_item)
        estou_liderando = dados_lances.get("estou_liderando", False)
        melhor_mercado = dados_lances.get("melhor_mercado")

        # Atualiza e verifica mudança de status para alerta sonoro / push
        if melhor_mercado is not None:
            await self.notifier.verificar_mudanca_lideranca(
                item_id=num_item,
                liderando_agora=estou_liderando,
                melhor_mercado=melhor_mercado,
            )

        # Se já estiver em 1º lugar, não faz nada
        if estou_liderando:
            return intervalo_polling

        # 4. Cálculo do próximo lance através do Engine Rígido (respeitando rigorosamente o stop-loss)
        intervalo_min = config_item.get("degrau_minimo", Decimal("0.01"))
        if hasattr(self.engine, "calcular_contra_lance"):
            novo_lance = self.engine.calcular_contra_lance(
                num_item=num_item,
                melhor_mercado=melhor_mercado,
                stop_loss=stop_loss,
                intervalo_minimo=intervalo_min,
            )
        elif hasattr(self.engine, "processar_lance"):
            decisao = self.engine.processar_lance(
                num_item=int(num_item) if num_item.isdigit() else 1,
                melhor_lance_mercado=float(melhor_mercado) if melhor_mercado else None,
                nossa_posicao=1 if estou_liderando else 2,
            )
            novo_lance = (
                Decimal(str(decisao.valor_lance))
                if decisao and decisao.deve_dar_lance and decisao.valor_lance is not None
                else None
            )
        else:
            from engine import calcular_contra_lance
            novo_lance = calcular_contra_lance(
                num_item=num_item,
                melhor_mercado=melhor_mercado,
                stop_loss=stop_loss,
                intervalo_minimo=intervalo_min,
            )

        if not novo_lance:
            # Stop-loss atingido ou lance inviável pelo motor
            return intervalo_polling

        # 5. Modo Sniper: nos modelos com prorrogação ou etapa aberta,
        # disparar o contra-lance nos últimos 4 segundos antes do encerramento da rodada
        if modo_sniper and segundos is not None:
            if segundos > 4:
                # Faltam mais de 4 segundos; agenda checagem para a janela crítica
                return max(1.0, float(segundos - 4))

            logger.info(
                f"[SNIPER] 🎯 Disparando contra-lance cirúrgico Item {num_item}: "
                f"R$ {novo_lance:.2f} a {segundos}s do encerramento (Stop-Loss: R$ {stop_loss:.2f})"
            )

        # Envia o lance e confirma modal
        sucesso = await self.bot.enviar_lance_comprasnet(page, num_item, novo_lance)
        if sucesso:
            msg = f"🚀 *Lance Disparado!* Item {num_item} | Valor: R$ {novo_lance:,.2f}"
            if hasattr(self.notifier, "enviar_telegram"):
                await self.notifier.enviar_telegram(msg)
            elif hasattr(self.notifier, "telegram") and hasattr(self.notifier.telegram, "enviar_mensagem"):
                self.notifier.telegram.enviar_mensagem(msg)

        return intervalo_polling

