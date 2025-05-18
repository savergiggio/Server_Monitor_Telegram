import time, json, psutil, os
from telegram_bot import send_alert
from datetime import datetime

CONFIG_FILE = "config.json"

def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)

last_uptime = None

def monitor_loop():
    global last_uptime
    config = load_config()
    last_uptime = get_uptime()

    while True:
        config = load_config()
        cpu = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent
        net = psutil.net_io_counters().bytes_sent + psutil.net_io_counters().bytes_recv
        uptime = get_uptime()

        if cpu > config["cpu_threshold"]:
            send_alert(f"⚠️ CPU alta: {cpu}%")

        if ram > config["ram_threshold"]:
            send_alert(f"⚠️ RAM alta: {ram}%")

        if disk > config["disk_threshold"]:
            send_alert(f"⚠️ DISK usage alto: {disk}%")

        if uptime < last_uptime and config["notify_reboot"]:
            send_alert("🔄 Server riavviato")
        last_uptime = uptime

        check_logins(config)

        time.sleep(10)

def get_uptime():
    with open("/proc/uptime", "r") as f:
        return float(f.readline().split()[0])

def check_logins(config):
    try:
        with open("/host/var/log/auth.log", "r") as f:
            lines = f.readlines()[-10:]

        for line in lines:
            if "sshd" in line:
                if "Accepted" in line and config["notify_ssh"]:
                    send_alert("🔐 Login SSH rilevato:\n" + line.strip())
                if "sftp" in line and config["notify_sftp"]:
                    send_alert("📁 Login SFTP rilevato:\n" + line.strip())
    except Exception as e:
        print(f"Errore controllo login: {e}")
