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
   (`ADF Duplex`, `Color`, 300 dpi by default).
4. Each page is rotated 180 degrees (ScanSnap feeds pages upside down),
   auto-levelled so the paper is white and the ink black, straightened and
   cropped to the sheet (the scan window is wider than the paper, so the grey
   scanner backing would otherwise show), and lightly sharpened. Then the
   pages are encoded as JPEG and wrapped into a PDF with `img2pdf`.
5. The PDF is written to a hidden temp dir inside the consume folder, chowned
   to the paperless user and atomically renamed into place as
   `Scan_YYYY-MM-DD_HH-MM-SS.pdf`. Paperless picks it up via inotify.
6. If the scanner disappears (lid closed, unplugged, power save) the daemon
   reconnects with backoff.

## Layout

```
Dockerfile               debian:trixie-slim + sane + python3-sane + img2pdf
docker-compose.yml       standalone compose for this repo
firmware/1300_0C26.nal   proprietary S1300 firmware (see below)
sane/dll.conf            load only the epjitsu backend
sane/epjitsu.conf        firmware path + USB id 04c5:11ed
documentscan/            the daemon (config, scanner, enhance, deskew, pdf, uploader, daemon)
host/                    udev rule that makes the scanner accessible to the container user
paperless/               post-consume script sending a Telegram message per document
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

### Running without root

The container runs as the paperless user (uid/gid 1001) with all
capabilities dropped, `no-new-privileges`, a read-only root filesystem and a
memory limit. For that user to open the scanner, the host needs the udev rule
in `host/60-scansnap-s1300.rules`, which gives the S1300's USB node to group
1001 on every (re)enumeration:

```sh
sudo cp host/60-scansnap-s1300.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=04c5 --attr-match=idProduct=11ed
ls -l /dev/bus/usb/001/   # scanner node should be root:mediaservices, mode 660
```

Only the scanner's USB bus (`/dev/bus/usb/001`) is mounted, not every USB
device; the `device_cgroup_rules` line still names major 189 because device
numbers on that bus change whenever the lid is closed and opened. If the
scanner is ever moved to another port, check `lsusb` for its bus number and
adjust the mount.

`PUID`/`PGID` only matter if you run the container as root instead; then the
delivered PDF is chowned to them.

## Configuration (environment variables)

| Variable | Default | Meaning |
| --- | --- | --- |
| `CONSUME_DIR` | `/consume` | paperless consume folder inside the container |
| `PUID` / `PGID` | `1001` / `1001` | owner to chown the PDF to, only when running as root |
| `SCAN_SOURCE` | `ADF Duplex` | `ADF Front`, `ADF Back` or `ADF Duplex` |
| `SCAN_MODE` | `Color` | `Lineart`, `Gray` or `Color` |
| `SCAN_RESOLUTION` | `300` | dpi, 50 to 600 |
| `PAGE_WIDTH_MM` | `219` | scan width; wider than A4 so the sheet is always fully captured, the backing is cropped away |
| `PAGE_HEIGHT_MM` | `0` | `0` lets the scanner detect the page length |
| `POLL_INTERVAL` | `0.5` | seconds between button polls |
| `FILENAME_PREFIX` | `Scan` | prefix of the delivered PDF |
| `JPEG_QUALITY` | `85` | JPEG quality of the embedded pages |
| `ROTATE_FRONT` | `180` | degrees to rotate front sides (0, 90, 180, 270) |
| `ROTATE_BACK` | `180` | degrees to rotate back sides in duplex mode |
| `ENHANCE` | `true` | auto-levels + sharpening; `false` keeps the raw scan |
| `AUTOCONTRAST_CUTOFF` | `1.0` | percent of brightest pixels treated as paper white; raise if backgrounds stay grey, lower if light colours wash out |
| `SHARPEN` | `true` | unsharp mask, applied last |
| `DESKEW` | `true` | straighten the page using its edges against the backing |
| `AUTOCROP` | `true` | crop to the detected sheet |
| `MAX_SKEW_DEG` | `5` | larger detected angles are ignored as misdetections |
| `CROP_INSET_MM` | `1.0` | trim this much inside the detected edge to hide edge shadows |
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
- **Pages come out upside down or sideways**: adjust `ROTATE_FRONT` /
  `ROTATE_BACK`. Paperless's own OCR-based auto-rotation only fixes the
  archived copy and needs enough text to be confident.
- **Grey borders or a slanted page**: deskew/crop looks for the scanner
  backing around the sheet by walking inward from each image border, so it
  needs the scan window to be wider than the paper (`PAGE_WIDTH_MM` defaults
  to 219 for that reason). A side where the sheet reaches the image border is
  left uncropped. Set `LOG_LEVEL=DEBUG` to see which edges were found and
  why others were rejected; `AUTOCROP=false` / `DESKEW=false` turn it off.
- **Feed skew**: sheet-fed scanners let the paper turn slightly while it is
  pulled through, so the top and bottom edges rarely agree exactly; the
  daemon uses the long side edges as the main reference.
- **Colours look washed out or the background stays grey**: tune
  `AUTOCONTRAST_CUTOFF`, or set `ENHANCE=false` to get the raw scanner output.
- **PDF appears in the consume folder but paperless does not import it**:
  check `docker logs paperless`; the file must be owned by the paperless user
  (`PUID`/`PGID`).
