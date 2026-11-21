# DocumentScan

Press the button on a Fujitsu ScanSnap S1300 and the document is scanned in
colour, duplex and turned into a PDF in a local outbox folder. It runs as a
systemd service on a Raspberry Pi (a Zero 2 W is enough) next to the scanner.

## How it works

1. SANE's `epjitsu` backend drives the scanner over USB, with the firmware
   shipped in this repository.
2. A small Python daemon keeps the device open and polls the backend's
   read-only `scan` (button) and `page-loaded` sensors twice a second.
3. On a button press with paper in the feeder it scans every sheet
   (`ADF Duplex`, `Color`, 300 dpi by default).
4. Each page is rotated 180 degrees (ScanSnap feeds pages upside down),
   straightened and cropped to the sheet (the scan window is wider than the
   paper, so the grey scanner backing would otherwise show), then cleaned up
   the way ScanSnap Manager does it: the paper colour and sensor noise are
   measured from the histogram, the image is denoised, white-balanced and
   levelled so the paper is pure white and the ink black, and sharpened last.
   Pages without coloured content are stored as greyscale, blank pages are
   dropped. Then the pages are encoded as JPEG and wrapped into a PDF with
   `img2pdf`. While the stack runs through, every raw page goes straight to a
   spool directory on disk; processing starts after the last sheet and takes
   the pages one at a time, so only the compressed pages are held in memory.
5. The PDF is written to a hidden temp dir inside the outbox, fsynced and
   atomically renamed into place as `Scan_YYYY-MM-DD_HH-MM-SS.pdf`.
6. If the scanner disappears (lid closed, unplugged, power save) the daemon
   reconnects with backoff. Each connection attempt runs in a fresh worker
   process (libsane leaks a libusb context per init/teardown, see
   Troubleshooting); the main process never touches SANE.

On a Pi Zero 2 W the feeder needs a few seconds per side (writing the raw
page to the SD card), then each 300 dpi colour page takes about 15 seconds to
process; the daemon peaks at roughly 200 MB.

## Layout

```
deploy/install.sh            install or update the service (run with sudo)
deploy/documentscan.service  systemd unit (own user, hardened, memory-limited)
deploy/documentscan.env      template for /etc/documentscan.env overrides
firmware/1300_0C26.nal       proprietary S1300 firmware (see below)
sane/dll.conf                load only the epjitsu backend
sane/epjitsu.conf            firmware path + USB id 04c5:11ed
documentscan/                the daemon (config, scanner, enhance, deskew, pdf, uploader, daemon)
paperless/                   post-consume script sending a Telegram message per document
tests/                       pytest units that need no hardware
```

## Firmware

The S1300 needs Fujitsu's proprietary `1300_0C26.nal`, extracted from the
Windows ScanSnap Manager driver. It is not redistributable under a free
licence; the copy in `firmware/` was taken from the community mirror
<https://github.com/stevleibelt/scansnap-firmware>
(65793 bytes, sha256
`d693c9150fb00442d3f33f103ffb028410cd3cc53cf3d6df0817fb9d596b17f3`).
If you have your own copy from the driver, replace the file and reinstall.

## Installing

On the Pi (Debian trixie / Raspberry Pi OS), from a checkout of this
repository:

```sh
sudo deploy/install.sh
journalctl -u documentscan -f
```

The script installs the Debian packages (`python3-sane`, `python3-pil`,
`python3-numpy`, `img2pdf`), creates the `documentscan` system user, copies
the daemon, SANE config and firmware to `/opt/documentscan` and enables the
service. Run it again after pulling changes. `/etc/documentscan.env` is only
created once, so local overrides survive updates.

The service runs as `documentscan` with no capabilities, a read-only system,
access to USB devices only and a memory limit. Debian's libsane udev rule
gives scanner USB nodes to the `scanner` group, which the service joins; no
extra udev rule is needed. SANE reads its config from
`/opt/documentscan/sane` (`SANE_CONFIG_DIR`), so the system-wide
`/etc/sane.d` is left alone. PDFs land in `/var/lib/documentscan/outbox`.

## Configuration (environment variables)

Set them in `/etc/documentscan.env` and `sudo systemctl restart documentscan`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `OUTPUT_DIR` | `/var/lib/documentscan/outbox` | where finished PDFs are written |
| `SPOOL_DIR` | `/var/lib/documentscan/spool` | raw pages of the scan in progress (about 27 MB per colour side) |
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
| `ENHANCE` | `true` | denoise, white-balance and level the page so the paper is white; `false` keeps the raw scan |
| `DENOISE` | `true` | median filter on luminance, blur on the colour channels, before levelling |
| `WHITE_MARGIN` | `3.0` | pixels brighter than the paper level minus this many noise standard deviations become white; raise if specks or bleed-through remain, lower if very light print disappears |
| `SHARPEN` | `true` | unsharp mask, applied last, with a threshold above the measured noise |
| `AUTO_COLOR` | `true` | store pages without coloured content as greyscale |
| `DROP_BLANK_PAGES` | `true` | drop pages with (almost) no ink, e.g. the backs of single-sided sheets; if every page is blank they are all kept |
| `BLANK_INK_PERCENT` | `0.02` | a page with less ink than this percent of its area counts as blank |
| `COLOR_PERCENT` | `0.01` | a page with more clearly saturated pixels than this percent counts as coloured |
| `DESKEW` | `true` | straighten the page using its edges against the backing |
| `AUTOCROP` | `true` | crop to the detected sheet |
| `MAX_SKEW_DEG` | `5` | larger detected angles are ignored as misdetections |
| `CROP_INSET_MM` | `1.0` | trim this much inside the detected edge to hide edge shadows |
| `SCAN_ON_PAPER_INSERT` | `false` | also start scanning when paper is inserted |
| `SANE_DEVICE` | unset | force a SANE device name instead of the first `epjitsu:` one |
| `LOG_LEVEL` | `INFO` | `DEBUG` prints every option set |
| `TZ` | system timezone | timezone used for the file name timestamp |

## Manual checks

```sh
# SANE with the service's config:
export SANE_CONFIG_DIR=/opt/documentscan/sane
# Scanner visible + firmware accepted?
scanimage -L
# All options incl. sensors (scan button, page-loaded, cover-open):
scanimage -A
# One manual duplex colour scan to /tmp:
scanimage --source "ADF Duplex" --mode Color --resolution 300 --batch=/tmp/page%d.pnm
# Unit tests, from the repository (needs python3-pytest):
python3 -m pytest tests
```

Note: only one process can hold the scanner, so stop the daemon
(`sudo systemctl stop documentscan`) before running `scanimage` by hand.

## Troubleshooting

- **`no scanner found` in the logs**: lid closed (the S1300 powers off and drops
  off the USB bus), cable unplugged, or the service user is not in the `scanner`
  group. The daemon retries automatically.
- **Lid open, LED flashing, button does nothing, logs keep saying `Scanner not
  found`**: the flashing LED means nobody has uploaded the firmware, i.e. the
  daemon does not see the scanner although Linux does (`lsusb` lists it).
  `scanimage -L` (with `SANE_CONFIG_DIR` as above) should list it; if it
  does, the daemon process is wedged: `sudo systemctl restart documentscan`. Every connection
  attempt now runs in a fresh worker process precisely because libsane leaks a
  libusb context (two file descriptors) per init/teardown cycle, which used to
  exhaust the open-file limit after a few hours of retrying with the lid
  closed. Check with `sudo ls /proc/$(systemctl show -p MainPID --value documentscan)/fd | wc -l`;
  it should stay in the single digits.
- **Button press does nothing**: check `journalctl -u documentscan`. A press
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
- **Grainy or grey paper, blue tint**: that is the raw sensor output of the
  S1300 (noise standard deviation around 8 levels, back side darker and bluer
  than the front). The epjitsu backend has no brightness, contrast or gamma
  controls, so all clean-up happens in the daemon (`ENHANCE`). The log line
  per page shows the measured paper colour and noise and the resulting kind
  (`color`, `gray`, `blank`). If specks or bleed-through remain, raise
  `WHITE_MARGIN`; if very light print disappears, lower it. `ENHANCE=false`
  gives the raw scanner output.
- **A page came out greyscale although it has colour, or a page is missing**:
  colour detection needs more than `COLOR_PERCENT` of the pixels to be clearly
  saturated and blank detection drops pages with less than `BLANK_INK_PERCENT`
  ink; lower or raise them, or turn off `AUTO_COLOR` / `DROP_BLANK_PAGES`.
- **Streaks along the paper direction**: dust on the glass strips or the white
  calibration strips inside the scanner; clean them with a lint-free cloth.
- **Service killed or Pi sluggish during big scans**: the unit caps memory at
  350 MB (`MemoryMax`). Lower `SCAN_RESOLUTION`, or free memory by booting
  without the desktop (`sudo systemctl set-default multi-user.target`).
