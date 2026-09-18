import sys
sys.path.insert(0, '.')

from pypdf import PdfReader
import re

def testar_pdf(caminho_pdf: str):
    print(f"\n{'='*60}")
    print(f"TESTANDO: {caminho_pdf}")
    print(f"{'='*60}")
    
    try:
        reader = PdfReader(caminho_pdf)
        print(f"Total de páginas: {len(reader.pages)}")
        
        for i, pagina in enumerate(reader.pages):
            try:
                texto = pagina.extract_text() or ""
                print(f"\n--- Página {i+1} (caracteres: {len(texto)}) ---")
                if len(texto) < 100:
                    print(texto)
                else:
                    # Mostra os primeiros 500 caracteres
                    print(texto[:500])
                    if len(texto) > 500:
                        print("...")
            except Exception as exc:
                print(f"Erro página {i+1}: {exc}")
        
        # Tenta extrair itens
        from edital_parser import EditalParser
        parser = EditalParser()
        
        # Para debug: ler o texto completo e aplicar extração
        texto_completo = "\n".join(
            (pagina.extract_text() or "") for pagina in reader.pages
        )
        
        print(f"\n{'='*60}")
        print(f"TEXTO COMPLETO: {len(texto_completo)} caracteres")
        print(f"{'='*60}")
        
        # Salvar para inspeção
        with open("/tmp/texto_edital_debug.txt", "w") as f:
            f.write(texto_completo)
        
        print(f"Texto salvo em /tmp/texto_edital_debug.txt")
        
    except Exception as exc:
        print(f"ERRO: {exc}")

# Teste com um arquivo PDF que esteja disponível
import glob
pdfs = glob.glob("*.pdf") + glob.glob("downloads/*.pdf") + glob.glob("**/*.pdf", recursive=True)
print(f"PDFs encontrados: {pdfs}")

for pdf in pdfs[:3]:
    testar_pdf(pdf)
