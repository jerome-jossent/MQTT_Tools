import cv2
import numpy as np
import paho.mqtt.client as mqtt
import time
import base64
from io import BytesIO
from PIL import Image

# Configuration
MQTT_BROKER = "localhost"  # Adresse de votre broker MQTT
MQTT_PORT = 1883
TOPIC_IMAGE = "portik/image"
TOPIC_VITESSE = "portik/vitesse"
TOPIC_VITESSE_FULL = "portik/vitesse_json"

# Paramètres de traitement
CROP_LEFT = 160
CROP_RIGHT = 250
CROP_TOP = 20
CROP_BOTTOM = 35
TOLERANCE = 0.80  # 80% de similarité
ZONE_RECHERCHE = 0.25  # 25% premières lignes
PIXELS_TO_MM = (680/500 + 862/500) / 2  # Conversion pixels vers mm (à ajuster selon votre caméra)

# Variables globales
crop_prec = None
time_prec = None

first_cropped_is_saved = False

def crop_image(image):
    global CROP_TOP, CROP_BOTTOM, CROP_LEFT, CROP_RIGHT
    h, w = image.shape[:2]
    return image[CROP_TOP:h - CROP_BOTTOM, CROP_LEFT:w - CROP_RIGHT]

def calculate_displacement(crop_current, crop_previous):
    """Calcule le déplacement vertical entre deux images"""
    h, w = crop_previous.shape[:2]

    # Zone de référence : 25% premières lignes de l'image précédente
    offset = 0
    zone_height = int(h * ZONE_RECHERCHE)
    template = crop_previous[offset:zone_height+offset, :]

    # Recherche de correspondance par template matching
    result = cv2.matchTemplate(crop_current, template, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

    displacement = max_loc[1] - offset
    return displacement, max_val

    # Vérification du seuil de tolérance
    if max_val >= TOLERANCE:
        # Déplacement vertical (positif = vers le bas)
        displacement = max_loc[1]
        return displacement, max_val
    else:
        return None, max_val


def on_message(client, userdata, msg):
    """Callback appelé à la réception d'une image"""
    global crop_prec, time_prec, first_cropped_is_saved

    time_current = time.time()

    try:
        # Décodage de l'image
        img_data = msg.payload
        nparr = np.frombuffer(img_data, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if image is None:
            print("Erreur: impossible de décoder l'image")
            return

        # 1) Cropper l'image
        crop = crop_image(image)

        if not first_cropped_is_saved:
            first_cropped_is_saved = True
            cv2.imwrite("first_cropped.jpg", crop)

        # Convertir en niveaux de gris pour le traitement
        crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

        # 2) Comparer avec l'image précédente (sauf première fois)
        if crop_prec is not None and time_prec is not None:
            displacement, confidence = calculate_displacement(crop_gray, crop_prec)

            if displacement is not None:
                # Calcul du temps écoulé
                delta_time = time_current - time_prec

                # Vitesse en pixels/seconde
                vitesse_pixels = displacement / delta_time

                # Conversion en m/s
                vitesse_mm_s = vitesse_pixels / PIXELS_TO_MM
                vitesse_m_s = vitesse_mm_s / 1000.0

                # Publication sur MQTT
                vitesse_data = {
                    "vitesse_m_s": round(vitesse_m_s, 3),
                    "vitesse_pixels_s": round(vitesse_pixels, 2),
                    "deplacement_pixels": displacement,
                    "delta_temps_ms": round(delta_time * 1000, 1),
                    "confiance": round(confidence, 2)
                }

                client.publish(TOPIC_VITESSE, str(vitesse_m_s))
                client.publish(TOPIC_VITESSE_FULL, str(vitesse_data))
                print(f"Vitesse: {vitesse_m_s:.3f} m/s (déplacement: {displacement}px, confiance: {confidence:.2f})")
            else:
                print(f"Pas de correspondance trouvée (confiance: {confidence:.2f} < {TOLERANCE})")

        # 3) Mise à jour de l'image précédente
        crop_prec = crop_gray.copy()
        time_prec = time_current

    except Exception as e:
        print(f"Erreur lors du traitement: {e}")


def on_connect(client, userdata, flags, rc):
    print(f"Connecté au broker MQTT")
    client.subscribe(TOPIC_IMAGE)
    print(f"Souscription au topic: \"{TOPIC_IMAGE}\"")


def main():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    print(f"Connexion au broker MQTT {MQTT_BROKER}:{MQTT_PORT}...")
    client.connect(MQTT_BROKER, MQTT_PORT, 60)

    print("En attente d'images...")
    client.loop_forever()

if __name__ == "__main__":
    main()