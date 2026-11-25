FROM python:3.15.2-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy only application files, excluding local data, virtualenvs, and Git history.
COPY pyproject.toml ./
COPY app/ ./app/

# Editable installation keeps templates, static assets, and SQL beside the code.
RUN pip install --no-cache-dir -e . \
    && mkdir -p /app/data \
    && chown 10001:10001 /app/data

USER 10001:10001

# init-db is destructive: run it only when no database exists.
# exec lets Flask receive Docker's shutdown signal directly.
CMD ["sh", "-ec", "if [ ! -e /app/data/reportboard.db ]; then flask --app app init-db; fi; exec flask --app app run --host=0.0.0.0 --port=5000 --no-debugger --no-reload"]

