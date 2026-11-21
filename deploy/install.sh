#!/bin/bash
# Install or update DocumentScan as a systemd service on Debian / Raspberry Pi OS.
# Run from a checkout of this repository: sudo deploy/install.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
cd "$(dirname "$0")/.."

apt-get install -y --no-install-recommends python3-sane python3-pil python3-numpy img2pdf

id documentscan >/dev/null 2>&1 || useradd --system --no-create-home \
    --home-dir /var/lib/documentscan --shell /usr/sbin/nologin documentscan

install -d -m 755 /opt/documentscan /opt/documentscan/sane /opt/documentscan/firmware
rm -rf /opt/documentscan/documentscan
cp -r documentscan /opt/documentscan/
find /opt/documentscan/documentscan -name __pycache__ -prune -exec rm -rf {} +
install -m 644 sane/dll.conf sane/epjitsu.conf /opt/documentscan/sane/
install -m 644 firmware/1300_0C26.nal /opt/documentscan/firmware/

install -m 644 deploy/documentscan.service /etc/systemd/system/documentscan.service
[ -e /etc/documentscan.env ] || install -m 640 -g documentscan deploy/documentscan.env /etc/documentscan.env

systemctl daemon-reload
systemctl enable documentscan.service
systemctl restart documentscan.service
systemctl --no-pager --lines=0 status documentscan.service
