FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create temp directory for PDF processing
RUN mkdir -p /tmp/pdf_translator

EXPOSE 5001

# 必须单进程（--workers 1）：progress_queues/cancel_flags 存在内存中，
# 多进程会导致 SSE 进度流找不到队列。用 --threads 处理并发请求。
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "1", "--threads", "8", "--timeout", "0", "--keep-alive", "5", "app:app"]
