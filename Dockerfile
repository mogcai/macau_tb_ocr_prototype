FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-paddle.txt ./
RUN python -m pip install --upgrade pip && \
    python -m pip install paddlepaddle && \
    python -m pip install -r requirements.txt -r requirements-paddle.txt

COPY macau_tb_ocr.py vision_ocr_worker.swift README_zh_HK.md config.example.json ./
ENTRYPOINT ["python", "macau_tb_ocr.py"]
