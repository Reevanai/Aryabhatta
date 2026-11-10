FROM public.ecr.aws/docker/library/python:3.12-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
RUN pip install --no-cache-dir uv==0.5.4
WORKDIR /src
COPY pyproject.toml uv.lock .python-version ./
COPY packages packages
COPY modules modules
COPY apps apps
RUN uv sync --frozen --no-dev --no-editable

FROM public.ecr.aws/docker/library/python:3.12-slim
RUN useradd --system --uid 10001 --home-dir /nonexistent sentinel \
 && mkdir -p /var/lib/sentinel/files && chown sentinel /var/lib/sentinel/files
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
USER sentinel
