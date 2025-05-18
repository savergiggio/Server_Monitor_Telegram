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
check_ssh_activity() {
    # Fetch the current SSH sessions
    local current_logins=$(LC_ALL=C who -u | awk '{print $1, $8, $3, $4, $5, $7}') # Extract username, IP, date, time and pid
    local last_logins=$(cat "$SSH_ACTIVITY_LOGINS" 2>/dev/null)

    # Update the saved state with the current SSH sessions
    echo "$current_logins" > "$SSH_ACTIVITY_LOGINS"

    # Loop through the current logins to identify new sessions
    while IFS= read -r current_login; do
        # If the current session is not in the last recorded state, it's new
        if ! grep -Fq "$current_login" <<< "$last_logins"; then
            local user=$(echo "$current_login" | awk '{print $1}')
            local ip=$(echo "$current_login" | awk '{print $2}' | tr -d '()')
            local login_time=$(echo "$current_login" | awk '{print $3, $4, $5}')
            local formatted_time=$(LC_ALL=C date -d "$login_time" +"%H:%M" 2>/dev/null)

            # Check if the IP is within any of the excluded CIDR ranges or exact matches
            if [[ $(check_ip_in_range "$ip") == "false" ]]; then
                # Prepare and send the alert message
                local message="New SSH login: User *[ $user ]* from IP *$ip* at $formatted_time."
                echo "$message"  # Echo the message to the terminal for logging
                send_telegram_alert "SSH-LOGIN" "$message"
            else
                echo "New SSH login: User *[ $user ]* from IP *$ip*. IP excluded, no alerts send."
            fi
        fi
    done <<< "$current_logins"
}


# Function to check for new SFTP sessions
# This function monitors active SFTP sessions by comparing the current sessions against a previously saved list.
# It extracts each session's PID, start time, and associated network connections.
# If a session is not in the saved list and the source IP isn't excluded based on predefined criteria,
# it sends a Telegram alert with detailed connection information.
# After checking, the function updates the saved list with current session details to log new sessions for future comparisons.
# The goal is to monitor and alert on unauthorized or unexpected SFTP activity from non-excluded IP ranges.
check_sftp_activity() {
    # Fetch all PIDs for sftp-server processes along with their start times, parent PIDs, and full command
    local current_sessions=$(LC_ALL=C ps -eo pid,ppid,lstart,cmd | grep [s]ftp-server | awk '{print $1, $2, $3, $4, $5, $6}')

    # Read the last recorded session details from the log file and remove any leading/trailing whitespace
    local last_sessions=$(cat "$SFTP_ACTIVITY_LOGINS" 2>/dev/null | sed 's/^[ \t]*//;s/[ \t]*$//')

    # Loop through each current session to check if it's new
    while IFS= read -r current_session; do
        # Trim spaces from current session string for accurate comparison
        local trimmed_session=$(echo "$current_session" | sed 's/^[ \t]*//;s/[ \t]*$//')

        # Check if this session is already recorded to avoid duplicates
        if ! grep -Fq "$trimmed_session" <<< "$last_sessions"; then
            local pid=$(echo "$trimmed_session" | awk '{print $1}')    # Extract the PID
            local ppid=$(echo "$trimmed_session" | awk '{print $2}')   # Extract the Parent PID
	    local raw_date=$(echo "$current_session" | awk '{print $3, $4, $5, $6}') # Extract the full date string as it appears
            local stime=$(LC_ALL=C date -d "$raw_date" +"%Y-%m-%d %H:%M")  # Format the start time correctly based on extracted raw date
            local htime=$(LC_ALL=C date -d "$raw_date" +"%H:%M")  # Format the start time correctly based on extracted raw date

            # Use 'ss' to fetch network connections associated with the PID or its parent
            local connection_details=$(ss -tnp | grep -E "pid=$pid|pid=$ppid" | awk '{split($4, a, ":"); split($5, b, ":"); if (length(a[1]) > 0 && length(b[1]) > 0) print a[1], "<->", b[1]}')

            # Parse source IP from connection details
            local src_ip=$(echo "$connection_details" | awk '{print $3}')

            # Check if the IP is within any of the excluded ranges
            if [[ $(check_ip_in_range "$src_ip") == "false" ]]; then
                # Check if there are valid network details to report
                if [ -n "$connection_details" ]; then
		    local message="New SFTP session: From IP *${src_ip}* at ${htime}"

                    echo "$message"  # Output the message to terminal for logging
                    send_telegram_alert "SFTP-MONITOR" "$message"  # Send the alert message through Telegram
                    echo "$trimmed_session $stime ${connection_details}" >> "$SFTP_ACTIVITY_LOGINS"
                fi
            else
                echo "New SFTP session from *$src_ip*. IP excluded, no alerts send."
            fi
        fi
    done <<< "$current_sessions"
}

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
