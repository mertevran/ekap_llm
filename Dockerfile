# =============================================================================
#  Toplu tarama için Docker imajı
# =============================================================================
#  DİKKAT: Bu imaj yalnızca Python uygulamasını içerir. Karar modeli Ollama
#  üzerinde çalışır ve Ollama bu imajın İÇİNDE DEĞİLDİR — ayrı çalışması ve
#  konteynerden erişilebilir olması gerekir.
#
#  Derleme:
#      docker build -t ekap-llm .
#
#  Çalıştırma (host üzerindeki Ollama'ya bağlanarak):
#      docker run --rm \
#        --env-file .env \
#        -e OLLAMA_HOST=http://host.docker.internal:11434 \
#        -v "$(pwd)/Sonuclar:/app/Sonuclar" \
#        ekap-llm
# =============================================================================
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# curl ve git, bağımlılık kurulumu ve teşhis için gerekli.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Önce sadece bağımlılık listesi kopyalanır: kod değiştiğinde Docker'ın
# katman önbelleği bozulmaz ve kurulum baştan yapılmaz.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Çıktı dizini. Sonuçları saklamak için dışarıdan bir birim (volume) bağlayın.
RUN mkdir -p Sonuclar/toplu_tarama

CMD ["python", "scripts/toplu_tarama.py"]
