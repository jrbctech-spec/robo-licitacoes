"""
proposta_parser.py – Leitor de Propostas Comerciais de Preços (PDF).

Extrai itens de planilhas/propostas de preços enviadas pela empresa em formato PDF:
  - Número do Item (numero_item)
  - Descrição do Produto/Serviço (descricao)
  - Quantidade (quantidade)
  - Unidade de Medida (unidade_medida)
  - Valor Unitário Proposto / Custo Base (valor_unitario)
  - Valor Total Proposto (valor_total)
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pypdf import PdfReader

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class ItemProposta:
    """Representa um item extraído da proposta comercial da empresa."""
    numero_item: int
    descricao: str
    quantidade: float = 1.0
    unidade_medida: str = "UN"
    valor_unitario: float = 0.0  # Custo base unitário da empresa
    valor_total: float = 0.0     # Custo total do item (quantidade * valor_unitario)


@dataclass
class ResultadoProposta:
    """Resultado da leitura de uma proposta comercial."""
    caminho_pdf: str
    empresa_nome: str = ""
    cnpj_empresa: str = ""
    itens: list[ItemProposta] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)
    sucesso: bool = False


class PropostaParser:
    """Extrai custos e itens de arquivos PDF de propostas comerciais de preços."""

    ITEM_PATTERNS = [
        # Item 1 - Descrição ... Qtd ... R$ Unitario ... R$ Total
        re.compile(
            r"(?:item\s+)?(\d{1,3})\s*[-–\.:]?\s*(.{5,120}?)\s+([\d.,]+)\s*(un|kg|m[²³]?|cx|pç|serv[iç]o|l|pct|gl|par)?\s+R\$\s*([\d.,]+)",
            re.IGNORECASE,
        ),
        # Tabular: 1  Descrição  10  UN  R$ 150,00  R$ 1.500,00
        re.compile(
            r"^\s*(\d{1,3})\s+(.{8,100})\s+([\d.,]+)\s+(un|kg|m[²³]?|cx|pç|serv[iç]o|l|pct|gl|par)?\s*(?:R\$\s*)?([\d.,]+)",
            re.IGNORECASE | re.MULTILINE,
        ),
    ]

    VALOR_REAIS = re.compile(r"R\$\s*([\d.,]+)", re.IGNORECASE)

    def parsear(self, caminho_pdf: str | Path) -> ResultadoProposta:
        caminho = Path(caminho_pdf)
        resultado = ResultadoProposta(caminho_pdf=str(caminho))

        if not caminho.exists():
            resultado.erros.append(f"Arquivo de proposta não encontrado: {caminho}")
            return resultado

        try:
            texto = self._extrair_texto_pdf(caminho)
        except Exception as exc:
            resultado.erros.append(f"Falha ao ler PDF da proposta: {exc}")
            return resultado

        logger.info(f"Lendo proposta comercial PDF: {caminho.name} ({len(texto)} chars)")
        self._extrair_itens_regex(texto, resultado)

        # Se RegEx não extrair itens válidos, aciona Gemini AI Fallback
        if not resultado.itens:
            logger.info("RegEx não encontrou itens na proposta. Acionando Gemini AI Fallback...")
            self._extrair_itens_gemini(texto, resultado)

        resultado.sucesso = len(resultado.itens) > 0
        return resultado

    @staticmethod
    def _extrair_texto_pdf(caminho: Path) -> str:
        reader = PdfReader(str(caminho))
        paginas: list[str] = []
        for p in reader.pages:
            paginas.append(p.extract_text() or "")
        return "\n".join(paginas)

    def _extrair_itens_regex(self, texto: str, resultado: ResultadoProposta) -> None:
        itens_map: dict[int, ItemProposta] = {}

        for pattern in self.ITEM_PATTERNS:
            for match in pattern.finditer(texto):
                try:
                    num_item = int(match.group(1))
                    desc = match.group(2).strip()
                    qtd = self._str_para_float(match.group(3))
                    unidade = (match.group(4) or "UN").upper()
                    val_unit = self._str_para_float(match.group(5))

                    if num_item in itens_map or num_item > 999 or val_unit <= 0:
                        continue

                    val_tot = round(qtd * val_unit, 2)
                    item = ItemProposta(
                        numero_item=num_item,
                        descricao=re.sub(r"\s+", " ", desc)[:250],
                        quantidade=qtd if qtd > 0 else 1.0,
                        unidade_medida=unidade,
                        valor_unitario=val_unit,
                        valor_total=val_tot,
                    )
                    itens_map[num_item] = item

                except (ValueError, IndexError):
                    continue

        resultado.itens = sorted(itens_map.values(), key=lambda x: x.numero_item)

    def _extrair_itens_gemini(self, texto: str, resultado: ResultadoProposta) -> None:
        """Extrai os custos da proposta via Gemini AI (Structured Output JSON)."""
        api_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            prompt = (
                "Você é um assistente financeiro de licitações.\n"
                "Extraia todos os ITENS E CUSTOS UNITÁRIOS da Proposta Comercial de Preços abaixo em JSON.\n"
                "Para cada item extraia:\n"
                "- numero_item (inteiro)\n"
                "- descricao (string)\n"
                "- quantidade (float)\n"
                "- unidade_medida (string ex: UN, KG)\n"
                "- valor_unitario (float em R$ - Custo Unitário Proposto)\n"
                "- valor_total (float em R$)\n\n"
                "TEXTO DA PROPOSTA:\n"
                f"{texto[:12000]}"
            )

            modelos = [
                "gemini-3.5-flash",
                "gemini-flash-latest",
                "gemini-3.1-flash-lite",
                "gemini-3.5-flash-lite",
                "gemini-3-flash-preview",
                "gemini-flash-lite-latest",
                "gemini-3.6-flash",
            ]
            response = None
            for mod in modelos:
                try:
                    response = client.models.generate_content(
                        model=mod,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.1,
                        ),
                    )
                    if response and response.text:
                        break
                except Exception as exc_mod:
                    logger.warning(f"Tentativa com modelo {mod} na proposta falhou: {exc_mod}")
                    continue

            if response and response.text:
                data = json.loads(response.text)
                itens_json = data.get("itens", []) if isinstance(data, dict) else data

                for i in itens_json:
                    if isinstance(i, dict) and "numero_item" in i and i.get("valor_unitario") is not None:
                        val_u = float(i["valor_unitario"])
                        qtd = float(i.get("quantidade", 1.0))
                        item = ItemProposta(
                            numero_item=int(i["numero_item"]),
                            descricao=str(i.get("descricao", ""))[:250],
                            quantidade=qtd,
                            unidade_medida=str(i.get("unidade_medida", "UN")).upper(),
                            valor_unitario=val_u,
                            valor_total=float(i.get("valor_total", val_u * qtd)),
                        )
                        resultado.itens.append(item)

                resultado.itens.sort(key=lambda x: x.numero_item)

        except Exception as exc:
            logger.error(f"Erro ao extrair proposta via Gemini AI: {exc}")

    @staticmethod
    def _str_para_float(valor_str: str) -> float:
        valor_str = valor_str.strip().replace(" ", "")
        if "," in valor_str and "." in valor_str:
            valor_str = valor_str.replace(".", "").replace(",", ".")
        elif "," in valor_str:
            valor_str = valor_str.replace(",", ".")
        try:
            return float(valor_str)
        except ValueError:
            return 0.0
