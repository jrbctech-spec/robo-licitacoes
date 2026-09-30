# TAREFA DE ENGENHARIA: Automação Total do Robô de Licitações (JRBC TECH)

## Objetivo:
Refatorar os módulos do robô para permitir a importação instantânea de planilhas padronizadas e a pilotagem automática de lances na sala de disputa do Compras.gov.br.

---

## 1. Correções e Limpeza de Erros na Interface (app_gui.py)
1. Corrigir a incompatibilidade de atributos do `ResultadoEdital`:
   - Substituir qualquer chamada a `res.orgao_comprador` por `getattr(res, 'orgao_comprador', getattr(res, 'orgao', 'Órgão Não Informado'))`.
   - Substituir qualquer chamada a `res.objeto_licitacao` por `getattr(res, 'objeto_licitacao', getattr(res, 'objeto', 'Sem descrição'))`.
2. Aprimorar a função `_escanear_pasta_processos`:
   - Fazer busca recursiva (`rglob`) em subpastas por arquivos `.xlsx` e `.csv`.
   - Carregar e adicionar os certames encontrados diretamente ao catálogo da aplicação (`self.app.adicionar_licitacao`), atualizando o dropdown `cmb_certames` automaticamente.
3. No seletor de arquivos (`_carregar_planilha_manual`), usar o filtro Linux compatível: `[("Planilhas Excel/CSV", "*.xlsx *.csv"), ("Todos os arquivos", "*.*")]`.

---

## 2. Motor de Disputa em Tempo Real (engine.py e browser_bot.py)
Implementar a lógica de operador inteligente para a sala de disputa do Compras.gov.br:
1. `PilotoDisputa`:
   - Recebe a lista de itens do certame com: `numero_item`, `preco_referencia`, `custo_real`, `margem_minima`, `piso_seguranca` (stop-loss) e `intervalo_minimo` (padrão R$ 5,00 ou R$ 0,01).
2. Algoritmo de Lance:
   - Se `melhor_valor == meu_valor`: Retorna status `LIDERANDO` e bloqueia novos envios (anti-autocanibalização).
   - Se `melhor_valor < meu_valor`:
     * Calcula o contra-lance: `novo_lance = melhor_valor - intervalo_minimo`.
     * Se `novo_lance >= piso_seguranca`: autoriza o envio do lance.
     * Se `novo_lance < piso_seguranca`: ativa `STOP_LOSS`, bloqueia o lance e dispara alerta sonoro/log.
3. Timing e Proteção:
   - Respeitar a regra de envio nos últimos 30 segundos da prorrogação de 2 minutos para evitar guerra predatória de lances antes do momento decisivo.

---

## 3. Testes Automatizados
- Criar testes unitários em `tests/test_piloto_disputa.py` simulando:
  * Manutenção de liderança sem lances repetidos.
  * Cálculo de contra-lance com decremento mínimo.
  * Bloqueio estrito no stop-loss (margem de 30% produto / 50% serviço).
