FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data \
    HF_HOME=/models/huggingface \
    EASYOCR_MODULE_PATH=/models/easyocr

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    fonts-dejavu-core \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip==25.1.1 \
    && pip install --index-url https://download.pytorch.org/whl/cpu torch==2.7.1 torchvision==0.22.1 \
    && pip install -r requirements.txt

COPY app.py ./
COPY .streamlit ./.streamlit
COPY rag_assistant ./rag_assistant
COPY tests ./tests

RUN mkdir -p /app/data /models/huggingface /models/easyocr

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--server.maxUploadSize=500", "--server.enableXsrfProtection=true", "--browser.gatherUsageStats=false"]
