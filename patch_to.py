import re

# 1. Modificar buscador_pncp.py para expor executar_varredura_tocantins
with open("buscador_pncp.py", "r", encoding="utf-8") as f:
    busca_content = f.read()

nova_func_busca = """
def executar_varredura_tocantins():
    logger.info("=== START: Varredura Exclusiva Tocantins (TO) ===")
    notificados = carregar_historico_notificados()
    try:
        from pncp_client import buscar_publicacoes_pncp
        for mod in [6, 8]:
            res_to = buscar_publicacoes_pncp(uf="TO", codigo_modalidade=mod, tamanho_pagina=50)
            itens_to = res_to.get("data", [])
            for item in itens_to:
                objeto = (item.get("objetoCompra") or "").lower()
                # from pncp_client TERMOS_CNAE might be needed or we can import it
                if any(t.lower() in objeto for t in TERMOS_CNAE):
                    processar_e_salvar_edital(item, "TI-TO", "Pregão Eletrônico" if mod == 6 else "Dispensa Eletrônica", notificados)
            time.sleep(1.5)
    except Exception as e:
        logger.error(f"Erro ao varrer PNCP TO: {e}")
        
    logger.info(">>> Varrendo NúcleoGov (Municípios do TO)...")
    op_nucleo = varrer_municipios_nucleogov()
    processar_outras_fontes(op_nucleo, notificados)
    
    logger.info(">>> Varrendo SIGA-TO (Governo do TO)...")
    op_siga = varrer_siga_to()
    processar_outras_fontes(op_siga, notificados)
    logger.info("=== FIM: Varredura Tocantins ===")
"""

if "def executar_varredura_tocantins" not in busca_content:
    busca_content = busca_content.replace("def executar_varredura_completa():", nova_func_busca + "\ndef executar_varredura_completa():")
    with open("buscador_pncp.py", "w", encoding="utf-8") as f:
        f.write(busca_content)

# 2. Atualizar app_gui.py
with open("app_gui.py", "r", encoding="utf-8") as f:
    app_content = f.read()

# PainelDashboard header injection
dash_header_target = """        ctk.CTkLabel(header_frame, text="📊 Dashboard Executivo JRBC TECH", font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")"""
dash_header_new = dash_header_target + """
        
        def _iniciar_busca_to():
            import threading
            import tkinter.messagebox as messagebox
            from buscador_pncp import executar_varredura_tocantins
            def worker():
                executar_varredura_tocantins()
                self.after(0, lambda: messagebox.showinfo("Pronto", "Varredura do Tocantins concluída!"))
            threading.Thread(target=worker, daemon=True).start()
            
        btn_start_to = ctk.CTkButton(header_frame, text="⚡ START / BUSCAR TOCANTINS", fg_color="#10b981", command=_iniciar_busca_to)
        btn_start_to.pack(side="right", padx=(10, 0))
"""
if "⚡ START / BUSCAR TOCANTINS" not in app_content.split("PainelDashboard")[1][:1000]:
    app_content = app_content.replace(dash_header_target, dash_header_new)

# PainelServicos modifications
serv_header_target = """        ctk.CTkLabel(header_frame, text="🛠️ Serviços & Field Service", font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")"""
serv_header_new = serv_header_target + """
        
        def _iniciar_busca_to_serv():
            import threading
            import tkinter.messagebox as messagebox
            from buscador_pncp import executar_varredura_tocantins
            def worker():
                executar_varredura_tocantins()
                self.after(0, self.carregar_servicos_nuvem)
                self.after(0, lambda: messagebox.showinfo("Pronto", "Varredura do Tocantins concluída! A tabela foi atualizada."))
            threading.Thread(target=worker, daemon=True).start()
            
        btn_start_to = ctk.CTkButton(header_frame, text="⚡ START / BUSCAR TOCANTINS", fg_color="#10b981", command=_iniciar_busca_to_serv)
        btn_start_to.pack(side="right", padx=(10, 0))
"""
if "btn_start_to = ctk.CTkButton(header_frame, text=\"⚡ START" not in app_content.split("class PainelServicos")[1][:1000]:
    app_content = app_content.replace(serv_header_target, serv_header_new)

# Filter 0,00 outside TO in PainelServicos.carregar_servicos_nuvem
# In def _concluir_carregamento(self) for PainelServicos
# Need to inject logic.
serv_concluir_target = """    def _concluir_carregamento(self):
        for row in self.tree_servicos.get_children():
            self.tree_servicos.delete(row)"""
serv_concluir_new = serv_concluir_target + """
        
        dados_filtrados = []
        for s in self.dados_nuvem:
            uf = s.get("uf", "")
            val = float(s.get("valor_estimado") or 0.0)
            if val == 0 and uf != "TO" and uf != "BR":
                continue
            dados_filtrados.append(s)
        self.dados_nuvem = dados_filtrados
"""
if "if val == 0 and uf != \"TO\" and uf !=" not in app_content.split("class PainelServicos")[1]:
    app_content = app_content.replace(serv_concluir_target, serv_concluir_new)

# Buttons Cadastrar Proposta and Enviar para Disputa
serv_btns_target = """        ctk.CTkButton(btn_frame, text="📄 Gerar Planilha de Custos", command=self.gerar_planilha_servicos).grid(row=0, column=2, padx=10)"""
serv_btns_new = serv_btns_target + """
        ctk.CTkButton(btn_frame, text="🚀 Cadastrar Proposta no Portal", fg_color="#2563eb", command=self.cadastrar_proposta).grid(row=0, column=3, padx=10)
        ctk.CTkButton(btn_frame, text="⚔️ Enviar para Disputa", fg_color="#dc2626", command=self.enviar_para_disputa).grid(row=0, column=4, padx=10)

    def cadastrar_proposta(self):
        sel = self.tree_servicos.selection()
        if not sel:
            import tkinter.messagebox as messagebox
            messagebox.showwarning("Aviso", "Selecione um serviço primeiro.")
            return
        idx = int(self.tree_servicos.index(sel[0]))
        if idx < len(self.dados_nuvem):
            item = self.dados_nuvem[idx]
            link = item.get("link_edital")
            if link and link != "https://pncp.gov.br":
                import webbrowser
                webbrowser.open(link)
            else:
                import tkinter.messagebox as messagebox
                messagebox.showinfo("Aviso", "Não há link direto para este portal.")

    def enviar_para_disputa(self):
        sel = self.tree_servicos.selection()
        if not sel:
            import tkinter.messagebox as messagebox
            messagebox.showwarning("Aviso", "Selecione um serviço primeiro.")
            return
        idx = int(self.tree_servicos.index(sel[0]))
        if idx < len(self.dados_nuvem):
            item = self.dados_nuvem[idx]
            from tkinter import messagebox
            import traceback
            try:
                # Pre-seleciona no Painel de Editais e troca de aba (Console)
                uf = item.get("uf", "")
                orgao = item.get("orgao_nome", "")[:20]
                pregao = item.get("numero_pregao", "")
                chave = f"[{uf}] {orgao} - {pregao}"
                
                # Assume que PainelEditais é acessível via app
                # The console is PainelConsole in self.app.frames["PainelConsole"]
                painel_console = self.app.frames.get("Console")
                painel_editais = self.app.frames.get("Editais")
                if painel_editais:
                    painel_editais.atualizar_lista_certames()
                if painel_console:
                    # Switch to console
                    self.app.sidebar_btn_console.invoke()
                    painel_console._on_click_connect() # Start the connection logic
                    messagebox.showinfo("Disputa", f"Enviando {chave} para o Console da Sessão...")
                else:
                    messagebox.showwarning("Aviso", "Painel Console não encontrado.")
            except Exception as e:
                logger.error(f"Erro ao enviar disputa: {traceback.format_exc()}")
"""
if "def cadastrar_proposta" not in app_content.split("class PainelServicos")[1]:
    app_content = app_content.replace(serv_btns_target, serv_btns_new)

with open("app_gui.py", "w", encoding="utf-8") as f:
    f.write(app_content)
