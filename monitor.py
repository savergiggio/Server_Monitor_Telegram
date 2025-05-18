import time, json, psutil, os, subprocess
from telegram_bot import send_alert
from datetime import datetime
import re
import ipaddress

CONFIG_FILE = "config.json"
SSH_ACTIVITY_LOGINS = "/tmp/ssh_activity_logins.txt"
SFTP_ACTIVITY_LOGINS = "/tmp/sftp_activity_logins.txt"
EXCLUDED_IPS = ["127.0.0.1", "192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"]  # Default excluded IPs/ranges

def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)

def check_ip_in_range(ip):
    """Check if an IP address is within any of the excluded ranges"""
    if not ip:
        return "true"  # Skip empty IPs
        
    try:
        ip_obj = ipaddress.ip_address(ip)
        for excluded in EXCLUDED_IPS:
            if "/" in excluded:  # This is a network range
                if ip_obj in ipaddress.ip_network(excluded, strict=False):
                    return "true"
            else:  # This is a single IP
                if ip == excluded:
                    return "true"
        return "false"
    except ValueError:
        return "true"  # In case of invalid IP, skip it

def check_ssh_activity(config):
    """Monitor active SSH sessions and detect new ones"""
    if not config.get("notify_ssh", True):
        return

    try:
        # Ottieni la lista di IP esclusi dal config
        excluded_ips = config.get("excluded_ips", EXCLUDED_IPS)
        
        # Debugging
        print("DEBUG: Verificando sessioni SSH attive")
        
        # Fetch the current SSH sessions using the 'who' command - prova diversi approcci
        who_output = ""
        try:
            who_output = subprocess.check_output("who -u", shell=True, text=True)
        except subprocess.CalledProcessError:
            try:
                # Fallback se who -u non funziona
                who_output = subprocess.check_output("who", shell=True, text=True)
            except:
                try:
                    # Ultimo tentativo
                    who_output = subprocess.check_output("w -h", shell=True, text=True)
                except:
                    print("DEBUG: Impossibile ottenere informazioni sugli utenti connessi")
                    return
        
        print(f"DEBUG: Output del comando who:\n{who_output}")
        current_logins = []
        
        for line in who_output.splitlines():
            parts = line.split()
            if len(parts) >= 5:  # Verifica che ci siano abbastanza parti
                username = parts[0]
                
                # Cerca l'indirizzo IP nella riga
                ip = None
                for part in parts:
                    if '(' in part and ')' in part:
                        ip = part.replace("(", "").replace(")", "")
                        break
                
                # Se non trova l'IP nel formato standard, prova a estrarlo come ultimo elemento
                if not ip and len(parts) >= 5:
                    possible_ip = parts[-3]
                    if re.match(r'\d+\.\d+\.\d+\.\d+', possible_ip):
                        ip = possible_ip
                
                if not ip:  # Se ancora non abbiamo un IP, usa un valore di fallback
                    ip = "unknown"
                    
                # Estrai la data se possibile
                login_date = datetime.now().strftime("%b %d %H:%M")
                if len(parts) >= 3:
                    try:
                        login_date = f"{parts[2]} {parts[3]} {parts[4]}"
                    except:
                        pass  # Usa il valore predefinito se c'è un errore
                
                # Estrai PID se disponibile
                pid = "unknown"
                for i, part in enumerate(parts):
                    if i > 0 and re.match(r'^\d+$', part):
                        pid = part
                        break
                
                current_logins.append(f"{username} {ip} {login_date} {pid}")
        
        print(f"DEBUG: Login attuali: {current_logins}")
        
        # Read the last recorded state
        last_logins = []
        if os.path.exists(SSH_ACTIVITY_LOGINS):
            with open(SSH_ACTIVITY_LOGINS, "r") as f:
                last_logins = f.read().splitlines()
        
        print(f"DEBUG: Login precedenti: {last_logins}")
        
        # Update the saved state
        with open(SSH_ACTIVITY_LOGINS, "w") as f:
            f.write("\n".join(current_logins))
        
        # Check for new sessions
        for current_login in current_logins:
            if current_login not in last_logins:
                print(f"DEBUG: Rilevato nuovo login: {current_login}")
                parts = current_login.split()
                username = parts[0]
                ip = parts[1]
                login_time = datetime.now().strftime("%H:%M")
                if len(parts) >= 3:
                    try:
                        login_time = parts[2] + " " + parts[3]
                    except:
                        pass  # Usa il valore predefinito
                
                # Check if the IP is within any of the excluded ranges
                is_excluded = False
                for excluded_ip in excluded_ips:
                    if "/" in excluded_ip:  # Check if it's a CIDR range
                        try:
                            if ipaddress.ip_address(ip) in ipaddress.ip_network(excluded_ip, strict=False):
                                is_excluded = True
                                break
                        except:
                            pass  # Se l'IP non è valido, continua
                    elif ip == excluded_ip:  # Direct match
                        is_excluded = True
                        break
                
                if not is_excluded and ip != "unknown":
                    message = f"🔐 Nuovo login SSH: Utente *[ {username} ]* da IP *{ip}* alle {login_time}."
                    print(message)
                    send_alert(message)
                else:
                    print(f"Nuovo login SSH: Utente *[ {username} ]* da IP *{ip}*. IP escluso o sconosciuto, nessun avviso inviato.")
    
    except Exception as e:
        print(f"Errore nel controllo dell'attività SSH: {e}")
        import traceback
        traceback.print_exc()

def check_sftp_activity(config):
    """Monitor active SFTP sessions and detect new ones"""
    if not config.get("notify_sftp", True):
        return

    try:
        # Ottieni la lista di IP esclusi dal config
        excluded_ips = config.get("excluded_ips", EXCLUDED_IPS)
        
        print("DEBUG: Verificando sessioni SFTP attive")
        
        # Prova diversi approcci per trovare i processi sftp-server
        try:
            ps_output = subprocess.check_output("ps -eo pid,ppid,lstart,cmd | grep [s]ftp-server", shell=True, text=True)
        except subprocess.CalledProcessError:
            try:
                # Secondo tentativo con formato diverso
                ps_output = subprocess.check_output("ps ax | grep [s]ftp-server", shell=True, text=True)
            except:
                # No sftp processes found, return empty
                print("DEBUG: Nessun processo sftp-server trovato")
                return
        
        print(f"DEBUG: Output del comando ps:\n{ps_output}")
        current_sessions = []
        
        for line in ps_output.splitlines():
            if not line.strip():
                continue
                
            # Verifica che la riga contenga effettivamente 'sftp-server'
            if "sftp-server" not in line:
                continue
                
            parts = line.split()
            if len(parts) >= 2:
                # Estrai PID e PPID (se disponibili)
                pid = parts[0]
                ppid = parts[1] if len(parts) > 1 else "unknown"
                
                # Genera un identificatore unico per questa sessione
                session_id = f"{pid} {ppid}"
                current_sessions.append(session_id)
        
        print(f"DEBUG: Sessioni SFTP attuali: {current_sessions}")
        
        # Read the last recorded sessions
        last_sessions = []
        if os.path.exists(SFTP_ACTIVITY_LOGINS):
            with open(SFTP_ACTIVITY_LOGINS, "r") as f:
                last_sessions = f.read().splitlines()
        
        print(f"DEBUG: Sessioni SFTP precedenti: {last_sessions}")
        
        # Check for new sessions - prima salviamo lo stato attuale
        with open(SFTP_ACTIVITY_LOGINS, "w") as f:
            f.write("\n".join(current_sessions))
        
                    # Ora controlliamo le nuove sessioni
        for current_session in current_sessions:
            if current_session not in last_sessions:
                print(f"DEBUG: Rilevata nuova sessione SFTP: {current_session}")
                parts = current_session.split()
                if not parts:
                    continue
                    
                pid = parts[0]
                if not pid.isdigit():
                    continue
                
                # Prova diversi metodi per determinare la connessione di rete
                src_ip = None
                
                # Metodo 1: Usa ss
                try:
                    ss_output = subprocess.check_output(f"ss -tnp | grep {pid}", shell=True, text=True)
                    print(f"DEBUG: Output ss per PID {pid}:\n{ss_output}")
                    
                    # Cerca un indirizzo IP nel formato standard
                    ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+):', ss_output)
                    if ip_match:
                        src_ip = ip_match.group(1)
                except:
                    pass
                
                # Metodo 2: Prova con netstat se ss ha fallito
                if not src_ip:
                    try:
                        netstat_output = subprocess.check_output(f"netstat -tnp 2>/dev/null | grep {pid}", shell=True, text=True)
                        print(f"DEBUG: Output netstat per PID {pid}:\n{netstat_output}")
                        
                        ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+):', netstat_output)
                        if ip_match:
                            src_ip = ip_match.group(1)
                    except:
                        pass
                
                # Metodo 3: Prova lsof come ultimo tentativo
                if not src_ip:
                    try:
                        lsof_output = subprocess.check_output(f"lsof -p {pid} -a -i -n", shell=True, text=True)
                        print(f"DEBUG: Output lsof per PID {pid}:\n{lsof_output}")
                        
                        ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+):', lsof_output)
                        if ip_match:
                            src_ip = ip_match.group(1)
                    except:
                        pass
                
                # Se abbiamo trovato un indirizzo IP, controlliamo se è escluso
                if src_ip:
                    print(f"DEBUG: Trovato IP per sessione SFTP: {src_ip}")
                    
                    # Controlla se l'IP rientra nelle esclusioni
                    is_excluded = False
                    for excluded_ip in excluded_ips:
                        if "/" in excluded_ip:  # Check if it's a CIDR range
                            try:
                                if ipaddress.ip_address(src_ip) in ipaddress.ip_network(excluded_ip, strict=False):
                                    is_excluded = True
                                    break
                            except:
                                pass  # Se l'IP non è valido, continua
                        elif src_ip == excluded_ip:  # Direct match
                            is_excluded = True
                            break
                    
                    if not is_excluded:
                        formatted_time = datetime.now().strftime("%H:%M")
                        message = f"📁 Nuova sessione SFTP: Da IP *{src_ip}* alle {formatted_time}"
                        print(message)
                        send_alert(message)
                    else:
                        print(f"Nuova sessione SFTP da IP *{src_ip}*. IP escluso, nessun avviso inviato.")
                else:
                    print(f"DEBUG: Impossibile determinare l'IP per la sessione SFTP con PID {pid}")
                
    except Exception as e:
        print(f"Errore nel controllo dell'attività SFTP: {e}")
        import traceback
        traceback.print_exc()

last_uptime = None

def monitor_loop():
    global last_uptime
    
    # Crea i file di stato se non esistono
    for filename in [SSH_ACTIVITY_LOGINS, SFTP_ACTIVITY_LOGINS]:
        if not os.path.exists(os.path.dirname(filename)):
            try:
                os.makedirs(os.path.dirname(filename))
            except:
                pass
        try:
            if not os.path.exists(filename):
                with open(filename, "w") as f:
                    f.write("")
        except:
            print(f"Errore nella creazione del file {filename}")
    
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
            
            # Controllo sessioni SSH e SFTP ogni 30 secondi (per evitare troppi controlli)
            current_time = time.time()
            if (current_time - last_check_time) >= 30:
                print("\n--- Controllo sessioni ---")
                
                # Esegui il monitoraggio attivo delle sessioni SSH e SFTP
                try:
                    check_ssh_activity(config)
                except Exception as e:
                    print(f"Errore durante check_ssh_activity: {e}")
                    
                try:
                    check_sftp_activity(config)
                except Exception as e:
                    print(f"Errore durante check_sftp_activity: {e}")
                    
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
