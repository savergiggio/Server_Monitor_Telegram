import os
import telegram
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
BOT_INSTANCE = None
UPDATER = None

# Funzione per inizializzare il bot e l'updater
def init_bot():
    global BOT_INSTANCE, UPDATER
    if not BOT_INSTANCE and BOT_TOKEN:
        try:
            BOT_INSTANCE = telegram.Bot(token=BOT_TOKEN)
            UPDATER = Updater(token=BOT_TOKEN, use_context=True)
            
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
    return bool(BOT_INSTANCE)

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
    
    # Import qui per evitare import circolari
    from monitor import get_system_resources, get_disk_info, get_network_info, get_top_processes
    
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

def send_alert(message):
    max_retries = 3
    retry_delay = 2
    
    # Inizializza il bot se non è già stato fatto
    if not init_bot():
        print("ERRORE: Impossibile inizializzare il bot Telegram")
        return
    
    for attempt in range(max_retries):
        try:
            print(f"Invio messaggio Telegram: {message}")
            
            # Verifica che il token e il chat ID siano impostati
            if not BOT_TOKEN or BOT_TOKEN == "token":
                print("ERRORE: BOT_TOKEN non configurato correttamente")
                return
                
            if not CHAT_ID or CHAT_ID == "id":
                print("ERRORE: CHAT_ID non configurato correttamente")
                return
            
            result = BOT_INSTANCE.send_message(chat_id=CHAT_ID, text=message, parse_mode="Markdown")
            print(f"Messaggio inviato con successo: {result}")
            return
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Errore invio messaggio Telegram (tentativo {attempt+1}/{max_retries}): {e}")
                time.sleep(retry_delay)
            else:
                print(f"Errore invio messaggio Telegram (tutti i tentativi falliti): {e}")

# Inizializza il bot quando il modulo viene importato
init_bot()
