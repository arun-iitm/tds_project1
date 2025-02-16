FROM python:3.13-slim

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    wget \
    sqlite3 \
    ffmpeg \
    imagemagick \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install NodeJS
RUN curl -sL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g prettier@3.4.2

# Upgrade pip & install dependencies
RUN python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel && \
    python3 -m pip install --no-cache-dir \
    openai uv fastapi uvicorn[standard] requests httpx \
    python-dotenv python-dateutil pytesseract pandas numpy \
    duckdb sqlalchemy beautifulsoup4 markdown

# Create and set working directory
WORKDIR /app

RUN mkdir -p /data

# Ignore pip complaining about root user
ENV PIP_ROOT_USER_ACTION=ignore

# Copy config file
COPY config.py /app/config.py

# Copy application files
COPY app.py .

EXPOSE 8000

# Correct CMD format
CMD ["python", "app.py", "uv"]