# 🤖 Robô de Licitações – JRBC TECH

Sistema modular em Python para monitorar, filtrar e analisar contratações públicas abertas consumindo a API pública do **PNCP (Portal Nacional de Contratações Públicas)**.

---

## 🚀 Recursos

- **Consulta Integrada ao PNCP**: Conecta-se à API do PNCP (`https://pncp.gov.br/pncp-api/v1/`) para capturar contratações públicas abertas.
- **Filtragem por UF e Modalidades**: Monitoramento direcionado para os estados de interesse (padrão `TO` e `GO`) e modalidades como Dispensa Eletrônica e Pregão Eletrônico.
- **Filtro de Inteligência em TI**: Aplica algoritmo de correspondência para palavras-chave estratégicas de TI da JRBC TECH (`suporte`, `manutencao`, `computador`, `rede`, `cabeamento`, `servidor`, `licenciamento`, `software`, `saas`, `ti`, `telecom`).
- **Resiliência e Tratamento de Erros**: Tratamento robusto contra erros de conexão e controle de *rate limit* (HTTP 429) com suporte a retentativas com backoff exponencial.
- **Exibição Formatada no Terminal**: Apresenta relatórios detalhados com Órgão/UASG/UF, Número do Certame, Link Oficial do PNCP, Objeto Resumido e Valor Total Estimado.

---

## 🛠️ Requisitos Prévia

- Python 3.10 ou superior.
- Acesso à internet para requisições na API do PNCP.

---

## 📦 Instalação

1. **Clonar ou baixar o repositório**:
   ```bash
   git clone <URL_DO_REPOSITORIO>
   cd robo-licitacoes
   ```

2. **Criar e ativar o ambiente virtual (recomendado)**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # no Linux/macOS
   # ou no Windows: .venv\Scripts\activate
   ```

3. **Instalar as dependências**:
   ```bash
   pip install -r requirements.txt
   ```

---

## ⚙️ Configuração

O robô pode ser configurado por variáveis de ambiente através de um arquivo `.env` na raiz do projeto.

1. **Copie o arquivo de exemplo**:
   ```bash
   cp .env.example .env
   ```

2. **Ajuste os parâmetros desejados em `.env`**:
   ```env
   # Estados de interesse (separados por vírgula)
   ESTADOS_INTERESSE=TO,GO

   # Palavras-chave de TI da JRBC TECH
   PALAVRAS_CHAVE_TI=suporte,manutencao,computador,rede,cabeamento,servidor,licenciamento,software,saas,ti,telecom

   # URL base da API pública do PNCP
   PNCP_BASE_URL=https://pncp.gov.br/pncp-api/v1/

   # Dias retroativos para busca de publicações
   DIAS_PUBLICACAO_RECUO=7

   # Timeout de rede em segundos
   HTTP_TIMEOUT=15.0

   # Máximo de retentativas
   MAX_RETRIES=3
   ```

---

## 💻 Execução

Para iniciar o monitoramento e busca de licitações:

```bash
python main.py
```

### Exemplo de Saída no Terminal:

```text
================================================================================
 🤖 ROBÔ DE LICITAÇÕES – JRBC TECH
 Monitoramento de Contratações Públicas Abertas (PNCP)
================================================================================
📌 Estados de Interesse: TO, GO
🎯 Palavras-chave TI (11): suporte, manutencao, computador, rede, cabeamento, servidor, licenciamento, software, saas, ti, telecom
================================================================================

🔍 Consultando API do PNCP para a UF: TO...
   ↳ 12 contratação(ões) retornada(s) do PNCP para TO.

────────────────────────────────────────────────────────────────────────────────
🏛️  Órgão / UASG / UF: PREFEITURA MUNICIPAL DE PALMAS (UASG: 980001 / UF: TO)
📄 Certame: 00012/2026 | 🔗 Link: https://pncp.gov.br/app/editais/12345678000199/2026/000012
📝 Objeto: Contratação de serviço de suporte técnico e manutenção de rede estruturada...
🏷️  Keywords TI encontradas: [suporte, manutencao, rede]
💰 Valor Total Estimado: R$ 150.000,00

================================================================================
📊 RESUMO DO MONITORAMENTO:
   - Total de contratações analisadas: 12
   - Oportunidades de TI filtradas para JRBC TECH: 1
================================================================================
```

---

## 🧪 Testes Automatizados

Para rodar os testes da suíte automatizada:

```bash
pytest
```

---

## 📄 Licença

Desenvolvido para **JRBC TECH**.
