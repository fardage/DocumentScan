FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libsane1 \
        sane-utils \
        python3 \
        python3-sane \
        python3-pil \
        python3-pytest \
        img2pdf \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# SANE: only the epjitsu backend, with the S1300 firmware baked in.
COPY sane/dll.conf sane/epjitsu.conf /etc/sane.d/
COPY firmware/1300_0C26.nal /usr/share/sane/epjitsu/1300_0C26.nal

WORKDIR /app
COPY documentscan/ /app/documentscan/

CMD ["python3", "-m", "documentscan.daemon"]
