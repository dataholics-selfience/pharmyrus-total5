# Pharmyrus Patent Search API v6.0

API FastAPI para descoberta de patentes farmacêuticas BR a partir de moléculas.

## Deploy na Railway

### 1. Via GitHub (Recomendado)

```bash
# Clone ou crie um repo
git init
git add .
git commit -m "Pharmyrus API v6.0"
git remote add origin https://github.com/seu-usuario/pharmyrus-api.git
git push -u origin main
```

Na Railway:
1. New Project → Deploy from GitHub
2. Selecione o repositório
3. Deploy acontece automaticamente

### 2. Via Railway CLI

```bash
# Instalar Railway CLI
npm install -g @railway/cli

# Login
railway login

# Criar projeto e deploy
railway init
railway up
```

## Uso da API

### Endpoint Principal

**POST /search**

```bash
curl -X POST https://seu-app.railway.app/search \
  -H "Content-Type: application/json" \
  -d '{
    "nome_molecula": "darolutamide",
    "nome_comercial": "Nubeqa"
  }'
```

### Resposta

```json
{
  "molecule_info": {
    "name": "darolutamide",
    "brand": "Nubeqa",
    "dev_codes": ["ODM-201", "BAY-1841788"],
    "cas": "1297538-32-9"
  },
  "wo_discovery": {
    "total_found": 15,
    "wo_numbers": ["WO2023222557", "WO2023194528", ...]
  },
  "br_patents": {
    "total": 8,
    "patents": [
      {"number": "BR112012008823A2", "source": "wo_extraction", "link": "..."},
      ...
    ]
  },
  "comparison": {
    "baseline": "Cortellis",
    "expected_brs": 8,
    "br_found": 8,
    "br_rate": "100%",
    "status": "Excellent"
  }
}
```

## Estratégias de Busca

A API utiliza 7 camadas de busca:

1. **PubChem Enrichment**: Extrai dev codes, CAS, sinônimos
2. **Google Search**: Busca WOs por ano (2006-2024)
3. **Google Patents Search**: Busca direta por patentes
4. **Company-based Search**: Busca por empresas farmacêuticas
5. **Dev Code Search**: Busca por códigos de desenvolvimento
6. **Google Patents Chain**: Extração de BR via worldwide_applications
7. **INPI Direct**: Crawler direto no INPI Brasil

## Baseline de Validação (Darolutamide)

| Métrica | Esperado (Cortellis) | API v6.0 |
|---------|---------------------|----------|
| WOs | 7 | 15+ |
| BRs | 8 | 8+ |

## Configuração

Variáveis de ambiente (opcionais):
- `PORT`: Porta do servidor (default: 8000)

As chaves SerpAPI já estão configuradas com rotação automática.

## Arquitetura

```
main.py
├── PubChem Service
├── WO Discovery (Multi-strategy)
├── BR Extraction (Google Patents Chain)
└── INPI Direct Search
```

## Licença

Proprietário - Pharmyrus/Genoi
