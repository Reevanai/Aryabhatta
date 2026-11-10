# syntax=docker/dockerfile:1
FROM public.ecr.aws/docker/library/python:3.12-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
# Behind a TLS-inspecting proxy, pass the proxy as a build arg and its CA as the optional
# build secret `extra_ca`. Neither is needed otherwise, and neither ends up in the image.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir uv==0.5.4
WORKDIR /src
COPY pyproject.toml uv.lock .python-version ./
COPY packages packages
COPY modules modules
COPY apps apps
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --no-editable

FROM public.ecr.aws/docker/library/python:3.12-slim
RUN useradd --system --uid 10001 --home-dir /nonexistent sentinel \
 && mkdir -p /var/lib/sentinel/files && chown sentinel /var/lib/sentinel/files
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
USER sentinel
