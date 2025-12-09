# Pharmyrus API v6.0

Brazilian Pharmaceutical Patent Search API

## Quick Deploy to Railway

### Option 1: Deploy via GitHub (Recommended)

1. Create a new GitHub repository
2. Upload all files from this folder to the repository root
3. Go to [Railway](https://railway.app)
4. Click "New Project"
5. Select "Deploy from GitHub repo"
6. Select your repository
7. Railway will auto-detect and deploy

### Option 2: Deploy via Railway CLI

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Initialize project
railway init

# Deploy
railway up
```

## URL Format

After deployment, your API will be available at:

```
https://<project-name>-production.up.railway.app
```

Or Railway may generate a random URL like:
```
https://<random-string>.railway.app
```

You can customize the domain in Railway Dashboard → Settings → Domains

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | API information |
| `/health` | GET | Health check |
| `/search` | POST | Main patent search |

## Example Request

```bash
curl -X POST https://your-app.railway.app/search \
  -H "Content-Type: application/json" \
  -d '{"nome_molecula": "darolutamide", "nome_comercial": "Nubeqa"}'
```

## Files Structure

```
pharmyrus-railway/
├── main.py           # FastAPI application
├── requirements.txt  # Python dependencies
├── nixpacks.toml     # Nixpacks build config
├── railway.toml      # Railway specific config
├── Procfile          # Start command (backup)
├── runtime.txt       # Python version
└── README.md         # This file
```

## Environment

- Python 3.11
- FastAPI 0.104.1
- uvicorn 0.24.0
- httpx 0.25.2
