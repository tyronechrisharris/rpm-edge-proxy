FROM python:3.13.15-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26

ARG VERSION=2.0.0
LABEL org.opencontainers.image.title="RPM Edge Proxy" \
      org.opencontainers.image.description="Low-latency TCP fan-out proxy for a radiation portal monitor" \
      org.opencontainers.image.version="${VERSION}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --chown=65534:65534 cas_proxy /app/cas_proxy
COPY --chown=65534:65534 config/config.json /config/config.json

USER 65534:65534

EXPOSE 9090 1600

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-m", "cas_proxy", "--check", "http://127.0.0.1:9090/healthz"]

ENTRYPOINT ["python", "-m", "cas_proxy"]
CMD ["--config", "/config/config.json"]
