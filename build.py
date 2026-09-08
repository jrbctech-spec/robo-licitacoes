"""
build.py – Script de empacotamento para gerar o executável (.exe) com PyInstaller.

Uso:
    python build.py              # Build padrão
    python build.py --onefile    # Executável único (mais lento no início)
    python build.py --clean      # Limpa builds anteriores antes de compilar

O executável gerado estará em: dist/robo_licitacoes/
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Configurações do build
# ─────────────────────────────────────────────────────────────────────────────

NOME_EXE = "robo_licitacoes"
SCRIPT_ENTRADA = "main.py"
ICONE = "assets/icon.ico" if Path("assets/icon.ico").exists() else "icon.ico"

# Arquivos de dados a incluir no executável
# Formato: ("origem", "destino_dentro_do_exe")
DADOS = [
    (".env.example", "."),
    ("schema.sql", "."),
    ("migration.sql", "."),
    ("assets", "assets"),
    ("installer.iss", "."),
]

# Módulos ocultos que o PyInstaller pode não detectar automaticamente
HIDDEN_IMPORTS = [
    "supabase",
    "gotrue",
    "postgrest",
    "realtime",
    "storage3",
    "httpx",
    "pydantic",
    "pydantic_settings",
    "pypdf",
    "playwright",
    "tenacity",
    "colorama",
    "rich",
    "dotenv",
    "customtkinter",
    "PIL",
    "google.genai",
    "license_manager",
    "diagnostics",
    "edital_parser",
    "proposta_parser",
    "pncp_client",
    "drive_scanner",
    "browser_bot",
    "engine",
]

# Coletar todos os submódulos do playwright e customtkinter
COLLECT_ALL = [
    "playwright",
    "customtkinter",
]


# ─────────────────────────────────────────────────────────────────────────────
# Funções utilitárias
# ─────────────────────────────────────────────────────────────────────────────

def verificar_pyinstaller() -> None:
    """Verifica se o PyInstaller está instalado."""
    try:
        import PyInstaller  # noqa: F401
        print("✅ PyInstaller encontrado.")
    except ImportError:
        print("❌ PyInstaller não instalado. Execute: pip install pyinstaller")
        sys.exit(1)


def limpar_builds_anteriores() -> None:
    """Remove diretórios build/ e dist/ de compilações anteriores."""
    for diretorio in ["build", "dist"]:
        caminho = Path(diretorio)
        if caminho.exists():
            shutil.rmtree(caminho)
            print(f"🧹 Diretório '{diretorio}' removido.")

    # Remove arquivos .spec gerados anteriormente
    for spec in Path(".").glob("*.spec"):
        spec.unlink()
        print(f"🧹 Arquivo '{spec}' removido.")


def verificar_playwright_chromium() -> None:
    """
    Verifica e instala o Chromium para o Playwright.

    NOTA IMPORTANTE sobre executáveis:
    O binário do Chromium NÃO é incluído dentro do .exe pelo PyInstaller.
    O usuário final deve ter o Playwright instalado e executar:
        playwright install chromium
    Ou o executável pode ser distribuído com o binário do navegador separadamente.
    """
    print("\n⚠️  AVISO SOBRE O PLAYWRIGHT:")
    print("   O binário do Chromium não é incluído no .exe automaticamente.")
    print("   O usuário final deve executar após instalar o .exe:")
    print("   > pip install playwright")
    print("   > playwright install chromium")
    print("   Ou distribua o binário do navegador separadamente.\n")


def montar_argumentos_pyinstaller(onefile: bool) -> list[str]:
    """Monta a lista de argumentos para o comando PyInstaller."""
    args = [
        sys.executable, "-m", "PyInstaller",
        "--name", NOME_EXE,
        "--noconfirm",              # Sobrescreve sem perguntar
        "--clean",                  # Limpa cache do PyInstaller antes de compilar
    ]

    # Modo de saída
    if onefile:
        args.append("--onefile")
        print("📦 Modo: Executável único (--onefile)")
    else:
        args.append("--onedir")
        print("📦 Modo: Diretório único (--onedir) [padrão recomendado]")

    # Interface: console (necessário para CLI interativa)
    args.append("--console")

    # Ícone (opcional)
    icone_path = Path(ICONE)
    if icone_path.exists():
        args.extend(["--icon", str(icone_path)])
        print(f"🎨 Ícone: {ICONE}")

    # Arquivos de dados adicionais
    sep = ";" if platform.system() == "Windows" else ":"
    for origem, destino in DADOS:
        origem_path = Path(origem)
        if origem_path.exists():
            args.extend(["--add-data", f"{origem}{sep}{destino}"])
            print(f"📁 Dados incluídos: {origem} → {destino}")
        else:
            print(f"⚠️  Arquivo de dados não encontrado (ignorado): {origem}")

    # Módulos ocultos
    for modulo in HIDDEN_IMPORTS:
        args.extend(["--hidden-import", modulo])

    # Coleta completa de módulos com submódulos (playwright)
    for pacote in COLLECT_ALL:
        args.extend(["--collect-all", pacote])

    # Exclui módulos desnecessários para reduzir tamanho
    exclusoes = [
        "matplotlib", "numpy", "pandas", "scipy",
        "PyQt5", "wx",
    ]
    for exc in exclusoes:
        args.extend(["--exclude-module", exc])

    # Ponto de entrada
    args.append(SCRIPT_ENTRADA)

    return args


def executar_build(args: list[str]) -> bool:
    """
    Executa o PyInstaller com os argumentos fornecidos.

    Returns:
        True se o build foi bem-sucedido.
    """
    print("\n🔨 Iniciando compilação com PyInstaller...")
    print("   Comando:", " ".join(args[:6]), "... [args omitidos]\n")

    resultado = subprocess.run(args, cwd=str(Path.cwd()))

    if resultado.returncode == 0:
        return True
    else:
        print(f"\n❌ Build falhou com código de saída: {resultado.returncode}")
        return False


def exibir_resultado_build(onefile: bool) -> None:
    """Exibe informações sobre o executável gerado."""
    if onefile:
        exe_nome = f"{NOME_EXE}.exe" if platform.system() == "Windows" else NOME_EXE
        exe_path = Path("dist") / exe_nome
    else:
        exe_path = Path("dist") / NOME_EXE

    if exe_path.exists():
        tamanho = (
            exe_path.stat().st_size / (1024 * 1024)
            if exe_path.is_file()
            else sum(f.stat().st_size for f in exe_path.rglob("*") if f.is_file()) / (1024 * 1024)
        )
        print(f"\n✅ BUILD CONCLUÍDO COM SUCESSO!")
        print(f"   📍 Localização: {exe_path.resolve()}")
        print(f"   💾 Tamanho: {tamanho:.1f} MB")
        print("\n📋 PRÓXIMOS PASSOS PARA O USUÁRIO FINAL:")
        print("   1. Copie .env.example para .env e preencha as variáveis.")
        print("   2. Execute: playwright install chromium")
        print("   3. Execute o programa.")
    else:
        print(f"\n⚠️  Executável não encontrado em: {exe_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build script – Robô de Licitações"
    )
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="Gera um único arquivo executável (mais lento na inicialização).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove builds anteriores antes de compilar.",
    )
    return parser.parse_args()


def compilar_inno_setup() -> None:
    """Verifica se o compilador do Inno Setup está presente e gera o instalador .exe."""
    iss_file = Path("installer.iss")
    if not iss_file.exists():
        return

    # Procura pelo compilador ISCC no sistema ou Windows padrão
    iscc_bin = shutil.which("iscc") or shutil.which("ISCC")
    if not iscc_bin and platform.system() == "Windows":
        candidatos = [
            Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
            Path("C:/Program Files/Inno Setup 6/ISCC.exe"),
            Path("C:/Program Files (x86)/Inno Setup 5/ISCC.exe"),
            Path("C:/Program Files/Inno Setup 5/ISCC.exe"),
        ]
        for c in candidatos:
            if c.exists():
                iscc_bin = str(c)
                break

    if iscc_bin:
        print("\n⚙️  Compilando instalador único com Inno Setup...")
        res = subprocess.run([iscc_bin, str(iss_file)])
        if res.returncode == 0:
            out_exe = Path("dist_installer/Instalador_Robo_Licitacoes.exe")
            print(f"\n🎉 INSTALADOR CRIADO COM SUCESSO!")
            print(f"   📍 Arquivo: {out_exe.resolve() if out_exe.exists() else 'dist_installer/'}")
        else:
            print("⚠️ Falha na compilação do instalador Inno Setup.")
    else:
        print("\n💡 DICA PARA GERAR O INSTALADOR .EXE ÚNICO:")
        print("   1. Abra o arquivo 'installer.iss' no software Inno Setup Compiler.")
        print("   2. Pressione Ctrl+F9 (Compile) para gerar 'Instalador_Robo_Licitacoes.exe'.")


def main() -> None:
    print("=" * 60)
    print(f"  🏗️  Build Script – {NOME_EXE}")
    print(f"  Sistema: {platform.system()} {platform.architecture()[0]}")
    print(f"  Python: {sys.version.split()[0]}")
    print("=" * 60 + "\n")

    args = parse_args()

    verificar_pyinstaller()

    if args.clean:
        limpar_builds_anteriores()

    verificar_playwright_chromium()

    pyinstaller_args = montar_argumentos_pyinstaller(onefile=args.onefile)
    sucesso = executar_build(pyinstaller_args)

    if sucesso:
        exibir_resultado_build(onefile=args.onefile)
        if not args.onefile:
            compilar_inno_setup()
        sys.exit(0)
    else:
        print("\n❌ Build falhou. Verifique os logs acima.")
        sys.exit(1)


if __name__ == "__main__":
    main()
