# DocumentScan

Press the button on a Fujitsu ScanSnap S1300 and the document is scanned in
colour, duplex, turned into a PDF and dropped into the paperless-ngx consume
folder. Paperless does the OCR. Everything runs in one Docker container.

## How it works

1. The container owns the scanner via USB passthrough and SANE's `epjitsu`
   backend (firmware baked into the image).
2. A small Python daemon keeps the device open and polls the backend's
   read-only `scan` (button) and `page-loaded` sensors twice a second.
3. On a button press with paper in the feeder it scans every sheet
   (`ADF Duplex`, `Color`, 300 dpi by default), encodes the pages as JPEG and
   wraps them into a PDF with `img2pdf`.
4. The PDF is written to a hidden temp dir inside the consume folder, chowned
   to the paperless user and atomically renamed into place as
   `Scan_YYYY-MM-DD_HH-MM-SS.pdf`. Paperless picks it up via inotify.
5. If the scanner disappears (lid closed, unplugged, power save) the daemon
   reconnects with backoff.

## Layout

```
Dockerfile               debian:trixie-slim + sane + python3-sane + img2pdf
docker-compose.yml       standalone compose for this repo
firmware/1300_0C26.nal   proprietary S1300 firmware (see below)
sane/dll.conf            load only the epjitsu backend
sane/epjitsu.conf        firmware path + USB id 04c5:11ed
documentscan/            the daemon (config, scanner, pdf, uploader, daemon)
tests/                   pytest units that need no hardware
```

## Firmware

The S1300 needs Fujitsu's proprietary `1300_0C26.nal`, extracted from the
Windows ScanSnap Manager driver. It is not redistributable under a free
licence; the copy in `firmware/` was taken from the community mirror
<https://github.com/stevleibelt/scansnap-firmware>
(65793 bytes, sha256
`d693c9150fb00442d3f33f103ffb028410cd3cc53cf3d6df0817fb9d596b17f3`).
If you have your own copy from the driver, replace the file and rebuild.

## Running

In `/srv/docker/docker-compose.yml` the service is defined as:

```yaml
  documentscan:
    build: /home/marvintseng/git/DocumentScan
    image: documentscan:local
    container_name: documentscan
    restart: unless-stopped
    environment:
      - TZ=Europe/Zurich
      - PUID=1001
      - PGID=1001
    volumes:
      - /srv/docker/paperless/consume:/consume
      - /dev/bus/usb:/dev/bus/usb
    device_cgroup_rules:
      - 'c 189:* rmw'
```

```sh
cd /srv/docker
docker compose build documentscan
docker compose up -d documentscan
docker logs -f documentscan
```

The `device_cgroup_rules` line grants access to every USB character device
(major 189) so the scanner keeps working after it re-enumerates, which happens
every time the lid is closed and opened.

## Configuration (environment variables)

| Variable | Default | Meaning |
| --- | --- | --- |
| `CONSUME_DIR` | `/consume` | paperless consume folder inside the container |
| `PUID` / `PGID` | `1001` / `1001` | owner of the delivered PDF (paperless user) |
| `SCAN_SOURCE` | `ADF Duplex` | `ADF Front`, `ADF Back` or `ADF Duplex` |
| `SCAN_MODE` | `Color` | `Lineart`, `Gray` or `Color` |
| `SCAN_RESOLUTION` | `300` | dpi, 50 to 600 |
| `PAGE_WIDTH_MM` | `210` | scan width (A4) |
| `PAGE_HEIGHT_MM` | `0` | `0` lets the scanner detect the page length |
| `POLL_INTERVAL` | `0.5` | seconds between button polls |
| `FILENAME_PREFIX` | `Scan` | prefix of the delivered PDF |
| `JPEG_QUALITY` | `85` | JPEG quality of the embedded pages |
| `SCAN_ON_PAPER_INSERT` | `false` | also start scanning when paper is inserted |
| `SANE_DEVICE` | unset | force a SANE device name instead of the first `epjitsu:` one |
| `LOG_LEVEL` | `INFO` | `DEBUG` prints every option set |
| `TZ` | unset | timezone used for the file name timestamp |

## Manual checks

```sh
cd /srv/docker
# Scanner visible + firmware accepted?
docker compose run --rm documentscan scanimage -L
# All options incl. sensors (scan button, page-loaded, cover-open):
docker compose run --rm documentscan scanimage -A
# One manual duplex colour scan to /tmp inside the container:
docker compose run --rm documentscan scanimage --source "ADF Duplex" --mode Color \
    --resolution 300 --batch=/tmp/page%d.pnm
# Unit tests:
docker compose run --rm -v "$PWD/tests:/app/tests:ro" documentscan python3 -m pytest /app/tests
```

Note: only one process can hold the scanner, so stop the daemon
(`docker compose stop documentscan`) before running `scanimage` by hand.

## Troubleshooting

- **`no scanner found` in the logs**: lid closed (the S1300 powers off and drops
  off the USB bus), cable unplugged, or `/dev/bus/usb` not passed through. The
  daemon retries automatically.
- **Button press does nothing**: check `docker logs documentscan`. A press
  with an empty feeder is logged and ignored.
- **PDF appears in the consume folder but paperless does not import it**:
  check `docker logs paperless`; the file must be owned by the paperless user
  (`PUID`/`PGID`).
