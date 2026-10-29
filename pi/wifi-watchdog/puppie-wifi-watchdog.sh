#!/bin/bash
set -u

IFACE="wlan0"
# The Pi returns to any Wi-Fi it has saved (nmcli connection show), so there is no Wi-Fi name to set here
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

# Try every saved Wi-Fi except the AP, one after another, until one connects.
# UUIDs are used because Wi-Fi names can contain spaces and colons
connect_saved_wifi() {
    local uuid type
    nmcli device wifi rescan ifname "$IFACE" >/dev/null 2>&1
    sleep 3
    while IFS=: read -r uuid type; do
        [ "$type" = "802-11-wireless" ] || continue
        [ "$(nmcli -g connection.id connection show "$uuid" 2>/dev/null)" = "$AP_CONN" ] && continue
        if timeout 15 nmcli connection up uuid "$uuid" >/dev/null 2>&1; then
            sleep 3
            check_connectivity && return 0
        fi
    done < <(nmcli -t -f UUID,TYPE connection show)
    return 1
}

current_wifi_name() {
    nmcli -t -g GENERAL.CONNECTION device show "$IFACE" 2>/dev/null
}

current_conn=$(current_wifi_name)

if [ "$current_conn" = "$AP_CONN" ]; then
    now=$(date +%s)
    last_try=$(cat "$AP_LAST_TRY_FILE" 2>/dev/null || echo 0)
    elapsed=$((now - last_try))
    if [ "$elapsed" -lt "$AP_RETRY_INTERVAL" ]; then
        exit 0
    fi
    echo "$now" > "$AP_LAST_TRY_FILE"
    echo "Currently in AP mode (${AP_CONN}). Trying to return to a saved Wi-Fi."
    nmcli connection down "$AP_CONN" >/dev/null 2>&1
    if connect_saved_wifi; then
        echo "Back on Wi-Fi ($(current_wifi_name))."
        echo 0 > "$FAIL_COUNT_FILE"
        rm -f "$AP_LAST_TRY_FILE"
    else
        echo "Could not return. Going back to AP mode."
        nmcli connection up "$AP_CONN" >/dev/null 2>&1
    fi
    exit 0
fi

if check_connectivity; then
    echo 0 > "$FAIL_COUNT_FILE"
    exit 0
fi

echo "No connectivity. Trying to reconnect."

if connect_saved_wifi; then
    echo "Reconnected ($(current_wifi_name))."
    echo 0 > "$FAIL_COUNT_FILE"
    exit 0
fi

fail_count=$(cat "$FAIL_COUNT_FILE" 2>/dev/null || echo 0)
fail_count=$((fail_count + 1))
echo "$fail_count" > "$FAIL_COUNT_FILE"
echo "Reconnect failed (${fail_count} in a row)."

if [ "$fail_count" -ge "$FAIL_THRESHOLD" ]; then
    echo "Threshold reached. Starting emergency AP mode (${AP_CONN})."
    nmcli connection up "$AP_CONN" >/dev/null 2>&1
fi
