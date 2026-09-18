"""
modo_descoberta_completa.py – Fluxo completo de descoberta de licitação.

Caminho: edital PDF → parser extrai itens → para cada item, pergunta custo ao usuário
→ calcula stop-loss automaticamente → persiste no Supabase.

Este é o fluxo que une edital_parser.py + engine.py + supabase_client.py
em um único comando, ideal para uso rápido no terminal.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import FloatPrompt, Confirm, Prompt
from rich.table import Table
from rich import print as rprint

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from engine import MotorDecisao, RegraItem, TipoIntervalo
from edital_parser import EditalParser, ResultadoEdital, InteligenciaComprasGov
from supabase_client import inserir_item, inserir_licitacao
from config import settings

console = Console()


def descoberta_completa(
    caminho_pdf: str | Path,
    sincronizar_supabase: bool = True,
) -> Optional[tuple[ResultadoEdital, list[RegraItem]]]:
    """
    Fluxo completo: ler edital → extrair itens → pedir custos → calcular stop-loss
    → persistir no Supabase.

    Args:
        caminho_pdf: Caminho para o PDF do edital.
        sincronizar_supabase: Se True, salva no Supabase.
    """
    caminho = Path(caminho_pdf)

    console.print(Panel.fit(
        f"[bold cyan]📖 Lendo edital: {caminho.name}[/bold cyan]\n"
        f"[dim]Extraindo itens, regras e inteligência regulatória...[/dim]",
        border_style="cyan",
    ))

    # 1. Parsear o edital
    parser = EditalParser()
    resultado = parser.parsear(caminho)

    # 2. Exibir inteligência regulatória
    intel = getattr(resultado, "inteligencia_compras_gov", None) or InteligenciaComprasGov()

    console.print()
    console.rule("[bold]🏛️ Inteligência Compras.gov.br[/bold]")

    table_intel = Table(show_header=True, header_style="bold white")
    table_intel.add_column("dimensão", style="bold white", width=25)
    table_intel.add_column("configuração", style="yellow")
    table_intel.add_column("impacto", style="dim")

    table_intel.add_row("🎯 Critério de Julgamento", intel.criterio_julgamento_legivel, "disputa por item/lote/desconto")
    table_intel.add_row("⚡ Modo de Disputa", intel.modo_disputa.nome_legivel, intel.modo_disputa.descricao_operacional[:80])
    table_intel.add_row("📉 Degrau Mínimo", intel.intervalo_lances.descricao, "intervalo obrigatório entre lances")
    table_intel.add_row("⚠️ Riscos Identificados", f"{len(intel.riscos_identificados)} verificações", "Lei 14.133/2021 + IN 73/2022")
    console.print(table_intel)

    # 3. Exibir riscos
    if intel.riscos_identificados:
        console.print()
        console.rule("[bold red]🛡️ Matriz de Riscos de Desclassificação[/bold red]")
        for r in intel.riscos_identificados:
            if r.severidade.value == "ALTO":
                sev = "[bold white on red] CRÍTICO [/bold white on red]"
            elif r.severidade.value == "MEDIO":
                sev = "[bold black on yellow] ATENÇÃO [/bold black on yellow]"
            else:
                sev = "[bold white on green] BAIXO [/bold white on green]"
            console.print(f"  {sev} {r.titulo}")
            console.print(f"    📄 {r.base_legal}")
            console.print(f"    📝 {r.descricao[:100]}")
            console.print(f"    ✅ {r.sugestao_mitigacao[:100]}")
            console.print()

    # 4. Se não encontrou itens, avisar
    if not resultado.itens:
        console.print(Panel.fit(
            "[yellow]⚠️ Nenhum item identificado automaticamente no edital.[/yellow]\n\n"
            "[dim]Motivos possíveis:\n"
            "  - PDF escaneado (imagem) — Gemini AI necessário\n"
            "  - Tabela em formato não padrão\n"
            "  - Edital muito recente ainda sem tabela de itens[/dim]",
            border_style="yellow",
        ))
        if not Confirm.ask("\nDeseja inserir itens manualmente?", default=True):
            return None
        itens = _entrada_manual()
        resultado.itens = itens
    else:
        console.print(f"\n✅ [bold green]{len(resultado.itens)} item(ns) extraídos automaticamente[/bold green]")

    # 5. Para cada item, pedir custo e calcular stop-loss
    console.print()
    console.rule("[bold]💰 Declaração de Custos e Stop-Loss[/bold]")

    regras: list[RegraItem] = []
    for item in resultado.itens:
        console.print()
        console.print(f"[bold cyan]Item {item.numero_item}:[/bold cyan] {item.descricao[:70]}")
        if item.quantidade and item.quantidade > 1:
            console.print(f"  [dim]Quantidade: {item.quantidade} {item.unidade_medida}[/dim]")
        if item.valor_referencia:
            console.print(f"  [green]Valor de Referência (Teto do Órgão): R$ {item.valor_referencia:,.2f}[/green]")

        # Pedir custo real
        custo = FloatPrompt.ask(
            "  [bold]Custo unitário real da empresa (R$)[/bold]",
            default=0.0,
        )
        if custo <= 0:
            console.print("  [red]❌ Custo inválido — item ignorado.[/red]")
            continue

        # Pedir margem
        margem = FloatPrompt.ask(
            "  [bold]Margem mínima desejada (% sobre custo)[/bold]",
            default=settings.margem_minima_percent,
        )

        # Calcular stop-loss
        preco_minimo = round(custo * (1 + margem / 100), 2)

        # Sugerir proposta inicial (1.5% abaixo do valor de referência)
        if item.valor_referencia and item.valor_referencia > 0:
            proposta_sugestao = round(item.valor_referencia * 0.985, 2)
            if proposta_sugestao < preco_minimo:
                proposta_sugestao = round(preco_minimo * 1.05, 2)
        else:
            proposta_sugestao = round(preco_minimo * 1.10, 2)

        console.print(f"  [yellow]Stop-Loss calculado:[/yellow] [bold red]R$ {preco_minimo:.2f}[/bold red]")
        console.print(f"  [green]Proposta inicial sugerida:[/green] [bold]R$ {proposta_sugestao:.2f}[/bold]")

        # Confirmar proposta
        usar_sugestao = Confirm.ask("  Usar proposta sugerida?", default=True)
        if not usar_sugestao:
            proposta_inicial = FloatPrompt.ask(
                "  [bold]Digite sua proposta inicial (R$)[/bold]",
                default=proposta_sugestao,
            )
        else:
            proposta_inicial = proposta_sugestao

        # Validar proposta > stop-loss
        if proposta_inicial <= preco_minimo:
            console.print(f"  [red]❌ Proposta R$ {proposta_inicial:.2f} <= Stop-Loss R$ {preco_minimo:.2f} — item ignorado.[/red]")
            continue

        # Criar regra
        regra = RegraItem(
            item_id=str(uuid.uuid4()),
            numero_item=item.numero_item,
            descricao=item.descricao,
            custo_unitario=custo,
            margem_minima_percent=margem,
            preco_minimo=preco_minimo,
            preco_inicial_proposta=proposta_inicial,
            decremento_minimo=item.decremento_minimo,
            decremento_percentual=item.decremento_percentual,
            valor_referencia=item.valor_referencia,
        )
        regras.append(regra)

        console.print(f"  ✅ [bold green]Item configurado — Stop-Loss: R$ {preco_minimo:.2f}[/bold green]")

    if not regras:
        console.print("\n[red]Nenhum item configurado com sucesso.[/red]")
        return None

    # 6. Persistir no Supabase
    console.print()
    console.rule("[bold]💾 Persistência no Supabase[/bold]")

    if sincronizar_supabase and Confirm.ask("Salvar licitação e itens no Supabase?", default=True):
        try:
            lic = inserir_licitacao({
                "numero_uasg": resultado.numero_uasg or "000000",
                "numero_pregao": resultado.numero_pregao or "0/0",
                "orgao": resultado.orgao[:250] if resultado.orgao else "Órgão Desconhecido",
                "objeto": resultado.objeto[:500] if resultado.objeto else "Aquisição/Contratação de Serviços",
                "modo_disputa": resultado.regras.modo_disputa or "aberto",
                "url_sala_disputa": "",
                "status": "configurada",
            })
            licitacao_id = lic.get("id")
            console.print(f"  ✅ Licitação salva com ID: [bold cyan]{licitacao_id}[/bold cyan]")

            for regra in regras:
                inserir_item({
                    "licitacao_id": licitacao_id,
                    "numero_item": regra.numero_item,
                    "descricao": regra.descricao[:300],
                    "custo_unitario": regra.custo_unitario,
                    "margem_minima_percent": regra.margem_minima_percent,
                    "preco_minimo": regra.preco_minimo,
                    "preco_inicial_proposta": regra.preco_inicial_proposta,
                    "decremento_minimo": regra.decremento_minimo,
                    "decremento_percentual": regra.decremento_percentual,
                    "valor_referencia": regra.valor_referencia,
                    "unidade_medida": "UN",
                    "quantidade": 1.0,
                })
            console.print(f"  ✅ {len(regras)} item(ns) sincronizados no Supabase.")
        except Exception as exc:
            console.print(f"  [yellow]⚠️ Falha ao persistir no Supabase: {exc}[/yellow]")
            console.print("  Os itens foram configurados em memória — continue com o monitoramento.")

    console.print()
    console.print(Panel.fit(
        f"[bold green]✅ DESCOBRTURA COMPLETA FINALIZADA[/bold green]\n\n"
        f"[bold]Itens configurados:[/bold] {len(regras)}\n"
        f"[bold]Modo:[/bold] {'SIMULAÇÃO' if settings.is_simulation else 'REAL ⚠️'}\n"
        f"[bold]Próximo passo:[/bold] Iniciar monitoramento com --test-engine ou no Compras.gov.br",
        border_style="green",
    ))

    return resultado, regras


def _entrada_manual() -> list:
    """Entrada manual de itens quando o parser não encontra nada."""
    console.print("\n[dim]Informe os itens manualmente (digite Enter em branco para finalizar):[/dim]")
    itens = []
    n = 1
    while True:
        desc = Prompt.ask(f"  Descrição do Item {n}", default="")
        if not desc:
            break
        itens.append(
            type("Item", (), {
                "numero_item": n,
                "descricao": desc,
                "quantidade": 1.0,
                "unidade_medida": "UN",
                "valor_referencia": None,
                "decremento_minimo": None,
                "decremento_percentual": None,
            })()
        )
        n += 1
    return itens


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fluxo completo de descoberta de licitação")
    parser.add_argument("pdf", nargs="?", help="Caminho para o PDF do edital")
    parser.add_argument("--nosync", action="store_true", help="Não sincronizar no Supabase")
    args = parser.parse_args()

    if not args.pdf:
        pdf = Prompt.ask("Caminho para o PDF do edital", default="edital.pdf")
    else:
        pdf = args.pdf

    caminho = Path(pdf)
    if not caminho.exists():
        console.print(f"[red]❌ Arquivo não encontrado: {caminho}[/red]")
        sys.exit(1)

    resultado, regras = descoberta_completa(caminho, sincronizar_supabase=not args.nosync)

    if resultado and regras:
        console.print("\n[bold]Itens configurados com sucesso:[/bold]")
        for r in regras:
            console.print(f"  Item {r.numero_item}: R$ {r.custo_unitario:.2f} (custo) → Stop-Loss: R$ {r.preco_minimo:.2f} → Proposta: R$ {r.preco_inicial_proposta:.2f}")
        console.print(f"\n[green]✅ Pronto para iniciar monitoramento![/green]")
