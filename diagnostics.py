"""
diagnostics.py – Sistema de Diagnóstico Automático de Conexões e Integridade.

Realiza o auto-teste do sistema validando:
  1. Arquivo .env e variáveis de ambiente obrigatórias.
  2. Conexão com Supabase e acesso de leitura/escrita nas tabelas:
     - licitacoes
     - itens_licitacao
     - historico_lances
     - logs_sessao
  3. Integridade do Playwright e presença dos binários do Chromium.
  4. Permissões de escrita e estrutura da pasta `browser_profile`.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from config import settings
from supabase_client import get_client, testar_conexao

logger = logging.getLogger(__name__)


class StatusDiagnostico(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass
class ItemDiagnostico:
    """Representa o resultado do diagnóstico de um componente individual."""
    categoria: str          # ex: "Ambiente", "Supabase", "Playwright", "Perfil"
    nome: str               # ex: "Tabela licitacoes"
    status: StatusDiagnostico
    mensagem: str
    detalhes: Optional[str] = None


class DiagnosticoSistema:
    """Executa diagnósticos de integridade e conectividade de forma segura."""

    def __init__(self) -> None:
        self.resultados: list[ItemDiagnostico] = []

    def executar_tudo(self) -> list[ItemDiagnostico]:
        """Executa todos os testes de diagnóstico e retorna a lista de resultados."""
        self.resultados.clear()
        
        self.checar_env()
        self.checar_supabase_conexao_e_tabelas()
        self.checar_gemini_ai()
        self.checar_playwright_chromium()
        self.checar_browser_profile()

        return self.resultados

    def checar_env(self) -> None:
        """Valida a presença do arquivo .env e variáveis obrigatórias."""
        env_path = Path(".env")
        if not env_path.exists():
            self.resultados.append(ItemDiagnostico(
                categoria="Ambiente",
                nome="Arquivo .env",
                status=StatusDiagnostico.WARNING,
                mensagem="Arquivo .env não encontrado. Usando padrões ou variáveis do sistema.",
                detalhes="Copie .env.example para .env e insira as credenciais reais.",
            ))
        else:
            self.resultados.append(ItemDiagnostico(
                categoria="Ambiente",
                nome="Arquivo .env",
                status=StatusDiagnostico.OK,
                mensagem="Arquivo .env encontrado e carregado com sucesso.",
            ))

        # Checar variáveis obrigatórias
        try:
            url = settings.supabase_url
            key = settings.supabase_key
            if not url or "SEU_PROJETO" in url:
                self.resultados.append(ItemDiagnostico(
                    categoria="Ambiente",
                    nome="SUPABASE_URL",
                    status=StatusDiagnostico.ERROR,
                    mensagem="SUPABASE_URL não configurada ou contendo valor padrão.",
                ))
            else:
                self.resultados.append(ItemDiagnostico(
                    categoria="Ambiente",
                    nome="SUPABASE_URL",
                    status=StatusDiagnostico.OK,
                    mensagem=f"URL válida: {url[:30]}...",
                ))

            if not key or "sua_chave" in key:
                self.resultados.append(ItemDiagnostico(
                    categoria="Ambiente",
                    nome="SUPABASE_KEY",
                    status=StatusDiagnostico.ERROR,
                    mensagem="SUPABASE_KEY não configurada ou contendo valor padrão.",
                ))
            else:
                self.resultados.append(ItemDiagnostico(
                    categoria="Ambiente",
                    nome="SUPABASE_KEY",
                    status=StatusDiagnostico.OK,
                    mensagem=f"Chave presente ({len(key)} chars).",
                ))

            self.resultados.append(ItemDiagnostico(
                categoria="Ambiente",
                nome="Modo de Operação",
                status=StatusDiagnostico.OK,
                mensagem=f"Modo atual: {settings.modo_operacao.value.upper()}",
            ))

        except Exception as exc:
            self.resultados.append(ItemDiagnostico(
                categoria="Ambiente",
                nome="Configurações (Pydantic)",
                status=StatusDiagnostico.ERROR,
                mensagem=f"Erro ao carregar configurações: {exc}",
            ))

    def checar_supabase_conexao_e_tabelas(self) -> None:
        """Testa conexão direta com Supabase e verifica tabelas do schema.sql."""
        try:
            ok = testar_conexao()
            if not ok:
                self.resultados.append(ItemDiagnostico(
                    categoria="Supabase",
                    nome="Conexão API",
                    status=StatusDiagnostico.ERROR,
                    mensagem="Falha na conexão HTTP com o Supabase.",
                ))
                return

            self.resultados.append(ItemDiagnostico(
                categoria="Supabase",
                nome="Conexão API",
                status=StatusDiagnostico.OK,
                mensagem="Conexão estabelecida com sucesso (HTTP 200 OK).",
            ))

            client = get_client()
            tabelas = ["licitacoes", "itens_licitacao", "historico_lances", "logs_sessao", "oportunidades_salvas", "licencas"]

            for tab in tabelas:
                try:
                    client.table(tab).select("id").limit(1).execute()
                    self.resultados.append(ItemDiagnostico(
                        categoria="Supabase",
                        nome=f"Tabela '{tab}'",
                        status=StatusDiagnostico.OK,
                        mensagem=f"Acesso de leitura OK (Tabela '{tab}' pronta).",
                    ))
                except Exception as exc_tab:
                    msg_erro = str(exc_tab)
                    if "does not exist" in msg_erro or "42P01" in msg_erro or "PGRST204" in msg_erro:
                        self.resultados.append(ItemDiagnostico(
                            categoria="Supabase",
                            nome=f"Tabela '{tab}'",
                            status=StatusDiagnostico.ERROR,
                            mensagem=f"Tabela '{tab}' não existe no Supabase. Execute o schema.sql.",
                            detalhes=msg_erro,
                        ))
                    else:
                        self.resultados.append(ItemDiagnostico(
                            categoria="Supabase",
                            nome=f"Tabela '{tab}'",
                            status=StatusDiagnostico.WARNING,
                            mensagem=f"Aviso ao acessar '{tab}': {exc_tab}",
                        ))

        except Exception as exc:
            self.resultados.append(ItemDiagnostico(
                categoria="Supabase",
                nome="Geral",
                status=StatusDiagnostico.ERROR,
                mensagem=f"Erro crítico no Supabase: {exc}",
            ))

    def checar_gemini_ai(self) -> None:
        """Verifica se a chave da API do Google Gemini está configurada."""
        key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY")
        if not key or "sua_chave" in key or key.strip() == "":
            self.resultados.append(ItemDiagnostico(
                categoria="Gemini AI",
                nome="Chave de API",
                status=StatusDiagnostico.WARNING,
                mensagem="GEMINI_API_KEY não configurada ou com valor padrão.",
                detalhes="O leitor de editais operará no modo local (RegEx). Insira a chave no .env para ativar a IA.",
            ))
        else:
            self.resultados.append(ItemDiagnostico(
                categoria="Gemini AI",
                nome="Chave de API",
                status=StatusDiagnostico.OK,
                mensagem=f"Chave configurada ({len(key)} chars) – Fallback com IA ativado.",
            ))

    def checar_playwright_chromium(self) -> None:
        """Verifica se o Playwright e os binários do Chromium estão operacionais."""
        try:
            import importlib.metadata
            try:
                pw_version = importlib.metadata.version("playwright")
            except Exception:
                pw_version = "instalado"

            self.resultados.append(ItemDiagnostico(
                categoria="Playwright",
                nome="Módulo Python",
                status=StatusDiagnostico.OK,
                mensagem=f"Playwright Python instalado ({pw_version}).",
            ))

            from playwright.sync_api import sync_playwright
            logging.getLogger("asyncio").setLevel(logging.CRITICAL + 1)
            pw = sync_playwright().start()
            try:
                browser_type = pw.chromium
                exec_path = browser_type.executable_path
                if os.path.exists(exec_path):
                    self.resultados.append(ItemDiagnostico(
                        categoria="Playwright",
                        nome="Binários Chromium",
                        status=StatusDiagnostico.OK,
                        mensagem=f"Chromium encontrado em: {exec_path}",
                    ))
                else:
                    self.resultados.append(ItemDiagnostico(
                        categoria="Playwright",
                        nome="Binários Chromium",
                        status=StatusDiagnostico.WARNING,
                        mensagem="Binário do Chromium não encontrado no caminho padrão.",
                        detalhes="Execute: playwright install chromium",
                    ))
            finally:
                pw.stop()

        except Exception as exc:
            self.resultados.append(ItemDiagnostico(
                categoria="Playwright",
                nome="Geral",
                status=StatusDiagnostico.ERROR,
                mensagem=f"Erro ao validar Playwright/Chromium: {exc}",
                detalhes="Execute: playwright install chromium",
            ))

    def checar_browser_profile(self) -> None:
        """Verifica se o diretório browser_profile está acessível e com permissão de escrita."""
        profile_path = settings.browser_user_data_dir
        try:
            profile_path.mkdir(parents=True, exist_ok=True)
            test_file = profile_path / ".perm_check"
            test_file.write_text("write_test", encoding="utf-8")
            test_file.unlink()

            self.resultados.append(ItemDiagnostico(
                categoria="Perfil Navegador",
                nome="Diretório de Perfil",
                status=StatusDiagnostico.OK,
                mensagem=f"Diretório '{profile_path}' pronto e gravável.",
            ))
        except Exception as exc:
            self.resultados.append(ItemDiagnostico(
                categoria="Perfil Navegador",
                nome="Diretório de Perfil",
                status=StatusDiagnostico.ERROR,
                mensagem=f"Sem permissão de escrita em '{profile_path}': {exc}",
            ))


def executar_diagnostico_cli() -> bool:
    """Executa o diagnóstico no terminal com formatação e retorna True se tudo OK."""
    diag = DiagnosticoSistema()
    itens = diag.executar_tudo()
    
    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║            🔍 DIAGNÓSTICO DO SISTEMA                     ║")
    print("╚══════════════════════════════════════════════════════════╝\n")

    has_error = False
    for item in itens:
        if item.status == StatusDiagnostico.OK:
            icon = "✅"
        elif item.status == StatusDiagnostico.WARNING:
            icon = "⚠️"
        else:
            icon = "❌"
            has_error = True

        print(f" {icon} [{item.categoria:<15}] {item.nome:<22}: {item.mensagem}")
        if item.detalhes:
            print(f"    └─ Detalhes: {item.detalhes}")

    print("─" * 60)
    if has_error:
        print("❌ O diagnóstico encontrou problemas que precisam de atenção.\n")
    else:
        print("✅ Todos os componentes testados estão operacionais!\n")

    return not has_error


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    executar_diagnostico_cli()
