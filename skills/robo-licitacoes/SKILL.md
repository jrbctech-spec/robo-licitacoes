---
name: robo-licitacoes
description: Skill de integração do Robô de Licitações (Compras.gov.br, PNCP, Parsing de Editais PDF e Supabase) para o Hermes Agent.
---

# 🤖 Skill: Robô de Licitações (Compras.gov.br & PNCP)

Esta skill conecta o Hermes Agent diretamente ao sistema **Robô de Licitações** em `/home/jrbc/Documentos/robo-licitacoes`.

O Hermes pode executar a ponte de comando rápida `hermes_cli.py` para realizar análises, pesquisas e testes, além do `main.py` para o menu completo interativo e a GUI gráfica.

---

## 🚀 Como Usar

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
**Nota:** PNCP pode estar indisponível (timeout modalidades 4, 6, 8). Alternativa: web_search + web_extract + Playwright no Compras.gov.br.

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

### 6. GUI Gráfica (app_gui.py)
Interface gráfica completa com CustomTkinter, com abas de editais, licitações, histórico, zonas & estratégia:
```bash
/home/jrbc/Documentos/robo-licitacoes/.venv/bin/python /home/jrbc/Documentos/robo-licitacoes/app_gui.py
```

### 7. Menu CLI Interativo (main.py)
Menu interativo no terminal com opções completas:
```bash
/home/jrbc/Documentos/robo-licitacoes/.venv/bin/python /home/jrbc/Documentos/robo-licitacoes/main.py
```
**Opções do menu:**
- [1] 📄 Carregar Edital e Declarar Custos
- [2] 🔌 Testar Conexão com Supabase
- [3] 🚀 Iniciar Monitoramento de Sessão
- [4] 📊 Análise de Zonas e Estratégia (MODO ANÁLISE)
- [5] 🔑 Gerenciar Licença & HWID
- [0] 🚪 Sair

### 8. Análise de Zonas e Estratégia (MODO ANÁLISE)
Analisa as zonas de lance para itens configurados e simula o comportamento do MotorComZonas em modo seco:
```bash
/home/jrbc/Documentos/robo-licitacoes/.venv/bin/python /home/jrbc/Documentos/robo-licitacoes/main.py
# Escolha a opção 4 no menu
```

### 9. Sistema de Alertas Automáticos
Polling automático de oportunidades no PNCP e web:
```bash
/home/jrbc/Documentos/robo-licitacoes/.venv/bin/python /home/jrbc/Documentos/robo-licitacoes/sistema_alertas.py --once
```

### 10. Modo de Descoberta Completa
Fluxo único: edital → itens → custos → stop-loss → persistir:
```bash
/home/jrbc/Documentos/robo-licitacoes/.venv/bin/python /home/jrbc/Documentos/robo-licitacoes/modo_descoberta_completa.py
```

### 11. Build (pyarmor + nuitka)
Compilação e empacotamento do sistema para distribuição:
```bash
/home/jrbc/Documentos/robo-licitacoes/.venv/bin/python /home/jrbc/Documentos/robo-licitacoes/build.py
```

---

## 🔒 Diretrizes de Segurança

- **NUNCA** enviar lances reais em modo live sem confirmação explícita do usuário.
- Respeitar rigorosamente a trava de **Stop-Loss** (`preco_minimo`) para prevenir lances com prejuízo.
- Modo SIMULATION ativo por padrão — confirmação explícita necessária para modo real.

---

## 🧠 Motor de Decisão

O motor é **100% determinístico** e auditável, com:
- Trava rígida de preço mínimo / stop-loss
- Latência zero (regras em memória RAM)
- Precisão financeira com Decimal e ROUND_HALF_UP
- Separação estrita: meu último lance vs. menor lance do processo vs. preço mínimo
- Proteção anti-duplicidade atômica
- Fail-safe: na dúvida, o robô NUNCA lança

### Régua de Zonas (estrategia_zonas.py)
Camada de estratégia que envolve o MotorDecisao e decide a postura de lance com base na zona:
- **Zona Eliminatória** (>=150% do piso): estratégia agressiva
- **Zona Competitiva** (115-150% do piso): estratégia padrão
- **Zona Preservação** (100-115% do piso): estratégia conservadora
- **Zona Desistida** (<100% do piso): bloqueio

### MotorComZonas
Wrapper que integra a régua de zonas ao MotorDecisao, adicionando análise de zona por item, emissão de eventos GUI, e aplicação da estratégia de zona na decisão de lance.

---

## 🧩 Componentes

| Componente | Arquivo | Função |
|-----------|---------|--------|
| Motor de Decisão | engine.py | Lógica determinística de lances |
| Régua de Zonas | estrategia_zonas.py | Estratégias por zona e classe MotorComZonas |
| Painel de Zonas GUI | gui_zonas.py | Componente visual PainelZonas para CustomTkinter |
| Parser de Editais | edital_parser.py | Extração de editais PDF com RegEx + Gemini |
| Parser de Propostas | proposta_parser.py | Parsing de propostas comerciais |
| Bot de Navegação | browser_bot.py | Playwright no Compras.gov.br |
| CLI Principal | hermes_cli.py | Comandos de diagnóstico, parse, pncp, scan |
| GUI | app_gui.py | Interface gráfica completa |
| Menu CLI | main.py | Menu interativo com análise de zonas |
| Supabase | supabase_client.py | Banco de dados e sincronização |
| Sistema de Alertas | sistema_alertas.py | Polling automático de oportunidades |
| Modo Descoberta | modo_descoberta_completa.py | Fluxo único edital→itens→custos→stop-loss |
| License Manager | license_manager.py | Gestão de licenças por HWID |
| Diagnóstico | diagnostics.py | Verificação de integridade do sistema |
| Drive Scanner | drive_scanner.py | Varredura de unidades de armazenamento |
| Config | config.py | Configurações com Pydantic |

---

## 📦 Dependências

- Python 3.12 (venv em ./.venv)
- customtkinter, tkinterdnd2, pydantic-settings
- playwright (Chromium em cache)
- supabase
- google-generativeai (Gemini)

---

## 🔑 Configuração

Copie `.env.example` para `.env` e preencha as credenciais:
```bash
cp /home/jrbc/Documentos/robo-licitacoes/.env.example /home/jrbc/Documentos/robo-licitacoes/.env
```

Variáveis necessárias:
- SUPABASE_URL
- SUPABASE_KEY
- GEMINI_API_KEY
- MODO_OPERACAO (SIMULATION ou REAL)

---

## 📍 Estados de Interesse

TO + vizinhos (PI, MA, GO, MT, RO) — foco em prestação de serviços (ou serviços + produtos/peças inclusos); excluir aquisição pura de equipamentos sem serviço associado.

---

## 📊 Status do Projeto

- **Diagnóstico:** 100% verde (Supabase, Playwright/Chromium, Gemini, modo SIMULATION)
- **Motor:** Determinístico, com stop-loss rígido e trava anti-duplicidade
- **Régua de Zonas:** Integrada ao motor via MotorComZonas
- **GUI:** Com aba de Zonas & Estratégia (PainelZonas)
- **PNCP:** Indisponível (timeout modalidades 4, 6, 8) — estratégia alternativa: web_search + web_extract + Playwright no Compras.gov.br
- **SICAF:** Não confirmado sem certificado digital
- **Edital TO 571069:** ENCERRADO em 23.02.2023 — não é ativo
- **PI:** 29 licitações de TI/suporte abertas (dados de licitacerta.com.br em 08/09/2026)
