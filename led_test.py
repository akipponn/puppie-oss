"""
RGB LEDの点灯テスト。赤→緑→青→白の順に2秒ずつ光らせる。

gpiozeroはPiのシステムPythonに入っているので、.venvではなく
/usr/bin/python3 で動かす。

このLEDはカソードコモン(共通脚=いちばん長い脚をGNDにつなぐ)。

  python3 led_test.py
"""
import time

from gpiozero import RGBLED

RED_PIN = 17    # 物理ピン11番
GREEN_PIN = 22  # 物理ピン15番
BLUE_PIN = 27   # 物理ピン13番
HOLD_SEC = 2

led = RGBLED(red=RED_PIN, green=GREEN_PIN, blue=BLUE_PIN)

COLORS = [
    ("赤", (1, 0, 0)),
    ("緑", (0, 1, 0)),
    ("青", (0, 0, 1)),
    ("白", (1, 1, 1)),
]

try:
    for name, color in COLORS:
        print(f"💡 {name}", flush=True)
        led.color = color
        time.sleep(HOLD_SEC)
    print("消灯します", flush=True)
finally:
    led.off()
    led.close()
