# IMEI Control Railway

Sistema Python com painel web e banco de dados para centralizar IMEIs lidos em 3 PCs.

## Como funciona

- Cada PC roda `agent.py`.
- O agente le uma pasta local.
- Ele encontra IMEIs de 15 digitos em arquivos recentes.
- Envia para o painel web.
- O painel permite buscar o IMEI nos 3 locais ao mesmo tempo.

## Rodar local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Acesse `http://localhost:5000`.

Senha padrao do `.env.example`: `123456`.

## Variaveis no Railway

```text
SECRET_KEY=uma-chave-grande
ADMIN_PASSWORD=sua-senha-admin
INGEST_TOKEN=token-dos-agentes
DATABASE_URL=gerado pelo PostgreSQL do Railway
```

## Agente nos 3 PCs

Copie a pasta `agent` para cada PC, edite `config.json` e rode:

```bash
python agent.py --config config.json
```
