# Puppie

Puppie is an open-source software and hardware module that lets toys and other objects hold a spoken conversation.

It started as the module inside KIKO, a wooden companion robot for older people made by Bona Mondo, which was built around a Raspberry Pi 4. KIKO itself has since moved to a smartphone-based design. With Puppie, Akiko ([@akipponn](https://github.com/akipponn)) continues the Raspberry Pi direction of that work: the module has been made smaller (it now runs on a Raspberry Pi Zero 2 W) and is released as open source.

## Status

In development. Things will change, and the setup has so far only been tested on our own hardware.

## How it works

You touch the sensor, Puppie listens, and it answers out loud. Touch it again to end the conversation.

- Speech to text and the conversation itself: OpenAI API
- Text to speech: Amazon Polly
- Weather forecast: Open-Meteo (no API key needed)

Puppie needs an internet connection, and you need your own API keys for OpenAI and AWS. The conversation language is currently set up for German.

## Quick start

On a Raspberry Pi Zero 2 W with Raspberry Pi OS Lite (64-bit):

```bash
sudo apt install -y portaudio19-dev python3-dev build-essential
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then:

1. Put your OpenAI API key and your city into `.env`. If you like, also give your Puppie its own name and role there (`PUPPIE_PERSONA`). All settings are listed under [Settings](#settings).
2. Put your AWS credentials into `~/.aws/credentials` (Polly reads them from there, not from `.env`).
3. Start one conversation by hand:

   ```bash
   python3 puppie_main.py
   ```

To make Puppie start on power-up and wait for a touch, and to set up the speaker and microphone, see [pi/README.md](pi/README.md).

## Settings

All settings live in `.env` (start from `.env.example`). Only the first two need to be filled in; the rest are optional and fall back to the default shown.

| Setting | Default | What it does |
|---|---|---|
| `OPENAI_API_KEY` | (required) | Your OpenAI API key, used for speech to text and the conversation. |
| `PUPPIE_HOME_CITY` | `Leipzig` | The city used for the weather forecast when you do not name one. |
| `PUPPIE_PERSONA` | `Dein Name ist Puppie. Du bist ein freundlicher Alltagsbegleiter und unterhältst dich gern mit allen.` | Who your Puppie is (its name and role), written in the language it speaks. |
| `PUPPIE_GPT_MODEL` | `gpt-5.4-nano` | The conversation model. `gpt-5.4-mini` gives smarter replies but is a little slower and costs more. |
| `PUPPIE_STT_LANGUAGE` | `de` | The language Puppie listens for (e.g. `en`, `ja`). Leave it empty for automatic detection, but short utterances may then be taken for another language. |
| `PUPPIE_VOLUME` | `0.7` | Speaker volume, from `0.0` to `1.0`. |
| `PUPPIE_IDLE_TIMEOUT_SEC` | `180` | Puppie ends the conversation quietly after this many seconds without anyone speaking. |
| `PUPPIE_DEBUG` | `0` | `1` saves every conversation to `log_puppie_conversation.csv` and shows what was said in the log (`journalctl`). Keep it at `0` unless you are fixing a problem. |

Amazon Polly does not use `.env`; it reads your AWS credentials from `~/.aws/credentials`.

## Hardware

This is what our own Puppie is built from. Other parts with the same specs should work too.

| Part | Qty | Notes |
|---|---|---|
| Raspberry Pi Zero 2 W with soldered pin header | 1 | |
| microSD card, 16 GB or larger | 1 | We use 32 GB. |
| 5 V micro USB power supply | 1 | 2.5 A recommended. A power bank works too. |
| [USB mini microphone](https://www.berrybase.de/usb-mini-mikrofon) | 1 | |
| Micro USB to USB-A adapter (OTG) | 1 | For the microphone. |
| MAX98357A I2S amplifier board | 1 | |
| [Speaker, 3 W, 4 Ω](https://www.berrybase.de/mono-gehaeuselautsprecher-3w-4-ohm) | 1 | |
| TTP223 touch sensor | 1 | Set to toggle mode by bridging the jumper pad on the board. |
| RGB LED, common cathode | 1 | |
| 220 Ω resistor | 3 | One for each LED color. |
| Breadboard and jumper wires | | |

### Wiring

| Part | Pin on the part | Pi pin (physical) | GPIO |
|---|---|---|---|
| Amplifier | VIN | 2 | 5 V |
| Amplifier | GND | 6 | GND |
| Amplifier | SD | 7 | GPIO4 |
| Amplifier | BCLK | 12 | GPIO18 |
| Amplifier | LRC | 35 | GPIO19 |
| Amplifier | DIN | 40 | GPIO21 |
| Touch sensor | VCC | 1 | 3.3 V |
| Touch sensor | GND | 9 | GND |
| Touch sensor | I/O | 16 | GPIO23 |
| RGB LED | Red (through a resistor) | 11 | GPIO17 |
| RGB LED | Blue (through a resistor) | 13 | GPIO27 |
| RGB LED | Green (through a resistor) | 15 | GPIO22 |
| RGB LED | Common (longest leg) | 14 | GND |

The speaker connects to the amplifier's + and − terminals, and the microphone goes into the Pi's USB port through the adapter. The audio setup on the Pi is described in [pi/README.md](pi/README.md).

## Privacy

Puppie sends your conversations to the external services you signed up for yourself. Nothing is sent to us, the people who made Puppie.

### What is sent out

| Sent to | What | When |
|---|---|---|
| OpenAI | A recording of your voice | Each time you speak (to turn it into text) |
| OpenAI | The conversation as text and Puppie's memory | Each time you speak (to come up with a reply) |
| OpenAI | The text of that conversation and the memory so far | When a conversation ends (to update the memory) |
| Amazon Polly | The text of Puppie's reply | Each reply (to turn it into speech) |
| Open-Meteo | A city name | Only when you ask about the weather |

How these services handle the data depends on their own terms.

### What stays on Puppie (the Raspberry Pi)

- **Memory** (`puppie_memory.txt`): after each conversation, Puppie sums up what is worth remembering as short bullet points, such as names, family and friends, likes, how you are feeling and your plans. It is not encrypted and holds at most 30 lines.
- **The last reply as audio** (`reply.wav`): overwritten with every reply.
- **Only in debug mode** (`PUPPIE_DEBUG=1`): the full conversation is written to `log_puppie_conversation.csv` and to the system log. Debug mode is off by default.

**Recordings of your voice are not saved on the Pi.**

### The microphone

Puppie starts listening when you touch the sensor. If nobody speaks for a set time after Puppie's last reply, it stops listening and goes back to standby. The time is 3 minutes by default and can be changed with `PUPPIE_IDLE_TIMEOUT_SEC`. Touching the sensor again or saying "Tschüss" also ends the conversation.

### Making Puppie forget

Delete `puppie_memory.txt` and Puppie forgets everything. It is a plain text file, so you can also open it and remove only some lines.

### When others use your Puppie

Tell the people who use it about the points above, especially that the memory can hold things like their health or family, and that their conversations are sent to external services.

## Partner

<a href="https://www.sachsen1100.de"><img src="assets/sachsen1100-so-geht-saechsisch_gruen.png" alt="Sachsen 1100 – So geht sächsisch." height="110"></a>
&nbsp;&nbsp;&nbsp;
<a href="https://www.so-geht-sächsisch.de"><img src="assets/sachsen1100-zeig-dein-sachsen_gruen.png" alt="1100 – Zeig Dein Sachsen" height="110"></a>

Puppie is a prize winner of "Zeig Dein Sachsen", the participation competition of the Free State of Saxony's campaign "So geht sächsisch." for the anniversary "1100 Jahre Sachsen", and is being realised as a partner project of the campaign.

- [www.so-geht-sächsisch.de](https://www.so-geht-sächsisch.de)
- [www.sachsen1100.de](https://www.sachsen1100.de)

The "Sachsen 1100 – So geht sächsisch." logo is a protected trademark of the Free State of Saxony and is not covered by this project's open-source license.

## Acknowledgements

Puppie exists because Bona Mondo UG made its first development possible as part of the KIKO project, and hosts this repository. Thank you.

KIKO ("KIKO – Persönlicher K.I.-Chatbot für Seniorinnen und Senioren", Bona Mondo UG, July 2024 to April 2025) was funded as one of the [model projects of the Zukunftsplattform für soziale Innovationen](https://www.soziales.sachsen.de/gefoerderte-modellvorhaben.html), a programme of the Saxon State Ministry for Social Affairs.

## License

Puppie is released under the [MIT License](LICENSE).
