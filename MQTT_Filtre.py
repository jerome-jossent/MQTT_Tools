#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import collections
import json
import signal
import sys
import time
import re
import paho.mqtt.client as mqtt

# --------------------------------------------------------------------------- #
# Paramètres généraux
# --------------------------------------------------------------------------- #

BROKER_HOST = "localhost"
BROKER_PORT = 1883

TOPIC_FILTER_NEW = "Filter/new"  # Topic pour créer un nouveau filtre
TOPIC_FILTER_DELETE = "Filter/delete"  # Topic pour supprimer un filtre
INITIAL_FILTER_TOPIC = "simulateur/A/value"  # Topic initial à filtrer
TOPIC_FILTER_INFO = "Filter/infos"  # Topic pour afficher les dernières infos
DEFAULT_MODE = 1
# 1 : moyenne glissante
# 2 : médiane glissante
# 3 : max glissant
# 4 : min glissant
DEFAULT_WINDOW_SIZE = 5 # x dernières valeurs (filtre temps réel)

class Filter:
    def __init__(self, source_topic, filter_name, window_size=DEFAULT_WINDOW_SIZE, mode=DEFAULT_MODE):
        self.source_topic = source_topic
        self.filter_name = filter_name  # Ex: "A_1", "A_2", etc.
        self.filtered_topic = f"{source_topic}_filtered_{filter_name}"
        self.mode_topic = f"{source_topic}_filtered_{filter_name}/mode"
        self.window_topic = f"{source_topic}_filtered_{filter_name}/window"
        self.mode = mode
        self.window_size = window_size
        self.window = collections.deque(maxlen=window_size)

    def process_value(self, value):
        self.window.append(value)

        if self.mode == 1:  # moyenne
            filtered = sum(self.window) / len(self.window)

        if self.mode == 2:  # median
            sorted_w = sorted(self.window)
            mid_i = len(sorted_w) // 2
            filtered = (sorted_w[mid_i - 1] + sorted_w[mid_i]) / 2.0 if len(sorted_w) % 2 == 0 else sorted_w[mid_i]

        if self.mode == 3:  # max
            filtered = max(self.window)

        if self.mode == 4:  # main
            filtered = min(self.window)

        return filtered

    def update_window_size(self, new_size):
        old_vals = list(self.window)
        self.window_size = new_size
        self.window = collections.deque(maxlen=new_size)
        for v in old_vals[-new_size:]:
            self.window.append(v)

    def delete_topics(self, client):
        """Supprime tous les topics du filtre en publiant des chaînes vides."""
        topics = [self.filtered_topic, self.mode_topic, self.window_topic]
        for topic in topics:
            client.publish(topic, payload="", qos=1, retain=True)

filters = {}  # key: filter_name (ex: "A_1"), value: instance Filter
source_counters = {}  # key: topic source, value: compteur pour la nomenclature
mode_names = ['none', 'moyenne', 'médiane', 'maximum', 'minimum']

# Fonctions utilitaires
def extract_variable_name(source_topic):
    """Extrait le nom de la variable depuis le topic source.
    Ex: 'simulateur/A/value' -> 'A'
    """
    match = re.search(r'simulateur/([^/]+)/value', source_topic)
    if match:
        return match.group(1)
    else:
        # Fallback: utiliser le topic complet sans les slashes
        return source_topic.replace('/', '_')

def generate_filter_name(source_topic):
    """Génère un nom unique pour le filtre basé sur le topic source.
    Ex: pour 'simulateur/A/value', génère 'A_1', 'A_2', etc.
    """
    variable_name = extract_variable_name(source_topic)

    if source_topic not in source_counters:
        source_counters[source_topic] = 0

    # Incrémenter le compteur et vérifier l'unicité
    while True:
        source_counters[source_topic] += 1
        proposed_name = f"{variable_name}_{source_counters[source_topic]}"

        # Vérifier si ce nom n'existe pas déjà
        if proposed_name not in filters:
            return proposed_name

def find_filter_by_param_topic(param_topic):
    """Trouve le filtre correspondant à un topic de paramètre."""
    for filter_name, filter_obj in filters.items():
        if param_topic == filter_obj.mode_topic or param_topic == filter_obj.window_topic:
            return filter_obj
    return None


# Gestion du signal SIGINT
def graceful_shutdown(signum, frame):
    print("\nArrêt demandé – nettoyage…")
    client.disconnect()
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_shutdown)

messages = []
messages_max = 10

# Display infos sur TOPIC_FILTER_INFO
def print_mqtt(message):
    print(message)
    messages.append(message)

    while len(messages) > messages_max:
        messages.pop(0)

    last_X_messages = "\n".join(messages)

    # ne garde que les 1000 derniers caractères
    last_X_messages = last_X_messages[-10000:]

    client.publish(TOPIC_FILTER_INFO, last_X_messages, qos=0, retain=True)

# Callback MQTT
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ Connexion au broker réussie.")
        client.publish(TOPIC_FILTER_NEW, "publier ici 1 par 1 les topics pointant sur les valeurs à filtrer")

        # Souscrire aux topics de gestion
        client.subscribe([
            (TOPIC_FILTER_NEW, 0),  # Topic de création
            (TOPIC_FILTER_DELETE, 0),  # Topic de suppression
            ("simulateur/+/value", 0),  # Topics de valeurs simulées par le script MQTT_Simulateur.py
        ])

        # Si aucun filtre n'existe, créer le filtre initial
        # if not filters:
        #     client.publish(TOPIC_FILTER_NEW, INITIAL_FILTER_TOPIC)
    else:
        print(f"❌ Échec de connexion (code {rc}).")

def on_disconnect(client, userdata, rc):
    if rc != 0:
        print(f"⚠️ Déconnexion inattendue (code {rc}) – tentative de reconnexion…")
    else:
        print("✅ Déconnexion propre.")

def on_message(client, userdata, msg):
    #global                     mode_names
    topic = msg.topic
    # print(f"DEBUG: Message reçu sur {topic}")

    # Création d'un nouveau filtre
    if topic == TOPIC_FILTER_NEW:
        try:
            source_topic = msg.payload.decode('utf-8').strip()

            # Générer un nom unique pour le filtre
            filter_name = generate_filter_name(source_topic)

            new_filter = Filter(source_topic, filter_name)
            filters[filter_name] = new_filter

            # S'abonner aux topics de paramètres de ce filtre
            client.subscribe([(new_filter.mode_topic, 0), (new_filter.window_topic, 0)])

            # S'assurer qu'on est bien abonné au topic source (au cas où ce ne serait pas déjà fait)
            client.subscribe(source_topic, 0)

            # Publier les paramètres initiaux
            client.publish(new_filter.mode_topic, str(DEFAULT_MODE), qos=0, retain=True)
            client.publish(new_filter.window_topic, str(DEFAULT_WINDOW_SIZE), qos=0, retain=True)

            print_mqtt(f"✅ Nouveau filtre créé: {filter_name} pour {source_topic}")
            print_mqtt(f"   → Topic filtré: {new_filter.filtered_topic}")
            return

        except Exception as e:
            print_mqtt(f"⚠️ Erreur lors de la création du filtre: {e}")
            return

    # Suppression d'un filtre
    if topic == TOPIC_FILTER_DELETE:
        try:
            filter_name = msg.payload.decode('utf-8').strip()

            if filter_name in filters:
                filter_obj = filters[filter_name]
                # Se désabonner des topics de paramètres
                client.unsubscribe([filter_obj.mode_topic, filter_obj.window_topic])
                # Supprimer les topics MQTT
                filter_obj.delete_topics(client)
                del filters[filter_name]
                print_mqtt(f"✅ Filtre supprimé: {filter_name}")
            else:
                print_mqtt(f"⚠️ Aucun filtre trouvé avec le nom: {filter_name}")
                show_active_filters()
            return
        except Exception as e:
            print_mqtt(f"⚠️ Erreur lors de la suppression du filtre: {e}")
            return

    # Gestion des valeurs entrantes - traiter TOUS les filtres qui correspondent
    for filter_name, filter_obj in filters.items():
        if topic == filter_obj.source_topic:
            try:
                val = float(msg.payload.decode('utf-8'))
                filtered_val = filter_obj.process_value(val)
                client.publish(filter_obj.filtered_topic, f"{filtered_val:.6f}", qos=0)
                # print(f"[{time.strftime('%H:%M:%S')}] {filter_name}: {filter_obj.source_topic} → {filtered_val:.6f}")
            except ValueError:
                print_mqtt(f"⚠️ Valeur invalide sur {topic}: {msg.payload}")
            # PAS DE BREAK ici - on continue pour traiter tous les filtres

    # Gestion des paramètres (mode et fenêtre)
    filter_obj = find_filter_by_param_topic(topic)
    if filter_obj:
        if topic == filter_obj.mode_topic:
            try:
                val = int(msg.payload.decode('utf-8'))
                if 1 <= val <= 4:
                    filter_obj.mode = val
                    mode_name = mode_names[val]
                    print_mqtt(f"[{time.strftime('%H:%M:%S')}] {filter_obj.filter_name} → Mode changé: {mode_name}")
                else:
                    print_mqtt(f"⚠️ Mode invalide (doit être 1 ou 4): {val}")
            except ValueError:
                print_mqtt(f"⚠️ Mode invalide sur {topic}: {msg.payload}")

        elif topic == filter_obj.window_topic:
            try:
                val = int(msg.payload.decode('utf-8'))
                if val > 0:
                    filter_obj.update_window_size(val)
                    print_mqtt(f"[{time.strftime('%H:%M:%S')}] {filter_obj.filter_name} → Taille fenêtre: {val}")
                else:
                    print_mqtt("⚠️ La taille de la fenêtre doit être positive")
            except ValueError:
                print_mqtt(f"⚠️ Taille de fenêtre invalide sur {topic}: {msg.payload}")


client = mqtt.Client()
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message

try:
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
except Exception as e:
    print(f"❌ Impossible de se connecter à {BROKER_HOST}:{BROKER_PORT} → {e}")
    sys.exit(1)

client.loop_start()

print("\n▶️  Système de filtrage multiple en cours – appuyez sur Ctrl‑C pour arrêter.")
print(f"   Filtre initial créé pour {INITIAL_FILTER_TOPIC}")
print("\n📋 Utilisation:")
print(f"   • Créer un filtre: publier le topic source sur '{TOPIC_FILTER_NEW}'")
print(f"   • Supprimer un filtre: publier le nom du filtre (ex: 'A_1') sur '{TOPIC_FILTER_DELETE}'")
print("   • Les noms de filtres sont générés automatiquement: A_1, A_2, B_1, etc.")
print("   • Topics générés:")
print("     - Valeurs filtrées (out): simulateur/X/value_filtered_X_N")
print("     - Mode (in): 1 à 4 (moyenne, médiane, max, min glissant) simulateur/X/value_filtered_X_N/mode")
print("     - Fenêtre (in): (taille de la fenêtre en nombre de valeurs) simulateur/X/value_filtered_X_N/window")


# Afficher les filtres actifs
def show_active_filters():
    if filters:
        print(f"\n📊 Filtres actifs ({len(filters)}):")
        for filter_name, filter_obj in filters.items():
            if filter_obj.mode == 1:
                mode_str = "moyenne"
            if filter_obj.mode == 2:
                mode_str = "médiane"
            if filter_obj.mode == 3:
                mode_str = "maximum"
            if filter_obj.mode == 4:
                mode_str = "minimum"
            print(f"   • {filter_name}: {filter_obj.source_topic} → {filter_obj.filtered_topic}")
            print(f"     Mode ({filter_obj.mode}): {mode_str}, Fenêtre: {filter_obj.window_size}")

# Afficher l'état initial
time.sleep(2)  # Laisser le temps au filtre initial de se créer
show_active_filters()

while True:
    time.sleep(1)