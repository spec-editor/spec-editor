FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY app.py .

# Create data directory for SQLite ledger
RUN mkdir -p /data

# Non-root user
RUN useradd --create-home --shell /bin/bash proxy && chown -R proxy:proxy /app /data
USER proxy

EXPOSE 8089

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8089/health').raise_for_status()"

CMD ["python", "app.py"]
