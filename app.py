import os
import json
import time
import psutil
import re
import ipaddress
import subprocess
from datetime import datetime
from pathlib import Path
from threading import Thread
from flask import Flask, render_template, request, redirect, jsonify

import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler

# Configurazione
CONFIG_FILE = "config.json"
AUTH_LOG_FILE = "/host/var/log/auth.log"  # Percorso al file auth.log all'interno del container
LAST_LOG_POSITION = "/tmp/last_log_position.txt"  # File per memorizzare l'ultima posizione di lettura

app = Flask(__name__)

# Caricamento della configurazione
def load_config():
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "cpu_threshold": 80,
            "ram_threshold": 80,
            "disk_threshold": 90,
            "net_threshold": 1000000,
            "notify_ssh": True,
            "notify_sftp": False,
            "notify_reboot": True,
            "excluded_ips": ["127.0.0.1", "192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"],
            "top_processes": 5,
            "bot_token": "",
            "chat_id": ""
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(default_config, f, indent=2)
        return default_config
    
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
            # Aggiungi campi mancanti con valori predefiniti
            if "bot_token" not in config:
                config["bot_token"] = ""
            if "chat_id" not in config:
                config["chat_id"] = ""
            if "excluded_ips" not in config:
                config["excluded_ips"] = ["127.0.0.1", "192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"]
            if "top_processes" not in config:
                config["top_processes"] = 5
            return config
    except Exception as e:
        print(f"Errore nel caricamento della configurazione: {e}")
        return {
            "cpu_threshold": 80,
            "ram_threshold": 80,
            "disk_threshold": 90,
            "net_threshold": 1000000,
            "notify_ssh": True,
            "notify_sftp": False,
            "notify_reboot": True,
            "excluded_ips": ["127.0.0.1", "192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"],
            "top_processes": 5,
            "bot_token": "",
            "chat_id": ""
        }

# Istanze globali per il bot Telegram
BOT_INSTANCE = None
UPDATER = None

# Inizializza il bot Telegram
def init_bot():
    global BOT_INSTANCE, UPDATER
    config = load_config()
    
    # Verifica se token e chat_id sono configurati
    if not config["bot_token"] or not config["chat_id"]:
        print("Token del bot o Chat ID non configurati")
        return False
    
    try:
        # Crea una nuova istanza del bot se necessario
        if BOT_INSTANCE is None or BOT_INSTANCE.token != config["bot_token"]:
            BOT_INSTANCE = telegram.Bot(token=config["bot_token"])
            
            # Se l'updater è in esecuzione, fermalo
            if UPDATER is not None:
                UPDATER.stop()
                
            # Crea un nuovo updater
            UPDATER = Updater(token=config["bot_token"], use_context=True)
            
            # Registra gli handler per i comandi
            dp = UPDATER.dispatcher
            dp.add_handler(CommandHandler("risorse", command_risorse))
            dp.add_handler(CommandHandler("start", command_start))
            dp.add_handler(CommandHandler("help", command_help))
            dp.add_handler(CallbackQueryHandler(button_callback))
            
            # Avvia il polling in un thread separato
            UPDATER.start_polling(drop_pending_updates=True)
            print("Bot Telegram inizializzato con successo")
        return True
    except Exception as e:
        print(f"Errore nell'inizializzazione del bot Telegram: {e}")
        return False

# Funzione per inviare un messaggio tramite il bot Telegram
def send_alert(message):
    config = load_config()
    max_retries = 3
    retry_delay = 2
    
    # Verifica se il bot è inizializzato
    if not init_bot():
        print("Impossibile inviare l'avviso: bot non inizializzato")
        return
    
    for attempt in range(max_retries):
        try:
            print(f"Invio messaggio Telegram: {message}")
            result = BOT_INSTANCE.send_message(
                chat_id=config["chat_id"], 
                text=message, 
                parse_mode="Markdown"
            )
            print(f"Messaggio inviato con successo: {result}")
            return
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Errore invio messaggio (tentativo {attempt+1}/{max_retries}): {e}")
                time.sleep(retry_delay)
            else:
                print(f"Errore invio messaggio (tutti i tentativi falliti): {e}")

# Funzione per costruire la tastiera inline per i comandi
def get_resource_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("CPU & RAM", callback_data="system_resources"),
            InlineKeyboardButton("Disco", callback_data="disk_resources")
        ],
        [
            InlineKeyboardButton("Top 5 Processi", callback_data="top_processes_5"),
            InlineKeyboardButton("Top 10 Processi", callback_data="top_processes_10")
        ],
        [
            InlineKeyboardButton("Rete", callback_data="network_resources"),
            InlineKeyboardButton("Tutti", callback_data="all_resources")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# Handler per il comando /risorse
def command_risorse(update, context):
    """Mostra la tastiera per richiedere le risorse"""
    update.message.reply_text(
        "Scegli quale informazione visualizzare:",
        reply_markup=get_resource_keyboard()
    )

# Handler per il comando /start
def command_start(update, context):
    """Messaggio di benvenuto e introduzione al bot"""
    update.message.reply_text(
        "Benvenuto nel Server Monitor Bot!\n\n"
        "Questo bot ti permette di monitorare lo stato del tuo server e ricevere notifiche "
        "quando vengono rilevati eventi importanti come accessi SSH o utilizzo elevato delle risorse.\n\n"
        "Usa /risorse per controllare lo stato attuale del server\n"
        "Usa /help per vedere tutti i comandi disponibili"
    )

# Handler per il comando /help
def command_help(update, context):
    """Mostra i comandi disponibili"""
    update.message.reply_text(
        "Comandi disponibili:\n\n"
        "/start - Avvia il bot\n"
        "/help - Mostra questo messaggio di aiuto\n"
        "/risorse - Visualizza le risorse del sistema\n"
    )

# Handler per i callback dei pulsanti
def button_callback(update, context):
    """Gestisce i callback dai pulsanti inline"""
    query = update.callback_query
    query.answer()
    
    data = query.data
    
    if data == "system_resources":
        resources = get_system_resources()
        query.edit_message_text(text=resources, parse_mode="Markdown")
    
    elif data == "disk_resources":
        disk_info = get_disk_info()
        query.edit_message_text(text=disk_info, parse_mode="Markdown")
    
    elif data == "network_resources":
        net_info = get_network_info()
        query.edit_message_text(text=net_info, parse_mode="Markdown")
    
    elif data.startswith("top_processes_"):
        num = int(data.split("_")[-1])
        processes = get_top_processes(num)
        query.edit_message_text(text=processes, parse_mode="Markdown")
    
    elif data == "all_resources":
        # Raccoglie tutte le informazioni
        resources = get_system_resources()
        disk_info = get_disk_info()
        net_info = get_network_info()
        processes = get_top_processes(5)
        
        # Combina tutte le informazioni in un unico messaggio
        all_info = f"{resources}\n\n{disk_info}\n\n{net_info}\n\n{processes}"
        query.edit_message_text(text=all_info, parse_mode="Markdown")
    
    # Aggiungi il pulsante per tornare al menu principale
    query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Torna al menu", callback_data="back_to_menu")]
        ])
    )
    
    if data == "back_to_menu":
        query.edit_message_text(
            text="Scegli quale informazione visualizzare:",
            reply_markup=get_resource_keyboard()
        )

# Ottiene le informazioni sulle risorse di sistema
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

# Ottiene le informazioni sul disco
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
        
        for i, part in enumerate(partitions[:5]):  # Limita a 5 partizioni
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

# Ottiene le informazioni sulla rete
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
                if addr.family == 2:  # Solo IPv4
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

# Ottiene i processi che utilizzano più risorse
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

# Verifica se un IP è in una lista di range esclusi
def check_ip_in_range(ip, excluded_ips):
    """Check if an IP address is within any of the excluded ranges"""
    if not ip:
        return True  # Skip empty IPs
        
    try:
        ip_obj = ipaddress.ip_address(ip)
        for excluded in excluded_ips:
            if "/" in excluded:  # This is a network range
                if ip_obj in ipaddress.ip_network(excluded, strict=False):
                    return True
            else:  # This is a single IP
                if ip == excluded:
                    return True
        return False
    except ValueError:
        return True  # In case of invalid IP, skip it

# Ottieni informazioni su un indirizzo IP
def get_ip_info(ip):
    """Ottiene informazioni su un indirizzo IP da ipinfo.io"""
    try:
        return f"https://ipinfo.io/{ip}"
    except Exception as e:
        print(f"Errore nel recupero delle informazioni IP: {e}")
        return ""

# Controlla il file auth.log per nuovi accessi SSH
def check_auth_log():
    """Monitora il file auth.log per individuare nuovi accessi SSH"""
    config = load_config()
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
    
    # Se il file è stato ruotato o troncato, ricomincia da zero
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
    ssh_pattern = re.compile(r'(\w+\s+\d+\s+\d+:\d+:\d+)\s+(\S+)\s+sshd\[\d+\]:\s+Accepted\s+\S+\s+for\s+(\S+)\s+from\s+(\S+)')
    
    for line in new_lines:
        match = ssh_pattern.search(line)
        if match:
            # Estrazione delle informazioni
            timestamp_str, hostname, username, source_ip = match.groups()
            
            # Controlla se l'IP è nella lista degli esclusi
            if not check_ip_in_range(source_ip, config["excluded_ips"]):
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

# Ottieni l'indirizzo IP locale del server
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

# Ottiene l'uptime del sistema
def get_uptime():
    """Ottiene l'uptime del sistema"""
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

# Thread di monitoraggio del sistema
def monitor_loop():
    """Thread principale per il monitoraggio del sistema"""
    last_uptime = 0
    
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
    
    try:
        last_uptime = get_uptime()
    except Exception as e:
        print(f"Errore nel leggere l'uptime: {e}")
        last_uptime = 0

    print("Monitor loop avviato.")
    last_check_time = 0
    
    while True:
        try:
            config = load_config()
            
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
            
            # Controllo accessi SSH ogni 30 secondi
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

# Route principale dell'applicazione web
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        # Elabora gli IP esclusi
        excluded_ips = []
        if "excluded_ips" in request.form and request.form["excluded_ips"].strip():
            excluded_ips = [ip.strip() for ip in request.form["excluded_ips"].split(",")]
        
        # Recupera il valore del numero di processi da visualizzare
        top_processes = 5  # Valore predefinito
        if "top_processes" in request.form:
            try:
                top_processes = int(request.form["top_processes"])
                # Limita il valore tra 1 e 20
                top_processes = max(1, min(20, top_processes))
            except ValueError:
                pass
        
        # Prendi il token del bot e il chat ID
        bot_token = request.form.get("bot_token", "")
        chat_id = request.form.get("chat_id", "")
        
        new_config = {
            "cpu_threshold": int(request.form["cpu"]),
            "ram_threshold": int(request.form["ram"]),
            "disk_threshold": int(request.form["disk"]),
            "net_threshold": int(request.form["net"]),
            "notify_ssh": "ssh" in request.form,
            "notify_sftp": "sftp" in request.form,
            "notify_reboot": "reboot" in request.form,
            "excluded_ips": excluded_ips,
            "top_processes": top_processes,
            "bot_token": bot_token,
            "chat_id": chat_id
        }
        
        with open(CONFIG_FILE, "w") as f:
            json.dump(new_config, f, indent=2)
            
        # Reinizializza il bot con il nuovo token e chat_id
        if bot_token and chat_id:
            init_bot()
            
        return redirect("/")

    config = load_config()
    return render_template("index.html", config=config)

# Route per testare la connessione del bot Telegram
@app.route("/test_telegram", methods=["POST"])
def test_telegram():
    try:
        bot_token = request.form.get("bot_token")
        chat_id = request.form.get("chat_id")
        
        if not bot_token or not chat_id:
            return jsonify({"success": False, "message": "Token del bot o Chat ID mancanti"})
        
        # Salva temporaneamente le configurazioni correnti
        config = load_config()
        old_token = config["bot_token"]
        old_chat_id = config["chat_id"]
        
        # Imposta temporaneamente i nuovi valori
        config["bot_token"] = bot_token
        config["chat_id"] = chat_id
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
            
        # Inizializza il bot e invia un messaggio di test
        if init_bot():
            message = "✅ Test di connessione effettuato con successo!"
            send_alert(message)
            return jsonify({"success": True, "message": "Connessione riuscita! Messaggio di test inviato."})
        else:
            # Ripristina i valori precedenti in caso di errore
            config["bot_token"] = old_token
            config["chat_id"] = old_chat_id
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
            return jsonify({"success": False, "message": "Impossibile connettersi con queste credenziali"})
            
    except Exception as e:
        return jsonify({"success": False, "message": f"Errore: {str(e)}"})

if __name__ == "__main__":
    # Avvia il thread di monitoraggio
    Thread(target=monitor_loop, daemon=True).start()
    
    # Avvia l'applicazione Flask
    app.run(host="0.0.0.0", port=5000)
