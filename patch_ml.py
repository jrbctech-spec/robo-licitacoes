import re

with open("app_gui.py", "r", encoding="utf-8") as f:
    content = f.read()

helper = """
def cotar_no_ml_gui(parent_widget, prod_dict):
    import threading
    import webbrowser
    import tkinter.messagebox as messagebox
    import customtkinter as ctk
    from precificador_mercadolivre import analisar_viabilidade_ml

    descricao = prod_dict.get("objeto", "")
    valor_ref = float(prod_dict.get("valor_estimado") or 0.0)

    modal = ctk.CTkToplevel(parent_widget)
    modal.title("Cotação Inteligente - Mercado Livre")
    modal.geometry("550x380")
    modal.transient(parent_widget.winfo_toplevel())
    modal.grab_set()

    lbl_status = ctk.CTkLabel(modal, text="⏳ Buscando preços no Mercado Livre...", font=("Arial", 14, "bold"))
    lbl_status.pack(pady=20)
    
    frame_res = ctk.CTkFrame(modal, fg_color="transparent")
    frame_res.pack(fill="both", expand=True, padx=20, pady=10)

    def _worker():
        res = analisar_viabilidade_ml(descricao, valor_ref)
        parent_widget.after(0, lambda: _exibir_resultado(res))
        
    def _exibir_resultado(res):
        lbl_status.destroy()
        if not res.get("sucesso"):
            ctk.CTkLabel(frame_res, text=f"❌ Falha: {res.get('motivo')}", text_color="red").pack()
            return
            
        ctk.CTkLabel(frame_res, text="🎯 Resultado da Cotação", font=("Arial", 16, "bold")).pack(pady=5)
        
        texto_res = (
            f"🛒 {res['titulo_oferta'][:60]}...\n\n"
            f"💰 Menor Preço (ML): R$ {res['custo_ml']:,.2f}\n"
            f"🚚 Frete Estimado: R$ {res['frete_estimado']:,.2f}\n"
            f"📦 Custo Total: R$ {res['custo_total']:,.2f}\n\n"
            f"⚖️ Margem (30%) - Piso: R$ {res['piso_minimo_30pct']:,.2f}\n"
            f"📈 Lucro Projetado: R$ {res['lucro_estimado']:,.2f} ({res['margem_estimada_pct']}%)\n"
            f"✅ Viável para Venda? {'SIM' if res['viavel'] else 'NÃO'}"
        )
        ctk.CTkLabel(frame_res, text=texto_res, justify="left").pack(pady=10)
        
        btn_frame = ctk.CTkFrame(frame_res, fg_color="transparent")
        btn_frame.pack(pady=10)
        
        ctk.CTkButton(btn_frame, text="🌐 Abrir Anúncio", command=lambda: webbrowser.open(res["link_oferta"])).pack(side="left", padx=5)
        
        def _aplicar():
            import os
            from supabase import create_client
            try:
                url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
                key = os.getenv("SUPABASE_KEY")
                sb = create_client(url, key)
                num_controle = prod_dict.get("numero_controle_pncp") or prod_dict.get("id")
                
                sb.table("licitacoes").update({
                    "custo_real": res["custo_total"],
                    "piso_seguro": res["piso_minimo_30pct"]
                }).eq("numero_controle_pncp" if "numero_controle_pncp" in prod_dict else "id", num_controle).execute()
                
                messagebox.showinfo("Sucesso", "Custo Base aplicado e Stop-Loss recalculado!")
                modal.destroy()
            except Exception as e:
                messagebox.showerror("Erro", str(e))
                
        ctk.CTkButton(btn_frame, text="✅ Aplicar como Custo Base", command=_aplicar, fg_color="green").pack(side="left", padx=5)

    threading.Thread(target=_worker, daemon=True).start()

"""

if "def cotar_no_ml_gui" not in content:
    content = content.replace('import json', 'import json\n' + helper, 1)

# Modify PainelProdutos
old_produtos = """        ctk.CTkButton(btn_frame, text="📄 Gerar Proposta Comercial", command=self.gerar_proposta_comercial).grid(row=0, column=2, padx=10)"""
new_produtos = old_produtos + """
        ctk.CTkButton(btn_frame, text="🛒 Cotar no Mercado Livre", command=self.cotar_mercado_livre).grid(row=0, column=3, padx=10)
        
    def cotar_mercado_livre(self):
        prod = self._get_selected_prod()
        if not prod: return
        cotar_no_ml_gui(self, prod)
"""
content = content.replace(old_produtos, new_produtos)

# Modify PainelEditais
old_editais = """        lbl_hint.grid(row=2, column=0, sticky="w", padx=16, pady=(0, 8))"""
new_editais = old_editais + """
        btn_cotar = ctk.CTkButton(card_itens, text="🛒 Cotar no Mercado Livre", command=self.cotar_mercado_livre)
        btn_cotar.grid(row=3, column=0, sticky="w", padx=16, pady=(0, 10))

    def cotar_mercado_livre(self):
        sel = self.tree_itens.selection()
        if not sel:
            import tkinter.messagebox as messagebox
            messagebox.showwarning("Aviso", "Selecione um item do edital primeiro.")
            return
        if hasattr(self, "licitacao_ativa"):
            cotar_no_ml_gui(self, self.licitacao_ativa)
"""
content = content.replace(old_editais, new_editais)

with open("app_gui.py", "w", encoding="utf-8") as f:
    f.write(content)
