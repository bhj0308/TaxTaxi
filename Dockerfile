FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on

# Install system dependencies
RUN apt-get update --fix-missing || true && \
    apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get update --fix-missing || true

WORKDIR /app

# Install PyTorch FIRST with its own index-url (ISOLATED)
RUN pip install --no-cache-dir \
    torch==2.1.0+cpu \
    torchvision==0.16.0+cpu \
    --index-url https://download.pytorch.org/whl/cpu

# Copy requirements and install OTHER packages (PyPI default)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Create directories
RUN mkdir -p /app/staticfiles /app/media && \
    chmod -R 755 /app/staticfiles /app/media

# Create non-root user
RUN useradd -m -u 1000 django && chown -R django:django /app
USER django

EXPOSE 8000
CMD ["uvicorn", "taxtaxi.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]