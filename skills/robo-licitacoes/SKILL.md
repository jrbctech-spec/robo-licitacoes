---
name: robo-licitacoes
description: Skill de integração do Robô de Licitações (Compras.gov.br, PNCP, Parsing de Editais PDF e Supabase) para o Hermes Agent.
---

# 🤖 Skill: Robô de Licitações (Compras.gov.br & PNCP)

Esta skill conecta o Hermes Agent diretamente ao sistema **Robô de Licitações** em `/home/jrbc/Documentos/robo-licitacoes`.

O Hermes pode executar a ponte de comando rápida `hermes_cli.py` para realizar análises, pesquisas e testes.

---

## 🛠️ Comandos da Ponte (`hermes_cli.py`)

### 1. Diagnóstico Completo do Robô
Verifica Supabase, Playwright Chromium, variáveis de ambiente e diretórios:
```bash
/home/jrbc/Documentos/robo-licitacoes/.venv/bin/python /home/jrbc/Documentos/robo-licitacoes/hermes_cli.py --diagnostics
```

### 2. Leitura e Análise de Editais PDF
Extrai itens, quantidades, valores de referência, modalidade e matriz de riscos regulatórios (Lei 14.133/2021):
```bash
/home/jrbc/Documentos/robo-licitacoes/.venv/bin/python /home/jrbc/Documentos/robo-licitacoes/hermes_cli.py --parse /caminho/para/edital.pdf
```

### 3. Busca de Oportunidades no PNCP
Pesquisa licitações abertas no Portal Nacional por UF ou palavra-chave:
```bash
/home/jrbc/Documentos/robo-licitacoes/.venv/bin/python /home/jrbc/Documentos/robo-licitacoes/hermes_cli.py --pncp --uf TO --kw "computador"
```

### 4. Teste do Motor de Decisão (Simulação & Stop-Loss)
Testa a lógica determinística de lances e travas de preço mínimo em memória:
```bash
/home/jrbc/Documentos/robo-licitacoes/.venv/bin/python /home/jrbc/Documentos/robo-licitacoes/hermes_cli.py --test-engine
```

### 5. Varredura Local de Processos
Escaneia uma pasta com vários editais em PDF e sincroniza no Supabase:
```bash
/home/jrbc/Documentos/robo-licitacoes/.venv/bin/python /home/jrbc/Documentos/robo-licitacoes/hermes_cli.py --scan /caminho/para/pasta_processos
```

---

## 🔒 Diretrizes de Segurança
- **NUNCA** enviar lances reais em modo live sem confirmação explícita do usuário.
- Respeitar rigorosamente a trava de **Stop-Loss** (`preco_minimo`) para prevenir lances com prejuízo.
