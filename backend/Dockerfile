# Backend do 4turismo (FastAPI). Usado pelo infra/compose.yaml.
#
# Um worker só de uvicorn, de propósito: a trava "uma conversa de cada vez"
# (flow_runtime) vale dentro de um processo. Com dois workers, duas mensagens do
# mesmo turista podem cair em processos diferentes e a corrida que perdia a
# pergunta do dia no meio da reserva volta. Subir para mais workers exige antes
# a trava no Mongo (ver CLAUDE.md, "Uma conversa de cada vez").
FROM python:3.11.9-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Sem root dentro do container. A pasta de mídia nasce com o dono certo, e o
# volume nomeado herda essa posse na primeira montagem.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data/media \
    && chown -R app:app /data/media
USER app

EXPOSE 8000

# python em vez de curl: a imagem slim não tem curl, e o health de verdade
# (banco + workers) é o que interessa aqui.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=8).status == 200 else 1)"

# --proxy-headers: o IP real e o https chegam pelo Caddy (X-Forwarded-*). Só o
# Caddy alcança esta porta (rede interna do compose), por isso o "*".
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
