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

1. Put your OpenAI API key and your city into `.env`.
2. Put your AWS credentials into `~/.aws/credentials` (Polly reads them from there, not from `.env`).
3. Start one conversation by hand:

   ```bash
   python3 puppie_main.py
   ```

To make Puppie start on power-up and wait for a touch, and to set up the speaker and microphone, see [pi/README.md](pi/README.md).

## Hardware

TODO: parts list for the Pi Zero 2 W version.

## Privacy

TODO: what Puppie stores (memory, conversation logs) and what it sends to external services.

## Partner

<!-- TODO: add the "Sachsen 1100 – So geht sächsisch." logo here, unchanged, from the logo package -->

Puppie is a prize winner of "Zeig Dein Sachsen", the participation competition of the Free State of Saxony's campaign "So geht sächsisch." for the anniversary "1100 Jahre Sachsen", and is being realised as a partner project of the campaign.

- [www.so-geht-sächsisch.de](https://www.so-geht-sächsisch.de)
- [www.sachsen1100.de](https://www.sachsen1100.de)

The "Sachsen 1100 – So geht sächsisch." logo is a protected trademark of the Free State of Saxony and is not covered by this project's open-source license.

TODO: KIKO was supported by ...

## License

TODO: not decided yet.
