# Build context: the irrigation/ directory.
#   docker build -f mcp/Dockerfile -t garden-tuya-mcp .
FROM python:3.11-slim

WORKDIR /app

# Non-root for parity with the cluster securityContext.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser

COPY mcp/requirements.txt /app/mcp/requirements.txt
RUN pip install --no-cache-dir -r /app/mcp/requirements.txt

COPY garden_core.py /app/garden_core.py
COPY mcp/gateway.py /app/mcp/gateway.py
COPY mcp/server.py /app/mcp/server.py

# Budget-state dir (a PVC is mounted here in the cluster).
RUN mkdir -p /var/lib/garden-mcp && chown appuser:appuser /var/lib/garden-mcp
USER appuser

ENV GARDEN_MCP_CONFIG=/etc/garden-mcp/config.json \
    GARDEN_MCP_STATE=/var/lib/garden-mcp \
    GARDEN_MCP_HOST=127.0.0.1 \
    GARDEN_MCP_PORT=8765 \
    PYTHONPATH=/app:/app/mcp

EXPOSE 8765
CMD ["python", "/app/mcp/server.py"]
