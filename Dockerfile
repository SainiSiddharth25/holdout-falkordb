FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates libgomp1 libssl3t64 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements-lock.txt pyproject.toml ./
RUN python3 -m venv /app/.venv && /app/.venv/bin/python -m pip install --no-cache-dir -r requirements-lock.txt
COPY holdout ./holdout
COPY scripts ./scripts
COPY fixtures ./fixtures
COPY dist ./dist
RUN /app/.venv/bin/python -m pip install --no-deps -e . && /app/.venv/bin/python scripts/probe_engine.py --install-native-module && mkdir -p /app/.runtime/sessions && useradd --uid 10001 --create-home holdout && chown -R holdout:holdout /app
USER holdout
EXPOSE 8000
CMD ["/app/.venv/bin/python", "-m", "holdout", "serve", "--host", "0.0.0.0", "--port", "8000"]
