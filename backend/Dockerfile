# One image, many process roles (api | ingress | web | worker | scheduler | migrate | all), chosen by ROLE.
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN pip install --prefix=/install .

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MIGRATIONS_DIR=/app/migrations
RUN useradd --system --uid 10001 --home /app app
WORKDIR /app
COPY --from=build /install /usr/local
COPY migrations ./migrations
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"
ENTRYPOINT ["python", "-m", "salesai"]
