"""
test_novo_motor_decisao.py – Suíte de Testes Automatizados dos Requisitos do Novo Motor.

Cenários cobertos:
  1. Meu lance = 50.00, Menor = 50.00, Piso = 45.00, Intervalo = 0.01 -> NÃO LANÇAR
  2. Meu lance = 50.00, Menor = 49.80, Piso = 45.00, Intervalo = 0.01 -> LANÇAR 49.79
  3. Meu lance = 46.00, Menor = 45.50, Piso = 45.00, Intervalo = 0.01 -> LANÇAR 45.49
  4. Meu lance = 45.50, Menor = 45.00, Piso = 45.00, Intervalo = 0.01 -> NÃO LANÇAR (PISO_ATINGIDO)
  5. Disputa encerrada -> NÃO LANÇAR
  6. Intervalo = 0.10, Meu lance = 50.00, Menor = 49.80, Piso = 45.00 -> LANÇAR 49.70
  7. Preço mínimo isolado por item (Item 1: R$ 180, Item 2: R$ 45, Item 3: R$ 170)
  8. Proteção contra lance duplicado (lance_pendente)
  9. Decremento percentual configurável
  10. Fail-Safe com item inativo
"""
import unittest
from decimal import Decimal
from engine import (
    MotorDecisao,
    RegraItem,
    TipoIntervalo,
    EstadoItemEnum,
    MotivoBloqueio,
    DecisaoLance,
)


class TestNovoMotorDecisao(unittest.TestCase):

    def setUp(self):
        self.motor = MotorDecisao(dry_run=True)

    def test_cenario_1_menor_igual_ao_meu_nao_lancar(self):
        """
        Teste 1:
        Meu lance = 50.00
        Menor = 50.00
        Piso = 45.00
        Intervalo = 0.01
        Resultado: NÃO LANÇAR
        """
        regra = RegraItem(
            item_id="item-01",
            numero_item=1,
            descricao="Item Teste 1",
            custo_unitario=40.00,
            margem_minima_percent=12.5,
            preco_minimo=45.00,
            preco_inicial_proposta=50.00,
            intervalo_minimo=0.01,
            tipo_intervalo=TipoIntervalo.MONETARIO,
        )
        self.motor.carregar_regras([regra])
        self.motor.iniciar_sessao()

        # Atualiza estado: menor lance = 50.00, nosso lance = 50.00
        self.motor.atualizar_estado(
            numero_item=1,
            melhor_lance_mercado=50.00,
            nosso_ultimo_lance=50.00,
            nossa_posicao=1,
        )
        decisao = self.motor.calcular_lance(1)

        self.assertFalse(decisao.deve_dar_lance)
        self.assertEqual(decisao.estado_resultante, EstadoItemEnum.LIDERANDO)
        self.assertEqual(decisao.motivo_bloqueio, MotivoBloqueio.JA_VENCENDO)
        print("\n✅ Teste 1 passou: Menor lance == Meu lance -> NÃO LANÇAR (LIDERANDO)")

    def test_cenario_2_superado_lancar_com_degrau(self):
        """
        Teste 2:
        Meu lance = 50.00
        Menor = 49.80
        Piso = 45.00
        Intervalo = 0.01
        Resultado: LANÇAR 49.79
        """
        regra = RegraItem(
            item_id="item-02",
            numero_item=2,
            descricao="Item Teste 2",
            custo_unitario=40.00,
            margem_minima_percent=12.5,
            preco_minimo=45.00,
            preco_inicial_proposta=50.00,
            intervalo_minimo=0.01,
            tipo_intervalo=TipoIntervalo.MONETARIO,
        )
        self.motor.carregar_regras([regra])
        self.motor.iniciar_sessao()

        # Concorrente deu 49.80 (menor que nosso último de 50.00)
        self.motor.atualizar_estado(
            numero_item=2,
            melhor_lance_mercado=49.80,
            nosso_ultimo_lance=50.00,
            nossa_posicao=2,
        )
        decisao = self.motor.calcular_lance(2)

        self.assertTrue(decisao.deve_dar_lance)
        self.assertEqual(decisao.valor_lance, 49.79)
        self.assertFalse(decisao.bloqueado_stop_loss)
        print(f"\n✅ Teste 2 passou: Menor 49.80 -> Novo lance calculado = R$ {decisao.valor_lance:.2f}")

    def test_cenario_3_superado_intermediario(self):
        """
        Teste 3:
        Meu lance = 46.00
        Menor = 45.50
        Piso = 45.00
        Intervalo = 0.01
        Resultado: LANÇAR 45.49
        """
        regra = RegraItem(
            item_id="item-03",
            numero_item=3,
            descricao="Item Teste 3",
            custo_unitario=40.00,
            margem_minima_percent=12.5,
            preco_minimo=45.00,
            preco_inicial_proposta=50.00,
            intervalo_minimo=0.01,
            tipo_intervalo=TipoIntervalo.MONETARIO,
        )
        self.motor.carregar_regras([regra])
        self.motor.iniciar_sessao()

        self.motor.atualizar_estado(
            numero_item=3,
            melhor_lance_mercado=45.50,
            nosso_ultimo_lance=46.00,
            nossa_posicao=2,
        )
        decisao = self.motor.calcular_lance(3)

        self.assertTrue(decisao.deve_dar_lance)
        self.assertEqual(decisao.valor_lance, 45.49)
        print(f"\n✅ Teste 3 passou: Menor 45.50 -> Novo lance calculado = R$ {decisao.valor_lance:.2f}")

    def test_cenario_4_preco_minimo_atingido_piso(self):
        """
        Teste 4:
        Meu lance: R$ 45.50
        Menor lance: R$ 45.00
        Piso: R$ 45.00
        Intervalo: R$ 0.01
        Novo lance matemático = 44.99 (< 45.00)
        Resultado: NÃO LANÇAR (PISO_ATINGIDO)
        """
        regra = RegraItem(
            item_id="item-04",
            numero_item=4,
            descricao="Item Teste 4",
            custo_unitario=40.00,
            margem_minima_percent=12.5,
            preco_minimo=45.00,
            preco_inicial_proposta=50.00,
            intervalo_minimo=0.01,
            tipo_intervalo=TipoIntervalo.MONETARIO,
        )
        self.motor.carregar_regras([regra])
        self.motor.iniciar_sessao()

        self.motor.atualizar_estado(
            numero_item=4,
            melhor_lance_mercado=45.00,
            nosso_ultimo_lance=45.50,
            nossa_posicao=2,
        )
        decisao = self.motor.calcular_lance(4)

        self.assertFalse(decisao.deve_dar_lance)
        self.assertTrue(decisao.bloqueado_stop_loss)
        self.assertEqual(decisao.estado_resultante, EstadoItemEnum.PISO_ATINGIDO)
        self.assertEqual(decisao.motivo_bloqueio, MotivoBloqueio.STOP_LOSS)
        self.assertIn("PREÇO MÍNIMO ATINGIDO", decisao.motivo)
        print(f"\n✅ Teste 4 passou: Preço mínimo atingido -> {decisao.motivo}")

    def test_cenario_5_disputa_encerrada_nao_lancar(self):
        """
        Teste 5:
        Disputa encerrada
        Resultado: NÃO LANÇAR
        """
        regra = RegraItem(
            item_id="item-05",
            numero_item=5,
            descricao="Item Teste 5",
            custo_unitario=40.00,
            margem_minima_percent=12.5,
            preco_minimo=45.00,
            preco_inicial_proposta=50.00,
        )
        self.motor.carregar_regras([regra])
        self.motor.iniciar_sessao()

        # Marca disputa como encerrada
        self.motor.atualizar_estado(
            numero_item=5,
            melhor_lance_mercado=48.00,
            disputa_aberta=False,
        )
        decisao = self.motor.calcular_lance(5)

        self.assertFalse(decisao.deve_dar_lance)
        self.assertEqual(decisao.estado_resultante, EstadoItemEnum.ENCERRADO)
        self.assertEqual(decisao.motivo_bloqueio, MotivoBloqueio.DISPUTA_ENCERRADA)
        print("\n✅ Teste 5 passou: Disputa encerrada -> NÃO LANÇAR (ENCERRADO)")

    def test_cenario_6_intervalo_personalizado_dez_centavos(self):
        """
        Teste 6:
        Intervalo = 0.10
        Meu lance = 50.00
        Menor = 49.80
        Piso = 45.00
        Resultado: LANÇAR 49.70
        """
        regra = RegraItem(
            item_id="item-06",
            numero_item=6,
            descricao="Item Teste 6",
            custo_unitario=40.00,
            margem_minima_percent=12.5,
            preco_minimo=45.00,
            preco_inicial_proposta=50.00,
            intervalo_minimo=0.10,
            tipo_intervalo=TipoIntervalo.MONETARIO,
        )
        self.motor.carregar_regras([regra])
        self.motor.iniciar_sessao()

        self.motor.atualizar_estado(
            numero_item=6,
            melhor_lance_mercado=49.80,
            nosso_ultimo_lance=50.00,
            nossa_posicao=2,
        )
        decisao = self.motor.calcular_lance(6)

        self.assertTrue(decisao.deve_dar_lance)
        self.assertEqual(decisao.valor_lance, 49.70)
        print(f"\n✅ Teste 6 passou: Intervalo R$ 0.10 -> Novo lance calculado = R$ {decisao.valor_lance:.2f}")

    def test_cenario_7_precos_minimos_independentes_por_item(self):
        """
        Teste 7:
        Item 1: Piso R$ 180.00, Menor R$ 185.00, Meu R$ 190.00 -> LANÇAR R$ 184.99
        Item 2: Piso R$ 45.00, Menor R$ 44.90, Meu R$ 50.00 -> NÃO LANÇAR (PISO_ATINGIDO)
        Item 3: Piso R$ 170.00, Menor R$ 175.00, Meu R$ 180.00 -> LANÇAR R$ 174.99
        """
        regras = [
            RegraItem(
                item_id="it-1", numero_item=1, descricao="Item 1",
                custo_unitario=150.0, margem_minima_percent=20.0, preco_minimo=180.00,
                preco_inicial_proposta=200.0, intervalo_minimo=0.01,
            ),
            RegraItem(
                item_id="it-2", numero_item=2, descricao="Item 2",
                custo_unitario=38.0, margem_minima_percent=18.42, preco_minimo=45.00,
                preco_inicial_proposta=55.0, intervalo_minimo=0.01,
            ),
            RegraItem(
                item_id="it-3", numero_item=3, descricao="Item 3",
                custo_unitario=140.0, margem_minima_percent=21.43, preco_minimo=170.00,
                preco_inicial_proposta=190.0, intervalo_minimo=0.01,
            ),
        ]
        self.motor.carregar_regras(regras)
        self.motor.iniciar_sessao()

        # Item 1
        self.motor.atualizar_estado(1, melhor_lance_mercado=185.00, nosso_ultimo_lance=190.00, nossa_posicao=2)
        dec_1 = self.motor.calcular_lance(1)
        self.assertTrue(dec_1.deve_dar_lance)
        self.assertEqual(dec_1.valor_lance, 184.99)

        # Item 2 (Concorrente deu 44.90, abaixo do piso de 45.00)
        self.motor.atualizar_estado(2, melhor_lance_mercado=44.90, nosso_ultimo_lance=50.00, nossa_posicao=2)
        dec_2 = self.motor.calcular_lance(2)
        self.assertFalse(dec_2.deve_dar_lance)
        self.assertEqual(dec_2.estado_resultante, EstadoItemEnum.PISO_ATINGIDO)

        # Item 3
        self.motor.atualizar_estado(3, melhor_lance_mercado=175.00, nosso_ultimo_lance=180.00, nossa_posicao=2)
        dec_3 = self.motor.calcular_lance(3)
        self.assertTrue(dec_3.deve_dar_lance)
        self.assertEqual(dec_3.valor_lance, 174.99)
        print("\n✅ Teste 7 passou: Preços mínimos 100% independentes por item testados com sucesso!")

    def test_cenario_8_protecao_anti_duplicidade_e_confirmacao(self):
        """
        Teste 8:
        Garante que um item com lance_pendente=True bloqueia novos lances concorrentes
        e só libera após confirmação explícita do portal.
        """
        regra = RegraItem(
            item_id="item-08",
            numero_item=8,
            descricao="Item Teste 8",
            custo_unitario=40.00,
            margem_minima_percent=12.5,
            preco_minimo=45.00,
            preco_inicial_proposta=50.00,
            intervalo_minimo=0.01,
        )
        self.motor.carregar_regras([regra])
        self.motor.iniciar_sessao()

        self.motor.atualizar_estado(8, melhor_lance_mercado=48.00, nosso_ultimo_lance=50.00, nossa_posicao=2)

        # 1. Ativa trava de envio
        self.assertTrue(self.motor.marcar_lance_em_envio(8))
        self.assertFalse(self.motor.marcar_lance_em_envio(8)) # Tentativa duplicada bloqueada!

        # 2. Motor bloqueia cálculo durante trânsito
        decisao = self.motor.calcular_lance(8)
        self.assertFalse(decisao.deve_dar_lance)
        self.assertEqual(decisao.motivo_bloqueio, MotivoBloqueio.LANCE_PENDENTE)

        # 3. Portal confirma envio
        self.motor.confirmar_lance_enviado(8, valor_confirmado=47.99)
        self.assertEqual(float(self.motor._estados[8].meu_ultimo_lance), 47.99)
        self.assertFalse(self.motor._estados[8].lance_pendente)
        print("\n✅ Teste 8 passou: Trava atômica anti-duplicidade e confirmação validadas!")

    def test_cenario_9_intervalo_percentual(self):
        """
        Teste 9:
        Intervalo percentual de 1,0% sobre menor lance de R$ 100,00 -> Novo lance = R$ 99,00
        """
        regra = RegraItem(
            item_id="item-09",
            numero_item=9,
            descricao="Item Teste 9",
            custo_unitario=70.00,
            margem_minima_percent=10.0,
            preco_minimo=77.00,
            preco_inicial_proposta=105.00,
            intervalo_minimo=1.0,
            tipo_intervalo=TipoIntervalo.PERCENTUAL,
        )
        self.motor.carregar_regras([regra])
        self.motor.iniciar_sessao()

        self.motor.atualizar_estado(9, melhor_lance_mercado=100.00, nosso_ultimo_lance=105.00, nossa_posicao=2)
        decisao = self.motor.calcular_lance(9)

        self.assertTrue(decisao.deve_dar_lance)
        self.assertEqual(decisao.valor_lance, 99.00)
        print(f"\n✅ Teste 9 passou: Intervalo 1.0% sobre 100.00 -> R$ {decisao.valor_lance:.2f}")


if __name__ == "__main__":
    unittest.main()
