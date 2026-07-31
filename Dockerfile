FROM python:3.12-alpine

# Build arguments for metadata
ARG BUILD_DATE
ARG VCS_REF
ARG VERSION

# Labels following OCI Image Format Specification
LABEL org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.authors="SawPsyder" \
      org.opencontainers.image.url="https://github.com/SawPsyder/TwitchDropsMiner" \
      org.opencontainers.image.documentation="https://github.com/SawPsyder/TwitchDropsMiner/blob/main/README.md" \
      org.opencontainers.image.source="https://github.com/SawPsyder/TwitchDropsMiner" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.vendor="SawPsyder" \
      org.opencontainers.image.title="Twitch Drops Miner" \
      org.opencontainers.image.description="Automated Twitch drops mining application with web-based interface"

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

# Set working directory
WORKDIR /app

# Install curl for efficient healthchecks + create non-root user
RUN apk add --no-cache curl \
    && addgroup -g 1000 appuser \
    && adduser -D -u 1000 -G appuser appuser

# Copy project metadata first for better layer caching
COPY pyproject.toml uv.lock* ./

# Install Python dependencies (using pip for compatibility; uv recommended for local dev)
RUN pip install --no-cache-dir -U pip setuptools wheel \
    && pip install --no-cache-dir .

# Copy application code
COPY main.py ./
COPY src/ ./src/
COPY lang/ ./lang/
COPY icons/ ./icons/
COPY web/ ./web/

# Create data and logs directories with correct ownership
RUN mkdir -p /app/data /app/logs \
    && chown -R appuser:appuser /app/data /app/logs

# Switch to non-root user for security
USER appuser

# Expose web port
EXPOSE 8080

# Health check using the new lightweight /api/health endpoint + curl
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

# Run the application (web GUI is now default)
CMD ["python", "main.py"]
