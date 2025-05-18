import os
import telegram
import time

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def send_alert(message):
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            print(f"Invio messaggio Telegram: {message}")
            
            # Verifica che il token e il chat ID siano impostati
            if not BOT_TOKEN or BOT_TOKEN == "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX":
                print("ERRORE: BOT_TOKEN non configurato correttamente")
                return
                
            if not CHAT_ID or CHAT_ID == "XXXXXXXX":
                print("ERRORE: CHAT_ID non configurato correttamente")
                return
                
            bot = telegram.Bot(token=BOT_TOKEN)
            result = bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="Markdown")
            print(f"Messaggio inviato con successo: {result}")
            return
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Errore invio messaggio Telegram (tentativo {attempt+1}/{max_retries}): {e}")
                time.sleep(retry_delay)
            else:
                print(f"Errore invio messaggio Telegram (tutti i tentativi falliti): {e}")
