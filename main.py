"""
Server Monitor - Main application file
"""
import os
from app import app, init_bot, monitor_loop
from threading import Thread

if __name__ == "__main__":
    # Initialize the Telegram bot if token and chat_id are available
    # from the environment variables
    bot_token = os.environ.get("BOT_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    
    if bot_token and chat_id:
        print("Configurazione bot da variabili d'ambiente")
        # Update config.json with environment variables
        from app import load_config, CONFIG_FILE
        import json
        
        config = load_config()
        config["bot_token"] = bot_token
        config["chat_id"] = chat_id
        
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        
        # Initialize the bot with the updated configuration
        init_bot()
    
    # Start the monitoring thread
    Thread(target=monitor_loop, daemon=True).start()
    
    # Start the Flask application
    app.run(host="0.0.0.0", port=5000)
