FROM python:3.11-slim

WORKDIR /app
COPY . .

# Installa dipendenze di sistema necessarie per il monitoraggio di rete e log
RUN apt-get update && apt-get install -y \
    procps \
    iproute2 \
    net-tools \
    lsof \
    iputils-ping \
    hostname \
    && rm -rf /var/lib/apt/lists/*

RUN pip install -r requirements.txt

# Crea le directory necessarie per i file di stato
RUN mkdir -p /tmp

EXPOSE 5000
CMD ["python", "main.py"]
