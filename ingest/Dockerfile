# Non-root, slim image matching the cluster's app template securityContext
# (runAsNonRoot, drop ALL caps, no privilege escalation).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# uid 10001 — arbitrary non-zero; matches runAsNonRoot in the deployment.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin garden
USER 10001

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
