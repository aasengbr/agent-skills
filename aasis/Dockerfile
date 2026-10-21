FROM python:3.12-slim
WORKDIR /app
COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt
COPY . .
# Disco persistente: monte um volume em /data (memória, token do Google, workspace)
ENV AASIS_DATA_DIR=/data PYTHONUNBUFFERED=1
RUN useradd -m aasis && mkdir -p /data && chown aasis /data
USER aasis
CMD ["python", "-m", "aasis.main", "--serve"]
