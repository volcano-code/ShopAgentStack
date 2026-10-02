FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Optional tracing: the default image keeps the original dependency environment.
ARG SHOP_AGENT_STACK_WITH_OTEL=false
COPY requirements.lock requirements-observability.txt ./
RUN if [ "$SHOP_AGENT_STACK_WITH_OTEL" = "true" ]; then \
      pip install --no-cache-dir -r requirements.lock -r requirements-observability.txt; \
    elif [ "$SHOP_AGENT_STACK_WITH_OTEL" = "false" ]; then \
      pip install --no-cache-dir -r requirements.lock; \
    else echo "SHOP_AGENT_STACK_WITH_OTEL must be true or false" >&2; exit 2; fi \
    && pip check
COPY shop_agent_stack ./shop_agent_stack
COPY tests ./tests
RUN useradd --create-home --uid 10001 shop_agent_stack && mkdir /data && chown shop_agent_stack:shop_agent_stack /data
USER shop_agent_stack
CMD ["uvicorn","shop_agent_stack.app:app","--host","0.0.0.0","--port","8010","--no-access-log"]
