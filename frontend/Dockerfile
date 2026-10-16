# Painel do 4turismo (build do React) servido pelo Caddy, que também é a porta
# de entrada de tudo: painel, API e os sites das agências. Usado pelo
# infra/compose.yaml; o Caddyfile entra por volume (infra/Caddyfile).

FROM node:22.21.1-bookworm-slim AS build
WORKDIR /app
COPY package.json yarn.lock ./
RUN corepack enable && yarn install --frozen-lockfile --network-timeout 600000
COPY . .
# O .env.production (versionado) deixa REACT_APP_BACKEND_URL vazio: o painel
# chama /api no mesmo domínio que o serviu. Ver o comentário dentro dele.
RUN yarn build

FROM caddy:2.8.4-alpine
COPY --from=build /app/build /srv
