# 4turismo

SaaS para agências de turismo receptivo e operadores de passeios: atendimento pelo WhatsApp, vagas por saída, orçamentos, follow-up e lembrete de embarque. Veja [CLAUDE.md](CLAUDE.md) para o contexto completo.

O nome mora em `frontend/src/lib/marca.js`.

## Stack

- **Backend**: FastAPI (Python): `backend/`
- **Frontend**: React + `@xyflow/react`: `frontend/`
- **Banco**: MongoDB em Docker (`docker-compose.yml`)
- **WhatsApp**: Cloud API oficial da Meta (padrão) e canal não oficial WuzAPI (opcional, exige ponte própria)

## Rodando localmente

Pré-requisitos: Docker Desktop, Python 3.11, Node.

```powershell
copy backend\.env.example backend\.env   # e gere os segredos indicados no arquivo
cd backend; pip install -r requirements.txt; cd ..
cd frontend; yarn install; cd ..
npm install
.\start-local.ps1
```

O script sobe o Mongo (`turismo-mongo`, porta 27019), o backend (8010) e o frontend (3010). Abra http://localhost:3010.


Só o banco:

```powershell
docker compose up -d      # sobe
docker compose stop       # para
docker compose down -v    # apaga o banco inteiro
```

## Testes

```powershell
cd backend; python -m pytest -q
```

Rodam contra um Mongo falso (`backend/tests/fakedb.py`), sem precisar do Docker.

## Deploy

Ainda não existe; será escrito quando houver servidor.
