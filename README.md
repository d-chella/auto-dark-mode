# auto-dark-mode

Switch Ubuntu (GNOME) between light and dark mode at sunrise and sunset.

Sets the same setting as **Settings → Appearance → Style**, so every app that
follows the system style (GTK4, GTK3 via the Yaru theme, Firefox, Chrome,
Electron, Qt) switches with it. Sunrise and sunset are computed locally with the
NOAA solar algorithm. Single Python 3 file, no extra packages, no network, no
root. Tested on Ubuntu 24.04 with GNOME 46.

## Install

```sh
./sun-theme.py install
```

This copies the script to `~/.local/bin/sun-theme`, writes
`~/.config/sun-theme/config.ini`, and enables a systemd user timer that checks
every 5 minutes and at login.

## Configure

Edit `~/.config/sun-theme/config.ini`:

```ini
[location]
# Decimal degrees, north and east positive. Filled in at install from the
# reference city of your system timezone; replace with exact values if you like.
latitude = 51.5074
longitude = -0.1278

# Everything under [light] / [dark] is applied as
#   gsettings set org.gnome.desktop.interface KEY VALUE
[light]
gtk-theme = Yaru-blue
icon-theme = Yaru-blue

[dark]
gtk-theme = Yaru-blue-dark
icon-theme = Yaru-blue-dark
```

`install` fills in the coordinates of your system timezone's reference city
(from `/usr/share/zoneinfo/zone1970.tab`). That is accurate to a few minutes of
sunrise/sunset, which is enough for a theme switch. Replace them with exact
coordinates if you want. Available themes: `ls /usr/share/themes /usr/share/icons`.

## Check

```sh
sun-theme status
```

Prints the location in use, today's sunrise and sunset, the wanted and current
mode, and the timer's next run. To apply a config change right away:

```sh
sun-theme apply --force
```

## Hooks

Executables in `~/.config/sun-theme/light.d/` and `~/.config/sun-theme/dark.d/`
run after each switch, for apps that do not follow the system style. Example
for GNOME Terminal's window chrome:

```sh
mkdir -p ~/.config/sun-theme/light.d ~/.config/sun-theme/dark.d
printf '#!/bin/sh\ngsettings set org.gnome.Terminal.Legacy.Settings theme-variant light\n' > ~/.config/sun-theme/light.d/terminal
printf '#!/bin/sh\ngsettings set org.gnome.Terminal.Legacy.Settings theme-variant dark\n' > ~/.config/sun-theme/dark.d/terminal
chmod +x ~/.config/sun-theme/*.d/terminal
```

## Uninstall

```sh
sun-theme uninstall
```

Removes the timer and `~/.local/bin/sun-theme`. The config directory is left in
place.

## Logs

```sh
journalctl --user -u sun-theme.service
```
