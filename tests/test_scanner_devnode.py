from documentscan.scanner import devnode_for


def test_devnode_for_libusb_name():
    assert devnode_for("epjitsu:libusb:001:004") == "/dev/bus/usb/001/004"


def test_devnode_for_other_name():
    assert devnode_for("epjitsu:/dev/usb/scanner0") is None
