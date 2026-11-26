import os
import sys
import threading
import time
import zlib

VENDOR = 0x054C
PRODUCTS = {0x0CE6: "DualSense Wireless Controller", 0x0DF2: "DualSense Edge Wireless Controller"}
BUS_USB = 0x0003
BUS_BT = 0x0005

USB_REPORT_ID = 0x02
USB_REPORT_SIZE = 63
BT_REPORT_ID = 0x31
BT_REPORT_SIZE = 78
BT_TAG = 0x10
BT_CRC_SEED = 0xA2
COMMON_SIZE = 47

F0_COMPATIBLE_VIBRATION = 0x01
F0_HAPTICS_SELECT = 0x02
F0_RIGHT_TRIGGER = 0x04
F0_LEFT_TRIGGER = 0x08
F1_MIC_MUTE_LED = 0x01
F1_LIGHTBAR = 0x04
F1_PLAYER_LEDS = 0x10
F1_MOTOR_POWER = 0x40
F2_LED_BRIGHTNESS = 0x01
F2_LIGHTBAR_SETUP = 0x02
LIGHTBAR_SETUP_ON = 0x01
OFF_LIGHTBAR_SETUP = 41

OFF_RUMBLE_RIGHT = 2
OFF_RUMBLE_LEFT = 3
OFF_MIC_LED = 8
OFF_RIGHT_TRIGGER = 10
OFF_LEFT_TRIGGER = 21
OFF_MOTOR_POWER = 36
OFF_FLAG2 = 38
OFF_BRIGHTNESS = 42
OFF_PLAYER_LEDS = 43
OFF_RED = 44

TRIGGER_SIZE = 11
TRIGGER_OFF = 0x05
TRIGGER_FEEDBACK = 0x21
TRIGGER_VIBRATION = 0x26
ZONES = 10

PLAYER_PATTERNS = {0: 0x00, 1: 0x04, 2: 0x0A, 3: 0x15, 4: 0x1B}
BRIGHTNESS = {"bright": 0, "medium": 1, "dim": 2}


def _byte(name, value, top=255):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > top:
        raise ValueError("%s must be a whole number from 0 to %d" % (name, top))
    return value


def trigger_off():
    return bytes([TRIGGER_OFF]) + bytes(TRIGGER_SIZE - 1)


def _zones(position, level):
    active = 0
    levels = 0
    for zone in range(position, ZONES):
        active |= 1 << zone
        levels |= (level - 1) << (3 * zone)
    return active, levels


def trigger_feedback(position, strength):
    _byte("position", position, ZONES - 1)
    _byte("strength", strength, 8)
    if strength == 0:
        return trigger_off()
    active, levels = _zones(position, strength)
    out = bytearray(TRIGGER_SIZE)
    out[0] = TRIGGER_FEEDBACK
    out[1:3] = active.to_bytes(2, "little")
    out[3:7] = levels.to_bytes(4, "little")
    return bytes(out)


def trigger_vibration(position, amplitude, frequency):
    _byte("position", position, ZONES - 1)
    _byte("amplitude", amplitude, 8)
    _byte("frequency", frequency)
    if amplitude == 0 or frequency == 0:
        return trigger_off()
    active, levels = _zones(position, amplitude)
    out = bytearray(TRIGGER_SIZE)
    out[0] = TRIGGER_VIBRATION
    out[1:3] = active.to_bytes(2, "little")
    out[3:7] = levels.to_bytes(4, "little")
    out[9] = frequency
    return bytes(out)


def common(rumble=None, right_trigger=None, left_trigger=None, lightbar=None, player_leds=None, brightness=None,
           mic_led=None, motor_power=None):
    out = bytearray(COMMON_SIZE)
    if rumble is not None:
        left, right = rumble
        out[0] |= F0_COMPATIBLE_VIBRATION | F0_HAPTICS_SELECT
        out[OFF_RUMBLE_LEFT] = _byte("left motor", left)
        out[OFF_RUMBLE_RIGHT] = _byte("right motor", right)
    for flag, offset, effect in ((F0_RIGHT_TRIGGER, OFF_RIGHT_TRIGGER, right_trigger), (F0_LEFT_TRIGGER, OFF_LEFT_TRIGGER, left_trigger)):
        if effect is None:
            continue
        effect = bytes(effect)
        if len(effect) != TRIGGER_SIZE:
            raise ValueError("a trigger effect is %d bytes" % TRIGGER_SIZE)
        out[0] |= flag
        out[offset:offset + TRIGGER_SIZE] = effect
    if mic_led is not None:
        out[1] |= F1_MIC_MUTE_LED
        out[OFF_MIC_LED] = _byte("mic LED", mic_led, 2)
    if lightbar is not None:
        out[1] |= F1_LIGHTBAR
        for i, c in enumerate(lightbar):
            out[OFF_RED + i] = _byte("light bar colour", c)
    if player_leds is not None:
        out[1] |= F1_PLAYER_LEDS
        out[OFF_PLAYER_LEDS] = _byte("player LEDs", player_leds, 0x1F)
    if motor_power is not None:
        rumble_cut, trigger_cut = motor_power
        out[1] |= F1_MOTOR_POWER
        out[OFF_MOTOR_POWER] = (_byte("trigger reduction", trigger_cut, 7) << 4) | _byte("rumble reduction", rumble_cut, 7)
    if brightness is not None:
        out[OFF_FLAG2] |= F2_LED_BRIGHTNESS
        out[OFF_BRIGHTNESS] = _byte("brightness", brightness, 2)
    return bytes(out)


def usb_report(payload):
    if len(payload) != COMMON_SIZE:
        raise ValueError("payload must be %d bytes" % COMMON_SIZE)
    return bytes([USB_REPORT_ID]) + payload + bytes(USB_REPORT_SIZE - 1 - COMMON_SIZE)


def bt_crc(data):
    return zlib.crc32(bytes([BT_CRC_SEED]) + bytes(data)) & 0xFFFFFFFF


def bt_report(payload, seq):
    if len(payload) != COMMON_SIZE:
        raise ValueError("payload must be %d bytes" % COMMON_SIZE)
    body = bytes([BT_REPORT_ID, (seq & 0x0F) << 4, BT_TAG]) + payload
    body += bytes(BT_REPORT_SIZE - 4 - len(body))
    return body + bt_crc(body).to_bytes(4, "little")


def parse_hid_id(text):
    for line in text.splitlines():
        if line.startswith("HID_ID="):
            parts = line[len("HID_ID="):].strip().split(":")
            if len(parts) == 3:
                try:
                    return int(parts[0], 16), int(parts[1], 16), int(parts[2], 16)
                except ValueError:
                    return None
    return None


def _uevent(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        key, sep, val = line.partition("=")
        if sep:
            out[key] = val
    out["_raw"] = text
    return out


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _battery(device_dir):
    folder = os.path.join(device_dir, "power_supply")
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return None
    for name in names:
        if not name.startswith("ps-controller-battery"):
            continue
        cap = _read(os.path.join(folder, name, "capacity"))
        status = _read(os.path.join(folder, name, "status"))
        try:
            level = int(cap)
        except ValueError:
            level = None
        return {"level": level, "status": status}
    return None


def _leds(device_dir):
    try:
        return sorted(os.listdir(os.path.join(device_dir, "leds")))
    except OSError:
        return []


def find(sys_root="/sys/class/hidraw", dev_root="/dev"):
    found = []
    try:
        names = sorted(os.listdir(sys_root), key=lambda n: (len(n), n))
    except OSError:
        return found
    for name in names:
        device_dir = os.path.join(sys_root, name, "device")
        ev = _uevent(os.path.join(device_dir, "uevent"))
        ids = parse_hid_id(ev.get("_raw", ""))
        if not ids:
            continue
        bus, vendor, product = ids
        if vendor != VENDOR or product not in PRODUCTS or bus not in (BUS_USB, BUS_BT):
            continue
        node = os.path.join(dev_root, name)
        found.append({
            "id": name,
            "node": node,
            "name": ev.get("HID_NAME") or PRODUCTS[product],
            "model": "edge" if product == 0x0DF2 else "dualsense",
            "connection": "usb" if bus == BUS_USB else "bluetooth",
            "address": ev.get("HID_UNIQ", ""),
            "battery": _battery(device_dir),
            "leds": _leds(device_dir),
            "writable": os.access(node, os.W_OK),
        })
    return found


_seq = {}
_seq_lock = threading.Lock()


def report_for(pad, payload):
    if pad["connection"] == "usb":
        return usb_report(payload)
    with _seq_lock:
        seq = _seq.get(pad["node"], 0)
        _seq[pad["node"]] = (seq + 1) & 0x0F
    return bt_report(payload, seq)


def send(pad, payload, opener=os.open, writer=os.write, closer=os.close):
    report = report_for(pad, payload)
    try:
        fd = opener(pad["node"], os.O_WRONLY | getattr(os, "O_NONBLOCK", 0))
    except PermissionError:
        raise PermissionError("No permission to write " + pad["node"] + ". The udev rule 70-anyps5-controllers.rules gives the signed-in user access; unplug and reconnect the controller after installing it.")
    try:
        n = writer(fd, report)
    finally:
        closer(fd)
    if n != len(report):
        raise OSError("short write to %s (%d of %d bytes)" % (pad["node"], n, len(report)))
    return report


def lightbar_on():
    out = bytearray(common())
    out[OFF_FLAG2] |= F2_LIGHTBAR_SETUP
    out[OFF_LIGHTBAR_SETUP] = LIGHTBAR_SETUP_ON
    return bytes(out)


def pulse(pad, left, right, seconds=0.5, sleep=time.sleep, **io):
    send(pad, common(rumble=(left, right)), **io)
    sleep(seconds)
    send(pad, common(rumble=(0, 0)), **io)


def _usage():
    print("usage: dualsense.py list | rumble <0-255> [seconds] | trigger <strength 0-8> [position 0-9] | off | light <r> <g> <b> [player 0-4]", file=sys.stderr)
    return 2


def main(argv):
    pads = find()
    if not argv or argv[0] == "list":
        for p in pads:
            print("%s %s %s %s battery=%s writable=%s" % (p["node"], p["connection"], p["name"], p["address"], p["battery"], p["writable"]))
        if not pads:
            print("no DualSense connected")
        return 0
    if not pads:
        print("no DualSense connected", file=sys.stderr)
        return 1
    try:
        nums = [int(x) for x in argv[1:]]
    except ValueError:
        return _usage()
    for p in pads:
        if argv[0] == "rumble" and nums:
            pulse(p, nums[0], nums[0], nums[1] if len(nums) > 1 else 0.5)
        elif argv[0] == "trigger" and nums:
            effect = trigger_feedback(nums[1] if len(nums) > 1 else 2, nums[0])
            send(p, common(right_trigger=effect, left_trigger=effect))
        elif argv[0] == "off":
            send(p, common(rumble=(0, 0), right_trigger=trigger_off(), left_trigger=trigger_off()))
        elif argv[0] == "light" and len(nums) >= 3:
            player = PLAYER_PATTERNS.get(nums[3]) if len(nums) > 3 else None
            send(p, lightbar_on())
            time.sleep(0.3)
            send(p, common(lightbar=tuple(nums[:3]), player_leds=player))
        else:
            return _usage()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
