# Raspberry Pi configuration files

A copy of the settings installed on the Raspberry Pi Zero 2W (64-bit OS) that runs Puppie.
The program itself (the `.py` files at the top of this repository) lives in `/home/<user name>/puppie/`.

## Audio input and output: `asound.conf`

Location: `/etc/asound.conf`

Makes the speaker (the MAX98357A I2S amplifier) and the USB microphone the default ALSA output and input.
Without it, sound goes to a headphone jack that the Pi Zero does not have, and you hear nothing.

You also need these lines in `/boot/firmware/config.txt` (they take effect after a reboot).

```
dtparam=i2s=on
dtoverlay=max98357a
```

## Start the watcher on power-up: `watcher/`

When the Pi is powered on, the watcher program `puppie_watcher.py` starts by itself and waits for a touch.
If the watcher stops, it is started again after 5 seconds.

| File | Location |
|---|---|
| `puppie-watcher.service` | `/etc/systemd/system/` |

To install (on the Pi, in `~/puppie`):

1. Copy the file into place. This also replaces `YOUR-USER-NAME` (2 places) with your user name (`$USER`).

   ```bash
   sed "s/YOUR-USER-NAME/$USER/g" pi/watcher/puppie-watcher.service | sudo tee /etc/systemd/system/puppie-watcher.service
   ```

2. Enable it

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now puppie-watcher.service
   ```

3. After about 30 seconds, touch the sensor. The LED pulses white, then turns green: go ahead and speak.

The log shows many `ALSA lib ...` and `jack server is not running` lines at start. They are harmless; the watcher is ready when it shows `Standby. Waiting for a touch...`.

To stop it, start it, or watch what it is doing:

```bash
sudo systemctl stop puppie-watcher.service
sudo systemctl start puppie-watcher.service
journalctl -u puppie-watcher.service -f
```

To turn off the automatic start, run `sudo systemctl disable --now puppie-watcher.service`.

If you start `python3 puppie_watcher.py` by hand while the service is running, the two fight over the touch sensor and the LED and neither works properly.
Run `stop` first when you want to start it by hand.

Tested on the Pi: after a reboot the watcher started by itself and waited for a touch (2026-10-09; again on a fresh SD card with the steps above, 2026-10-29).

The service also sets the microphone gain each time it starts, because the gain goes back to 0 after a reboot.
The line is `ExecStartPre=-/usr/bin/amixer -q -c Device sset Mic 8` (the range is 0 to 16).
`Device` is the card name of the USB microphone, the same name used in `asound.conf`.
If your microphone has another card name or control name, check with `arecord -l` and `amixer -c <card name> scontents`, and change the line.

## Emergency way in when Wi-Fi is lost: `wifi-watchdog/`

If the Pi cannot reach its usual Wi-Fi for a while, it becomes a Wi-Fi access point named `Puppie-Setup`.
Connect your computer to it and you can log in with `ssh <user name>@10.42.0.1` to fix things.

- Every 1 minute 30 seconds it checks the connection and reconnects if it is down
- After 3 failures in a row it brings up `Puppie-Setup`
- While `Puppie-Setup` is up, it tries the usual Wi-Fi only once every 8 minutes (each try takes the access point down for a moment, so trying often would make it hard to connect)

| File | Location |
|---|---|
| `puppie-wifi-watchdog.sh` | `/usr/local/sbin/` (make it executable) |
| `puppie-wifi-watchdog.service` | `/etc/systemd/system/` |
| `puppie-wifi-watchdog.timer` | `/etc/systemd/system/` |

To install:

1. In `puppie-wifi-watchdog.sh`, set `HOME_CONN` to the connection name of your usual Wi-Fi
2. Create the connection for the access point (choose your own password, 8 characters or more)

   ```bash
   sudo nmcli connection add type wifi ifname wlan0 con-name Puppie-Setup autoconnect no \
     ssid Puppie-Setup wifi.mode ap wifi.band bg ipv4.method shared \
     wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<password>"
   ```

3. Enable the timer

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now puppie-wifi-watchdog.timer
   ```

To see whether it is running, use `journalctl -u puppie-wifi-watchdog.service`.

Tested on a fresh SD card (2026-10-29): with the phone hotspot off, `Puppie-Setup` came up after about 5 minutes and `ssh` to `10.42.0.1` worked; with the hotspot back on, the Pi returned to it by itself. The [Quick start](../README.md#set-up-the-emergency-wi-fi) has the same steps as ready-to-paste commands.
