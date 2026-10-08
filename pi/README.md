# Raspberry Pi側の設定ファイル

KIKOを動かしているRaspberry Pi Zero 2W(64-bit OS)に入れてある設定の控えです。
プログラム本体(リポジトリ直下の `.py`)は `/home/<ユーザー名>/puppie/` に置いています。

## 音の入出力 `asound.conf`

置き場所: `/etc/asound.conf`

スピーカー(I2SアンプのMAX98357A)とUSBマイクを、ALSAの既定の出力・入力に固定します。
これがないと、Pi Zeroには無いヘッドホン端子に音が出て無音になります。

あわせて `/boot/firmware/config.txt` に次の設定が必要です(再起動後に有効)。

```
dtparam=i2s=on
dtoverlay=max98357a
```

## Wi-Fiが切れたときの非常口 `wifi-watchdog/`

ふだんのWi-Fiにつながらない状態が続くと、Pi自身がWi-Fiの親機 `Puppie-Setup` になります。
そこにパソコンをつなげば `ssh <ユーザー名>@10.42.0.1` で入って直せます。

- 1分30秒ごとにつながっているか確かめ、切れていたらつなぎ直す
- 3回続けて失敗したら `Puppie-Setup` を出す
- `Puppie-Setup` を出している間は、8分に1回だけ元のWi-Fiへ戻れるか試す(頻繁に試すと、その間は親機が消えてつなげなくなるため)

| ファイル | 置き場所 |
|---|---|
| `puppie-wifi-watchdog.sh` | `/usr/local/sbin/`(実行権限を付ける) |
| `puppie-wifi-watchdog.service` | `/etc/systemd/system/` |
| `puppie-wifi-watchdog.timer` | `/etc/systemd/system/` |

入れる手順:

1. `puppie-wifi-watchdog.sh` の `HOME_CONN` を、ふだん使うWi-Fiの接続名に書き換える
2. 親機用の接続を作る(パスワードは自分で決める。8文字以上)

   ```bash
   sudo nmcli connection add type wifi ifname wlan0 con-name Puppie-Setup autoconnect no \
     ssid Puppie-Setup wifi.mode ap wifi.band bg ipv4.method shared \
     wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<パスワード>"
   ```

3. タイマーを有効にする

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now puppie-wifi-watchdog.timer
   ```

動いているかは `journalctl -u puppie-wifi-watchdog.service` で見られます。

手順2のコマンドは、動いているPiの設定内容から書き起こしたもので、この形のまま実行して確かめてはいません。
