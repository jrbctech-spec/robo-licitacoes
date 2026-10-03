import httpx
url = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
params = {
    "dataInicial": "20241001",
    "dataFinal": "20241002",
    "codigoModalidadeContratacao": 6,
    "pagina": 1,
    "tamanhoPagina": 10,
    "uf": "TO"
}
headers = {"User-Agent": "Mozilla/5.0"}
r = httpx.get(url, params=params, headers=headers)
print(r.status_code)
print(r.text[:500])
