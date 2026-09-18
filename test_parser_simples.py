#!/usr/bin/env python3
"""Script de teste do parser de editais."""
import sys
import os
import logging

# Configura logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Adiciona o diretório do projeto ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    print("=" * 60)
    print("TESTE DO PARSER DE EDITAIS - Robô de Licitações")
    print("=" * 60)
    
    # Importar configurações
    from config import settings
    
    gemini_key = settings.gemini_api_key
    print(f"\n[CONFIG] GEMINI_API_KEY: {'SIM' if gemini_key else 'NÃO CONFIGURADO'}")
    if gemini_key:
        print(f"[CONFIG] Valor: {gemini_key[:10]}... (total {len(gemini_key)} chars)")
    
    # Importar parser
    from edital_parser import EditalParser, ItemEdital
    
    parser = EditalParser()
    
    # Testar com texto simulado de "Material de Expediência"
    print("\n[TESTE] Texto simulado - Material de Expediência")
    print("-" * 40)
    
    texto_teste = """
    EDMAR MENDES EIRELE - 2024/016 - Materiais de Expediência e Informática
    
    ITENS DE SERVIÇOS E MATERIAIS:
    
    ITEM 01 - Suporte, Manutenção e Assistência Técnica em Equipamentos de Informática e Telecomunicações.
    Descrição: Serviço de manutenção preventiva e corretiva em computadores, notebooks, impressoras e equipamentos de rede.
    R$ 85,00 por service call
    
    ITEM 02 - Peças de Reposição para Equipamentos de Informática.
    Descrição: Reposição de peças avariadas de equipamentos de TI.
    R$ 120,00 por peça
    
    ITEM 03 - Instalação de Sistemas Operacionais e Softwares.
    Descrição: Instalação, configuração e ativação de sistemas operacionais e softwares.
    R$ 45,00 por instalação
    
    ITEM 04 - Serviço de Backup e Recuperação de Dados.
    Descrição: Serviço de backup e recuperação de dados em discos rígidos e SSDs.
    R$ 150,00 por serviço
    
    ITEM 05 - Consultoria em TI.
    Descrição: Prestação de serviços de consultoria em tecnologia da informação.
    R$ 200,00 por hora
    """
    
    # Cria um objeto resultado mínimo para o teste
    from dataclasses import dataclass, field
    from typing import Optional, Any, List
    
    @dataclass
    class ResultadoTeste:
        caminho_pdf: str = ""
        numero_pregao: str = ""
        numero_uasg: str = ""
        orgao: str = ""
        objeto: str = ""
        regras: Any = None
        inteligencia_compras_gov: Any = None
        itens: List[ItemEdital] = field(default_factory=list)
        erros_parsing: List[str] = field(default_factory=list)
        sucesso: bool = False
        extracao_ia_usada: bool = False
    
    resultado = ResultadoTeste()
    resultado.regras = type('obj', (object,), {
        'decremento_global_valor': 0.01,
        'decremento_global_percent': None,
        'modo_disputa': 'aberto'
    })()
    
    # Testar estratégia 2 (itens explícitos)
    itens = parser._extrair_itens_explicitos(texto_teste, resultado)
    print(f"[RESULTADO] Estratégia 2 (ITEM explícito + R$): {len(itens)} item(ns) extraídos")
    for item in itens:
        print(f"  Item {item.numero_item}: {item.descricao[:50]}... | R$ {item.valor_referencia}")
    
    print("\n" + "=" * 60)
    print("TESTE CONCLUÍDO")
    print("=" * 60)

if __name__ == "__main__":
    main()
