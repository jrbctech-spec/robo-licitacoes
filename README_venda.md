# ROBÔ DE LICITAÇÕES

**Automação inteligente para Compras.gov.br — Pregão Eletrônico**

---

## O QUE É

Um robô de software que ajuda pequenas empresas e profissionais liberais a:

1. **Ler editais de licitação** (PDFs) e extrair automaticamente os itens, valores de referência e regras do certame
2. **Calcular stop-loss por item** — nunca envia lance abaixo do custo real + margem mínima
3. **Simular lances** antes de ir para o Compras.gov.br — verificar se a estratégia faz sentido
4. **Acompanhar pregões em tempo real** via Playwright (navegador automatizado)
5. **Registar tudo no Supabase** — histórico de itens, lances, logs de sessão

---

## PARA QUEM É

- Profissionais MEI/EPP que desejam participar de pregões eletrônicos
- Técnicos de TI, manutenção de computadores, instalação de redes, suporte técnico
- Empreendedores que querem automatizar a disputa de licitações sem votar no manual

---

## PRINCIPAIS FUNCIONALIDADES

### 📄 Parsing de Editais
- Lê PDFs de editais de pregão eletrônico
- Extrai itens, quantidades, valores de referência, unidade de medida
- Detecta regras do certame: modo de disputa, critério de julgamento, degrau de lance
- Se o RegEx local falhar, usa **Gemini AI** (fallback) para ler PDFs escaneados

### 🧠 Inteligência Regulatória
A cada edital carregado, o robô cruza com:
- Lei nº 14.133/2021 (Nova Lei de Licitações)
- IN SEGES/ME nº 73/2022 (Pregão e Concorrência Eletrônica)
- IN SEGES/ME nº 67/2021 (Dispensa Eletrônica)
- Decreto nº 10.024/2019

Identifica automaticamente:
- Riscos de desclassificação (valor acima do teto, inexequibilidade, prazo de proposta ajustada, declarações ME/EPP)
- Modo de disputa operacional (aberto, aberto-fechado, fechado-aberto, dispensa)
- Intervalo mínimo de degrau entre lances

### 🛡️ Motor de Decisão Determinístico (engine.py)
**Núcleo do robô.** Calcula lances com:
- **Stop-Loss rígido por item** — custo real + margem mínima configurada
- **Validação dupla** — no cálculo e na pré-envio (anti-falha)
- **Proteção anti-duplicidade** — trava atômica de lance pendente
- **Isolamento de itens** — cada item tem seu próprio estado
- **Sete estados por item** — AGUARDANDO, LIDERANDO, SUPERADO, CALCULANDO, VALIDANDO, ENVIANDO, PISO_ATINGIDO, ENCERRADO
- **Fail-safe** — na dúvida, não lança

**9 testes unitários, todos passando.**

### 🌐 Automação Playwright (browser_bot.py)
- Navega no Compras.gov.br (Comprasnet Web) em modo Simulação ou Real
- Lê estado da página a cada ciclo de polling
- Calcula e envia lances conforme decisão do motor
- Só envia em modo REAL após confirmação explícita do usuário

### 🗄️ Supabase
- Tabelas: licitacoes, itens_licitacao, historico_lances, logs_sessao, oportunidades_salvas, licencas
- AsyncLogQueue: registro de logs não-bloqueante durante sessões de lances
- Upsert inteligente para sincronização

### 🔑 Licença e HWID
- Identificador único de máquina (HWID) para controle de cópias
- Validação online no Supabase
- Cache offline criptografado (48h de tolerância a indisponibilidade)
- Modelo pronto para venda de licenças mensais

### 📊 Interface Gráfica (GUI)
- Painel de Editais & Custos — upload PDF, tabela de itens, stop-loss em tempo real
- Painel de Sessão & Lances — URL, controles de início/pausa/encerramento, lances ativos
- Painel de Console de Logs — log em tempo real com filtros por nível
- Painel de Oportunidades PNCP — busca de licitações abertas por UF e palavra-chave

### 🚨 Sistema de Alertas (sistema_alertas.py)
- Varredura automática do PNCP a cada N segundos
- Filtra por UF e palavras-chave configuradas
- Alerta no terminal e na GUI quando nova oportunidade relevante é detectada
- Compatível com modo standalone (script separado)

### 📁 Modo de Descoberta Completa (modo_descoberta_completa.py)
- Um comando: ler edital → extrair itens → pedir custos → calcular stop-loss → persistir no Supabase
- Ideal para uso rápido no terminal, sem abrir a GUI

---

## INSTALAÇÃO

### Pré-requisitos
- Python 3.11 ou 3.12
- Playwright com Chromium instalado
- Conta no Supabase (projeto criado com as tabelas do schema.sql)
- Chave API do Google Gemini (opcional — para parsing avançado)

### Passo a passo

```bash
# 1. Clone ou acesse o diretório
cd /caminho/para/robo-licitacoes

# 2. Crie ambiente virtual
python3 -m venv .venv
source .venv/bin/activate

# 3. Instale dependências
pip install -r requirements.txt

# 4. Instale Playwright e o Chromium
playwright install chromium

# 5. Copie o arquivo de configuração
cp .env.example .env

# 6. Edite o .env preenchendo:
#    - SUPABASE_URL e SUPABASE_KEY
#    - GEMINI_API_KEY (opcional)
#    - Modo: simulation (padrão) ou real

# 7. Execute o robô
python main.py                    # Interface gráfica (padrão)
python main.py --cli              # Menu interativo no terminal
python main.py --diagnostics      # Auto-teste de conexões
```

---

## USO RÁPIDO (terminal)

```bash
# Diagnóstico (testa todas as conexões)
python main.py --diagnostics

# Parser de edital PDF
python main.py --parse caminho/para/edital.pdf

# Busca no PNCP (quando disponível)
python main.py --pncp --uf TO --kw "manutencao"

# Varredura de pasta local com sync no Supabase
python main.py --scan /caminho/para/pasta_processos

# Teste do motor de decisão
python main.py --test-engine

# Simulação completa (teste de integração)
python teste_integracao_browser.py

# Modo de descoberta completa (edital → itens → custos → stop-loss → persistir)
python modo_descoberta_completa.py caminho/para/edital.pdf
```

---

## ARQUITETURA DO CÓDIGO

```
robo-licitacoes/
├── main.py                    # Ponto de entrada (GUI ou CLI)
├── app_gui.py                 # Interface gráfica (CustomTkinter)
├── engine.py                  # Motor de decisão determinístico
├── edital_parser.py           # Leitura e parsing de editais PDF
├── proposta_parser.py         # Leitura de propostas comerciais da empresa
├── browser_bot.py             # Automação Playwright do Compras.gov.br
├── pncp_client.py             # Cliente da API do PNCP (Portal Nacional)
├── supabase_client.py         # Conexão e operações com Supabase
├── config.py                  # Configurações com Pydantic Settings
├── diagnostics.py             # Sistema de diagnóstico automático
├── license_manager.py         # Gerenciamento de licenças e HWID
├── drive_scanner.py           # Scanner local de pastas de processos
├── sistema_alertas.py         # Sistema de alertas automáticos de novas oportunidades
├── modo_descoberta_completa.py  # Fluxo completo: edital → itens → custos → stop-loss → persistir
├── teste_integracao_browser.py  # Teste de integração motor + simulação de mercado
├── simulacao_completa.py         # Teste final simulando uma licitação completa
├── hermes_cli.py                  # Ponte CLI Hermes Agent ↔ Robô
├── build.py                  # Script de empacotamento PyInstaller
├── pyproject.toml             # Configuração de pacote Python
├── .env.example               # Template de configuração com comentários
├── schema.sql                 # Schema do Supabase (tabelas)
├── requirements.txt           # Dependências Python
└── skills/                    # Skill de integração Hermes Agent
```

---

## SEGURANÇA

- **Nunca envia lances reais sem confirmação explícita.** Por padrão, opera em modo SIMULATION.
- **Stop-Loss é inegociável.** O motor rejeita qualquer lance abaixo do preço mínimo configurado.
- **Validação dupla:** cálculo + pré-envio. Se algo falhar, não há lance.
- **Anti-duplicidade:** trava atômica evita envio acidental duplicado.
- **Licença com HWID:** uma chave por máquina, com cache offline tolerante.

---

## MODELO DE NEGÓCIO (venda de cópias)

O robô está preparado para distribuição como produto licenciado:

1. **HWID único por máquina** — impede compartilhamento de chave
2. **Validação online no Supabase** — um painel administrativo central
3. **Cache offline** — tolera quedas temporárias do Supabase
4. **build.py** — empacotamento via PyInstaller para distribuição como .exe
5. **pyproject.toml** — configuração de pacote Python para distribuição via pip

Fluxo de venda:
1. Comprador instala o robô
2. No primeiro lançamento, é pedido a chave de licença
3. Chave gerada pelo administrador e entregue ao comprador
4. HWID da máquina do comprador é vinculado automaticamente à chave
5. Mensalidade renovável com controle de expiração

---

## LIMITAÇÕES

- **PNCP indisponível momentaneamente** — o robô depende do portal nacional para busca automática de oportunidades; enquanto indisponível, use busca manual ou sistema de alertas com fallback.
- **Seletores do Compras.gov.br são heurísticos** — podem precisar de ajuste se o portal mudar sua interface. O ideal é validar com uma página real antes de ir para produção.
- **Gemini AI requer chave configurada** — sem chave, o parsing de PDFs escaneados pode falhar.
- **Playwright + Chromium** — o usuário final precisa ter o Chromium instalado: `playwright install chromium`.

---

## PRÓXIMOS DESENVOLVIMENTOS

- Teste de integração com página real do Compras.gov.br
- Adaptação automática de seletores (detecção do DOM)
- Painel administrativo web para gerenciar licenças de clientes
- Exportação de relatórios de simulação em PDF/CSV
- Integração com Telegram para alertas de oportunidades

---

## CONTATO

Jakson Ribeiro da Costa  
CNPJ: 65.488.003/0001-61  
Palmas — TO
