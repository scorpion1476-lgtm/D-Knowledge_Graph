# D-Knowledge_Graph reference container.
# Uses a distroless-style two-stage build so the runtime image contains only
# Python and the installed wheel.
FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip build \
 && python -m build --wheel --outdir /wheels

FROM python:3.12-slim
LABEL org.opencontainers.image.title="d-knowledge-graph" \
      org.opencontainers.image.description="Local first knowledge graph and multi agent research platform" \
      org.opencontainers.image.licenses="LicenseRef-DKG-Source-Available-NonCommercial" \
      org.opencontainers.image.source="local"
RUN useradd --system --uid 10001 --home-dir /data dkg \
 && mkdir -p /data \
 && chown -R dkg:dkg /data
COPY --from=builder /wheels/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl \
 && rm -f /tmp/*.whl
USER dkg
ENV DKG_HOME=/data \
    DKG_ALLOW_OUTBOUND=0 \
    DKG_TELEMETRY=0
WORKDIR /data
EXPOSE 8765
ENTRYPOINT ["dkg"]
CMD ["status"]
