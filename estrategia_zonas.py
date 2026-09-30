"""
Módulo: estrategia_zonas.py
Finalidade: Cálculo dinâmico do valor do próximo lance baseado em zonas de risco/margem.
"""

from decimal import Decimal, ROUND_HALF_UP

class EstrategiaZonas:
    def __init__(
        self,
        valor_referencia: Decimal,
        stop_loss: Decimal,
        decremento_minimo_edital: Decimal = Decimal("0.01")
    ):
        self.valor_referencia = valor_referencia
        self.stop_loss = stop_loss
        self.decremento_minimo = decremento_minimo_edital
        
        # Faixa total disponível para disputa
        self.amplitude_disputa = self.valor_referencia - self.stop_loss
        
        # Limiares percentuais a partir do piso (Stop-Loss)
        # Exemplo: 0% a 25% acima do piso = Vermelha | 25% a 60% = Amarela | >60% = Verde
        self.limiar_vermelha = self.stop_loss + (self.amplitude_disputa * Decimal("0.25"))
        self.limiar_amarela = self.stop_loss + (self.amplitude_disputa * Decimal("0.60"))

    def identificar_zona(self, melhor_lance_atual: Decimal) -> str:
        """Determina em qual zona a disputa se encontra no momento."""
        if melhor_lance_atual <= self.limiar_vermelha:
            return "VERMELHA"
        elif melhor_lance_atual <= self.limiar_amarela:
            return "AMARELA"
        else:
            return "VERDE"

    def calcular_decremento(self, melhor_lance_atual: Decimal) -> Decimal:
        """
        Calcula a intensidade do corte de preço de acordo com a zona.
        Pode ser ajustado conforme a regra do edital (percentual ou valor fixo).
        """
        zona = self.identificar_zona(melhor_lance_atual)

        if zona == "VERDE":
            # Corte mais expressivo: 1% do lance atual ou 10x o decremento mínimo
            decremento = max(
                (melhor_lance_atual * Decimal("0.01")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                self.decremento_minimo * 10
            )
        elif zona == "AMARELA":
            # Corte moderado: 0.2% do lance atual ou 3x o decremento mínimo
            decremento = max(
                (melhor_lance_atual * Decimal("0.002")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                self.decremento_minimo * 3
            )
        else:  # VERMELHA
            # Disputa no limite: decremento mínimo exato
            decremento = self.decremento_minimo

        return decremento

    def sugerir_proximo_lance(
        self,
        melhor_lance_atual: Decimal,
        meu_ultimo_lance: Decimal | None = None
    ) -> tuple[Decimal | None, str]:
        """
        Retorna (proximo_lance, motivo).
        Se retornar None, significa que devemos segurar o lance ou que atingiu o stop-loss.
        """
        # Se já somos o melhor lance, não cobrimos a nós mesmos
        if meu_ultimo_lance is not None and meu_ultimo_lance == melhor_lance_atual:
            return None, "MANTER: Já possuímos a melhor proposta."

        decremento = self.calcular_decremento(melhor_lance_atual)
        novo_lance = melhor_lance_atual - decremento

        # Verificação atômica de Stop-Loss
        if novo_lance < self.stop_loss:
            # Tenta cobrir exatamente no stop-loss se for menor que o melhor lance
            if self.stop_loss < melhor_lance_atual:
                return self.stop_loss, "LIMITE: Lance fixado exatamente no Stop-Loss."
            return None, "STOP-LOSS: Proposta concorrente abaixo da margem mínima viável."

        zona = self.identificar_zona(melhor_lance_atual)
        return novo_lance, f"SUCESSO: Lance calculado na Zona {zona} (corte de R$ {decremento})."
