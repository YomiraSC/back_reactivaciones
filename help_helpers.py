import datetime
import json

def agregar_coma_al_dni(dni):
    """Recibe un número de DNI y devuelve el mismo número con una coma al final."""
    return f"{dni},"

def formatear_conversacion(mensajes):
    """
    Formatea una conversación a partir de una lista de mensajes recuperados desde Firestore.
    Cada mensaje debe tener las claves: 'mensaje' y 'sender' (True si es cliente, False si es el bot).
    """
    conversacion_formateada = []

    for msg in sorted(mensajes, key=lambda x: x.get("fecha", "")):
        rol = "Cliente" if msg.get("sender", True) else "Asistente"
        texto = msg.get("mensaje", "").strip()
        if texto:
            conversacion_formateada.append(f"{rol}: {texto}")

    return "\n".join(conversacion_formateada)


def quitar_coma_al_dni(dni):
    """Recibe un DNI con posible coma al final y la elimina si está presente."""
    return dni.rstrip(",")
