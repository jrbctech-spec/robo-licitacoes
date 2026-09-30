import logging
from decimal import Decimal
from filtro_dispensas import buscar_dispensas_abertas
from validador_margem import avaliar_viabilidade_item

logging.basicConfig(level=logging.INFO, format="%(message)s")

def executar_teste_imediato():
    print("=== INICIANDO TESTE IMEDIATO DO ROBÔ DE LICITAÇÕES ===\n")
    
    # Vamos usar um termo amplo para garantir que a API retorne resultados abertos agora
    termo_teste = "computadores"
    print(f"🔍 Buscando Dispensas Eletrônicas abertas no PNCP para: '{termo_teste}'...")
    
    dispensas = buscar_dispensas_abertas(termo_teste)
    
    if not dispensas:
        print("Nenhuma dispensa aberta encontrada no momento para este termo. Tente mais tarde ou com outro termo.")
        return
        
    print(f"✅ {len(dispensas)} dispensas abertas encontradas!\n")

    # Vamos processar as 3 primeiras para o teste
    for i, edital in enumerate(dispensas[:3], 1):
        orgao = edital.get("orgao_nome") or edital.get("orgao_razao_social") or "Órgão Desconhecido"
        objeto = edital.get("objeto") or edital.get("description") or "Sem descrição"
        
        # Tentando obter o valor estimado do governo
        valor_estimado = edital.get("valor_global") or edital.get("valor_total_estimado")
        
        print(f"--- Dispensa #{i}: {orgao} ---")
        print(f"Objeto: {objeto[:100]}...")
        
        if not valor_estimado:
            print("❌ Valor estimado não divulgado.\n")
            continue
            
        try:
            valor_referencia = Decimal(str(valor_estimado))
        except:
            print("❌ Erro ao ler valor estimado.\n")
            continue
            
        # [MOCK DE TESTE] Simulando que encontramos o item no nosso `catalogo_custos`
        # Vamos definir nosso custo como 60% do valor do governo para garantir que bata a meta de 30% de margem.
        custo_simulado = valor_referencia * Decimal("0.60")
        
        # Passando para o validador de margem
        resultado = avaliar_viabilidade_item(
            valor_referencia_governo=valor_referencia,
            custo_distribuidor=custo_simulado
        )
        
        if resultado["viavel"]:
            margem = resultado["margem_maxima_estimada_pct"]
            print(f"🎯 DISPENSA ELETRÔNICA ENCONTRADA (Margem: {margem}%)")
            print(f"   Governo paga até: R$ {resultado['teto_governo']}")
            print(f"   Nosso Custo Fictício: R$ {resultado['custo_total']}")
            print(f"   Lance inicial sugerido: R$ {resultado['lance_sugerido_entrada']}")
            
            link_edital = edital.get("url_edital")
            if link_edital:
                print(f"   Link: {link_edital}")
        else:
            print(f"⚠️ {resultado['motivo']}")
        print("\n")

if __name__ == "__main__":
    executar_teste_imediato()
