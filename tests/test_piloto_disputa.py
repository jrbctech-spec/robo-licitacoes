"""
tests/test_piloto_disputa.py – Testes unitários para o Piloto de Disputa (JRBC TECH).

Validações obrigatórias:
  1. Manutenção de liderança sem lances repetidos (Anti-autocanibalização).
  2. Cálculo de contra-lance com decremento mínimo configurado.
  3. Bloqueio estrito no stop-loss com regras de margem (30% Produto / 50% Serviço).
  4. Timing e proteção na prorrogação de 2 minutos (janela dos últimos 30 segundos).
"""
from __future__ import annotations

import unittest
from engine import ItemPiloto, PilotoDisputa, TipoItemEnum


class TestPilotoDisputa(unittest.TestCase):
    """Bateria de testes determinísticos do Piloto de Disputa em Tempo Real."""

    def setUp(self) -> None:
        """Inicializa itens de teste para produtos e serviços."""
        # Item 1: PRODUTO (Hardware) - Custo R$ 100,00 -> Stop-Loss R$ 130,00 (30% margem)
        self.item_produto = ItemPiloto(
            numero_item=1,
            custo_real=100.00,
            preco_referencia=200.00,
            margem_minima=30.0,
            intervalo_minimo=0.01,
            tipo_item=TipoItemEnum.PRODUTO,
        )

        # Item 2: SERVICO (Suporte TI) - Custo R$ 200,00 -> Stop-Loss R$ 300,00 (50% margem)
        self.item_servico = ItemPiloto(
            numero_item=2,
            custo_real=200.00,
            preco_referencia=500.00,
            margem_minima=50.0,
            intervalo_minimo=5.00,
            tipo_item=TipoItemEnum.SERVICO,
        )

        self.piloto = PilotoDisputa(itens=[self.item_produto, self.item_servico])

    def test_manutencao_lideranca_anti_autocanibalizacao(self) -> None:
        """Quando o menor lance do portal é o meu próprio lance, NÃO deve reenviar lance."""
        # Simula que meu lance atual no item 1 é R$ 180,00 e o menor lance do portal também é R$ 180,00
        self.piloto.registrar_lance_enviado(numero_item=1, valor=180.00)

        decisao = self.piloto.avaliar_lance(
            numero_item=1,
            melhor_valor=180.00,
            meu_valor=180.00,
        )

        self.assertFalse(decisao.deve_enviar, "Não deve enviar lance quando já está liderando.")
        self.assertEqual(decisao.status, "LIDERANDO")
        self.assertIn("Anti-autocanibalização", decisao.motivo)
        self.assertIsNone(decisao.valor_lance)

    def test_calculo_contra_lance_decremento_minimo_centavos(self) -> None:
        """Calcula contra-lance aplicando decremento de R$ 0,01 sobre o concorrente."""
        self.piloto.registrar_lance_enviado(numero_item=1, valor=180.00)

        # Concorrente cobriu com R$ 175.50
        decisao = self.piloto.avaliar_lance(
            numero_item=1,
            melhor_valor=175.50,
            meu_valor=180.00,
        )

        self.assertTrue(decisao.deve_enviar)
        self.assertEqual(decisao.status, "LANCE_AUTORIZADO")
        self.assertEqual(decisao.valor_lance, 175.49)
        self.assertFalse(decisao.bloqueado_stop_loss)

    def test_calculo_contra_lance_decremento_minimo_cinco_reais(self) -> None:
        """Calcula contra-lance aplicando decremento de R$ 5,00 sobre o concorrente no serviço."""
        self.piloto.registrar_lance_enviado(numero_item=2, valor=450.00)

        # Concorrente deu R$ 400.00
        decisao = self.piloto.avaliar_lance(
            numero_item=2,
            melhor_valor=400.00,
            meu_valor=450.00,
        )

        self.assertTrue(decisao.deve_enviar)
        self.assertEqual(decisao.status, "LANCE_AUTORIZADO")
        self.assertEqual(decisao.valor_lance, 395.00)

    def test_bloqueio_estrito_stop_loss_produto_30_pct(self) -> None:
        """Bloqueia lance quando o contra-lance atinge ou fica abaixo do piso de 30% do produto."""
        # Stop-Loss do item 1 é R$ 130,00 (100 * 1.30)
        self.assertEqual(self.item_produto.piso_seguranca, 130.00)

        self.piloto.registrar_lance_enviado(numero_item=1, valor=131.00)

        # Concorrente dá lance de R$ 130.00. O próximo contra-lance seria R$ 129.99 (abaixo do piso R$ 130.00)
        decisao = self.piloto.avaliar_lance(
            numero_item=1,
            melhor_valor=130.00,
            meu_valor=131.00,
        )

        self.assertFalse(decisao.deve_enviar, "Deve bloquear lance que viola o stop-loss.")
        self.assertEqual(decisao.status, "STOP_LOSS")
        self.assertTrue(decisao.bloqueado_stop_loss)
        self.assertIn("STOP-LOSS atingido", decisao.motivo)

    def test_bloqueio_estrito_stop_loss_servico_50_pct(self) -> None:
        """Bloqueia lance quando o contra-lance atinge ou fica abaixo do piso de 50% do serviço."""
        # Stop-Loss do item 2 é R$ 300,00 (200 * 1.50)
        self.assertEqual(self.item_servico.piso_seguranca, 300.00)

        self.piloto.registrar_lance_enviado(numero_item=2, valor=310.00)

        # Concorrente dá lance de R$ 302.00. Com decremento de R$ 5,00, novo lance seria R$ 297.00 (< R$ 300.00)
        decisao = self.piloto.avaliar_lance(
            numero_item=2,
            melhor_valor=302.00,
            meu_valor=310.00,
        )

        self.assertFalse(decisao.deve_enviar, "Deve acionar trava de segurança stop-loss para serviço.")
        self.assertEqual(decisao.status, "STOP_LOSS")
        self.assertTrue(decisao.bloqueado_stop_loss)

    def test_timing_prorrogacao_ultimos_30_segundos(self) -> None:
        """Durante a prorrogação de 2 minutos, segura contra-lances até os últimos 30 segundos."""
        self.piloto.registrar_lance_enviado(numero_item=1, valor=180.00)

        # Concorrente cobriu com R$ 170.00, mas faltam 75s para acabar a prorrogação
        decisao_aguardo = self.piloto.avaliar_lance(
            numero_item=1,
            melhor_valor=170.00,
            meu_valor=180.00,
            tempo_restante_prorrogacao_s=75.0,
            em_prorrogacao=True,
        )

        self.assertFalse(decisao_aguardo.deve_enviar)
        self.assertEqual(decisao_aguardo.status, "AGUARDANDO_JANELA_FINAL")
        self.assertIn("75.0s", decisao_aguardo.motivo)

        # Quando faltam 20s (<= 30s), autoriza o envio decisivo
        decisao_envio = self.piloto.avaliar_lance(
            numero_item=1,
            melhor_valor=170.00,
            meu_valor=180.00,
            tempo_restante_prorrogacao_s=20.0,
            em_prorrogacao=True,
        )

        self.assertTrue(decisao_envio.deve_enviar)
        self.assertEqual(decisao_envio.status, "LANCE_AUTORIZADO")
        self.assertEqual(decisao_envio.valor_lance, 169.99)

    def test_lance_inicial_abertura_sem_concorrentes(self) -> None:
        """Quando não há concorrentes nem lances prévios, autoriza proposta de abertura."""
        decisao = self.piloto.avaliar_lance(
            numero_item=1,
            melhor_valor=None,
            meu_valor=None,
        )

        self.assertTrue(decisao.deve_enviar)
        self.assertEqual(decisao.status, "LANCE_AUTORIZADO")
        self.assertEqual(decisao.valor_lance, 200.00)  # Preço de referência


if __name__ == "__main__":
    unittest.main()
