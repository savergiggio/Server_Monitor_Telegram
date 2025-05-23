from flask import Flask, render_template, request, redirect
from threading import Thread
import json
from monitor import monitor_loop, CONFIG_FILE

app = Flask(__name__)

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
        
        new_config = {
            "cpu_threshold": int(request.form["cpu"]),
            "ram_threshold": int(request.form["ram"]),
            "disk_threshold": int(request.form["disk"]),
            "net_threshold": int(request.form["net"]),
            "notify_ssh": "ssh" in request.form,
            "notify_sftp": "sftp" in request.form,
            "notify_reboot": "reboot" in request.form,
            "excluded_ips": excluded_ips,
            "top_processes": top_processes
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(new_config, f, indent=2)
        return redirect("/")

    with open(CONFIG_FILE) as f:
        config = json.load(f)
        # Assicurati che gli IP esclusi siano presenti nella configurazione
        if "excluded_ips" not in config:
            config["excluded_ips"] = ["127.0.0.1", "192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"]
        # Assicurati che top_processes sia presente nella configurazione
        if "top_processes" not in config:
            config["top_processes"] = 5
    
    return render_template("index.html", config=config)

if __name__ == "__main__":
    Thread(target=monitor_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
