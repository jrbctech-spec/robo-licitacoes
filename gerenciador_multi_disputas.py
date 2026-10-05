import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import requests
from supabase import create_client

from navegador_gestor import obter_gestor

logger = logging.getLogger("MultiDisputas")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip('"\'')
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip('"\'')

def notificar_telegram(texto: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"Erro ao enviar Telegram: {e}")

@dataclass
class RegraItemMonitorado:
    numero_item: int
    descricao: str
    preco_minimo: float
    decremento_minimo: float = 1.00
    melhor_lance_mercado: Optional[float] = None
    nossa_posicao: Optional[int] = None
    liderando: bool = False

@dataclass
class CertameMonitorado:
    id_banco: str
    uasg: str
    numero_pregao: str
    orgao: str
    url_sala: str
    itens: Dict[int, RegraItemMonitorado] = field(default_factory=dict)
    ativo: bool = True

class MotorMultiDisputas:
    def __init__(self):
        self.certames: Dict[str, CertameMonitorado] = {}
        self._task_loop: Optional[asyncio.Task] = None
        self._rodando = False
        
        url_sb = os.getenv("SUPABASE_URL", "")
        key_sb = os.getenv("SUPABASE_KEY", "")
        if url_sb and key_sb:
            self.sb = create_client(url_sb, key_sb)
        else:
            self.sb = None

    def adicionar_certame(self, certame: CertameMonitorado):
        chave = f"{certame.uasg}-{certame.numero_pregao}"
        self.certames[chave] = certame
        logger.info(f"✅ Certame {chave} adicionado ao monitoramento.")
        self._atualizar_supabase(certame.id_banco, "EM_DISPUTA")

    def remover_certame(self, uasg: str, pregao: str):
        chave = f"{uasg}-{pregao}"
        if chave in self.certames:
            c = self.certames.pop(chave)
            logger.info(f"❌ Certame {chave} removido.")
            self._atualizar_supabase(c.id_banco, "FINALIZADO")

    def _atualizar_supabase(self, id_banco: str, status_analise: str):
        if not self.sb or not id_banco or id_banco == "direct_import":
            return
        try:
            self.sb.table("licitacoes").update({
                "status": "em_andamento" if status_analise == "EM_DISPUTA" else "encerrado",
                "status_analise": status_analise
            }).eq("id", id_banco).execute()
        except Exception as e:
            logger.error(f"Erro atualizando banco: {e}")

    async def iniciar_loop(self):
        if self._rodando:
            return
        self._rodando = True
        logger.info("🚀 Iniciando Motor Assíncrono Multi-Disputas")
        self._task_loop = asyncio.create_task(self._loop_principal())

    async def parar_loop(self):
        self._rodando = False
        if self._task_loop:
            self._task_loop.cancel()
        logger.info("🛑 Motor Multi-Disputas Parado.")

    async def _loop_principal(self):
        gestor = await obter_gestor()
        
        while self._rodando:
            if not self.certames:
                await asyncio.sleep(5)
                continue

            certames_ativos = list(self.certames.values())
            for certame in certames_ativos:
                if not certame.ativo:
                    continue
                    
                try:
                    page = await gestor.obter_pagina_para_url(certame.url_sala)
                    await self._processar_pagina_certame(page, certame)
                except Exception as e:
                    logger.error(f"Erro ao processar {certame.uasg}-{certame.numero_pregao}: {e}")
            
            await asyncio.sleep(4)

    async def _processar_pagina_certame(self, page, certame: CertameMonitorado):
        linhas = await page.query_selector_all("table tbody tr, .item-licitacao, tr[data-item]")
        if not linhas:
            return

        for tr in linhas:
            texto_linha = await tr.inner_text()
            if not texto_linha.strip(): continue

            m_num = re.search(r"(?:Item\s*|^\s*)(\d+)", texto_linha, re.IGNORECASE)
            if not m_num: continue
            
            num_item = int(m_num.group(1))
            if num_item not in certame.itens: continue
            
            regra = certame.itens[num_item]
            
            valores = re.findall(r"R\$\s*([\d\.,]+)", texto_linha)
            v_float = []
            for v in valores:
                try:
                    v_float.append(float(v.replace(".", "").replace(",", ".")))
                except ValueError:
                    pass
                    
            melhor_mercado = min(v_float) if v_float else None
            m_pos = re.search(r"(\d+)º", texto_linha)
            nossa_pos = int(m_pos.group(1)) if m_pos else 2
            
            if melhor_mercado is not None:
                regra.melhor_lance_mercado = melhor_mercado
                regra.nossa_posicao = nossa_pos
                
                if nossa_pos == 1:
                    if not regra.liderando:
                        regra.liderando = True
                        msg = f"🟢 <b>[LIDERANDO]</b>\nÓrgão: {certame.orgao}\nPregão: {certame.numero_pregao}\nItem {num_item}: R$ {melhor_mercado:,.2f}"
                        notificar_telegram(msg)
                else:
                    if regra.liderando:
                        regra.liderando = False
                        msg = f"⚠️ <b>[ULTRAPASSADO]</b>\nÓrgão: {certame.orgao}\nPregão: {certame.numero_pregao}\nItem {num_item}\nLíder: R$ {melhor_mercado:,.2f}"
                        notificar_telegram(msg)
                        
                    lance = round(melhor_mercado - regra.decremento_minimo, 2)
                    if lance >= regra.preco_minimo:
                        sucesso = await self._enviar_lance(tr, lance)
                        if sucesso:
                            msg = f"🚀 <b>[LANCE ENVIADO]</b>\nPregão: {certame.numero_pregao} | Item {num_item}\nValor: R$ {lance:,.2f}"
                            notificar_telegram(msg)
                    else:
                        logger.warning(f"🛑 [STOP-LOSS] Pregão {certame.numero_pregao} Item {num_item}. Lance {lance} abortado (Piso: {regra.preco_minimo})")

    async def _enviar_lance(self, linha, lance: float) -> bool:
        try:
            inp = await linha.query_selector("input[name*='lance'], input[type='text'], input.valor-lance")
            if not inp: return False
            await inp.fill(f"{lance:.2f}".replace(".", ","))
            await asyncio.sleep(0.3)
            btn = await linha.query_selector("button:has-text('Enviar'), button:has-text('Confirmar'), button.btn-lance")
            if btn:
                await btn.click()
                return True
        except Exception as e:
            logger.error(f"Erro ao digitar/enviar lance: {e}")
        return False

# Instância global Singleton
_motor_multi = MotorMultiDisputas()

def obter_motor_multi() -> MotorMultiDisputas:
    return _motor_multi
