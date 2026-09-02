# ⚡ Robô de Licitações — Automação & Inteligência em Pregões Eletrônicos (JRBC)

<div align="center">
  <img src="assets/logo.png" alt="JRBC Logo" width="220" />
  <p><b>Sistema Modular de Automação, Inteligência de Mercado e Assistência de Lances em Licitações Públicas (Compras.gov.br)</b></p>
</div>

---

## 🚀 Sobre o Projeto

O **Robô de Licitações JRBC** é uma solução completa de engenharia de software voltada para automação, análise preditiva e participação estratégica em pregões eletrônicos no portal **Compras.gov.br** (Comprasnet Web).

Desenvolvido em Python com interface moderna **CustomTkinter (Dark Mode)**, o sistema integra inteligência artificial via **Google Gemini 2.5 Flash** para leitura automatizada de editais em PDF, consulta direta à **API do PNCP (Portal Nacional de Contratações Públicas)**, sincronização assíncrona com banco de dados **Supabase (PostgreSQL)** e trava rígida de **Stop-Loss** determinística com latência zero.

---

## ✨ Funcionalidades Principais

### 🔍 1. Buscador de Oportunidades PNCP
- **Integração nativa com a API do PNCP** (`/v1/contratacoes/publicacao` e `/v1/contratacoes/proposta`).
- **Filtros avançados**: por Palavra-Chave no Objeto, Estado (UF), Modalidade (Pregão, Dispensa, Concorrência) e Intervalo de Datas.
- **Importação Direta em 1 Clique**: Baixa anexos de editais para `./downloads_editais/` e importa os itens direto para o robô.
- **Favoritamento no Supabase**: Salve oportunidades relevantes no banco de dados para acompanhamento.

### 📄 2. Leitor Inteligente de Editais (AI Parser)
- Isolamento automático de anexos técnicos e **Termo de Referência**.
- **Filtro anticláusula jurídica**: Ignora seções jurídicas ("DO OBJETO", "DA HABILITAÇÃO", etc.).
- Extração estruturada de número do item, descrição, quantidade, unidade e valor de referência do órgão.
- Fallback para a API do **Google Gemini 2.5 Flash** (Structured Outputs JSON) para editais escaneados ou tabelas complexas.

### 🛡️ 3. Motor de Decisão Determinístico & Trava Stop-Loss
- **Dupla Verificação Rígida**: Nenhum lance abaixo do piso (`Custo Unitário × (1 + Margem Mínima%)`) é gerado ou enviado.
- **Cálculo de Sugestão de Abertura**: Desconto automático estratégico de 1,5% abaixo da referência do órgão.
- **Modo Simulação (Dry-Run)** por padrão: Permite testar o comportamento antes de operar em modo real.
- **Latência Zero**: Regras pré-carregadas em memória RAM sem chamadas de I/O de rede durante a disputa de lances.

### 📑 4. Leitor de Propostas Comerciais (PDF)
- Leitura automatizada das propostas de preços emitidas pela própria empresa.
- Associa custos base diretamente aos itens da licitação na interface e no banco de dados.

### 📂 5. Leitor de Pastas de Processos (Google Drive / Local)
- Varredura em lote de diretórios organizados por UASG/Órgão.
- Leitura automatizada dos PDFs de editais e planilhas locais com sincronização em lote no Supabase.

### 🔍 6. Diagnóstico Automático de Conexões
- Módulo de auto-teste completo (`diagnostics.py`) que valida:
  - Arquivo `.env` e variáveis de ambiente;
  - Conexão e permissões em todas as 5 tabelas do Supabase;
  - Status do motor de navegação Chromium do Playwright;
  - Perfil persistente do navegador.

---

## 🏗️ Estrutura do Repositório

```text
robo-licitacoes/
├── assets/
│   ├── logo.png             # Logomarca oficial JRBC
│   └── icon.png             # Ícone do sistema (256x256)
├── downloads/               # Downloads temporários de editais
├── downloads_editais/       # PDFs de editais baixados via PNCP
├── .env.example             # Modelo de variáveis de ambiente (sem segredos)
├── .gitignore               # Arquivo de segurança para o Git
├── requirements.txt         # Lista de dependências Python
├── schema.sql               # DDL completo das tabelas PostgreSQL (Supabase)
├── Robo-Licitacoes.desktop  # Atalho e lançador Linux para Área de Trabalho
├── run.sh                   # Script executável de inicialização rápida
│
├── config.py                # Configurações com Pydantic Settings
├── supabase_client.py       # Cliente Supabase & Fila AsyncLogQueue
├── edital_parser.py         # Leitor e extrator de PDFs de editais (RegEx + Gemini AI)
├── proposta_parser.py       # Leitor de propostas comerciais de custos
├── pncp_client.py           # Cliente da API pública do PNCP
├── drive_loader.py          # Leitor da API do Google Drive
├── drive_scanner.py         # Escaneador em lote de pastas de processos
├── engine.py                # Motor determinístico de lances e Stop-Loss
├── browser_bot.py           # Automação do Compras.gov.br via Playwright
├── diagnostics.py           # Sistema de diagnóstico automático de conexões
├── app_gui.py               # Interface Gráfica CustomTkinter (Dark Mode)
└── main.py                  # Ponto de entrada CLI e inicialização
```

---

## ⚡ Guia de Instalação e Execução

### 1. Clonar o Repositório
```bash
git clone https://github.com/seu-usuario/robo-licitacoes.git
cd robo-licitacoes
```

### 2. Criar Ambiente Virtual e Instalar Dependências
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Instalar os Binários do Playwright Chromium
```bash
playwright install chromium
```

### 4. Configurar as Variáveis de Ambiente
Copie o modelo `.env.example` para `.env` e insira suas credenciais:
```bash
cp .env.example .env
```

Conteúdo de exemplo do `.env`:
```env
SUPABASE_URL=https://sua-instancia.supabase.co
SUPABASE_KEY=seu_token_aqui
GEMINI_API_KEY=sua_chave_gemini_api_aqui
GOV_CPF=12345678900
GOV_PASSWORD=sua_senha_gov_br_aqui
```

### 5. Configurar o Banco de Dados (Supabase)
1. Acesse o **SQL Editor** do seu painel no Supabase.
2. Execute o script contido em `schema.sql`.
3. Serão criadas as tabelas: `licitacoes`, `itens_licitacao`, `historico_lances`, `logs_sessao` e `oportunidades_salvas`.

---

## 🖥️ Como Executar

- **Via Script Lançador (Linux / Bash):**
  ```bash
  ./run.sh
  ```

- **Via Terminal Python:**
  ```bash
  source .venv/bin/activate
  python main.py
  ```

- **Para rodar o Auto-Diagnóstico:**
  ```bash
  ./run.sh --diagnostics
  ```

---

## 🛡️ Trava Rígida de Segurança (Stop-Loss)

A proteção patrimonial da sua empresa é garantida através de uma **dupla camada de validação**:

```text
Preço Mínimo (Stop-Loss) = Custo Unitário Real × (1 + Margem Mínima % / 100)
```

1. **Camada 1 (`engine.py`)**: Valida o valor calculado contra o piso antes de emitir a decisão.
2. **Camada 2 (`browser_bot.py`)**: Validação final de segurança pré-clique. Se um lance por qualquer motivo violar o stop-loss, o envio é abortado instantaneamente.

---

## 📜 Licença e Autor

Desenvolvido por **JRBC Automation**.
Uso destinado à automação ética e transparente conforme a **Lei nº 14.133/2021**.
