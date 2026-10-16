# Deploy do 4turismo

Um servidor Linux com Docker roda tudo: Caddy (porta de entrada com HTTPS), backend, MongoDB e a ponte do WhatsApp (WuzAPI) com o Postgres dela. O WhatsApp é **só pelo canal não oficial** (QR), por decisão de 16/10/2026.

## O que contratar

- **VPS**: 2 vCPU, 4 GB de RAM, 40 GB de disco, Ubuntu 24.04. A primeira subida compila o frontend e a WuzAPI, e com menos memória o build do React pode morrer sem explicação.
- **Domínio** do 4turismo, com acesso ao DNS.
- **Conta no Sentry** (plano grátis) e num monitor de disponibilidade (UptimeRobot ou Better Stack, plano grátis).
- **Destino de backup fora do servidor**: Backblaze B2, Cloudflare R2 ou outro que o rclone aceite.

## 1. DNS

Com `APP_DOMINIO=app.4turismo.com.br` e `SITE_DOMINIO=4turismo.com.br`, crie três registros **A** com o IP do servidor:

| Nome | Para quê |
|---|---|
| `app` | o painel |
| `@` (o domínio sozinho) | redireciona para o painel |
| `*` | os sites das agências (`agencia.4turismo.com.br`) |

Se usar Cloudflare, deixe os três **sem proxy** (nuvem cinza): o certificado é do Caddy, e com o proxy laranja a emissão sob demanda dos sites não funciona.

## 2. Servidor

```sh
# Docker
curl -fsSL https://get.docker.com | sh

# Firewall: só SSH e web
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443 && ufw --force enable

# Código
git clone https://github.com/logostecnologias/4turismo.git /opt/4turismo
cd /opt/4turismo/infra
cp .env.example .env
```

## 3. Preencher o `.env`

Abra `infra/.env` e siga os comentários. Para gerar os segredos:

```sh
openssl rand -hex 32   # rode uma vez para cada: JWT_SECRET, MONGO_SENHA, WUZAPI_DB_SENHA, WUZAPI_ADMIN_TOKEN
openssl rand -hex 16   # WUZAPI_ENCRYPTION_KEY
docker run --rm python:3.11-slim sh -c "pip -q install cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"   # CREDENTIALS_KEY
```

**Guarde uma cópia do `.env` preenchido fora do servidor** (gerenciador de senhas). Sem a `CREDENTIALS_KEY`, as credenciais cifradas no banco viram lixo. Sem a `WUZAPI_ENCRYPTION_KEY`, todos os números precisam ler o QR de novo.

## 4. Subir

```sh
cd /opt/4turismo/infra
docker compose up -d --build     # a primeira vez demora (compila frontend e WuzAPI)
docker compose ps                # todos "running"; backend "healthy"
curl -s https://app.4turismo.com.br/api/health
```

O health deve responder `{"status":"ok","banco":"ok","workers":"ok",...}`. Abra `https://app.SEU-DOMINIO`, entre com `ADMIN_EMAIL`/`ADMIN_PASSWORD` e crie a agência piloto.

## 5. Conectar o WhatsApp da agência

1. Entre na agência (modo suporte) → **Configuração → WhatsApp** → nova conexão pelo canal não oficial.
2. Leia o QR com o celular da agência (WhatsApp → Aparelhos conectados).
3. Mande uma mensagem de outro número para o da agência e confira se ela aparece em **Atendimento**. Se não aparecer, veja `docker compose logs backend wuzapi`.

Lembre a agência do risco do canal não oficial: o número pode ser restringido pelo WhatsApp, e por isso não se usa a plataforma para mensagem em massa.

## 6. Monitoramento

- **Uptime**: monitor HTTP em `https://app.SEU-DOMINIO/api/health`, a cada 5 min, alertando por e-mail/WhatsApp. Ele responde 503 quando o banco ou um processo de fundo cai.
- **Sentry**: crie um projeto Python, cole o DSN em `SENTRY_DSN` no `.env` e rode `docker compose up -d backend`.

## 7. Backup

```sh
chmod +x /opt/4turismo/infra/backup.sh
apt install -y rclone && rclone config        # configure o destino e ponha o nome em BACKUP_REMOTO
/opt/4turismo/infra/backup.sh                 # rode uma vez à mão e confira a saída
crontab -e
# 0 3 * * * /opt/4turismo/infra/backup.sh >> /var/log/4turismo-backup.log 2>&1
```

Cada backup tem `mongo.archive.gz`, `wuzapi.sql.gz`, `media.tar.gz` e uma cópia do `.env`, em `/var/backups/4turismo/<data>/`, guardados por 14 dias.

### Restaurar um backup

**Faça uma vez antes do piloto**, de preferência num servidor de teste. É o único jeito de saber que o backup funciona.

```sh
cd /opt/4turismo/infra
B=/var/backups/4turismo/2026-10-20_0300      # o backup escolhido

cp $B/env .env                               # as mesmas chaves do backup
docker compose up -d mongo wuzapi-db
docker compose stop backend wuzapi caddy

# Mongo
docker compose exec -T mongo mongorestore --username turismo --password "$(grep ^MONGO_SENHA .env | cut -d= -f2)" \
  --authenticationDatabase admin --archive --gzip --drop < $B/mongo.archive.gz

# Sessões do WhatsApp
docker compose exec -T wuzapi-db psql -U wuzapi -d postgres -c "DROP DATABASE wuzapi WITH (FORCE);" -c "CREATE DATABASE wuzapi OWNER wuzapi;"
gunzip -c $B/wuzapi.sql.gz | docker compose exec -T wuzapi-db psql -U wuzapi -d wuzapi

# Mídia
docker run --rm -v turismo_media:/dados -v $B:/b:ro alpine:3.20 sh -c "rm -rf /dados/* && tar xzf /b/media.tar.gz -C /dados"

docker compose up -d
```

## Atualizar

```sh
cd /opt/4turismo && git pull
cd infra && docker compose up -d --build
```

As migrações do banco rodam sozinhas na subida do backend. A WuzAPI só muda quando o hash em `compose.yaml` muda. Troque-o de propósito e teste a conversa depois, porque a forma das mensagens muda entre versões.

## Comandos úteis

```sh
docker compose logs -f backend            # log do backend
docker compose restart backend            # reiniciar só o backend
docker compose exec mongo mongosh -u turismo -p --authenticationDatabase admin turismo
```
