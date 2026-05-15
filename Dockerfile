FROM python:3.10-slim

WORKDIR /app

# System deps for ML libraries
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "app.py"]RUN pip install --no-cache-dir --timeout=120 -r requirements.txt