"""
hermes_cli.py – Interface de ponte entre o Hermes Agent e o Robô de Licitações.

Permite ao Hermes Agent (ou ao usuário via terminal) executar ações rápidas no robô:
  python hermes_cli.py --diagnostics
  python hermes_cli.py --parse <caminho_pdf>
  python hermes_cli.py --pncp [--uf TO] [--kw "computador"]
  python hermes_cli.py --scan <caminho_pasta>
  python hermes_cli.py --test-engine
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Adiciona o diretório do projeto ao sys.path
BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

def cmd_diagnostics() -> None:
    """Executa os testes de diagnóstico do sistema."""
    from diagnostics import executar_diagnostico_cli
    executar_diagnostico_cli()

def cmd_parse(pdf_path: str) -> None:
    """Parseia um arquivo de edital PDF e imprime o resumo estruturado."""
    from edital_parser import EditalParser
    p = Path(pdf_path)
    if not p.exists():
        print(f"❌ Arquivo PDF não encontrado: {p}")
        sys.exit(1)

    print(f"📖 Parseando edital: {p.name}...")
    parser = EditalParser()
    res = parser.parsear(p)

    print(f"\n✅ RESULTADO DO PARSING:")
    print(f"  - Órgão: {res.orgao or 'N/A'}")
    print(f"  - Pregão: {res.numero_pregao or 'N/A'} (UASG {res.numero_uasg or 'N/A'})")
    print(f"  - Objeto: {res.objeto[:150]}..." if res.objeto else "  - Objeto: N/A")
    print(f"  - Critério de Julgamento: {res.inteligencia_compras_gov.criterio_julgamento_legivel}")
    print(f"  - Modo de Disputa: {res.inteligencia_compras_gov.modo_disputa.nome_legivel}")
    print(f"  - Total de Itens Extraídos: {len(res.itens)}")
    print("-" * 60)

    for item in res.itens:
        val_ref_str = f"R$ {item.valor_referencia:,.2f}" if item.valor_referencia else "N/A"
        print(f"  Item {item.numero_item:02d} | {item.descricao[:70]}")
        print(f"          Qtd: {item.quantidade} {item.unidade_medida} | Valor Ref: {val_ref_str}")

def cmd_pncp(uf: str, kw: str, limite: int) -> None:
    """Busca licitações publicadas no PNCP."""
    from pncp_client import buscar_publicacoes_pncp
    print(f"🌐 Consultando PNCP (UF: {uf}, Palavra-chave: '{kw or 'todas'}')...")
    res = buscar_publicacoes_pncp(uf=uf if uf != "TODAS" else None, palavra_chave=kw, tamanho_pagina=limite)
    itens = res.get("data", [])
    print(f"\n✅ {len(itens)} licitação(ões) encontrada(s) no PNCP:")
    for idx, item in enumerate(itens, 1):
        orgao = item.get("orgaoEntidade", {}).get("razaoSocial", "Órgão Desconhecido")
        obj = item.get("objetoCompra", "Sem descrição")[:100]
        val_tot = item.get("valorTotalEstimado")
        val_str = f"R$ {val_tot:,.2f}" if val_tot else "N/A"
        print(f"  {idx}. [{orgao[:40]}]")
        print(f"     Objeto: {obj}")
        print(f"     Valor Estimado: {val_str}")
        print("-" * 60)

def cmd_scan(folder_path: str) -> None:
    """Escaneia uma pasta local contendo PDFs e sincroniza no Supabase."""
    from drive_scanner import escanear_pasta_local
    p = Path(folder_path)
    if not p.exists() or not p.is_dir():
        print(f"❌ Pasta não encontrada: {p}")
        sys.exit(1)

    print(f"🔍 Escaneando pasta local: {p}...")
    resultados = escanear_pasta_local(p)
    print(f"✅ Concluído! {len(resultados)} edital(is) processado(s) e sincronizado(s) no Supabase.")

def cmd_test_engine() -> None:
    """Roda a suíte de testes unitários do motor de decisão de lances."""
    import subprocess
    subprocess.run([sys.executable, str(BASE_DIR / "test_novo_motor_decisao.py")])

def main() -> None:
    parser = argparse.ArgumentParser(description="Ponte Hermes Agent <-> Robô de Licitações")
    parser.add_argument("--diagnostics", action="store_true", help="Executa testes de diagnóstico do sistema")
    parser.add_argument("--parse", type=str, metavar="PDF_PATH", help="Parseia um edital em PDF")
    parser.add_argument("--pncp", action="store_true", help="Busca licitações no PNCP")
    parser.add_argument("--uf", type=str, default="TO", help="UF para busca no PNCP (padrão: TO)")
    parser.add_argument("--kw", type=str, default="", help="Palavra-chave para busca no PNCP")
    parser.add_argument("--limite", type=int, default=10, help="Limite de resultados do PNCP")
    parser.add_argument("--scan", type=str, metavar="FOLDER_PATH", help="Escaneia pasta local de processos")
    parser.add_argument("--test-engine", action="store_true", help="Testa o motor de decisão de lances")

    args = parser.parse_args()

    if args.diagnostics:
        cmd_diagnostics()
    elif args.parse:
        cmd_parse(args.parse)
    elif args.pncp:
        cmd_pncp(args.uf, args.kw, args.limite)
    elif args.scan:
        cmd_scan(args.scan)
    elif args.test_engine:
        cmd_test_engine()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
