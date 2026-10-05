"""
main.py – Ponto de entrada principal do Robô de Licitações.

Menu interativo (CLI) para:
  1. Carregar edital (PDF) e declarar custos por item.
  2. Testar conexão com o Supabase.
  3. Iniciar monitoramento em modo Simulação (Dry-Run) ou Real.

Uso:
    python main.py
    python main.py --modo real  # ⚠️  Apenas com confirmação explícita
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
from rich.table import Table
from rich import print as rprint

# Importação tardia de config para exibir erros amigáveis
try:
    from config import ModoOperacao, settings
except RuntimeError as exc:
    print(f"\n❌ ERRO DE CONFIGURAÇÃO: {exc}")
    print("👉  Copie o arquivo .env.example para .env e preencha os valores.\n")
    sys.exit(1)

from supabase_client import (
    inserir_item,
    inserir_licitacao,
    log_queue,
    testar_conexao,
    EntradaLog,
)
from edital_parser import (
    EditalParser,
    ItemEdital,
    ResultadoEdital,
    InteligenciaComprasGov,
    SeveridadeRisco,
)
from engine import MotorDecisao, RegraItem
from browser_bot import BrowserBot
from license_manager import (
    validar_licenca,
    salvar_chave_local,
    obter_chave_salva,
    obter_hwid,
    StatusLicenca,
)

# ─────────────────────────────────────────────────────────────────────────────
# Configuração de logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("robo_licitacoes.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)
console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Banner
# ─────────────────────────────────────────────────────────────────────────────

BANNER = """
[bold cyan]╔══════════════════════════════════════════════════════════╗
║       🤖  ROBÔ DE LICITAÇÕES – Compras.gov.br           ║
║          Pregão Eletrônico | Comprasnet Web              ║
╚══════════════════════════════════════════════════════════╝[/bold cyan]
"""


def exibir_banner() -> None:
    """Exibe o banner de boas-vindas e o modo de operação atual."""
    console.print(BANNER)
    modo = settings.modo_operacao.value.upper()
    cor = "red" if settings.is_real else "green"
    console.print(
        f"  Modo de Operação: [bold {cor}]{modo}[/bold {cor}]"
        + (" ⚠️  LANCES REAIS SERÃO ENVIADOS!" if settings.is_real else " 🛡️  Seguro")
    )
    console.print(f"  Margem Mínima: [bold]{settings.margem_minima_percent}%[/bold]")

    try:
        res_lic = validar_licenca()
        if res_lic.valida:
            dias = f" ({res_lic.dias_restantes} dias restantes)" if res_lic.dias_restantes is not None else ""
            console.print(f"  Licença: [bold green]Ativa ✅[/bold green] – [cyan]{res_lic.cliente_nome or 'Cliente'}[/cyan]{dias}\n")
        else:
            console.print(f"  Licença: [bold yellow]⚠️  {res_lic.mensagem}[/bold yellow]\n")
    except Exception:
        console.print()


# ─────────────────────────────────────────────────────────────────────────────
# Opção 1: Carregar edital e declarar custos
# ─────────────────────────────────────────────────────────────────────────────

def menu_carregar_edital() -> Optional[tuple[ResultadoEdital, list[RegraItem]]]:
    """
    Guia o usuário no carregamento do edital PDF e declaração de custos.

    Returns:
        Tupla (ResultadoEdital, lista de RegraItem) ou None se cancelado.
    """
    console.rule("[bold]📄 Carregar Edital[/bold]")

    console.print("  [1] 💻 Carregar Arquivo PDF Local / Pasta")
    console.print("  [2] 🌐 Buscar na API do PNCP (Portal Nacional)")
    opcao_fonte = Prompt.ask("  Selecione a fonte do edital", choices=["1", "2"], default="1")

    caminho: Optional[Path] = None

    if opcao_fonte == "1":
        caminho_str = Prompt.ask(
            "  Caminho para o PDF do edital ou diretório local",
            default="edital.pdf",
        )
        caminho = Path(caminho_str)

        if not caminho.exists():
            console.print(f"[red]❌ Arquivo ou diretório não encontrado: {caminho}[/red]")
            return None
    else:
        try:
            from pncp_client import carregar_certame_pncp
            url_pncp = Prompt.ask("  Cole o link ou parâmetros da compra no PNCP")
            console.print(f"  🌐 Consultando API do PNCP: {url_pncp}...")
            compra, itens_pncp, pdf_pncp = carregar_certame_pncp(url_pncp)

            if pdf_pncp and Path(pdf_pncp).exists():
                caminho = Path(pdf_pncp)
            else:
                console.print("[yellow]⚠️ PDF não encontrado no PNCP. Usando dados da API...[/yellow]")
                caminho = None
        except Exception as exc:
            console.print(f"[red]❌ Erro ao consultar PNCP: {exc}[/red]")
            return None

    if caminho is not None:
        console.print(f"\n  📖 Lendo edital: {caminho.name}...")
        parser = EditalParser()
        resultado = parser.parsear(caminho)
    else:
        # Monta o resultado a partir dos dados retornados pela API do PNCP
        num_compra = compra.get("numeroCompra", "") if "compra" in locals() else ""
        ano_compra = compra.get("anoCompra", "") if "compra" in locals() else ""
        num_pregao = f"{num_compra}/{ano_compra}" if num_compra and ano_compra else ""
        orgao_nome = compra.get("orgaoEntidade", {}).get("razaoSocial", "") if "compra" in locals() else ""
        objeto_desc = compra.get("objeto", "") if "compra" in locals() else ""
        uasg_cod = str(compra.get("unidadeOrgao", {}).get("codigoUnidade", "")) if "compra" in locals() else ""
        
        lista_itens: list[ItemEdital] = []
        if "itens_pncp" in locals() and itens_pncp:
            for idx, it in enumerate(itens_pncp, 1):
                num_it = int(it.get("numeroItem", idx))
                desc_it = str(it.get("descricao", f"Item {num_it}"))
                qtd_it = float(it.get("quantidade", 1.0))
                un_it = str(it.get("unidadeMedida", "UN")).upper()
                v_ref = it.get("valorUnitarioEstimado") or it.get("valorTotal")
                val_ref = float(v_ref) if v_ref else None
                lista_itens.append(ItemEdital(
                    numero_item=num_it,
                    descricao=desc_it,
                    unidade_medida=un_it,
                    quantidade=qtd_it,
                    valor_referencia=val_ref,
                ))
        
        resultado = ResultadoEdital(
            caminho_pdf="PNCP_API",
            numero_pregao=num_pregao,
            numero_uasg=uasg_cod,
            orgao=orgao_nome,
            objeto=objeto_desc,
            itens=lista_itens,
            sucesso=len(lista_itens) > 0,
        )

    if not resultado.itens:
        console.print(
            "[yellow]⚠️  Nenhum item identificado automaticamente no edital.[/yellow]"
        )
        if not Confirm.ask("  Deseja inserir itens manualmente?", default=True):
            return None

    # Exibe resumo e inteligência regulatória Compras.gov.br
    intel = getattr(resultado, "inteligencia_compras_gov", None) or InteligenciaComprasGov()

    table_intel = Table(title="🏛️ Inteligência Operacional Compras.gov.br (Lei 14.133 / IN 73)", border_style="cyan")
    table_intel.add_column("Dimensão Regulamentar", style="bold white", width=26)
    table_intel.add_column("Configuração Identificada", style="yellow")
    table_intel.add_column("Impacto Operacional no Robô", style="dim")

    table_intel.add_row("🎯 Critério de Julgamento", intel.criterio_julgamento_legivel, "Determina disputa por item, lote ou desconto.")
    table_intel.add_row("⚡ Modo de Disputa", intel.modo_disputa.nome_legivel, intel.modo_disputa.descricao_operacional)
    table_intel.add_row("📉 Degrau Mínimo (Lances)", intel.intervalo_lances.descricao, "Intervalo obrigatório e anti-flood (3s).")

    cron_str = f"{intel.cronograma.data_abertura or 'Data no Edital'} às {intel.cronograma.hora_abertura or '09:00'} ({intel.cronograma.fuso_horario})"
    table_intel.add_row("⏰ Cronograma Oficial", cron_str, f"Prazo de proposta readequada: mín. {intel.cronograma.prazo_envio_proposta_ajustada_horas}h.")

    console.print()
    console.print(table_intel)

    # Tabela de Riscos de Desclassificação
    if intel.riscos_identificados:
        table_riscos = Table(title="🛡️ Matriz de Riscos de Desclassificação & Conformidade", border_style="red")
        table_riscos.add_column("Severidade", justify="center", width=12)
        table_riscos.add_column("Risco / Alerta", style="bold")
        table_riscos.add_column("Base Legal", style="cyan", width=24)
        table_riscos.add_column("Diretriz de Mitigação do Robô", style="green")

        for r in intel.riscos_identificados:
            if r.severidade == SeveridadeRisco.ALTO:
                sev_styled = "[bold white on red] CRÍTICO [/bold white on red]"
            elif r.severidade == SeveridadeRisco.MEDIO:
                sev_styled = "[bold black on yellow] ATENÇÃO [/bold black on yellow]"
            elif r.severidade == SeveridadeRisco.BAIXO:
                sev_styled = "[bold white on green] BAIXO [/bold white on green]"
            else:
                sev_styled = "[bold white on blue] INFO [/bold white on blue]"

            table_riscos.add_row(sev_styled, f"{r.titulo}\n[dim]{r.descricao}[/dim]", r.base_legal, r.sugestao_mitigacao)

        console.print()
        console.print(table_riscos)

    if resultado.erros_parsing:
        for erro in resultado.erros_parsing:
            console.print(f"  [yellow]⚠️  {erro}[/yellow]")

    # Coleta dados da licitação
    console.rule("\n[bold]📋 Dados da Licitação[/bold]")
    numero_uasg = Prompt.ask(
        "  UASG do órgão",
        default=resultado.numero_uasg or "",
    )
    numero_pregao = Prompt.ask(
        "  Número do Pregão",
        default=resultado.numero_pregao or "",
    )
    url_pregao = Prompt.ask(
        "  URL da página de lances (Comprasnet)",
        default="",
    )

    # Declaração de custos por item
    console.rule("\n[bold]💰 Declaração de Custos por Item[/bold]")
    console.print(
        "  [dim]Para cada item, informe o custo unitário real e a margem mínima.[/dim]\n"
    )

    regras: list[RegraItem] = []
    itens_para_processar = resultado.itens if resultado.itens else _coletar_itens_manual()

    for item in itens_para_processar:
        console.print(
            f"\n  [bold cyan]Item {item.numero_item}:[/bold cyan] {item.descricao[:80]}"
        )
        if item.quantidade:
            console.print(
                f"    Qtd: {item.quantidade} {item.unidade_medida}"
            )
        if item.valor_referencia:
            console.print(
                f"    Valor Ref./Teto: [green]R$ {item.valor_referencia:,.2f}[/green]"
            )

        custo = FloatPrompt.ask(
            f"    Custo unitário real (R$)",
            default=0.0,
        )
        if custo <= 0:
            console.print("    [red]Custo inválido – item ignorado.[/red]")
            continue

        margem = FloatPrompt.ask(
            f"    Margem mínima (%)",
            default=settings.margem_minima_percent,
        )
        preco_minimo = settings.calcular_preco_minimo(custo) if margem == settings.margem_minima_percent \
            else round(custo * (1 + margem / 100), 4)

        preco_inicial = FloatPrompt.ask(
            f"    Sua proposta inicial (R$) [Stop-loss: R$ {preco_minimo:.4f}]",
            default=round(preco_minimo * 1.10, 2),
        )

        if preco_inicial <= preco_minimo:
            console.print(
                f"    [red]❌ Proposta inicial R$ {preco_inicial:.4f} ≤ "
                f"Stop-Loss R$ {preco_minimo:.4f}. Item ignorado.[/red]"
            )
            continue

        # Cria um ID temporário (será substituído após persistência no Supabase)
        item_id_temp = str(uuid.uuid4())

        regra = RegraItem(
            item_id=item_id_temp,
            numero_item=item.numero_item,
            descricao=item.descricao,
            custo_unitario=custo,
            margem_minima_percent=margem,
            preco_minimo=preco_minimo,
            preco_inicial_proposta=preco_inicial,
            decremento_minimo=item.decremento_minimo,
            decremento_percentual=item.decremento_percentual,
            valor_referencia=item.valor_referencia,
        )
        regras.append(regra)

        console.print(
            f"    ✅ Stop-Loss definido: [bold red]R$ {preco_minimo:.4f}[/bold red]"
        )

    if not regras:
        console.print("[red]Nenhum item configurado. Abortando.[/red]")
        return None

    # Persiste no Supabase
    if Confirm.ask("\n  Salvar licitação e itens no Supabase?", default=True):
        _persistir_licitacao(resultado, numero_uasg, numero_pregao, url_pregao, regras)

    return resultado, regras


def _coletar_itens_manual() -> list[ItemEdital]:
    """Coleta itens manualmente quando o parser não identificar automaticamente."""
    itens: list[ItemEdital] = []
    console.print("  Informe os itens manualmente (Enter em branco para finalizar):")
    numero = 1
    while True:
        descricao = Prompt.ask(f"  Descrição do Item {numero} (Enter para finalizar)", default="")
        if not descricao:
            break
        itens.append(ItemEdital(numero_item=numero, descricao=descricao))
        numero += 1
    return itens


def _persistir_licitacao(
    resultado: ResultadoEdital,
    numero_uasg: str,
    numero_pregao: str,
    url_pregao: str,
    regras: list[RegraItem],
) -> Optional[str]:
    """Salva a licitação e seus itens no Supabase."""
    try:
        console.print("  💾 Salvando no Supabase...")
        lic = inserir_licitacao({
            "numero_uasg": numero_uasg,
            "numero_pregao": numero_pregao,
            "objeto": resultado.objeto,
            "modo_disputa": resultado.regras.modo_disputa,
            "url_edital": url_pregao,
            "status": "pendente",
        })
        licitacao_id = lic.get("id")

        for regra in regras:
            inserir_item({
                "licitacao_id": licitacao_id,
                "numero_item": regra.numero_item,
                "descricao": regra.descricao,
                "custo_unitario": regra.custo_unitario,
                "margem_minima_percent": regra.margem_minima_percent,
                "preco_minimo": regra.preco_minimo,
                "preco_inicial_proposta": regra.preco_inicial_proposta,
                "decremento_minimo": regra.decremento_minimo,
                "decremento_percentual": regra.decremento_percentual,
            })

        console.print(
            f"  [green]✅ Licitação salva! ID: {licitacao_id}[/green]"
        )
        return licitacao_id

    except Exception as exc:
        console.print(f"  [yellow]⚠️  Falha ao salvar no Supabase: {exc}[/yellow]")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Opção 2: Testar conexão com Supabase
# ─────────────────────────────────────────────────────────────────────────────

def menu_testar_conexao() -> None:
    """Testa e exibe o status da conexão com o Supabase."""
    console.rule("[bold]🔌 Testar Conexão com Supabase[/bold]")
    console.print(f"  URL: {settings.supabase_url}")
    console.print("  Verificando conexão...")

    if testar_conexao():
        console.print("  [bold green]✅ Conexão com Supabase OK![/bold green]")
    else:
        console.print(
            "  [bold red]❌ Falha na conexão. "
            "Verifique SUPABASE_URL e SUPABASE_KEY no arquivo .env.[/bold red]"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Opção 3: Iniciar monitoramento
# ─────────────────────────────────────────────────────────────────────────────

def menu_iniciar_monitoramento(regras: list[RegraItem]) -> None:
    """
    Inicia a sessão de monitoramento e lances.

    Args:
        regras: Lista de RegraItem pré-configurados com custos e stop-losses.
    """
    console.rule("[bold]🚀 Iniciar Monitoramento[/bold]")

    if not regras:
        console.print("[red]❌ Nenhuma regra de item configurada.[/red]")
        console.print("  👉  Execute primeiro a opção [1] para carregar o edital.")
        return

    # Exibe resumo das regras carregadas
    tabela = Table(title="Regras de Lance Carregadas", show_header=True)
    tabela.add_column("Item", style="cyan", justify="center")
    tabela.add_column("Descrição", style="white", max_width=40)
    tabela.add_column("Custo", style="yellow", justify="right")
    tabela.add_column("Stop-Loss", style="red bold", justify="right")
    tabela.add_column("Proposta Inicial", style="green", justify="right")

    for r in regras:
        tabela.add_row(
            str(r.numero_item),
            r.descricao[:38] + "..." if len(r.descricao) > 38 else r.descricao,
            f"R$ {r.custo_unitario:.4f}",
            f"R$ {r.preco_minimo:.4f}",
            f"R$ {r.preco_inicial_proposta:.4f}",
        )
    console.print(tabela)

    url_pregao = Prompt.ask("\n  URL da página de lances do pregão")
    if not url_pregao.startswith("http"):
        console.print("[red]URL inválida.[/red]")
        return

    intervalo = FloatPrompt.ask("  Intervalo de polling (segundos)", default=5.0)

    # Confirmação do modo de operação
    modo_atual = "SIMULAÇÃO" if settings.is_simulation else "REAL ⚠️"
    console.print(f"\n  Modo de operação atual: [bold]{modo_atual}[/bold]")

    dry_run = settings.is_simulation

    if settings.is_real:
        console.print(
            Panel(
                "[bold red]⚠️  ATENÇÃO: Lances REAIS serão enviados ao Compras.gov.br!\n"
                "Esta ação tem efeito legal e financeiro.\n"
                "Certifique-se de que todos os stop-losses estão corretos.[/bold red]",
                title="MODO REAL",
                border_style="red",
            )
        )
        if not Confirm.ask("  Confirma o início em MODO REAL?", default=False):
            console.print("  Operação cancelada.")
            return
        dry_run = False

    # Inicializa e inicia o motor + bot
    sessao_id = str(uuid.uuid4())
    console.print(f"\n  Sessão ID: [dim]{sessao_id}[/dim]")
    console.print("  Iniciando... (Ctrl+C para interromper)\n")

    try:
        motor = MotorDecisao(dry_run=dry_run)
        motor.carregar_regras(regras)
        motor.iniciar_sessao()

        bot = BrowserBot(motor=motor, sessao_id=sessao_id)
        bot.iniciar(url_pregao=url_pregao, intervalo_polling_s=intervalo)

    except ValueError as exc:
        console.print(f"[red]❌ Erro nas regras: {exc}[/red]")
    except Exception as exc:
        console.print(f"[red]❌ Erro ao iniciar: {exc}[/red]")
        logger.exception("Erro ao iniciar sessão de monitoramento")


# ─────────────────────────────────────────────────────────────────────────────
# Opção 4: Gerenciar Licença e HWID
# ─────────────────────────────────────────────────────────────────────────────

def menu_gerenciar_licenca() -> None:
    """Exibe o HWID e permite validar/ativar uma nova chave de licença."""
    console.rule("[bold]🔑 Gerenciador de Licença & Assinatura[/bold]")
    hwid = obter_hwid()
    console.print(f"  [bold]Identificador da Máquina (HWID):[/bold] [cyan]{hwid}[/cyan]")
    console.print("  [dim]Envie este identificador para o suporte se necessário.[/dim]\n")

    res = validar_licenca()
    if res.valida:
        console.print(f"  Status: [bold green]LICENÇA ATIVA ✅[/bold green]")
        console.print(f"  Cliente: [bold]{res.cliente_nome}[/bold]")
        if res.cnpj_ou_cpf:
            console.print(f"  CNPJ/CPF: {res.cnpj_ou_cpf}")
        if res.valida_ate:
            console.print(f"  Validade: {res.valida_ate.strftime('%d/%m/%Y')} ({res.dias_restantes} dias restantes)")
        console.print(f"  Plano: {res.plano or 'Mensal'}")
    else:
        console.print(f"  Status: [bold red]INATIVA / EXPIRADA ❌[/bold red]")
        console.print(f"  Mensagem: [yellow]{res.mensagem}[/yellow]")

    console.print()
    if Confirm.ask("  Deseja ativar uma nova chave de licença?", default=not res.valida):
        chave = Prompt.ask("  Digite ou cole a chave de ativação (Ex: JRBC-XXXX-XXXX-XXXX)")
        if chave.strip():
            console.print("  ⏳ Validando com o servidor Supabase...")
            nova_res = validar_licenca(chave.strip())
            if nova_res.valida:
                salvar_chave_local(chave.strip())
                console.print(f"  [bold green]✅ Licença ativada com sucesso para {nova_res.cliente_nome}![/bold green]")
            else:
                console.print(f"  [bold red]❌ Falha na ativação: {nova_res.mensagem}[/bold red]")


# ─────────────────────────────────────────────────────────────────────────────
# Menu Principal
# ─────────────────────────────────────────────────────────────────────────────

def menu_principal() -> None:
    """Loop do menu interativo principal."""
    regras_carregadas: list[RegraItem] = []

    while True:
        exibir_banner()

        status_regras = (
            f"[green]{len(regras_carregadas)} item(s) configurado(s)[/green]"
            if regras_carregadas
            else "[dim]nenhum item carregado[/dim]"
        )

        console.print(f"  Status: {status_regras}\n")
        console.print("  [1] 📄 Carregar Edital e Declarar Custos")
        console.print("  [2] 🔌 Testar Conexão com Supabase")
        console.print("  [3] 🚀 Iniciar Monitoramento de Sessão")
        console.print("  [4] 🔑 Gerenciar Licença & HWID")
        console.print("  [0] 🚪 Sair\n")

        opcao = Prompt.ask("  Escolha uma opção", choices=["0", "1", "2", "3", "4"])

        console.print()

        if opcao == "1":
            result = menu_carregar_edital()
            if result:
                _, regras_carregadas = result
                console.print(
                    f"\n  [green]✅ {len(regras_carregadas)} item(s) configurado(s).[/green]"
                )

        elif opcao == "2":
            menu_testar_conexao()

        elif opcao == "3":
            menu_iniciar_monitoramento(regras_carregadas)

        elif opcao == "4":
            menu_gerenciar_licenca()

        elif opcao == "0":
            console.print("  [dim]Encerrando... Até logo! 👋[/dim]\n")
            sys.exit(0)

        Prompt.ask("\n  Pressione Enter para continuar", default="")
        console.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """Processa argumentos de linha de comando."""
    parser = argparse.ArgumentParser(
        description="Robô de Licitações – Compras.gov.br",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python main.py                   # Abre interface gráfica (padrão)
  python main.py --cli             # Menu de texto no terminal
  python main.py --modo simulation # Força modo simulação
  python main.py --modo real       # ⚠️  Modo real (requer confirmação)
        """,
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Executa o diagnóstico completo de conexões no terminal e encerra.",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Usa o menu interativo de texto em vez da interface gráfica.",
    )
    parser.add_argument(
        "--modo",
        choices=["simulation", "real"],
        help="Sobrescreve o MODO_OPERACAO do .env.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Nível de log (sobrescreve LOG_LEVEL do .env).",
    )
    parser.add_argument(
        "--cadastrar",
        action="store_true",
        help="Aciona a automação de cadastro de proposta no Comprasnet",
    )
    parser.add_argument(
        "--uasg",
        type=str,
        help="Número da UASG para cadastro de proposta",
    )
    parser.add_argument(
        "--compra",
        type=str,
        help="Número da Compra/Pregão para cadastro de proposta",
    )
    return parser.parse_args()


def main() -> None:
    """
    Ponto de entrada principal.

    Por padrão abre a interface gráfica (CustomTkinter).
    Use --cli para o menu interativo de texto.
    Use --diagnostics para executar o auto-teste de conexões.
    """
    args = parse_args()

    if args.cadastrar:
        if not args.uasg or not args.compra:
            print("Erro: Os parâmetros --uasg e --compra são obrigatórios para --cadastrar.")
            sys.exit(1)
        print(f"Iniciando automação de cadastro para UASG: {args.uasg} | Compra: {args.compra}")
        
        from browser_bot import BrowserBot
        from engine import MotorDecisao
        import json
        
        bot = BrowserBot(motor=MotorDecisao(dry_run=False))
        # Para testes via CLI, passa lista vazia ou carrega de 'itens_proposta.json' se existir
        itens_proposta = []
        try:
            with open("itens_proposta.json", "r") as f:
                itens_proposta = json.load(f)
        except Exception:
            print("Aviso: 'itens_proposta.json' não encontrado. Abrindo tela apenas para navegação.")
            
        bot.cadastrar_proposta_comprasnet(args.uasg, args.compra, itens_proposta)
        sys.exit(0)

    if args.diagnostics:
        from diagnostics import executar_diagnostico_cli
        ok = executar_diagnostico_cli()
        sys.exit(0 if ok else 1)

    if args.modo:
        import os
        os.environ["MODO_OPERACAO"] = args.modo

    if args.log_level:
        logging.getLogger().setLevel(args.log_level)

    if args.cli:
        # Modo CLI de texto (menu Rich interativo)
        try:
            menu_principal()
        except KeyboardInterrupt:
            console.print("\n\n  [dim]Interrompido pelo usuário.[/dim]\n")
            sys.exit(0)
    else:
        # Modo GUI (padrão) — abre a interface gráfica
        try:
            from app_gui import run_gui
            run_gui()
        except ImportError as exc:
            console.print(
                f"[red]❌ Falha ao carregar a interface gráfica: {exc}[/red]\n"
                "   Instale: [bold]pip install customtkinter[/bold]\n"
                "   Ou use o modo CLI: [bold]python main.py --cli[/bold]"
            )
            sys.exit(1)


if __name__ == "__main__":
    main()
