FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GYMRATS_DB=/data/gymrats.db

WORKDIR /app

# Dependências primeiro (camada em cache enquanto os requirements não mudam).
COPY requirements.txt requirements-api.txt ./
RUN pip install -r requirements.txt -r requirements-api.txt

COPY api.py sync.py sheets.py ./

# Sem root. O host precisa dar a pasta data/ para o uid 1000 (ver DEPLOY.md).
RUN useradd --system --uid 1000 gymrats && mkdir -p /data && chown gymrats /data
USER gymrats

EXPOSE 8000

# Imagem slim não tem curl: o healthcheck usa o próprio Python.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)" || exit 1

# 0.0.0.0 aqui é DENTRO do container: nenhuma porta é publicada no host, só o
# Caddy alcança o serviço pela rede "web".
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
