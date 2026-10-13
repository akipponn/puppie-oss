"""
The watcher: a program that is kept running all the time.
It starts the conversation program (puppie_main.py) ahead of time and keeps it on standby.
Each time the touch sensor (GPIO23) is touched, it sends a signal (SIGUSR1) to make
puppie_main.py start or end a conversation. While on standby, puppie_main.py keeps the microphone stopped.
The RGB LED also belongs to this program: it reads the "@@LED <state>" lines that puppie_main.py
prints to standard output and shows the state of the conversation.

  Off             Standby
  Pulsing white   Getting ready for a conversation (from the touch until ready)
  Solid green     Listening (go ahead and speak)
  Pulsing blue    Thinking
  Solid blue      Puppie is speaking
  Solid red       In trouble (no network, crashed)

gpiozero is installed in the Pi's system Python, so run this file with
/usr/bin/python3, not the .venv (puppie_main.py is started with the .venv).
"""
import os
import signal
import subprocess
import threading
import time

from gpiozero import DigitalInputDevice, RGBLED

TOUCH_PIN = 23  # physical pin 16
RED_PIN = 17    # physical pin 11
GREEN_PIN = 22  # physical pin 15
BLUE_PIN = 27   # physical pin 13
POLL_INTERVAL_SEC = 0.05

LED_MARKER = "@@LED "
PULSE_FADE_SEC = 0.8
# After a crash, keep red on for this many seconds, then go back to off (standby) and restart
ERROR_HOLD_SEC = 5

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_CMD = [os.path.join(BASE_DIR, ".venv/bin/python"), "-u", "puppie_main.py"]
# Play "just a moment" only when touched before puppie_main.py has finished loading (ten-odd seconds)
WAIT_VOICE_CMD = ["aplay", "-q", os.path.join(BASE_DIR, "Voices/vicky_wait.wav")]


# Run the cleanup in `finally` on SIGTERM too (e.g. the stop signal from systemd)
def _handle_sigterm(signum, frame):
    raise KeyboardInterrupt()

signal.signal(signal.SIGTERM, _handle_sigterm)


def is_running(proc):
    return proc is not None and proc.poll() is None


def show_led(state):
    if state == "starting":
        led.pulse(PULSE_FADE_SEC, PULSE_FADE_SEC, on_color=(1, 1, 1))
    elif state == "listening":
        led.color = (0, 1, 0)
    elif state == "thinking":
        led.pulse(PULSE_FADE_SEC, PULSE_FADE_SEC, on_color=(0, 0, 1))
    elif state == "speaking":
        led.color = (0, 0, 1)
    elif state == "error":
        led.color = (1, 0, 0)
    else:  # idle
        led.off()


# Read puppie_main.py's output line by line, pick out the LED instructions and pass the rest on to the log as is.
# The LED itself is switched in the main loop (the thread only records the wanted state).
def read_main_output(proc):
    global wanted_led, main_ready
    for line in proc.stdout:
        if line.startswith(LED_MARKER):
            wanted_led = line[len(LED_MARKER):].strip()
            if wanted_led == "idle":
                main_ready = True
        else:
            print(line, end="", flush=True)


def start_main():
    global main_proc, reader, main_ready
    main_ready = False
    main_proc = subprocess.Popen(MAIN_CMD, cwd=BASE_DIR, stdout=subprocess.PIPE, text=True, bufsize=1,
                                 env={**os.environ, "PUPPIE_STANDBY": "1"})
    reader = threading.Thread(target=read_main_output, args=(main_proc,), daemon=True)
    reader.start()


# The TTP223 has been modified to toggle mode: each touch flips it between High and Low.
# The sensor stays High even when a conversation ends on its own (goodbye / silence),
# so treat "a change" as one touch, not the level.
sensor = DigitalInputDevice(TOUCH_PIN, pull_up=False)
led = RGBLED(red=RED_PIN, green=GREEN_PIN, blue=BLUE_PIN)
last_value = sensor.value
main_proc = None
reader = None
main_ready = False  # True once puppie_main.py has finished loading and is on standby
wanted_led = "idle"
shown_led = "idle"
error_until = None

print("👀 Watcher started. Waiting for touch...", flush=True)

try:
    start_main()
    while True:
        value = sensor.value
        if value != last_value:
            last_value = value
            if is_running(main_proc):
                print("👆 Touch detected", flush=True)
                if not main_ready:
                    subprocess.Popen(WAIT_VOICE_CMD)
                # Whether to start or end a conversation is decided by puppie_main.py from its own state
                main_proc.send_signal(signal.SIGUSR1)
                if shown_led == "idle":
                    wanted_led = "starting"

        # If puppie_main.py dies, turn on red, wait a little and restart it
        if main_proc is not None and main_proc.poll() is not None:
            reader.join(timeout=1)
            print(f"⚠️ The conversation program exited (code={main_proc.returncode}). Restarting it", flush=True)
            wanted_led = "error"
            error_until = time.monotonic() + ERROR_HOLD_SEC
            main_proc = None

        if error_until is not None and time.monotonic() > error_until:
            wanted_led = "idle"
            error_until = None
            if main_proc is None:
                start_main()

        if wanted_led != shown_led:
            shown_led = wanted_led
            show_led(shown_led)

        time.sleep(POLL_INTERVAL_SEC)

except KeyboardInterrupt:
    pass

finally:
    if is_running(main_proc):
        main_proc.terminate()
        main_proc.wait()
    led.off()
    led.close()
