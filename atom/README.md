# Puppie on the M5Stack Atom VoiceS3R

A smaller Puppie: the same conversation as the Raspberry Pi version, on an M5Stack Atom VoiceS3R (an ESP32-S3 with a microphone, a speaker and a button, about 24 × 24 mm).

**Status:** experimental. It talks in Japanese for now, and some things are still fixed in the code (see [Not yet](#not-yet)). The Raspberry Pi version in the [main README](../README.md) is the complete one.

## Parts

| Part | Needed? | Notes |
|---|---|---|
| [M5Stack Atom VoiceS3R](https://docs.m5stack.com/en/core/Atom_VoiceS3R) | yes | Shows up on USB as "Atom EchoS3R" (its old name) |
| USB-C cable (data, not charge-only) | yes | For flashing and for power |
| M5Stack RGB LED Unit (3 LEDs) + Grove cable | optional | Shows what Puppie is doing |
| Copper tape and a wire | optional | A touch spot, e.g. behind wood. Without it, use the button |

## Wiring

- **LED:** plug the RGB LED Unit into the Grove port (data on G2).
- **Touch:** connect a piece of copper tape to **G8** with a wire. Puppie measures the touch spot when it starts, so do not touch it during startup.

## Install the software

1. Install the [Arduino IDE](https://www.arduino.cc/en/software) (tested with 2.3).
2. In **Boards Manager**, install **esp32** by Espressif Systems (tested with 3.3.12).
3. In **Library Manager**, install **M5Unified** (tested with 0.2.29; it also installs M5GFX).

## Add your keys

In the `puppie_atom` folder, copy `secrets.h.example` to `secrets.h` and fill in your OpenAI API key and your Wi-Fi name and password. The Atom only uses 2.4 GHz Wi-Fi.

`PUPPIE_LANGUAGE` sets the language of Puppie's few fixed sentences (like "Shall I forget everything?"): `"de"`, `"en"` or `"ja"`. Without it, they are in German.

`secrets.h` is not in git, so your keys stay on your computer. But they are also written into the Atom when you flash it: if you give the Atom to someone else, [erase it](#erase-everything) first.

## Flash

1. Open `atom/puppie_atom/puppie_atom.ino` in the Arduino IDE.
2. In **Tools**, choose:
   - Board: **ESP32S3 Dev Module**
   - USB CDC On Boot: **Enabled**
   - USB Mode: **Hardware CDC and JTAG**
   - PSRAM: **OPI PSRAM** (with QSPI, the Atom sees no PSRAM and Puppie cannot record)
   - Flash Size: **8MB (64Mb)**
   - Partition Scheme: **8M with spiffs (3MB APP/1.5MB SPIFFS)**
3. The first time, the Atom has no serial port yet. Hold its reset button for about 2 seconds until a green light comes on. (M5Stack's [Arduino guide](https://docs.m5stack.com/en/arduino/atom_echos3r/program) shows this too.) Now a port like `/dev/cu.usbmodem…` (Mac) or `COM…` (Windows) appears; choose it in **Tools → Port**.
4. Click **Upload**.

The same with `arduino-cli`:

```bash
arduino-cli compile --upload -p /dev/cu.usbmodem1101 --fqbn "esp32:esp32:esp32s3:CDCOnBoot=cdc,USBMode=hwcdc,PSRAM=opi,FlashSize=8M,PartitionScheme=default_8MB" atom/puppie_atom
```

## Talk to Puppie

Touch the copper tape (or press the button) and speak. The LED shows what is happening:

| LED | Meaning |
|---|---|
| white, fading | starting up |
| green | listening |
| blue, fading | thinking |
| blue | speaking |
| red | something went wrong |
| white | setup mode (see [Settings page](#settings-page)) |
| off | waiting for a touch |

After a reply Puppie keeps listening, so you can just go on talking. The conversation ends after 30 seconds of silence, when you touch again, or when you say goodbye (bye, see you, good night, Tschüss, バイバイ, おやすみ …; Puppie still answers first). A touch while Puppie is speaking stops it and lets you speak.

The Serial Monitor (115200 baud) shows what Puppie heard and answered.

## Settings page

You can change Puppie's settings from a smartphone or a computer, without flashing again: the Wi-Fi, the home town for the weather, and how long Puppie keeps listening when nobody speaks.

1. Keep the button on top pressed, and press the reset button on the side briefly (or plug in USB while you hold the button). The LED stays white without fading; then let go of the button. (White and fading means it started normally: try again.)
2. On your smartphone or computer, join the Wi-Fi **Puppie-Setup** (no password). The settings page opens by itself. If it does not, open `http://192.168.4.1` in the browser.
3. Change what you want and tap **Save**. The Atom restarts and uses the new settings.

| Setting | Notes |
|---|---|
| Wi-Fi name and password | 2.4 GHz only. Leave the password empty to keep the saved one |
| Home town | For the weather, e.g. `Leipzig` or `Tokyo` (Latin letters are safest). Empty: Leipzig |
| Seconds of silence | Puppie stops listening after this time, 10 to 600 (default 30) |

The settings are kept in flash. The Wi-Fi from the page is used instead of `WIFI_NAME` / `WIFI_PASSWORD` in `secrets.h`. Puppie-Setup has no password, so only use setup mode for the few minutes you need it.

If the page does not open by itself even though you are connected: your device may remember Puppie-Setup from an earlier time. Open `http://192.168.4.1` in the browser, or forget Puppie-Setup in your Wi-Fi settings and join it again.

## Memory

When a conversation ends, Puppie writes a short note about you: facts you told it about yourself, like what you do not eat or where you live. The note is kept in the Atom's flash, so it is still there after the power was off, and it goes along with the next conversations.

**To make Puppie forget:** touch, and keep your finger on the touch spot for 5 seconds. Puppie stops the conversation and asks whether to forget everything. Touch once more to say yes. If you do not touch within 10 seconds, the note is kept. Only the note is erased: Wi-Fi, keys and everything else stay as they are.

You cannot see the note on the Atom yet (only in the Serial Monitor, as `MEMORY_RESULT`). Seeing it on a settings page is planned.

Puppie's fixed sentences (the question above and its answers) are made by OpenAI the first time they are needed and then kept in flash too, so after that they play at once, also without internet.

## Erase everything

To remove the note, the saved sentences and your keys and Wi-Fi from the Atom (for example before you give it away), erase its whole flash. `esptool` comes with the esp32 boards package (on a Mac in `~/Library/Arduino15/packages/esp32/tools/esptool_py/<version>/`), or install it with `pip install esptool`:

```bash
esptool --port /dev/cu.usbmodem1101 erase_flash
```

If it cannot connect, hold the reset button for about 2 seconds first (as in [Flash](#flash), step 3). After this the Atom does nothing until you flash it again.

## Not yet

These are planned (see the main README for the Pi version, which already has them):

- The API key on the settings page (now it is in `secrets.h`)
- Seeing the saved note (and forgetting it) on that settings page

## Safety and privacy

The [Safety](../README.md#safety) section of the main README applies here too. Your voice and the conversation are sent to OpenAI, the same as with the Pi version (see [Privacy](../README.md#privacy)).

The Atom keeps no recordings and no conversation log. It does keep the short [note about you](#memory) in its flash, and that note is sent to OpenAI with each conversation and when it is updated. If other people talk to Puppie too, tell them that Puppie remembers what they say about themselves, and how to make it forget.
