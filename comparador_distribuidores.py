import os
from supabase import create_client
import re

def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    # Remover caracteres especiais
    t = re.sub(r'[^\w\s]', '', texto).lower()
    return t

def extrair_palavras_chave(descricao: str) -> list[str]:
    # Stopwords simples
    stopwords = {'de', 'para', 'com', 'e', 'ou', 'em', 'um', 'uma', 'o', 'a', 'do', 'da'}
    palavras = normalizar_texto(descricao).split()
    return [p for p in palavras if p not in stopwords and len(p) > 2]

def cruzar_item_com_distribuidores(descricao_edital: str, valor_referencia_governo: float) -> dict:
    url = os.getenv("SUPABASE_URL", "https://szphoxostihnpcovyzvx.supabase.co")
    key = os.getenv("SUPABASE_KEY")
    
    if not key:
        return {"erro": "SUPABASE_KEY não encontrada."}
        
    try:
        sb = create_client(url, key)
        
        # Pega todos os itens (num cenário real, faríamos um filtro melhor ou NLP)
        res = sb.table("catalogo_distribuidores").select("*").execute()
        catalogo = res.data
        
        if not catalogo:
            # Fake/Mock mode if table is empty for demonstration purposes
            catalogo = [
                {"id": 1, "distribuidor": "Fujioka", "descricao": "SSD 480GB Kingston", "preco_custo": 120.0, "estoque": 45},
                {"id": 2, "distribuidor": "Pauta", "descricao": "Toner 105A HP Compatível", "preco_custo": 25.0, "estoque": 120},
                {"id": 3, "distribuidor": "Martins", "descricao": "Patch Cord Furukawa 2m", "preco_custo": 8.0, "estoque": 500}
            ]
        
        palavras_edital = set(extrair_palavras_chave(descricao_edital))
        
        melhor_match = None
        maior_score = 0
        menor_custo = float('inf')
        
        for produto in catalogo:
            desc_prod = produto.get("descricao", "")
            palavras_prod = set(extrair_palavras_chave(desc_prod))
            
            # Score de similaridade simples (interseção)
            score = len(palavras_edital.intersection(palavras_prod))
            
            # Condição de match (se tiver pelo menos 1 palavra chave comum relevante)
            # Para ficar dinâmico no teste, vamos aceitar 1.
            if score > 0:
                custo_prod = float(produto.get("preco_custo", float('inf')))
                if score > maior_score or (score == maior_score and custo_prod < menor_custo):
                    maior_score = score
                    menor_custo = custo_prod
                    melhor_match = produto
                    
        if not melhor_match:
            return {"encontrado": False, "motivo": "Nenhum produto correspondente no catálogo de distribuidores."}
            
        custo_distribuidor = menor_custo
        frete_palmas = 30.00 # Custo fixo estimado
        custo_total = custo_distribuidor + frete_palmas
        piso_margem = custo_total * 1.30
        
        lucro_projetado_rs = valor_referencia_governo - custo_total
        margem_pct = (lucro_projetado_rs / valor_referencia_governo * 100) if valor_referencia_governo > 0 else 0
        
        return {
            "encontrado": True,
            "distribuidor": melhor_match.get("distribuidor", "Desconhecido"),
            "descricao_fornecedor": melhor_match.get("descricao", ""),
            "custo_distribuidor": custo_distribuidor,
            "frete_estimado": frete_palmas,
            "custo_total": custo_total,
            "estoque": melhor_match.get("estoque", 0),
            "piso_venda": piso_margem,
            "lucro_projetado_rs": lucro_projetado_rs,
            "margem_pct": margem_pct,
            "aprovado_lucro": lucro_projetado_rs > 0 and margem_pct >= 30.0
        }

    except Exception as e:
        return {"erro": str(e)}
