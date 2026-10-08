"""
常時起動しておく見張りプログラム。
おしゃべりプログラム(puppie_main.py)を先に起動して待機させておき、
タッチセンサー(GPIO23)がタッチされるたびに合図(SIGUSR1)を送って
会話を始めさせる/終わらせる。待機中のpuppie_main.pyはマイクを止めている。
RGB LEDもここで持ち、puppie_main.pyが標準出力に出す「@@LED 状態」の行を
読み取って会話の状態を表示する。

  消灯          待機中
  白の明滅      会話の準備中(タッチ直後〜準備完了)
  緑の点灯      聞いている(どうぞ話して)
  青の明滅      考え中
  青の点灯      Puppieが話している
  赤の点灯      困っている(ネット切れ、異常終了)

gpiozeroはPiのシステムPythonに入っているので、このファイルは
.venvではなく /usr/bin/python3 で動かす(puppie_main.pyは.venvで起動する)。
"""
import os
import signal
import subprocess
import threading
import time

from gpiozero import DigitalInputDevice, RGBLED

TOUCH_PIN = 23  # 物理ピン16番
RED_PIN = 17    # 物理ピン11番
GREEN_PIN = 22  # 物理ピン15番
BLUE_PIN = 27   # 物理ピン13番
POLL_INTERVAL_SEC = 0.05

LED_MARKER = "@@LED "
PULSE_FADE_SEC = 0.8
# 異常終了したとき、赤をこの秒数だけ点けてから消灯(待機)に戻し、起動し直す
ERROR_HOLD_SEC = 5

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_CMD = [os.path.join(BASE_DIR, ".venv/bin/python"), "-u", "puppie_main.py"]
# puppie_main.pyの読み込み(十数秒)が終わる前にタッチされたときだけ「ちょっと待ってね」を流す
WAIT_VOICE_CMD = ["aplay", "-q", os.path.join(BASE_DIR, "Voices/vicky_wait.wav")]


# SIGTERM（systemdの停止シグナルなど）でもfinallyの後始末を通す
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


# puppie_main.pyの出力を1行ずつ読み、LEDの指示だけ抜き取って残りはそのままログに流す。
# LED自体はメインループ側で切り替える(スレッドからは希望の状態を書き残すだけ)。
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


# TTP223はトグル式に改造済みで、タッチのたびにHigh/Lowが反転する。
# 会話が自分で終わって(さよなら/無発話)もセンサーはHighのまま残るので、
# レベルではなく「変化したら1タッチ」として扱う。
sensor = DigitalInputDevice(TOUCH_PIN, pull_up=False)
led = RGBLED(red=RED_PIN, green=GREEN_PIN, blue=BLUE_PIN)
last_value = sensor.value
main_proc = None
reader = None
main_ready = False  # puppie_main.pyが読み込みを終えて待機に入ったらTrue
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
                print("👆 タッチ検知", flush=True)
                if not main_ready:
                    subprocess.Popen(WAIT_VOICE_CMD)
                # 会話を始めるか終えるかはpuppie_main.py側が自分の状態で決める
                main_proc.send_signal(signal.SIGUSR1)
                if shown_led == "idle":
                    wanted_led = "starting"

        # puppie_main.pyが落ちたら赤を点け、少し待って起動し直す
        if main_proc is not None and main_proc.poll() is not None:
            reader.join(timeout=1)
            print(f"⚠️ おしゃべりプログラムが終了しました (code={main_proc.returncode})。起動し直します", flush=True)
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
