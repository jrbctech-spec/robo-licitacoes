with open("browser_bot.py", "r", encoding="utf-8") as f:
    content = f.read()

header = """
from supabase_client import AsyncLogQueue, EntradaLog, log_queue, registrar_lance

CNPJ_ALVO = "65488003000161"
RAZAO_SOCIAL_ALVO = "JAKSON RIBEIRO DA COSTA"

def auto_selecionar_empresa_e_avancar(page, url_sala_final: str):
    \"\"\"
    Detecta e automatiza a seleção do CNPJ da JRBC TECH e avança para a sala de disputa.
    \"\"\"
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
"""

if "CNPJ_ALVO" not in content:
    content = content.replace("from supabase_client import AsyncLogQueue, EntradaLog, log_queue, registrar_lance", header)

target = """                # Se acabou de logar e caiu na home do portal antigo, redireciona UMA ÚNICA VEZ para a sala de disputa
                if is_intro_landing and not is_on_auth and not self._redirecionou_pos_login:"""

new_target = """                if is_intro_landing and not is_on_auth:
                    try:
                        auto_selecionar_empresa_e_avancar(active_page, self.url_alvo or "")
                    except Exception as e:
                        self._log("WARNING", f"Falha ao executar auto_selecionar_empresa_e_avancar: {e}")

                # Se acabou de logar e caiu na home do portal antigo, redireciona UMA ÚNICA VEZ para a sala de disputa
                if is_intro_landing and not is_on_auth and not self._redirecionou_pos_login:"""

if "auto_selecionar_empresa_e_avancar(active_page" not in content:
    content = content.replace(target, new_target)

with open("browser_bot.py", "w", encoding="utf-8") as f:
    f.write(content)
