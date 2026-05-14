FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY dev_tunnel.py triage_manual_cli.py /app/

RUN pip install --no-cache-dir ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "triage_service.api.triage_api:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
