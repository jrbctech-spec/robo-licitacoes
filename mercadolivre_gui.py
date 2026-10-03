import threading
import webbrowser
import tkinter.messagebox as messagebox
import customtkinter as ctk
from precificador_mercadolivre import analisar_viabilidade_ml

def cotar_no_ml_gui(parent_widget, prod_dict):
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
                key = os.getenv("SUPABASE_KEY") or ""
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
