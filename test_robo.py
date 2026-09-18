"""
test_robo.py – Testes unitários para o Robô de Licitações JRBC TECH.
"""

import unittest
from unittest.mock import MagicMock, patch
import requests

from config import Config
from pncp_client import PNCPClient, PNCPError
from main import (
    extrair_texto_objeto,
    extrair_orgao_uasg_uf,
    extrair_certame_e_link,
    extrair_valor_total,
    objeto_contem_palavras_chave,
    formatar_objeto_resumido,
)


class TestConfig(unittest.TestCase):

    def test_config_defaults(self):
        cfg = Config()
        self.assertEqual(cfg.estados_interesse, ["TO", "GO"])
        self.assertIn("Dispensa Eletrônica (Art. 75, II)", cfg.modalidades)
        self.assertIn("Pregão Eletrônico", cfg.modalidades)
        self.assertIn("suporte", cfg.palavras_chave_ti)
        self.assertIn("saas", cfg.palavras_chave_ti)

    def test_config_parsing_string(self):
        cfg = Config(
            estados_interesse="TO, GO, SP",
            palavras_chave_ti="ti, suporte, rede",
        )
        self.assertEqual(cfg.estados_interesse, ["TO", "GO", "SP"])
        self.assertEqual(cfg.palavras_chave_ti, ["ti", "suporte", "rede"])


class TestMainHelpers(unittest.TestCase):

    def test_extrair_texto_objeto(self):
        item = {"objetoCompra": " Fornecimento de Licenciamento de Software SaaS "}
        self.assertEqual(extrair_texto_objeto(item), "Fornecimento de Licenciamento de Software SaaS")

        item_alt = {"descricao": "Manutenção de Servidores"}
        self.assertEqual(extrair_texto_objeto(item_alt), "Manutenção de Servidores")

    def test_extrair_orgao_uasg_uf(self):
        item = {
            "orgaoEntidade": {"razaoSocial": "Secretaria de Saúde"},
            "unidadeOrgao": {"codigoUnidade": "123456", "ufSigla": "TO"},
        }
        res = extrair_orgao_uasg_uf(item, uf_fallback="TO")
        self.assertEqual(res, "Secretaria de Saúde (UASG: 123456 / UF: TO)")

    def test_extrair_certame_e_link(self):
        item = {
            "numeroCompra": "15",
            "anoCompra": "2026",
            "orgaoEntidade": {"cnpj": "12.345.678/0001-99"},
        }
        num, link = extrair_certame_e_link(item)
        self.assertEqual(num, "15/2026")
        self.assertEqual(link, "https://pncp.gov.br/app/editais/12345678000199/2026/000015")

    def test_extrair_valor_total(self):
        item = {"valorTotalEstimado": 150000.5}
        self.assertEqual(extrair_valor_total(item), "R$ 150.000,50")

    def test_objeto_contem_palavras_chave(self):
        text = "Contratação de serviços de SUPORTE técnico e Manutenção de rede de computadores."
        keywords = ["suporte", "manutencao", "telecom", "servidor"]
        found = objeto_contem_palavras_chave(text, keywords)
        self.assertEqual(found, ["suporte", "manutencao"])

    def test_formatar_objeto_resumido(self):
        text = "  Texto curto   com   espaços  "
        self.assertEqual(formatar_objeto_resumido(text), "Texto curto com espaços")


class TestPNCPClient(unittest.TestCase):

    @patch("requests.Session.get")
    def test_buscar_contratacoes_sucesso(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"objetoCompra": "Aquisição de Servidores", "anoCompra": "2026"}
            ],
            "totalRegistros": 1,
        }
        mock_get.return_value = mock_resp

        client = PNCPClient(max_retries=1)
        res = client.buscar_contratacoes_publicacao(uf="TO")

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["objetoCompra"], "Aquisição de Servidores")

    @patch("requests.Session.get")
    def test_rate_limit_retry(self, mock_get):
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_resp_429.headers = {"Retry-After": "0"}

        mock_resp_200 = MagicMock()
        mock_resp_200.status_code = 200
        mock_resp_200.json.return_value = {"data": [{"id": 1}]}

        mock_get.side_effect = [mock_resp_429, mock_resp_200]

        client = PNCPClient(max_retries=2)
        res = client.buscar_contratacoes_publicacao(uf="GO")
        self.assertEqual(len(res), 1)


if __name__ == "__main__":
    unittest.main()
