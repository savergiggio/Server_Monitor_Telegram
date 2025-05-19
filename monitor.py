import time, json, psutil, os, subprocess
from telegram_bot import send_alert
from datetime import datetime
import re
import ipaddress
import subprocess
import requests
from pathlib import Path

CONFIG_FILE = "config.json"
AUTH_LOG_FILE = "/host/var/log/auth.log"  # Percorso al file auth.log all'interno del container
LAST_LOG_POSITION = "/tmp/last_log_position.txt"  # File per memorizzare l'ultima posizione di lettura
EXCLUDED_IPS = ["127.0.0.1", "192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"]  # Default excluded IPs/ranges

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


# Aggiungi queste funzioni al file monitor.py

def get_system_resources():
    """Ottiene informazioni su CPU e RAM"""
    try:
        cpu = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory()
        swap = psutil.swap_memory()
        
        # Ottiene il carico di sistema (1, 5, 15 minuti)
        try:
            load_avg = os.getloadavg()
            load_str = f"Load avg: {load_avg[0]:.2f}, {load_avg[1]:.2f}, {load_avg[2]:.2f}"
        except:
            load_str = "Load avg: non disponibile"
            
        uptime_seconds = get_uptime()
        days, remainder = divmod(int(uptime_seconds), 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
        
        return (f"*Risorse di Sistema*\n"
                f"CPU: *{cpu}%*\n"
                f"RAM: *{ram.percent}%* ({ram.used // (1024*1024)} MB / {ram.total // (1024*1024)} MB)\n"
                f"Swap: *{swap.percent}%* ({swap.used // (1024*1024)} MB / {swap.total // (1024*1024)} MB)\n"
                f"{load_str}\n"
                f"Uptime: {uptime_str}")
    except Exception as e:
        return f"Errore nel recupero delle risorse: {e}"

def get_disk_info():
    """Ottiene informazioni sull'utilizzo del disco"""
    try:
        disk = psutil.disk_usage("/")
        
        # Formatta le dimensioni in GB
        used_gb = disk.used / (1024**3)
        total_gb = disk.total / (1024**3)
        free_gb = disk.free / (1024**3)
        
        # Ottiene le partizioni
        partitions = psutil.disk_partitions()
        partitions_info = ""
        
        for i, part in enumerate(partitions[:5]):  # Limita a 5 partizioni per non intasare il messaggio
            if "loop" not in part.device:  # Ignora i dispositivi loop
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    partitions_info += (f"\n{part.device} ({part.mountpoint}): "
                                       f"{usage.percent}% usato "
                                       f"({usage.used / (1024**3):.1f} GB / {usage.total / (1024**3):.1f} GB)")
                except:
                    pass
        
        if len(partitions) > 5:
            partitions_info += f"\n... e altre {len(partitions) - 5} partizioni"
            
        return (f"*Informazioni Disco*\n"
                f"Root Usage: *{disk.percent}%*\n"
                f"Usato: {used_gb:.1f} GB\n"
                f"Libero: {free_gb:.1f} GB\n"
                f"Totale: {total_gb:.1f} GB\n"
                f"*Partizioni*:{partitions_info}")
    except Exception as e:
        return f"Errore nel recupero delle informazioni sul disco: {e}"

def get_network_info():
    """Ottiene informazioni sul traffico di rete"""
    try:
        # Ottieni le statistiche di rete
        net_io = psutil.net_io_counters()
        
        # Converti in formato leggibile
        sent_mb = net_io.bytes_sent / (1024**2)
        recv_mb = net_io.bytes_recv / (1024**2)
        
        # Ottieni le connessioni attive
        connections = psutil.net_connections()
        established = sum(1 for conn in connections if conn.status == 'ESTABLISHED')
        listen = sum(1 for conn in connections if conn.status == 'LISTEN')
        
        # Ottieni le informazioni sulle interfacce di rete
        net_if = psutil.net_if_addrs()
        interfaces = []
        
        for interface, addresses in net_if.items():
            for addr in addresses:
                if addr.family == socket.AF_INET:  # Solo IPv4
                    interfaces.append(f"{interface}: {addr.address}")
                    break
        
        return (f"*Informazioni Rete*\n"
                f"Dati inviati: {sent_mb:.2f} MB\n"
                f"Dati ricevuti: {recv_mb:.2f} MB\n"
                f"Connessioni stabilite: {established}\n"
                f"Porte in ascolto: {listen}\n"
                f"*Interfacce*:\n" + "\n".join(interfaces[:5]))  # Limita a 5 interfacce
    except Exception as e:
        return f"Errore nel recupero delle informazioni di rete: {e}"

def get_top_processes(limit=5):
    """Ottiene i processi che utilizzano più risorse"""
    try:
        # Ottieni tutti i processi
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_percent', 'create_time']):
            try:
                pinfo = proc.info
                pinfo['cpu_percent'] = proc.cpu_percent(interval=0.1)
                processes.append(pinfo)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        
        # Ordina per utilizzo CPU (decrescente)
        processes = sorted(processes, key=lambda p: p['cpu_percent'], reverse=True)
        
        # Limita al numero richiesto
        top_processes = processes[:limit]
        
        result = f"*Top {limit} Processi (CPU)*\n"
        result += "```\n"
        result += f"{'PID':>7} {'CPU%':>6} {'MEM%':>6} {'USER':12} {'NAME'}\n"
        result += "-" * 50 + "\n"
        
        for proc in top_processes:
            result += f"{proc['pid']:7d} {proc['cpu_percent']:6.1f} {proc['memory_percent']:6.1f} {proc['username'][:12]:12} {proc['name']}\n"
        
        result += "```"
        return result
    except Exception as e:
        return f"Errore nel recupero dei processi attivi: {e}"

if __name__ == "__main__":
    monitor_loop()
