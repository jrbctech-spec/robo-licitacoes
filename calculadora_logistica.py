"""
Módulo: calculadora_logistica.py
Finalidade: Cálculo de distâncias, deslocamento e custos operacionais
com origem fixa em Palmas - TO.
"""
from decimal import Decimal, ROUND_HALF_UP

# Parâmetros base da JRBC TECH (Palmas/TO)
CIDADE_ORIGEM = "Palmas"
UF_ORIGEM = "TO"
CONSUMO_MEDIO_KM_L = Decimal("10.0")  # 10 km por litro
PRECO_COMBUSTIVEL_LITRO = Decimal("6.20")  # R$ / litro
VALOR_DIARIA_ALIMENTACAO_HOSPEDAGEM = Decimal("250.00")  # Para cidades > 150 km
CUSTO_HORA_TECNICA = Decimal("65.00")  # Hora de técnico em trânsito/campo

# Tabela aproximada de distâncias rodoviárias de Palmas para polos do TO
DISTANCIAS_TO_KM = {
    "PALMAS": 0,
    "PORTO NACIONAL": 60,
    "PARAISO DO TOCANTINS": 65,
    "PARAISO": 65,
    "MIRACEMA DO TOCANTINS": 75,
    "GURUPI": 220,
    "ARAGUAINA": 380,
    "ARAGUAÍNA": 380,
    "COLINAS DO TOCANTINS": 270,
    "DIANOPOLIS": 340,
    "GUARAÍ": 180,
    "PORTO ALEGRE DO TOCANTINS": 290,
    "PORTO ALEGRE DO TO": 290,
    "ITACAJÁ": 300,
    "ITACAJA": 300,
}

def calcular_custo_deslocamento(municipio_destino: str, uf_destino: str) -> dict:
    municipio_limpo = municipio_destino.strip().upper()
    uf_limpa = uf_destino.strip().upper()
    
    # 1. Se for remoto, custo de deslocamento é zero
    if "REMOTO" in municipio_limpo:
        return {
            "modalidade": "100% Remoto",
            "distancia_total_km": 0,
            "custo_combustivel": Decimal("0.00"),
            "custo_diarias": Decimal("0.00"),
            "custo_total_logistica": Decimal("0.00")
        }
        
    # 2. Distância aproximada (ida e volta)
    distancia_ida = DISTANCIAS_TO_KM.get(municipio_limpo, 200 if uf_limpa == "TO" else 800)
    distancia_total = distancia_ida * 2
    
    # Combustível
    litros = Decimal(str(distancia_total)) / CONSUMO_MEDIO_KM_L
    custo_combustivel = (litros * PRECO_COMBUSTIVEL_LITRO).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    
    # Diárias (se for acima de 150 km de Palmas)
    custo_diarias = Decimal("0.00")
    if distancia_ida > 150:
        custo_diarias = VALOR_DIARIA_ALIMENTACAO_HOSPEDAGEM
        
    custo_total = custo_combustivel + custo_diarias
    
    return {
        "modalidade": "Presencial / Field Service",
        "distancia_total_km": distancia_total,
        "custo_combustivel": custo_combustivel,
        "custo_diarias": custo_diarias,
        "custo_total_logistica": custo_total
    }
