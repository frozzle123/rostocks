FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Create the data directory for persistent storage
RUN mkdir -p /data && chown -R nobody:nogroup /data && chmod 755 /data

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create database directory if needed
RUN mkdir -p /data

# Switch to non-root user
USER nobody

# Run the bot
CMD ["python", "main.py"]
