#!/usr/bin/env python3
"""Switch GNOME between light and dark mode at sunrise and sunset.

Sunrise/sunset are computed locally (NOAA algorithm, accurate to ~1 min).
No network access, no extra packages, no root.

Usage:
  auto-dark-mode apply [--force]  set light/dark for the current time (default)
  auto-dark-mode status           show today's sunrise/sunset and current mode
  auto-dark-mode install          copy to ~/.local/bin, write config + systemd timer
  auto-dark-mode uninstall        remove the timer and the copy in ~/.local/bin

Config: ~/.config/auto-dark-mode/config.ini (created by `install`).
Hooks:  executables in ~/.config/auto-dark-mode/light.d/ and dark.d/ run after a switch.
"""
import configparser
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "auto-dark-mode"
CONFIG = Path(os.environ.get("AUTO_DARK_MODE_CONFIG", CONFIG_DIR / "config.ini"))
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
BIN = Path.home() / ".local" / "bin" / "auto-dark-mode"
SCHEMA = "org.gnome.desktop.interface"

DEFAULT_CONFIG = """\
[location]
# Decimal degrees, north and east positive. Filled in at install from the
# reference city of your system timezone; replace with exact values if you like.
latitude = {lat:.4f}
longitude = {lon:.4f}

# Everything under [light] / [dark] is applied as
#   gsettings set org.gnome.desktop.interface KEY VALUE
# List themes with: ls /usr/share/themes /usr/share/icons
[light]
gtk-theme = Yaru-blue
icon-theme = Yaru-blue

[dark]
gtk-theme = Yaru-blue-dark
icon-theme = Yaru-blue-dark
"""

SERVICE = """\
[Unit]
Description=Switch GNOME light/dark mode by sunrise and sunset

[Service]
Type=oneshot
ExecStart=%h/.local/bin/auto-dark-mode apply
"""

TIMER = """\
[Unit]
Description=Check every 5 minutes whether to switch light/dark mode

[Timer]
OnStartupSec=15s
OnCalendar=*:0/5
AccuracySec=30s

[Install]
WantedBy=timers.target
"""


def sun_times(lat, lon, day):
    """Sunrise and sunset (aware UTC datetimes) for `day` at lat/lon.

    Returns (rise, set), or a bool when the sun never crosses the horizon
    that day: True for midnight sun, False for polar night.
    """
    rad, deg = math.radians, math.degrees
    jd = day.toordinal() + 1721424.5          # Julian day at 0h UT
    t = (jd - 2451545.0) / 36525.0            # Julian centuries since J2000
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    m = rad(357.52911 + t * (35999.05029 - 0.0001537 * t))
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    c = (math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + math.sin(2 * m) * (0.019993 - 0.000101 * t)
         + math.sin(3 * m) * 0.000289)
    omega = rad(125.04 - 1934.136 * t)
    lam = rad(l0 + c - 0.00569 - 0.00478 * math.sin(omega))
    eps0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    eps = rad(eps0 + 0.00256 * math.cos(omega))
    decl = math.asin(math.sin(eps) * math.sin(lam))
    y = math.tan(eps / 2) ** 2
    l0r = rad(l0)
    eqt = 4 * deg(y * math.sin(2 * l0r) - 2 * e * math.sin(m)
                  + 4 * e * y * math.sin(m) * math.cos(2 * l0r)
                  - 0.5 * y * y * math.sin(4 * l0r)
                  - 1.25 * e * e * math.sin(2 * m))
    latr = rad(lat)
    cos_ha = (math.cos(rad(90.833)) / (math.cos(latr) * math.cos(decl))
              - math.tan(latr) * math.tan(decl))
    if cos_ha > 1:
        return False
    if cos_ha < -1:
        return True
    ha = deg(math.acos(cos_ha))
    noon = 720 - 4 * lon - eqt                # minutes after 0h UT
    midnight = datetime.combine(day, time(), tzinfo=timezone.utc)
    return (midnight + timedelta(minutes=noon - 4 * ha),
            midnight + timedelta(minutes=noon + 4 * ha))


def iso6709(part, deg_len):
    """'+5130' -> 51.5; degrees, minutes, optional seconds."""
    sign = -1 if part[0] == "-" else 1
    d = part[1:]
    deg, minutes, sec = d[:deg_len], d[deg_len:deg_len + 2], d[deg_len + 2:] or "0"
    return sign * (int(deg) + int(minutes) / 60 + int(sec) / 3600)


def coords_from_timezone():
    """Coordinates of the system timezone's reference city, from zone1970.tab."""
    tz = os.path.realpath("/etc/localtime").split("/zoneinfo/", 1)[-1]
    for line in open("/usr/share/zoneinfo/zone1970.tab"):
        fields = line.rstrip("\n").split("\t")
        if not line.startswith("#") and fields[2] == tz:
            lat, lon = re.match(r"([+-]\d+)([+-]\d+)", fields[1]).groups()
            return iso6709(lat, 2), iso6709(lon, 3), f"timezone {tz}"
    sys.exit(f"timezone {tz} not found in zone1970.tab; set latitude/longitude in {CONFIG}")


def location(cfg):
    if cfg.has_option("location", "latitude"):
        return (cfg.getfloat("location", "latitude"),
                cfg.getfloat("location", "longitude"), "config")
    return coords_from_timezone()


def load_config():
    if not CONFIG.exists():
        sys.exit(f"{CONFIG} not found. Run `auto-dark-mode install` or create it.")
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG)
    return cfg


def is_day(cfg, now):
    lat, lon, _ = location(cfg)
    times = sun_times(lat, lon, now.astimezone().date())
    if isinstance(times, bool):
        return times, times
    rise, sset = times
    return rise <= now < sset, times


def gsettings(*args):
    return subprocess.run(["gsettings", *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def apply(cfg, force=False):
    now = datetime.now(timezone.utc)
    day, _ = is_day(cfg, now)
    mode = "light" if day else "dark"
    wanted = f"prefer-{mode}"
    current = gsettings("get", SCHEMA, "color-scheme").strip("'")
    if current == wanted and not force:
        return
    print(f"switching to {mode} mode")
    gsettings("set", SCHEMA, "color-scheme", wanted)
    for key, value in cfg.items(mode):
        gsettings("set", SCHEMA, key, value)
    hook_dir = CONFIG_DIR / f"{mode}.d"
    if hook_dir.is_dir():
        for hook in sorted(hook_dir.iterdir()):
            if os.access(hook, os.X_OK):
                subprocess.run([str(hook)])


def status(cfg):
    now = datetime.now(timezone.utc)
    day, times = is_day(cfg, now)
    lat, lon, source = location(cfg)
    fmt = "%Y-%m-%d %H:%M %Z"
    print(f"location: {lat:.2f}, {lon:.2f} (from {source})")
    if isinstance(times, bool):
        print("sun does not rise/set today:", "midnight sun" if times else "polar night")
    else:
        print("sunrise:", times[0].astimezone().strftime(fmt))
        print("sunset: ", times[1].astimezone().strftime(fmt))
    print("now:    ", now.astimezone().strftime(fmt))
    print("wanted: ", "light" if day else "dark")
    print("current:", gsettings("get", SCHEMA, "color-scheme"), flush=True)
    subprocess.run(["systemctl", "--user", "--no-pager", "list-timers", "auto-dark-mode.timer"])


def install():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG.exists():
        lat, lon, source = coords_from_timezone()
        CONFIG.write_text(DEFAULT_CONFIG.format(lat=lat, lon=lon))
        print(f"wrote {CONFIG} with location {lat:.2f}, {lon:.2f} from {source}")
    BIN.parent.mkdir(parents=True, exist_ok=True)
    if Path(__file__).resolve() != BIN.resolve():
        shutil.copy(__file__, BIN)
    BIN.chmod(0o755)
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    (UNIT_DIR / "auto-dark-mode.service").write_text(SERVICE)
    (UNIT_DIR / "auto-dark-mode.timer").write_text(TIMER)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "auto-dark-mode.timer"], check=True)
    apply(load_config(), force=True)
    print("installed; check with: auto-dark-mode status")


def uninstall():
    subprocess.run(["systemctl", "--user", "disable", "--now", "auto-dark-mode.timer"])
    for name in ("auto-dark-mode.service", "auto-dark-mode.timer"):
        (UNIT_DIR / name).unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    BIN.unlink(missing_ok=True)
    print(f"removed timer and {BIN}; config left in {CONFIG_DIR}")


if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args else "apply"
    if cmd == "apply":
        apply(load_config(), force="--force" in args)
    elif cmd == "status":
        status(load_config())
    elif cmd == "install":
        install()
    elif cmd == "uninstall":
        uninstall()
    else:
        sys.exit(__doc__)
