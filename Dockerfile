FROM python:3.13.15-slim-bookworm

ARG VERSION=1.1.0
LABEL org.opencontainers.image.title="RPM Edge Proxy" \
      org.opencontainers.image.description="Hardened two-RPM TCP broadcast proxy for an isolated network" \
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
