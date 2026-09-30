"""
Módulo: validador_margem.py
Calcula viabilidade contra custos de distribuidores usando Decimal.
"""
from decimal import Decimal, ROUND_HALF_UP

MARGEM_MINIMA = Decimal("0.30") # 30%

def avaliar_viabilidade_item(
    valor_referencia_governo: Decimal,
    custo_distribuidor: Decimal,
    frete_despesas: Decimal = Decimal("0.00")
) -> dict:
    """
    Avalia se o preço estimado do governo permite lucrar >= 30%.
    """
    custo_total = (custo_distribuidor + frete_despesas).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    
    # Preço mínimo para garantir rigorosamente 30% de margem sobre o custo
    piso_venda = (custo_total * (Decimal("1.00") + MARGEM_MINIMA)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    
    if valor_referencia_governo < piso_venda:
        return {
            "viavel": False,
            "motivo": f"Inviável: Referência (R$ {valor_referencia_governo}) abaixo do piso com 30% (R$ {piso_venda})"
        }
    
    # Se o valor do governo for maior que nosso piso, calculamos o lucro potencial máximo
    lucro_potencial = valor_referencia_governo - custo_total
    margem_potencial = (lucro_potencial / custo_total).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) * 100
    
    return {
        "viavel": True,
        "custo_total": custo_total,
        "piso_minimo_30pct": piso_venda,
        "teto_governo": valor_referencia_governo,
        "lucro_maximo_estimado": lucro_potencial,
        "margem_maxima_estimada_pct": margem_potencial,
        "lance_sugerido_entrada": (valor_referencia_governo * Decimal("0.98")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    }
