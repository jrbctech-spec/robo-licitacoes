import httpx
from datetime import datetime, timedelta

hoje = datetime.now()
data_final = hoje.strftime("%Y%m%d")
data_inicial = (hoje - timedelta(days=20)).strftime("%Y%m%d")

url = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
params = {
    "dataInicial": data_inicial,
    "dataFinal": data_final,
    "codigoModalidadeContratacao": 6,
    "pagina": 1,
    "tamanhoPagina": 10,
    "uf": "TO"
}
headers = {"User-Agent": "Mozilla/5.0"}
r = httpx.get(url, params=params, headers=headers)
print(r.status_code)
print(r.text[:500])
