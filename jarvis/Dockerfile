FROM python:3.12-slim
WORKDIR /app
COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt
COPY . .
# Disco persistente: monte um volume em /data (memória, token do Google, workspace)
ENV JARVIS_DATA_DIR=/data PYTHONUNBUFFERED=1
RUN useradd -m jarvis && mkdir -p /data && chown jarvis /data
USER jarvis
CMD ["python", "-m", "jarvis.main", "--serve"]
