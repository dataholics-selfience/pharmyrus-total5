# Pharmyrus API v7.0 - "Cortellis Killer"

Deep Pharmaceutical Patent Search with Worldwide Coverage

## 🚀 O que há de novo na v7.0

- **Deep Navigation**: Navega diretamente em cada WO usando `google_patents_details`
- **Worldwide Coverage**: Extrai patentes de TODOS os países, não apenas BR
- **Múltiplas Fontes**: worldwide_applications, family_members, also_published_as, citations
- **Detalhes Completos**: título, abstract, claims, inventors, assignee, datas, status
- **EPO OPS Integration**: Busca adicional via API do European Patent Office
- **INPI Direct**: Busca direta no INPI com variações em português

## 📊 Comparação com Cortellis (Darolutamide)

| Métrica | Cortellis | Pharmyrus v7.0 |
|---------|-----------|----------------|
| WOs esperados | 7 | 71+ |
| BRs esperados | 8 | 8+ |
| Países | BR apenas | TODOS |
| Detalhes | Básico | Completo |
| Claims | Não | Sim |

## 🌐 URLs da API

### Busca via Browser (GET)

```
# Busca básica
https://[sua-url]/api/v1/search/darolutamide

# Com nome comercial
https://[sua-url]/api/v1/search/darolutamide?brand=Nubeqa

# Outras moléculas
https://[sua-url]/api/v1/search/olaparib
https://[sua-url]/api/v1/search/venetoclax
https://[sua-url]/api/v1/search/enzalutamide
https://[sua-url]/api/v1/search/abiraterone
```

### Busca via API (POST)

```bash
curl -X POST https://[sua-url]/search \
  -H "Content-Type: application/json" \
  -d '{"nome_molecula": "darolutamide", "nome_comercial": "Nubeqa"}'
```

### Outros Endpoints

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/` | GET | Info da API |
| `/health` | GET | Health check |
| `/search` | POST | Busca completa |
| `/api/v1/search/{molecule}` | GET | Busca via URL |

## 📦 Estrutura de Resposta

```json
{
  "api_version": "7.0 Cortellis Killer",
  "molecule_info": {...},
  "wo_discovery": {
    "total_found": 71,
    "wo_numbers": [...]
  },
  "worldwide_patents": {
    "total": 500+,
    "by_country": {"BR": 8, "US": 45, "EP": 30, ...},
    "patents": [...]
  },
  "br_patents": {
    "total": 8,
    "patents": [
      {
        "number": "BR112012008823A2",
        "title": "...",
        "abstract": "...",
        "inventors": [...],
        "assignee": "Orion Corporation",
        "claims": [...],
        "filing_date": "...",
        "status": "..."
      }
    ]
  },
  "comparison": {
    "status": "SUPERIOR"
  }
}
```

## 🛠 Deploy no Railway

1. Crie repo no GitHub
2. Upload arquivos NA RAIZ
3. Railway → New Project → Deploy from GitHub
4. Aguarde build (~2 min)

## ⏱ Performance

- Tempo médio: 10-15 minutos por molécula
- 71 WOs processados
- 3 fontes de dados (SerpAPI, INPI, EPO)
- Rate limiting automático

## 📁 Arquivos

```
├── main.py           # FastAPI (35KB)
├── requirements.txt  # Dependencies
├── Procfile          # Start command
├── runtime.txt       # Python 3.11
└── .gitignore
```
