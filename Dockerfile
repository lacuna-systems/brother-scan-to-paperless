# syntax=docker/dockerfile:1.7

FROM debian:bookworm-slim AS driver-downloader

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*


FROM driver-downloader AS brscan4-downloader

ARG BRSCAN4_VERSION=0.4.11-1
ARG BRSCAN4_SHA256=027b73648722ac8c8eb1a9c419d284a6562cc763feac9740a2b75a683b092972

RUN curl --fail --location --silent --show-error \
        "https://download.brother.com/welcome/dlf105200/brscan4-${BRSCAN4_VERSION}.amd64.deb" \
        --output /tmp/brscan.deb \
    && echo "${BRSCAN4_SHA256}  /tmp/brscan.deb" | sha256sum --check --strict


FROM driver-downloader AS brscan5-downloader

ARG BRSCAN5_VERSION=1.7.0-0
ARG BRSCAN5_SHA256=9c3a200db3dc7b2b3332dff8c5226b4f3ee560d20b2e1f4918b1faf7124517b2

RUN curl --fail --location --silent --show-error \
        "https://download.brother.com/welcome/dlf104033/brscan5-${BRSCAN5_VERSION}.amd64.deb" \
        --output /tmp/brscan.deb \
    && echo "${BRSCAN5_SHA256}  /tmp/brscan.deb" | sha256sum --check --strict


FROM python:3.12-slim-bookworm AS runtime

ARG TARGETARCH

RUN if [ "${TARGETARCH}" != "amd64" ]; then \
        echo "These image targets support only amd64" >&2; \
        exit 1; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates libtiff-tools sane-utils snmp tini \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /consume \
    && install -d -m 0700 /staging

ENV CONSUME_DIR= \
    DELIVERY_MODE=consume \
    LOG_FILE= \
    PAPERLESS_ALLOW_INSECURE_HTTP=false \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUTO_REGISTER_SCANNER=true \
    STAGING_DIR=/staging

WORKDIR /app

COPY src/brother_scan_to_paperless/ /opt/brother-scan-to-paperless/brother_scan_to_paperless/
COPY --chmod=755 src/brother_scan_daemon.py /opt/brother-scan-to-paperless/brother_scan_daemon.py
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/doc/brother-scan-to-paperless/

RUN ln -s /opt/brother-scan-to-paperless/brother_scan_daemon.py \
        /usr/local/bin/brother-scan-to-paperless

EXPOSE 54925/udp

STOPSIGNAL SIGTERM

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/brother-scan-to-paperless"]
CMD ["run"]


FROM runtime AS brscan4

COPY --from=brscan4-downloader /tmp/brscan.deb /tmp/brscan.deb

RUN dpkg --install /tmp/brscan.deb \
    && rm /tmp/brscan.deb \
    && command -v brsaneconfig4 >/dev/null \
    && grep --quiet --line-regexp brother4 /etc/sane.d/dll.conf \
    && ldd /usr/lib64/sane/libsane-brother4.so.1.0.7 > /tmp/brscan.ldd \
    && ! grep --quiet "not found" /tmp/brscan.ldd \
    && rm /tmp/brscan.ldd

ENV BRSCAN_VERSION=4 \
    SCANNER_DEVICE=brother4:net1;dev0

LABEL org.opencontainers.image.title="Brother Scan to Paperless (brscan4)" \
      org.opencontainers.image.description="Brother brscan4 scan-button daemon for Paperless-ngx" \
      org.opencontainers.image.licenses="MIT AND LicenseRef-Brother-EULA" \
      org.opencontainers.image.source="https://github.com/zshel/brother-scan-to-paperless"


FROM runtime AS brscan5

COPY --from=brscan5-downloader /tmp/brscan.deb /tmp/brscan.deb

RUN dpkg --install /tmp/brscan.deb \
    && rm /tmp/brscan.deb \
    && command -v brsaneconfig5 >/dev/null \
    && grep --quiet --line-regexp brother5 /etc/sane.d/dll.conf \
    && ldd /opt/brother/scanner/brscan5/libsane-brother5.so.1.0.7 \
        > /tmp/brscan.ldd \
    && ldd /opt/brother/scanner/brscan5/libLxBsScanCoreApi.so.3.2.6 \
        >> /tmp/brscan.ldd \
    && ! grep --quiet "not found" /tmp/brscan.ldd \
    && rm /tmp/brscan.ldd

ENV BRSCAN_VERSION=5 \
    SCANNER_DEVICE=brother5:net1;dev0

LABEL org.opencontainers.image.title="Brother Scan to Paperless (brscan5)" \
      org.opencontainers.image.description="Brother brscan5 scan-button daemon for Paperless-ngx" \
      org.opencontainers.image.licenses="MIT AND LicenseRef-Brother-EULA" \
      org.opencontainers.image.source="https://github.com/zshel/brother-scan-to-paperless"
