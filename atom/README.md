# Puppie on the M5Stack Atom VoiceS3R

A smaller Puppie: the same conversation as the Raspberry Pi version, on an M5Stack Atom VoiceS3R (an ESP32-S3 with a microphone, a speaker and a button, about 24 × 24 mm).

**Status:** experimental. It talks in Japanese for now, and some things are still fixed in the code (see [Not yet](#not-yet)). The Raspberry Pi version in the [main README](../README.md) is the complete one.

## Parts

| Part | Needed? | Notes |
|---|---|---|
| M5Stack Atom VoiceS3R | yes | Shows up on USB as "Atom EchoS3R" (its old name) |
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

`secrets.h` is not in git, so your keys stay on your computer. But they are also written into the Atom when you flash it: if you give the Atom to someone else, flash it again without your keys first.

## Flash

1. Open `atom/puppie_atom/puppie_atom.ino` in the Arduino IDE.
2. In **Tools**, choose:
   - Board: **ESP32S3 Dev Module**
   - USB CDC On Boot: **Enabled**
   - USB Mode: **Hardware CDC and JTAG**
   - PSRAM: **OPI PSRAM** (with QSPI, the Atom sees no PSRAM and Puppie cannot record)
   - Flash Size: **8MB (64Mb)**
   - Partition Scheme: **8M with spiffs (3MB APP/1.5MB SPIFFS)**
3. The first time, the Atom has no serial port yet. Hold its reset button for about 2 seconds until a green light comes on. Now a port like `/dev/cu.usbmodem…` (Mac) or `COM…` (Windows) appears; choose it in **Tools → Port**.
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
| off | waiting for a touch |

After a reply Puppie keeps listening, so you can just go on talking. The conversation ends after 30 seconds of silence, or when you touch again. A touch while Puppie is speaking stops it and lets you speak.

The Serial Monitor (115200 baud) shows what Puppie heard and answered.

## Not yet

These are planned (see the main README for the Pi version, which already has them):

- Ending the conversation when you say goodbye
- Remembering you between conversations
- A settings page on your phone for Wi-Fi, city and API key (now they are in `secrets.h` and in the code)
- The weather is always for Leipzig (`HOME_LATITUDE` / `HOME_LONGITUDE` in the code)

## Safety and privacy

The [Safety](../README.md#safety) section of the main README applies here too. Your voice and the conversation are sent to OpenAI, the same as with the Pi version (see [Privacy](../README.md#privacy)). The Atom version keeps no conversation log and no memory yet.
