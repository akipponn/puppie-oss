#!/bin/bash
set -u

IFACE="wlan0"
# ふだん使うWi-Fiの、NetworkManagerでの接続名（nmcli connection show で確認できる）
HOME_CONN="YOUR-WIFI-NAME"
AP_CONN="Puppie-Setup"
STATE_DIR="/var/lib/puppie-wifi-watchdog"
FAIL_COUNT_FILE="$STATE_DIR/fail_count"
AP_LAST_TRY_FILE="$STATE_DIR/ap_last_try"
FAIL_THRESHOLD=3
AP_RETRY_INTERVAL=480

mkdir -p "$STATE_DIR"
[ -f "$FAIL_COUNT_FILE" ] || echo 0 > "$FAIL_COUNT_FILE"

check_connectivity() {
    local gw
    gw=$(ip route show default dev "$IFACE" 2>/dev/null | awk '{print $3; exit}')
    if [ -z "$gw" ]; then
        return 1
    fi
    ping -c 2 -W 2 "$gw" >/dev/null 2>&1
}

current_conn=$(nmcli -t -g GENERAL.CONNECTION device show "$IFACE" 2>/dev/null)

if [ "$current_conn" = "$AP_CONN" ]; then
    now=$(date +%s)
    last_try=$(cat "$AP_LAST_TRY_FILE" 2>/dev/null || echo 0)
    elapsed=$((now - last_try))
    if [ "$elapsed" -lt "$AP_RETRY_INTERVAL" ]; then
        exit 0
    fi
    echo "$now" > "$AP_LAST_TRY_FILE"
    echo "現在AP(${AP_CONN})モード。元のWi-Fiへの復帰を試みます。"
    nmcli connection down "$AP_CONN" >/dev/null 2>&1
    timeout 10 nmcli connection up "$HOME_CONN" >/dev/null 2>&1
    sleep 3
    if check_connectivity; then
        echo "元のWi-Fi(${HOME_CONN})に復帰しました。"
        echo 0 > "$FAIL_COUNT_FILE"
        rm -f "$AP_LAST_TRY_FILE"
    else
        echo "復帰失敗。APモードに戻します。"
        nmcli connection up "$AP_CONN" >/dev/null 2>&1
    fi
    exit 0
fi

if check_connectivity; then
    echo 0 > "$FAIL_COUNT_FILE"
    exit 0
fi

echo "疎通なし。再接続を試みます。"
timeout 10 nmcli connection up "$HOME_CONN" >/dev/null 2>&1
sleep 3

if check_connectivity; then
    echo "再接続に成功しました。"
    echo 0 > "$FAIL_COUNT_FILE"
    exit 0
fi

fail_count=$(cat "$FAIL_COUNT_FILE" 2>/dev/null || echo 0)
fail_count=$((fail_count + 1))
echo "$fail_count" > "$FAIL_COUNT_FILE"
echo "再接続失敗(連続${fail_count}回)。"

if [ "$fail_count" -ge "$FAIL_THRESHOLD" ]; then
    echo "しきい値到達。緊急APモード(${AP_CONN})を起動します。"
    nmcli connection up "$AP_CONN" >/dev/null 2>&1
fi
