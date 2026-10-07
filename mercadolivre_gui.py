import threading
import urllib.parse
import webbrowser
import tkinter.messagebox as messagebox
import customtkinter as ctk
from precificador_mercadolivre import (
    analisar_viabilidade_ml,
    limpar_termo_busca,
)


def cotar_no_ml_gui(parent_widget, prod_dict):
    """
    Janela de Cotação Inteligente de Hardware e Peças.
    Permite busca filtrada no Mercado Livre, ajuste manual do termo e abertura direta na Shopee.
    """
    descricao = str(prod_dict.get("descricao") or prod_dict.get("objeto") or "")
    valor_ref = float(prod_dict.get("valor_referencia") or prod_dict.get("valor_estimado") or 0.0)

    # Isole apenas o produto/peça (removendo termos de serviço, burocracia, etc.)
    termo_inicial = limpar_termo_busca(descricao)

    modal = ctk.CTkToplevel(parent_widget)
    modal.title("🛒 Cotação Inteligente - Mercado Livre & Shopee")
    modal.geometry("620x460")
    modal.minsize(560, 420)
    modal.transient(parent_widget.winfo_toplevel())
    modal.grab_set()

    # Barra de busca customizável no topo
    frame_busca = ctk.CTkFrame(modal, fg_color="transparent")
    frame_busca.pack(fill="x", padx=16, pady=(14, 8))
    frame_busca.grid_columnconfigure(0, weight=1)

    ctk.CTkLabel(
        frame_busca,
        text="🔍 Peça / Hardware Isolado:",
        font=("Arial", 11, "bold"),
        text_color="#9CA3AF"
    ).grid(row=0, column=0, sticky="w", padx=2, pady=(0, 2))

    entry_termo = ctk.CTkEntry(
        frame_busca,
        placeholder_text="Ex: Bateria Nobreak 12V 7Ah, Toner HP 105A, Switch 24 portas...",
        font=("Arial", 12)
    )
    entry_termo.insert(0, termo_inicial)
    entry_termo.grid(row=1, column=0, sticky="ew", padx=(0, 6))

    def _obter_termo_atual():
        return entry_termo.get().strip() or termo_inicial

    def _abrir_shopee():
        termo_atual = _obter_termo_atual()
        webbrowser.open(f"https://shopee.com.br/search?keyword={urllib.parse.quote(termo_atual)}")

    def _abrir_mercadolivre():
        termo_atual = _obter_termo_atual()
        webbrowser.open(f"https://lista.mercadolivre.com.br/{urllib.parse.quote(termo_atual)}")

    btn_shopee = ctk.CTkButton(
        frame_busca,
        text="🟠 Abrir na Shopee",
        font=("Arial", 11, "bold"),
        fg_color="#EE4D2D",
        hover_color="#D03E1F",
        text_color="#FFFFFF",
        command=_abrir_shopee,
        width=140,
    )
    btn_shopee.grid(row=1, column=1, padx=(0, 6))

    btn_abrir_ml = ctk.CTkButton(
        frame_busca,
        text="🟡 Abrir no Mercado Livre",
        font=("Arial", 11, "bold"),
        fg_color="#FFE600",
        hover_color="#E5CF00",
        text_color="#1F2937",
        command=_abrir_mercadolivre,
        width=160,
    )
    btn_abrir_ml.grid(row=1, column=2, padx=(0, 6))

    btn_repesquisar = ctk.CTkButton(
        frame_busca,
        text="🔍 Cotar Aqui",
        font=("Arial", 11, "bold"),
        fg_color="#38BDF8",
        hover_color="#0284C7",
        text_color="#080C14",
        width=95,
    )
    btn_repesquisar.grid(row=1, column=3)

    # Frame de resultados
    frame_conteudo = ctk.CTkFrame(modal, fg_color="#181A20", corner_radius=10)
    frame_conteudo.pack(fill="both", expand=True, padx=16, pady=(4, 14))

    lbl_status = ctk.CTkLabel(
        frame_conteudo,
        text=f"⏳ Cotando '{termo_inicial}' no Mercado Livre...",
        font=("Arial", 13, "bold"),
        text_color="#38BDF8"
    )
    lbl_status.pack(pady=30)

    frame_detalhes = ctk.CTkFrame(frame_conteudo, fg_color="transparent")

    def _executar_busca():
        termo_digitado = entry_termo.get().strip()
        if not termo_digitado:
            messagebox.showwarning("Aviso", "Digite um termo para pesquisar.")
            return

        for w in frame_detalhes.winfo_children():
            w.destroy()
        frame_detalhes.pack_forget()

        lbl_status.configure(text=f"⏳ Buscando '{termo_digitado}' no Mercado Livre...")
        lbl_status.pack(pady=30)
        btn_repesquisar.configure(state="disabled")

        def _worker():
            res = analisar_viabilidade_ml(termo_digitado, valor_ref, ja_limpo=True)
            modal.after(0, lambda: _exibir_resultado(res))

        threading.Thread(target=_worker, daemon=True).start()

    btn_repesquisar.configure(command=_executar_busca)
    entry_termo.bind("<Return>", lambda e: _executar_busca())

    def _exibir_resultado(res: dict):
        lbl_status.pack_forget()
        btn_repesquisar.configure(state="normal")
        frame_detalhes.pack(fill="both", expand=True, padx=16, pady=12)

        for w in frame_detalhes.winfo_children():
            w.destroy()

        if not res.get("sucesso"):
            ctk.CTkLabel(
                frame_detalhes,
                text=f"❌ Nenhuma oferta encontrada: {res.get('motivo')}\n\nTente simplificar o termo acima e buscar novamente.",
                text_color="#EF4444",
                font=("Arial", 12)
            ).pack(pady=20)
            return

        ctk.CTkLabel(
            frame_detalhes,
            text=f"🎯 Melhor Oferta Encontrada no Mercado Livre",
            font=("Arial", 14, "bold"),
            text_color="#10B981"
        ).pack(anchor="w", pady=(0, 6))

        titulo_prod = str(res.get("titulo_oferta", ""))[:80]
        ctk.CTkLabel(
            frame_detalhes,
            text=f"📦 {titulo_prod}...",
            font=("Arial", 11, "italic"),
            text_color="#E2E8F0"
        ).pack(anchor="w", pady=(0, 8))

        # Grid financeiro
        grid_vals = ctk.CTkFrame(frame_detalhes, fg_color="#22242A", corner_radius=8)
        grid_vals.pack(fill="x", pady=6)
        grid_vals.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(grid_vals, text=f"💰 Menor Preço ML: R$ {res['custo_ml']:,.2f}", font=("Arial", 12)).grid(row=0, column=0, padx=12, pady=6, sticky="w")
        ctk.CTkLabel(grid_vals, text=f"🚚 Frete Estimado: R$ {res['frete_estimado']:,.2f}", font=("Arial", 12)).grid(row=0, column=1, padx=12, pady=6, sticky="w")
        ctk.CTkLabel(grid_vals, text=f"📦 Custo Total: R$ {res['custo_total']:,.2f}", font=("Arial", 12, "bold"), text_color="#38BDF8").grid(row=1, column=0, padx=12, pady=6, sticky="w")
        ctk.CTkLabel(grid_vals, text=f"⚖️ Stop-Loss (Piso 30%): R$ {res['piso_minimo_30pct']:,.2f}", font=("Arial", 12, "bold"), text_color="#10B981").grid(row=1, column=1, padx=12, pady=6, sticky="w")

        ref_str = f"R$ {valor_ref:,.2f}" if valor_ref > 0 else "Sigiloso / A Definir"
        status_viavel = "🟢 SIM (Viável com Margem)" if res.get("viavel") else "⚠️ ATENÇÃO (Preço Edital Apertado)"
        cor_viavel = "#10B981" if res.get("viavel") else "#F59E0B"

        ctk.CTkLabel(
            frame_detalhes,
            text=f"🏛️ Ref. Edital: {ref_str}   |   Lucro Est.: R$ {res['lucro_estimado']:,.2f} ({res['margem_estimada_pct']}%)   |   {status_viavel}",
            font=("Arial", 11, "bold"),
            text_color=cor_viavel
        ).pack(anchor="w", pady=(8, 12))

        # Botões de ação
        btn_box = ctk.CTkFrame(frame_detalhes, fg_color="transparent")
        btn_box.pack(fill="x", pady=(4, 0))

        ctk.CTkButton(
            btn_box,
            text="🌐 Abrir Anúncio no ML",
            font=("Arial", 11, "bold"),
            command=lambda: webbrowser.open(res["link_oferta"]),
            fg_color="#FFE600",
            hover_color="#E5CF00",
            text_color="#1F2937",
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_box,
            text="🟠 Abrir Busca na Shopee",
            font=("Arial", 11, "bold"),
            command=_abrir_shopee,
            fg_color="#EE4D2D",
            hover_color="#D03E1F",
            text_color="#FFFFFF",
        ).pack(side="left", padx=8)

        def _aplicar():
            import os
            from supabase_client import get_client
            try:
                sb = get_client()
                lic_id = prod_dict.get("id")
                num_controle = prod_dict.get("numero_controle_pncp")
                num_item = prod_dict.get("numero_item")

                # Se for item específico
                if lic_id and num_item:
                    try:
                        sb.table("itens_licitacao").update({
                            "custo_unitario": res["custo_total"],
                            "preco_minimo": res["piso_minimo_30pct"]
                        }).eq("licitacao_id", str(lic_id)).eq("numero_item", int(num_item)).execute()
                    except Exception:
                        pass

                # Atualiza o cabeçalho
                if lic_id:
                    sb.table("licitacoes").update({
                        "custo_real": res["custo_total"],
                        "piso_seguro": res["piso_minimo_30pct"]
                    }).eq("id", str(lic_id)).execute()
                elif num_controle:
                    sb.table("licitacoes").update({
                        "custo_real": res["custo_total"],
                        "piso_seguro": res["piso_minimo_30pct"]
                    }).eq("numero_controle_pncp", str(num_controle)).execute()

                messagebox.showinfo("Sucesso", "Custo Base aplicado e Stop-Loss recalculado no Supabase com sucesso!")
                modal.destroy()
            except Exception as e:
                messagebox.showerror("Erro", f"Falha ao salvar no Supabase:\n{e}")

        ctk.CTkButton(
            btn_box,
            text="✅ Aplicar Custo Base",
            font=("Arial", 11, "bold"),
            command=_aplicar,
            fg_color="#10B981",
            hover_color="#059669",
            text_color="#FFFFFF"
        ).pack(side="right")

    # Dispara primeira busca automática
    def _worker_inicial():
        res = analisar_viabilidade_ml(termo_inicial, valor_ref, ja_limpo=True)
        modal.after(0, lambda: _exibir_resultado(res))

    threading.Thread(target=_worker_inicial, daemon=True).start()
