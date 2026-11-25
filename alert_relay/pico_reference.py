# Pico W 告警器參考程式（MicroPython）
#
# ⚠ 未在實機上測試過。這是照 alert_relay/protocol.py 的格式寫的起點，給硬體組參考：
#   接線、腳位、燈號效果請依實際硬體修改；行為要與 fake_pico.py 一致（交接文件 6.3）。
#
# 使用方式：用 Thonny 把本檔存到 Pico W，檔名改成 main.py，開機自動執行。
#   1. 改下面的 WIFI_SSID／WIFI_PASSWORD（現場旅行路由器）
#   2. 開機後序列埠會印出 Pico 的 IP，轉發程式用 --pico <這個 IP>
#   3. 先用 python alert_relay/fake_alerts.py --pico <IP> 測，再接轉發程式
#
# 行為：
#   caution   黃燈亮 1 秒
#   danger    紅燈亮＋蜂鳴器響 0.5 秒（紅燈 1 秒）
#   heartbeat 綠燈閃一下；超過 3 秒沒收到任何訊息 → 綠燈熄滅
#   每收到一則訊息，先回 {"ack": seq} 再處理燈號（轉發端靠 ack 量延遲）

import json
import socket
import time

import network
from machine import Pin

WIFI_SSID = "請改成現場路由器的名稱"
WIFI_PASSWORD = "請改成密碼"
PORT = 5005

# 腳位：依實際接線修改（GP 編號）
LED_GREEN = Pin(13, Pin.OUT)
LED_YELLOW = Pin(14, Pin.OUT)
LED_RED = Pin(15, Pin.OUT)
BUZZER = Pin(16, Pin.OUT)          # 有源蜂鳴器：高電位就響
ONBOARD = Pin("LED", Pin.OUT)      # Pico W 板上的 LED，連上 WiFi 後亮

LINK_TIMEOUT_MS = 3000


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    for _ in range(30):
        if wlan.isconnected():
            break
        ONBOARD.toggle()
        time.sleep(0.5)
    if not wlan.isconnected():
        raise RuntimeError("WiFi 連線失敗，檢查名稱與密碼")
    ONBOARD.on()
    ip = wlan.ifconfig()[0]
    print("Pico IP:", ip)
    return ip


def main():
    connect_wifi()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", PORT))
    sock.settimeout(0.05)                 # 50 ms 輪詢一次，讓燈號計時能更新

    off_at = {LED_YELLOW: 0, LED_RED: 0, BUZZER: 0, LED_GREEN: 0}
    last_rx = time.ticks_ms()
    link_up = False

    while True:
        now = time.ticks_ms()
        try:
            data, addr = sock.recvfrom(512)
        except OSError:                   # 逾時：沒有新訊息
            data = None

        if data:
            try:
                msg = json.loads(data)
            except ValueError:
                msg = None
            if msg is not None:
                sock.sendto(json.dumps({"ack": msg.get("seq")}), addr)   # 先回 ack
                last_rx = now
                link_up = True
                level = msg.get("level")
                if level == "caution":
                    LED_YELLOW.on()
                    off_at[LED_YELLOW] = time.ticks_add(now, 1000)
                elif level == "danger":
                    LED_RED.on()
                    BUZZER.on()
                    off_at[LED_RED] = time.ticks_add(now, 1000)
                    off_at[BUZZER] = time.ticks_add(now, 500)
                elif level == "heartbeat":
                    LED_GREEN.off()                                     # 閃一下
                    off_at[LED_GREEN] = time.ticks_add(now, 100)

        # 到時間就關燈／關蜂鳴器（綠燈相反：「閃一下」結束後要再亮回來）
        for pin, t in off_at.items():
            if t and time.ticks_diff(now, t) >= 0:
                off_at[pin] = 0
                if pin is LED_GREEN:
                    if link_up:
                        LED_GREEN.on()
                else:
                    pin.off()

        # 3 秒沒收到任何訊息 → 綠燈熄滅
        if link_up and time.ticks_diff(now, last_rx) > LINK_TIMEOUT_MS:
            link_up = False
            LED_GREEN.off()
        elif link_up and not off_at[LED_GREEN]:
            LED_GREEN.on()


main()
