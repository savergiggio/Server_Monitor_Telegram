from flask import Flask, render_template, request, redirect
from threading import Thread
import json
from monitor import monitor_loop, CONFIG_FILE

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        new_config = {
            "cpu_threshold": int(request.form["cpu"]),
            "ram_threshold": int(request.form["ram"]),
            "disk_threshold": int(request.form["disk"]),
            "net_threshold": int(request.form["net"]),
            "notify_ssh": "ssh" in request.form,
            "notify_sftp": "sftp" in request.form,
            "notify_reboot": "reboot" in request.form,
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(new_config, f, indent=2)
        return redirect("/")

    with open(CONFIG_FILE) as f:
        config = json.load(f)
    return render_template("index.html", config=config)

if __name__ == "__main__":
    Thread(target=monitor_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
