import time, json, psutil, os, subprocess
from telegram_bot import send_alert
from datetime import datetime
import re
import ipaddress
import subprocess
import requests
from pathlib import Path
import socket
from collections import defaultdict

CONFIG_FILE = "config.json"
AUTH_LOG_FILE = "/host/var/log/auth.log"  # Percorso al file auth.log all'interno del container
LAST_LOG_POSITION = "/tmp/last_log_position.txt"  # File per memorizzare l'ultima posizione di lettura
EXCLUDED_IPS = ["127.0.0.1", "192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"]  # Default excluded IPs/ranges
TOP_PROCESSES_DEFAULT = 5  # Numero di default di processi da visualizzare

def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)

def check_ip_in_range(ip):
    """Check if an IP address is within any of the excluded ranges"""
    if not ip:
        return True  # Skip empty IPs
        
    try:
        ip_obj = ipaddress.ip_address(ip)
        for excluded in EXCLUDED_IPS:
            if "/" in excluded:  # This is a network range
                if ip_obj in ipaddress.ip_network(excluded, strict=False):
                    return True
            else:  # This is a single IP
                if ip == excluded:
                    return True
        return False
    except ValueError:
        return True  # In case of invalid IP, skip it

def get_ip_info(ip):
    """Ottiene informazioni su un indirizzo IP da ipinfo.io"""
    try:
        return f"https://ipinfo.io/{ip}"
    except Exception as e:
        print(f"Errore nel recupero delle informazioni IP: {e}")
        return ""

def check_auth_log():
    """
    Monitora il file auth.log per individuare nuovi accessi SSH e invia notifiche per quelli provenienti
    da indirizzi IP non esclusi.
    """
    print("Controllo nuovi accessi SSH da auth.log...")
    
    # Verifica che il file di log esista
    if not os.path.exists(AUTH_LOG_FILE):
        print(f"File {AUTH_LOG_FILE} non trovato. Controlla il volume montato.")
        return

    # Determina da quale posizione iniziare a leggere il file
    last_position = 0
    if os.path.exists(LAST_LOG_POSITION):
        try:
            with open(LAST_LOG_POSITION, 'r') as f:
                last_position = int(f.read().strip() or '0')
        except Exception as e:
            print(f"Errore nella lettura dell'ultima posizione: {e}")
    
    # Ottieni la dimensione attuale del file
    current_size = os.path.getsize(AUTH_LOG_FILE)
    
    # Se il file è stato ruotato o troncato (dimensione minore dell'ultima posizione), ricomincia da zero
    if current_size < last_position:
        last_position = 0
    
    # Leggi solo le nuove righe dal file
    with open(AUTH_LOG_FILE, 'r') as f:
        f.seek(last_position)
        new_lines = f.readlines()
        
        # Aggiorna la posizione dell'ultima lettura
        with open(LAST_LOG_POSITION, 'w') as pos_file:
            pos_file.write(str(f.tell()))
    
    # Pattern per trovare i log di accesso SSH
    # Esempio di riga: May 19 09:08:01 hostname sshd[1234]: Accepted password for username from 192.168.1.1 port 12345 ssh2
    # O: May 19 09:08:01 hostname sshd[1234]: Accepted publickey for username from 192.168.1.1 port 12345 ssh2
    ssh_pattern = re.compile(r'(\w+\s+\d+\s+\d+:\d+:\d+)\s+(\S+)\s+sshd\[\d+\]:\s+Accepted\s+\S+\s+for\s+(\S+)\s+from\s+(\S+)')
    
    for line in new_lines:
        match = ssh_pattern.search(line)
        if match:
            # Estrazione delle informazioni
            timestamp_str, hostname, username, source_ip = match.groups()
            
            # Controlla se l'IP è nella lista degli esclusi
            if not check_ip_in_range(source_ip):
                # Ottieni timestamp formattato
                try:
                    # Aggiungi l'anno attuale poiché il log non lo include
                    current_year = datetime.now().year
                    full_timestamp_str = f"{timestamp_str} {current_year}"
                    # Converti in oggetto datetime
                    timestamp = datetime.strptime(full_timestamp_str, "%b %d %H:%M:%S %Y")
                    formatted_date = timestamp.strftime("%d %b %Y %H:%M")
                except Exception as e:
                    print(f"Errore nella formattazione della data: {e}")
                    formatted_date = timestamp_str
                
                # Ottieni l'indirizzo IP locale
                try:
                    local_ip = get_local_ip()
                except Exception as e:
                    local_ip = "unknown"
                    print(f"Errore nel recupero dell'IP locale: {e}")
                
                # Preparazione del messaggio
                message = (f"*SSH Connection detected*\n"
                           f"Connection from *{source_ip}* as *{username}* on *{hostname}* ({local_ip})\n"
                           f"Date: {formatted_date}\n"
                           f"More information: {get_ip_info(source_ip)}")
                
                print(f"Nuovo accesso SSH rilevato: {username} da {source_ip} su {hostname}")
                send_alert(message)
            else:
                print(f"Accesso SSH da {source_ip} escluso dalle notifiche.")

def get_local_ip():
    """Ottiene l'indirizzo IP locale del server"""
    try:
        # Usa hostname -I per ottenere gli indirizzi IP
        result = subprocess.run(['hostname', '-I'], capture_output=True, text=True)
        # Prende il primo IP (solitamente quello principale)
        ip = result.stdout.strip().split()[0]
        return ip
    except Exception as e:
        print(f"Errore nel recupero dell'IP locale: {e}")
        return "unknown"

def monitor_loop():
    global last_uptime
    
    # Crea i file di stato se non esistono
    if not os.path.exists(os.path.dirname(LAST_LOG_POSITION)):
        try:
            os.makedirs(os.path.dirname(LAST_LOG_POSITION))
        except:
            pass
    
    try:
        if not os.path.exists(LAST_LOG_POSITION):
            with open(LAST_LOG_POSITION, "w") as f:
                f.write("0")
    except:
        print(f"Errore nella creazione del file {LAST_LOG_POSITION}")
    
    config = load_config()
    try:
        last_uptime = get_uptime()
    except Exception as e:
        print(f"Errore nel leggere l'uptime: {e}")
        last_uptime = 0

    # Imposta gli IP esclusi all'avvio
    global EXCLUDED_IPS
    if "excluded_ips" in config:
        EXCLUDED_IPS = config["excluded_ips"]

    print("Monitor loop avviato.")
    last_check_time = 0
    
    while True:
        try:
            config = load_config()
            
            # Aggiorna la lista degli IP esclusi ad ogni ciclo
            if "excluded_ips" in config:
                EXCLUDED_IPS = config["excluded_ips"]
            
            # Monitora risorse di sistema
            cpu = psutil.cpu_percent(interval=1)
            ram = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").percent
            net = psutil.net_io_counters().bytes_sent + psutil.net_io_counters().bytes_recv
            
            try:
                uptime = get_uptime()
            except:
                uptime = 0
            
            # Invia avvisi per l'utilizzo elevato delle risorse
            if cpu > config["cpu_threshold"]:
                send_alert(f"⚠️ CPU alta: {cpu}%")

            if ram > config["ram_threshold"]:
                send_alert(f"⚠️ RAM alta: {ram}%")

            if disk > config["disk_threshold"]:
                send_alert(f"⚠️ DISK usage alto: {disk}%")

            # Rileva riavvii del sistema
            if uptime < last_uptime and config["notify_reboot"]:
                send_alert("🔄 Server riavviato")
            last_uptime = uptime
            
            # Controllo accessi SSH ogni 30 secondi (per evitare troppi controlli)
            current_time = time.time()
            if (current_time - last_check_time) >= 30:
                print("\n--- Controllo accessi SSH ---")
                
                # Esegui il monitoraggio degli accessi SSH da auth.log
                if config.get("notify_ssh", True):
                    try:
                        check_auth_log()
                    except Exception as e:
                        print(f"Errore durante check_auth_log: {e}")
                    
                last_check_time = current_time
                print("--- Fine controllo ---\n")

            time.sleep(10)
            
        except Exception as e:
            print(f"Errore nel monitor_loop: {e}")
            time.sleep(10)  # In caso di errore, aspetta comunque prima di riprovare

def get_uptime():
    try:
        with open("/proc/uptime", "r") as f:
            return float(f.readline().split()[0])
    except:
        try:
            # Fallback per Docker: leggi uptime del sistema host
            with open("/host/proc/uptime", "r") as f:
                return float(f.readline().split()[0])
        except:
            return 0  # Se non riusciamo a leggere l'uptime, restituiamo 0

if __name__ == "__main__":
    monitor_loop()
