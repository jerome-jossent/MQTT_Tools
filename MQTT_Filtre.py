#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import collections
import json
import signal
import sys
import time
import paho.mqtt.client as mqtt

# --------------------------------------------------------------------------- #
# Paramètres généraux
# --------------------------------------------------------------------------- #

BROKER_HOST = "localhost"
BROKER_PORT = 1883

TOPIC_FILTER_NEW = "Filter/new"  # Topic pour créer un nouveau filtre
INITIAL_FILTER_TOPIC = "simulateur/A/value"  # Topic initial à filtrer
DEFAULT_MODE = 1  # moyenne glissante
DEFAULT_WINDOW_SIZE = 9


# --------------------------------------------------------------------------- #
# Classe Filter
# --------------------------------------------------------------------------- #

class Filter:
    def __init__(self, source_topic, window_size=DEFAULT_WINDOW_SIZE, mode=DEFAULT_MODE):
        self.source_topic = source_topic
        self.filtered_topic = f"{source_topic}_filtered"
        self.mode_topic = f"{source_topic}_filtered/parameters/mode"
        self.window_topic = f"{source_topic}_filtered/parameters/window"
        self.mode = mode
        self.window_size = window_size
        self.window = collections.deque(maxlen=window_size)

    def process_value(self, value):
        self.window.append(value)

        if self.mode == 1:  # moyenne
            filtered = sum(self.window) / len(self.window)
        else:  # median
            sorted_w = sorted(self.window)
            mid_i = len(sorted_w) // 2
            filtered = (sorted_w[mid_i - 1] + sorted_w[mid_i]) / 2.0 if len(sorted_w) % 2 == 0 else sorted_w[mid_i]

        return filtered

    def update_window_size(self, new_size):
        old_vals = list(self.window)
        self.window_size = new_size
        self.window = collections.deque(maxlen=new_size)
        for v in old_vals[-new_size:]:
            self.window.append(v)


# --------------------------------------------------------------------------- #
# Variables globales
# --------------------------------------------------------------------------- #

filters = {}  # key: source_topic, value: Filter instance


# --------------------------------------------------------------------------- #
# Gestion du signal SIGINT
# --------------------------------------------------------------------------- #

def graceful_shutdown(signum, frame):
    print("\nArrêt demandé – nettoyage…")
    client.disconnect()
    sys.exit(0)


signal.signal(signal.SIGINT, graceful_shutdown)


# --------------------------------------------------------------------------- #
# Callback MQTT
# --------------------------------------------------------------------------- #

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ Connexion au broker réussie.")
        # Souscrire à tous les topics possibles avec des wildcards
        client.subscribe([
            (TOPIC_FILTER_NEW, 0),
            ("simulateur/+/value", 0),
            ("simulateur/+/value_filtered/parameters/mode", 0),
            ("simulateur/+/value_filtered/parameters/window", 0)
        ])

        # Si aucun filtre n'existe, créer le filtre initial
        if not filters:
            client.publish(TOPIC_FILTER_NEW, INITIAL_FILTER_TOPIC)
    else:
        print(f"❌ Échec de connexion (code {rc}).")


def on_disconnect(client, userdata, rc):
    if rc != 0:
        print(f"⚠️ Déconnexion inattendue (code {rc}) – tentative de reconnexion…")
    else:
        print("✅ Déconnexion propre.")


def on_message(client, userdata, msg):
    topic = msg.topic
    # print(f"DEBUG: Message reçu sur {topic}")

    # Création d'un nouveau filtre
    if topic == TOPIC_FILTER_NEW:
        try:
            source_topic = msg.payload.decode('utf-8')

            if source_topic in filters:
                print(f"⚠️ Un filtre existe déjà pour le topic {source_topic}")
                return

            new_filter = Filter(source_topic)
            filters[source_topic] = new_filter

            # Publier les paramètres initiaux
            client.publish(new_filter.mode_topic, str(DEFAULT_MODE), qos=0)
            client.publish(new_filter.window_topic, str(DEFAULT_WINDOW_SIZE), qos=0)

            print(f"✅ Nouveau filtre créé pour {source_topic}")
            return

        except Exception as e:
            print(f"⚠️ Erreur lors de la création du filtre: {e}")
            if source_topic in filters:
                del filters[source_topic]
            return

    # Gestion des paramètres et valeurs pour les filtres existants
    for f in filters.values():
        if topic == f.source_topic:
            try:
                val = float(msg.payload.decode('utf-8'))
                filtered_val = f.process_value(val)
                client.publish(f.filtered_topic, f"{filtered_val:.6f}", qos=0)
                print(f"[{time.strftime('%H:%M:%S')}] {f.source_topic} → {filtered_val:.6f}")
            except ValueError:
                print(f"⚠️ Valeur invalide sur {topic}: {msg.payload}")

        elif topic == f.mode_topic:
            try:
                val = int(msg.payload.decode('utf-8'))
                if val in (1, 2):
                    f.mode = val
                    print(
                        f"[{time.strftime('%H:%M:%S')}] {f.source_topic} → Mode changé: {'moyenne' if val == 1 else 'median'}")
                else:
                    print(f"⚠️ Mode invalide (doit être 1 ou 2): {val}")
            except ValueError:
                print(f"⚠️ Mode invalide sur {topic}: {msg.payload}")

        elif topic == f.window_topic:
            try:
                val = int(msg.payload.decode('utf-8'))
                if val > 0:
                    f.update_window_size(val)
                    print(f"[{time.strftime('%H:%M:%S')}] {f.source_topic} → Taille fenêtre: {val}")
                else:
                    print("⚠️ La taille de la fenêtre doit être positive")
            except ValueError:
                print(f"⚠️ Taille de fenêtre invalide sur {topic}: {msg.payload}")


# --------------------------------------------------------------------------- #
# Programme principal
# --------------------------------------------------------------------------- #

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
print(f"   Créez d'autres filtres en publiant le topic source sur {TOPIC_FILTER_NEW}")
print("   Les paramètres seront automatiquement créés avec _filtered/parameters/\n")

while True:
    time.sleep(1)
