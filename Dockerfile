FROM python:3.12-slim

WORKDIR /app

# Copiamos primero requirements para aprovechar cache de capas
COPY requirements.txt .

RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# Copiamos el resto del código
COPY . .

# Render suele exponer el puerto vía $PORT
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
