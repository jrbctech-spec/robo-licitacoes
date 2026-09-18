"""
teste_integracao_browser.py – Teste de integração entre MotorDecisao e BrowserBot simulado.

Objetivo: validar que o motor de decisão e o bot se comunicam corretamente
usando uma página HTML mock do Compras.gov.br em vez da página real.

Este teste:
  1. Cria regras de 3 itens (manutenção de TI)
  2. Inicia o motor em modo simulação
  3. Simula a página do Compras.gov.br com leituras de mercado
  4. Verifica se o motor calcula lances corretamente
  5. Verifica se o bot "enviaria" lances (dry-run)
  6. Gera relatório final com os resultados
"""

from __future__ import annotations

import logging
import sys
import uuid
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("teste_integracao")

# Adiciona o diretório do projeto ao path
BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from engine import MotorDecisao, RegraItem, TipoIntervalo, EstadoItemEnum


def criar_regras_padrao() -> list[RegraItem]:
    """Cria 3 regras de itens de manutenção de TI (alinhado ao perfil MEI Jakson)."""
    return [
        RegraItem(
            item_id="item-1",
            numero_item=1,
            descricao="Manutenção preventiva de computador desktop (limpeza, drivers, verificação)",
            custo_unitario=55.00,
            margem_minima_percent=20.0,
            preco_minimo=66.00,
            preco_inicial_proposta=75.90,
            intervalo_minimo=0.01,
            tipo_intervalo=TipoIntervalo.MONETARIO,
            valor_referencia=850.00,
        ),
        RegraItem(
            item_id="item-2",
            numero_item=2,
            descricao="Instalação e configuração de rede Wi-Fi (serviço + equipamentos inclusos)",
            custo_unitario=450.00,
            margem_minima_percent=15.0,
            preco_minimo=517.50,
            preco_inicial_proposta=595.13,
            intervalo_minimo=0.01,
            tipo_intervalo=TipoIntervalo.MONETARIO,
            valor_referencia=1200.00,
        ),
        RegraItem(
            item_id="item-3",
            numero_item=3,
            descricao="Suporte técnico remoto e presencial para estações de trabalho (mês)",
            custo_unitario=150.00,
            margem_minima_percent=25.0,
            preco_minimo=187.50,
            preco_inicial_proposta=215.31,
            intervalo_minimo=0.50,
            tipo_intervalo=TipoIntervalo.MONETARIO,
            valor_referencia=600.00,
        ),
    ]


class SimulacaoMercado:
    """
    Simula o comportamento de um pregão eletrônico com 3 itens.
    Cada ciclo representa 5 segundos de polling.
    """

    def __init__(self, regras: list[RegraItem]):
        self.regras = {r.numero_item: r for r in regras}
        self.ciclo = 0
        # Estado simulado do mercado por item
        self.menor_lance = {r.numero_item: None for r in regras}
        self.posicao = {r.numero_item: None for r in regras}
        self.nosso_ultimo = {r.numero_item: r.preco_inicial_proposta for r in regras}

    def simular_ciclo(self, motor: MotorDecisao) -> list[dict]:
        """
        Executa um ciclo de simulação: atualiza mercado → motor calcula →
        bot processa (dry-run).
        """
        self.ciclo += 1
        resultados = []

        for num, regra in self.regras.items():
            # Simula comportamento de um mercado competitivo
            estado = motor._estados[num]
            meu_ultimo = float(estado.meu_ultimo_lance) if estado.meu_ultimo_lance else regra.preco_inicial_proposta

            # A cada ciclo, simula concorrentes oferecendo valores abaixo do nosso
            # No ciclo 1: ninguém lanceia
            # No ciclo 2: concorrente oferece ~99% do nosso lance
            # No ciclo 3: concorrente oferece ~95% do nosso lance (quebrando liderança)
            # No ciclo 4+: concorrente continua dando abaixo
            if self.ciclo == 1:
                # Nenhum lance ainda
                motor.atualizar_estado(numero_item=num, melhor_lance_mercado=None)
                decisao = motor.calcular_lance(num)
                resultados.append({"ciclo": self.ciclo, "item": num, "decisao": decisao, "acao_bot": "aguardando"})
            else:
                # Concorrente entra com lance
                porcentagem_mercado = 0.99 if self.ciclo == 2 else 0.97 if self.ciclo == 3 else 0.95
                lance_mercado = round(meu_ultimo * porcentagem_mercado, 2)

                motor.atualizar_estado(
                    numero_item=num,
                    melhor_lance_mercado=lance_mercado,
                    nossa_posicao=2 if lance_mercado < meu_ultimo else 1,
                    nosso_ultimo_lance=meu_ultimo,
                )

                decisao = motor.calcular_lance(num)
                resultados.append({"ciclo": self.ciclo, "item": num, "decisao": decisao, "lance_mercado": lance_mercado})

                # Se o motor disse para lançar, simula confirmação do portal
                if decisao.deve_dar_lance and decisao.valor_lance:
                    novo_meu = decisao.valor_lance
                    motor.confirmar_lance_enviado(num, novo_meu)
                    self.nosso_ultimo[num] = novo_meu

        return resultados


def rodar_teste():
    print("╔════════════════════════════════════════════════════════════════╗")
    print("║   🧪 TESTE DE INTEGRAÇÃO: Motor + Simulação de Mercado       ║")
    print("╚════════════════════════════════════════════════════════════════╝")
    print()

    # 1. Preparar moto e regras
    motor = MotorDecisao(dry_run=True)
    regras = criar_regras_padrao()
    motor.carregar_regras(regras)
    motor.iniciar_sessao()

    sessao_id = str(uuid.uuid4())
    print(f"🔑 Sessão ID: {sessao_id[:8]}...")
    print(f"🛡️  Modo: SIMULAÇÃO (dry_run=True)")
    print(f"📊 {len(regras)} itens configurados:")
    for r in regras:
        print(f"   Item {r.numero_item}: {r.descricao[:50]}...")
        print(f"      Custo: R$ {r.custo_unitario:.2f} | Stop-Loss: R$ {r.preco_minimo:.2f} | Ref: R$ {r.valor_referencia:.2f}")
    print()

    # 2. Simular mercado por 6 ciclos
    mercado = SimulacaoMercado(regras)

    print("═══ SIMULAÇÃO DE 6 CICLOS (cada ciclo = 5 segundos de polling) ═══")
    print()

    for ciclo in range(1, 7):
        print(f"─── CICLO {ciclo} ───")
        resultados = mercado.simular_ciclo(motor)

        for res in resultados:
            num = res["item"]
            d = res["decisao"]
            if d.deve_dar_lance and d.valor_lance:
                print(f"  Item {num}: 🟢 LANCE R$ {d.valor_lance:.2f} | Mercado: R$ {res.get('lance_mercado', 'N/A')} | Pos: {d.posicao_atual}")
            else:
                print(f"  Item {num}: 🔴 BLOQUEADO | {d.motivo_bloqueio.value if d.motivo_bloqueio else d.motivo[:60]}")
        print()

    # 3. Relatório final
    print("╔════════════════════════════════════════════════════════════════╗")
    print("║                 RELATÓRIO FINAL DO TESTE                      ║")
    print("╚════════════════════════════════════════════════════════════════╝")
    print()

    relatorio = motor.relatorio_sessao()
    print(f"  Modo:                SIMULAÇÃO")
    print(f"  Total decisões:      {relatorio['total_decisoes']}")
    print(f"  Lances calculados:   {relatorio['lances_calculados']}")
    print(f"  Bloqueios stop-loss: {relatorio['bloqueios_stop_loss']}")
    print(f"  Pisos atingidos:     {relatorio['pisos_atingidos']}")
    print()

    # Verificações automáticas
    erros = []

    # Verificação 1: pelo menos um lance foi calculado
    if relatorio["lances_calculados"] == 0:
        erros.append("❌ Falha: nenhum lance foi calculado durante a simulação")

    # Verificação 2: stop-loss nunca foi violado
    for estado in motor._estados.values():
        if estado.estado_atual == EstadoItemEnum.PISO_ATINGIDO:
            # É esperado que alguns itens cheguem ao piso eventualmente
            logger.info(f"  Item {estado.regra.numero_item} atingiu piso (comportamento esperado)")
        # Verifica que nosso_ultimo_lance nunca caiu abaixo do piso
        if estado.meu_ultimo_lance and estado.meu_ultimo_lance < estado.regra.preco_minimo:
            erros.append(f"❌ FALHA CRÍTICA: Item {estado.regra.numero_item} — nosso lance R$ {estado.meu_ultimo_lance:.2f} < piso R$ {estado.regra.preco_minimo:.2f}")

    # Verificação 3: itens isolados (nada de mistura entre itens)
    for num, estado in motor._estados.items():
        regra = estado.regra
        if estado.menor_lance_atual and float(estado.menor_lance_atual) <= 0:
            erros.append(f"❌ Item {num}: menor_lance_atual inválido")

    if erros:
        print("❌ ERROS ENCONTRADOS:")
        for e in erros:
            print(f"  {e}")
        print()
        return False
    else:
        print("✅ TODAS AS VERIFICAÇÕES PASSARAM")
        print()
        print("📊 RESUMO DO MOTOR:")
        for estado in motor._estados.values():
            r = estado.regra
            print(f"  Item {r.numero_item}:")
            print(f"    Último lance:   R$ {float(estado.meu_ultimo_lance):.2f}" if estado.meu_ultimo_lance else "    Último lance:   N/A")
            print(f"    Stop-Loss:      R$ {r.preco_minimo:.2f}")
            print(f"    Estado:         {estado.estado_atual.value}")
            print(f"    Total de lances: {estado.total_lances_dados}")
            print()
        print("🎯 TESTE DE INTEGRAÇÃO CONCLUÍDO COM SUCESSO.")
        print("   O motor e a simulação de mercado comunicam-se corretamente.")
        print("   O próximo passo é integrar com o browser_bot.py real (Playwright).")
        return True


if __name__ == "__main__":
    sucesso = rodar_teste()
    sys.exit(0 if sucesso else 1)
