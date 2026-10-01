"""
Módulo: motor_prioridade_geografica.py
Finalidade: Aplica a matriz de decisão geográfica da JRBC TECH:
- Tocantins (TO): Prioridade de captação (presencial e remoto).
- Demais Estados: Apenas contratos remotos ou venda de produtos com lucro líquido garantido.
"""

from decimal import Decimal, ROUND_HALF_UP
import unicodedata

def normalizar(texto: str) -> str:
    if not texto:
        return ""
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    ).lower()

# Termos que comprovam execução remota para outros estados
TERMOS_REMOTO = [
    "remoto", "a distancia", "service desk", "help desk",
    "suporte remoto", "nuvem", "cloud", "monitoramento"
]

def avaliar_viabilidade_certame(payload: dict) -> dict:
    """
    Avalia se o edital deve ser aceito e notificado.
    Retorna: {'aprovado': bool, 'motivo': str, 'prioridade': str, 'lucro_estimado': Decimal, 'piso_seguro': Decimal}
    """
    uf = (payload.get("uf") or "BR").strip().upper()
    tipo = payload.get("tipo_contratacao", "SERVICO")
    objeto_norm = normalizar(payload.get("objeto", ""))
    valor_estimado = Decimal(str(payload.get("valor_estimado") or "0.00"))

    # =========================================================================
    # REGRA 1: ESTADO DO TOCANTINS (CASA / PRIORIDADE MÁXIMA)
    # =========================================================================
    if uf == "TO":
        margem_alvo = Decimal("0.50") if tipo == "SERVICO" else Decimal("0.30")
        fator_piso = Decimal("0.50") if tipo == "SERVICO" else Decimal("0.70")
        piso_seguro = (valor_estimado * fator_piso).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        lucro_estimado = valor_estimado - piso_seguro

        return {
            "aprovado": True,
            "prioridade": "ALTA (LOCAL TOCANTINS)",
            "piso_seguro": piso_seguro,
            "lucro_estimado": lucro_estimado,
            "margem_percentual": margem_alvo * 100,
            "motivo": f"Certame no Tocantins. Logística viável para atendimento local/presencial."
        }

    # =========================================================================
    # REGRA 2: DEMAIS ESTADOS (FILTRO DE LUCRATIVIDADE CERTA)
    # =========================================================================
    
    # 2.1 - Se for SERVIÇO fora do TO: Só aceita se for 100% REMOTO
    if tipo == "SERVICO":
        eh_remoto = any(termo in objeto_norm for termo in TERMOS_REMOTO)
        
        if not eh_remoto:
            return {
                "aprovado": False,
                "motivo": f"Descartado: Serviço presencial fora do TO ({uf}). Risco de prejuízo com deslocamento e hospedagem."
            }
        
        # Serviço Remoto aprovado
        fator_piso = Decimal("0.50")
        piso_seguro = (valor_estimado * fator_piso).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        lucro_estimado = valor_estimado - piso_seguro

        return {
            "aprovado": True,
            "prioridade": "MÉDIA (SERVIÇO REMOTO NACIONAL)",
            "piso_seguro": piso_seguro,
            "lucro_estimado": lucro_estimado,
            "margem_percentual": 50.0,
            "motivo": f"Serviço 100% Remoto em {uf}. Sem custo de deslocamento."
        }

    # 2.2 - Se for PRODUTO fora do TO: Abate frete interestadual e exige lucro mínimo
    else:
        # Custo estimado de frete/seguro para envio interestadual (médio: R$ 350,00)
        FRETE_ESTIMADO = Decimal("350.00")
        
        # Se o valor total do edital for muito baixo, o frete consome o lucro
        if valor_estimado < Decimal("3000.00"):
            return {
                "aprovado": False,
                "motivo": f"Descartado: Venda em {uf} com valor muito baixo (R$ {valor_estimado:,.2f}). O frete anula a margem."
            }

        # Custo base estimado: 60% do valor do órgão + frete
        custo_base = (valor_estimado * Decimal("0.60")) + FRETE_ESTIMADO
        piso_seguro = (custo_base * Decimal("1.30")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        
        if piso_seguro > valor_estimado:
            return {
                "aprovado": False,
                "motivo": f"Descartado: Margem insuficiente em {uf} após abater o frete interestadual."
            }

        lucro_estimado = valor_estimado - custo_base
        return {
            "aprovado": True,
            "prioridade": "OPORTUNIDADE NACIONAL (LUCRO GARANTIDO)",
            "piso_seguro": piso_seguro,
            "lucro_estimado": lucro_estimado,
            "margem_percentual": 30.0,
            "motivo": f"Produto com entrega em {uf}. Margem líquida >= 30% confirmada após custos de envio."
        }
