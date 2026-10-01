import unicodedata
from decimal import Decimal, ROUND_HALF_UP

def normalizar(t: str) -> str:
    if not t: return ""
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").lower()

TERMOS_REMOTO = ["remoto", "a distancia", "service desk", "help desk", "suporte remoto", "nuvem", "cloud"]
TERMOS_CNAE = [
    "computador", "notebook", "microcomputador", "informatica", "suporte",
    "manutencao", "cabeamento", "rede", "toner", "cartucho", "locacao",
    "estacao de trabalho", "perifericos", "servidor"
]

def avaliar_certame(payload: dict) -> dict:
    objeto_norm = normalizar(payload.get("objeto", ""))
    uf = (payload.get("uf") or "BR").strip().upper()
    tipo = payload.get("tipo_contratacao", "SERVICO")
    valor = float(payload.get("valor_estimado") or 0.0)

    # Crivo 1: Obrigatoriamente conter termo dos CNAEs da JRBC TECH
    if not any(termo in objeto_norm for termo in TERMOS_CNAE):
        return {"aprovado": False, "motivo": "Fora do escopo dos CNAEs"}

    # Crivo 2: Regra do Estado do Tocantins (Prioridade Máxima / Casa)
    if uf == "TO":
        margem = 50.0 if tipo == "SERVICO" else 30.0
        piso = valor * (0.50 if tipo == "SERVICO" else 0.70)
        return {
            "aprovado": True,
            "prioridade": "LOCAL_TO",
            "margem_alvo": margem,
            "piso_seguro": piso,
            "motivo": "Certame em Tocantins: atendimento local viável em Palmas/região."
        }

    # Crivo 3: Outros Estados - Bloqueio de prejuízo logístico
    if tipo == "SERVICO":
        eh_remoto = any(termo in objeto_norm for termo in TERMOS_REMOTO)
        if not eh_remoto:
            return {"aprovado": False, "motivo": f"Serviço presencial fora de TO ({uf}). Descartado por custo de deslocamento."}
        
        return {
            "aprovado": True,
            "prioridade": "REMOTO_NACIONAL",
            "margem_alvo": 50.0,
            "piso_seguro": valor * 0.50,
            "motivo": f"Serviço 100% Remoto ({uf}). Sem custo de deslocamento."
        }
    else: # PRODUTO
        if valor < 2500.0:
            return {"aprovado": False, "motivo": f"Venda em {uf} com valor muito baixo (< R$ 2.500). Frete inviabiliza margem."}
        
        # Desconta frete médio e valida margem mínima de 30%
        custo_com_frete = (valor * 0.60) + 300.0
        piso = custo_com_frete * 1.30
        if piso > valor:
            return {"aprovado": False, "motivo": f"Margem em {uf} insuficiente após custos de frete interestadual."}

        return {
            "aprovado": True,
            "prioridade": "VENDA_NACIONAL",
            "margem_alvo": 30.0,
            "piso_seguro": piso,
            "motivo": f"Venda de produto em {uf} com lucro líquido >= 30% garantido."
        }
