#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import math
import time
import random
from collections import deque

import paho.mqtt.client as mqtt

# --------------------------------------------------------------------------- #
# Configuration du broker MQTT
# --------------------------------------------------------------------------- #

BROKER_HOST = "localhost"
BROKER_PORT = 1883

TOPIC_NEW = "simulateur/new"
TOPIC_DELETE = "simulateur/delete"
TOPIC_README = "simulateur/readme"

# Format des topics pour les paramètres
TOPIC_PARAM_PERIOD = "simulateur/{}/parameters/period"
TOPIC_PARAM_MIN = "simulateur/{}/parameters/min"
TOPIC_PARAM_MAX = "simulateur/{}/parameters/max"
TOPIC_PARAM_NOISE = "simulateur/{}/parameters/noise"
TOPIC_PARAM_PERIOD_PUBLISH = "simulateur/{}/parameters/period_publish"

# --------------------------------------------------------------------------- #
# README – documentation « live » envoyée au broker lors de la connexion
# --------------------------------------------------------------------------- #

SIMULATOR_README_TEXT = """
## Simulateur MQTT – Documentation

Ce simulateur publie des valeurs aléatoires sur un topic à intervalles réguliers.  
Pour créer une nouvelle variable simulée, publiez un message JSON (ou une chaîne
de paires clé=valeur séparées par des virgules) sur le topic **simulateur/new**.
Pour supprimer une variable, publiez son nom sur le topic **simulateur/delete**.

### Format de la charge utile

| Champ     | Type    | Description                                           |
|-----------|---------|-------------------------------------------------------|
| `name`    | string  | Nom unique de la variable (ex : "A", "B"…).           |
| `min`     | float   | Valeur minimale du tirage aléatoire.                  |
| `max`     | float   | Valeur maximale du tirage aléatoire.                  |
| `noise`   | float   | Écart‑type du bruit gaussien (0 → pas de bruit).      |
| `period`  | float   | Intervalle en secondes entre deux publications.       |

#### Exemple JSON

```json
{
  "name": "B",
  "min": 0,
  "max": 100,
  "noise": 5,
  "period": 3
}"""


# --------------------------------------------------------------------------- #
# Classe représentant une variable simulée
# --------------------------------------------------------------------------- #

class SimVar:
    """Variable simulée qui génère une sinusoïde + bruit gaussien."""

    def __init__(self, name, period=60.0,
                 min_val=15.0, max_val=61.0,
                 noise_stddev=5.0,
                 period_publish=0.50):
        self.name = name
        self.period = float(period)
        self.min = float(min_val)
        self.max = float(max_val)
        self.noise_stddev = float(noise_stddev)
        self.period_publish = float(period_publish)

        self._phase = 0.0
        self._next_time = time.time()
        self.queue = deque()

    def _sinusoid_value(self):
        """Valeur sinusoïdale brute (entre min et max)."""
        amplitude = (self.max - self.min) / 2.0
        mid_point = (self.max + self.min) / 2.0
        return mid_point + amplitude * math.sin(self._phase)

    def _apply_noise(self, val):
        """Ajoute un bruit gaussien."""
        if self.noise_stddev == 0:
            return val
        return val + random.gauss(0, self.noise_stddev)

    def publish_params(self, client: mqtt.Client):
        """Publie chaque paramètre sur son propre topic."""
        params = {
            TOPIC_PARAM_PERIOD.format(self.name): str(self.period),
            TOPIC_PARAM_MIN.format(self.name): str(self.min),
            TOPIC_PARAM_MAX.format(self.name): str(self.max),
            TOPIC_PARAM_NOISE.format(self.name): str(self.noise_stddev),
            TOPIC_PARAM_PERIOD_PUBLISH.format(self.name): str(self.period_publish)
        }
        for topic, value in params.items():
            client.publish(topic, payload=value, qos=1, retain=True)

    def delete_params(self, client: mqtt.Client):  # NOUVEAU: Méthode pour supprimer
        """Supprime tous les paramètres en publiant des chaînes vides."""
        topics = [
            TOPIC_PARAM_PERIOD.format(self.name),
            TOPIC_PARAM_MIN.format(self.name),
            TOPIC_PARAM_MAX.format(self.name),
            TOPIC_PARAM_NOISE.format(self.name),
            TOPIC_PARAM_PERIOD_PUBLISH.format(self.name),
            f"simulateur/{self.name}/value"
        ]
        for topic in topics:
            client.publish(topic, payload="", qos=1, retain=True)

    def update_param(self, param_name: str, value: float):
        """Met à jour un paramètre spécifique."""
        if param_name == 'period':
            self.period = float(value)
        elif param_name == 'min':
            self.min = float(value)
        elif param_name == 'max':
            self.max = float(value)
        elif param_name == 'noise':
            self.noise_stddev = float(value)
        elif param_name == 'period_publish':
            self.period_publish = float(value)

    def publish_pending(self, client: mqtt.Client):
        """Publie les messages qui sont arrivés depuis le dernier appel."""
        while self.queue:
            payload = self.queue.popleft()
            topic = f"simulateur/{self.name}/value"
            client.publish(topic, payload=payload, qos=0)

    def step(self, now: float):
        """
        Gère la génération de valeurs et l'envoi à intervalles réguliers.
        Doit être appelé en boucle principale (ex. 10 ms).
        """
        if now >= self._next_time:
            # ---- Génération d'une nouvelle valeur ----
            base_val = self._sinusoid_value()
            value = self._apply_noise(base_val)

            # On empile le message à publier
            self.queue.append(f"{value:.4f}")

            # Mise à jour pour l'intervalle suivant
            self._next_time += self.period_publish

            # Incrémenter la phase (en radians) : 2π * Δt / période
            delta_t = self.period_publish
            self._phase += 2.0 * math.pi * delta_t / self.period
            if self._phase > 2.0 * math.pi:
                self._phase -= 2.0 * math.pi


# --------------------------------------------------------------------------- #
# Variables globales
# --------------------------------------------------------------------------- #

simvars = {}  # dictionnaire name → SimVar


# --------------------------------------------------------------------------- #
# Callbacks MQTT
# --------------------------------------------------------------------------- #

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ Connecté au broker")
        # Publication du README
        client.publish(TOPIC_README,
                       payload=SIMULATOR_README_TEXT.strip(),
                       retain=True,
                       qos=0)

        client.subscribe(TOPIC_NEW)
        client.subscribe(TOPIC_DELETE)  # NOUVEAU: S'abonner au topic de suppression

        # S'abonner aux topics de paramètres pour toutes les variables
        for pattern in [TOPIC_PARAM_PERIOD, TOPIC_PARAM_MIN, TOPIC_PARAM_MAX,
                        TOPIC_PARAM_NOISE, TOPIC_PARAM_PERIOD_PUBLISH]:
            client.subscribe(pattern.format('+'))

        # Création de la variable A via le topic new
        init_params = {
            "name": "A",
            "period": 60.0,
            "min": 15.0,
            "max": 61.0,
            "noise": 5.0,
            "period_publish": 0.50
        }
        client.publish(TOPIC_NEW, payload=json.dumps(init_params), qos=1)
    else:
        print(f"❌ Connexion échouée (code {rc})")


def on_message(client, userdata, msg):
    try:
        # Gestion des nouvelles variables simulées
        if msg.topic == TOPIC_NEW:
            payload = json.loads(msg.payload)
            name = payload["name"]
            sv = SimVar(
                name=name,
                period=float(payload.get("period", 60.0)),
                min_val=float(payload.get("min", 15.0)),
                max_val=float(payload.get("max", 61.0)),
                noise_stddev=float(payload.get("noise", 5.0)),
                period_publish=float(payload.get("period_publish", 0.5))
            )
            simvars[name] = sv
            sv.publish_params(client)
            print(f"✅ Variable '{name}' créée avec les paramètres spécifiés")
            return

        # Gestion de la suppression de variables simulées
        if msg.topic == TOPIC_DELETE:
            name = msg.payload.decode('utf-8').strip()
            if name in simvars:
                simvars[name].delete_params(client)
                del simvars[name]
                print(f"✅ Variable '{name}' supprimée")
            else:
                print(f"⚠️ Variable '{name}' non trouvée")
            return

        # Gestion des modifications de paramètres
        parts = msg.topic.split('/')
        if len(parts) == 4 and parts[0] == 'simulateur' and parts[2] == 'parameters':
            name = parts[1]
            param = parts[3]
            if name in simvars:
                try:
                    value = float(msg.payload.decode())
                    simvars[name].update_param(param, value)
                    print(f"✅ Paramètre {param} de {name} mis à jour à {value}")
                except ValueError as e:
                    print(f"❌ Valeur invalide pour {param}: {e}")

    except Exception as e:
        print(f"❌ Erreur lors du traitement du message : {e}")


# --------------------------------------------------------------------------- #
# Boucle principale : publication et gestion des variables
# --------------------------------------------------------------------------- #

def main_loop(client: mqtt.Client):
    try:
        while True:
            now = time.time()
            for sv in list(simvars.values()):
                sv.step(now)
                sv.publish_pending(client)
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\n🛑 Arrêt du simulateur")
        client.disconnect()


# --------------------------------------------------------------------------- #
# Construction du client MQTT + lancement
# --------------------------------------------------------------------------- #

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
client.loop_start()

print("\n▶️ Simulateur MQTT démarré – appuyez sur Ctrl‑C pour arrêter.")
main_loop(client)