"""Thin wrapper around python-sane for the ScanSnap S1300 (epjitsu backend)."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Iterator

import sane
from _sane import error as SaneError  # python-sane 2.9 does not re-export this as sane.error
from PIL import Image

from .config import Config

log = logging.getLogger(__name__)

# SANE option names (python-sane exposes them with '-' replaced by '_').
SENSOR_BUTTON = "scan"
SENSOR_PAGE_LOADED = "page_loaded"
SENSOR_COVER_OPEN = "cover_open"
SENSOR_POWER_SAVE = "power_save"


class ScannerNotFound(RuntimeError):
    """No usable SANE device was found."""


_LIBUSB_NAME = re.compile(r"libusb:(\d{3}):(\d{3})$")


def devnode_for(sane_name: str) -> str | None:
    """Map a SANE name like ``epjitsu:libusb:001:004`` to ``/dev/bus/usb/001/004``."""
    match = _LIBUSB_NAME.search(sane_name)
    if not match:
        return None
    return f"/dev/bus/usb/{match.group(1)}/{match.group(2)}"


def _node_identity(path: str) -> tuple[int, int]:
    st = os.stat(path)
    return (st.st_ino, st.st_rdev)


class Scanner:
    """Owns one open SANE device handle.

    Any :class:`SaneError` raised by the methods here means the device handle is
    unusable (unplugged, powered off, I/O error) and the caller should
    :meth:`close` and reconnect.
    """

    def __init__(self, config: Config):
        self.config = config
        self.dev: Any = None
        self.name: str | None = None
        self.model: str | None = None
        self._devnode: str | None = None
        self._devnode_id: tuple[int, int] | None = None

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        sane.init()
        try:
            devices = sane.get_devices()
        except SaneError as exc:
            sane.exit()
            raise ScannerNotFound(f"SANE device enumeration failed: {exc}") from exc

        chosen = None
        if self.config.sane_device:
            for entry in devices:
                if entry[0] == self.config.sane_device:
                    chosen = entry
                    break
            if chosen is None:
                sane.exit()
                raise ScannerNotFound(
                    f"configured SANE_DEVICE {self.config.sane_device!r} not found; "
                    f"available: {[d[0] for d in devices]}"
                )
        else:
            for entry in devices:
                if entry[0].startswith("epjitsu:"):
                    chosen = entry
                    break
            if chosen is None and devices:
                chosen = devices[0]
            if chosen is None:
                sane.exit()
                raise ScannerNotFound(
                    "no scanner found (is it plugged in, lid open, USB passed through, "
                    "and the firmware file present?)"
                )

        name, vendor, model, _kind = chosen
        try:
            self.dev = sane.open(name)
        except SaneError as exc:
            sane.exit()
            raise ScannerNotFound(f"could not open {name}: {exc}") from exc
        self.name = name
        self.model = f"{vendor} {model}".strip()
        self._devnode = devnode_for(name)
        self._devnode_id = None
        if self._devnode:
            try:
                self._devnode_id = _node_identity(self._devnode)
            except OSError:
                log.warning("Cannot stat %s; USB disconnects will only be noticed when scanning", self._devnode)
        log.info("Connected to %s (%s)", self.model, name)
        self._apply_scan_settings()

    def close(self) -> None:
        if self.dev is not None:
            try:
                self.dev.close()
            except Exception:  # noqa: BLE001 - best effort
                pass
            self.dev = None
        self._devnode = None
        self._devnode_id = None
        try:
            sane.exit()
        except Exception:  # noqa: BLE001
            pass

    def check_alive(self) -> None:
        """Raise ScannerNotFound if the USB device node vanished or was recreated.

        The epjitsu backend swallows USB errors when reading its sensors, so a
        scanner that powered off (lid closed) or re-enumerated would otherwise
        go unnoticed until the next scan attempt.
        """
        if self._devnode is None or self._devnode_id is None:
            return
        try:
            current = _node_identity(self._devnode)
        except FileNotFoundError:
            raise ScannerNotFound(f"scanner {self._devnode} disappeared (lid closed or unplugged?)")
        if current != self._devnode_id:
            raise ScannerNotFound(f"scanner {self._devnode} was re-enumerated")

    # -- sensors -------------------------------------------------------------

    def read_sensor(self, py_name: str) -> bool | None:
        """Read a read-only sensor option, or None if the device lacks it.

        Note: ``dev.scan`` would resolve to the ``scan()`` method, so sensors are
        read through the underlying handle by option index.
        """
        opt = self.dev.opt.get(py_name)
        if opt is None or not opt.is_active():
            return None
        return bool(self.dev.dev.get_option(opt.index))

    def button_pressed(self) -> bool:
        return bool(self.read_sensor(SENSOR_BUTTON))

    def page_loaded(self) -> bool:
        value = self.read_sensor(SENSOR_PAGE_LOADED)
        # If the backend has no paper sensor, assume paper is there.
        return True if value is None else value

    # -- scanning ------------------------------------------------------------

    def _set_option(self, py_name: str, value: Any) -> None:
        opt = self.dev.opt.get(py_name)
        if opt is None:
            log.warning("Scanner has no option %r; skipping", py_name)
            return
        if not opt.is_active() or not opt.is_settable():
            log.debug("Option %r not settable; skipping", py_name)
            return
        setattr(self.dev, py_name, value)
        log.debug("Set %s = %r", py_name, getattr(self.dev, py_name))

    def _apply_scan_settings(self) -> None:
        cfg = self.config
        self._set_option("source", cfg.scan_source)
        self._set_option("mode", cfg.scan_mode)
        self._set_option("resolution", cfg.scan_resolution)
        if cfg.page_width_mm > 0:
            self._set_option("page_width", cfg.page_width_mm)
        # page_height 0 lets epjitsu detect the page length itself.
        self._set_option("page_height", cfg.page_height_mm)

    def iter_pages(self) -> Iterator[Image.Image]:
        """Scan every sheet in the feeder, yielding each page as soon as it is read.

        The next page is only read when the caller asks for it, so pages can be
        processed one at a time instead of all being held in memory; the paper
        simply waits in the feeder meanwhile.
        """
        self._apply_scan_settings()
        count = 0
        try:
            for image in self.dev.multi_scan():
                count += 1
                log.info("Scanned page %d (%dx%d, %s)", count, image.width, image.height, image.mode)
                yield image
                del image  # don't hold the raw page while the next one is read
        finally:
            try:
                self.dev.cancel()
            except Exception:  # noqa: BLE001
                pass
