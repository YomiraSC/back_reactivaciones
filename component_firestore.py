from datetime import datetime, timedelta
import pytz
from google.cloud import firestore

class DataBaseFirestoreManager:
    def __init__(self):
        self.db = self._connect()
        self.tz = pytz.timezone("America/Lima")
    
    def _connect(self):
        """Establece la conexión a Firestore."""
        try:
            db = firestore.Client()
            print("✅ Conexión exitosa a Firestore")
            return db
        except Exception as e:
            print(f"❌ ERROR al conectar con Firestore: {e}")
            return None

    def _reconnect_if_needed(self):
        """Verifica si la conexión sigue activa y la restablece si es necesario."""
        try:
            _ = self.db.collection("reactivaciones").document("connection_test").get()  # Verificar si se puede leer un documento
        except Exception as e:
            print(f"⚠ Conexión perdida, intentando reconectar... {e}")
            self.db = self._connect()

    def crear_documento(self, celular, id_cliente, id_bot, mensaje, sender):
        """Crea un documento en la colección 'clientes'."""
        self._reconnect_if_needed()
        
        data = {
            "celular": celular,
            "fecha": firestore.SERVER_TIMESTAMP, 
            "id_cliente": id_cliente,
            "id_bot": id_bot,
            "mensaje": mensaje,
            "sender": sender
        }
        try:
            doc_ref = self.db.collection("reactivaciones").document() 
            doc_ref.set(data)
            print("✅ Documento creado exitosamente.")
        except Exception as e:
            print(f"❌ Error al crear el documento: {e}")

    def recuperar_mensajes_hoy(self, id_bot, celular):
        """Recupera todos los mensajes de hoy para un cliente específico."""
        self._reconnect_if_needed()

        try:
            now = datetime.now(self.tz)
            start_datetime = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_datetime = start_datetime + timedelta(days=1)

            query = (self.db.collection("reactivaciones")
                     .where("id_bot", "==", id_bot)
                     .where("celular", "==", celular)
                     .where("fecha", ">=", start_datetime)
                     .where("fecha", "<", end_datetime))
            
            docs = query.stream()
            mensajes = [doc.to_dict() for doc in docs]
            return mensajes
        
        except Exception as e:
            print(f"❌ Error al recuperar mensajes de hoy: {e}")
            return []

    def recuperar_mensajes_hoy_alt(self, id_bot, celular):
        """Recupera todos los mensajes de hoy (en UTC) para un cliente específico."""
        self._reconnect_if_needed()

        try:
            # Mostrar zona horaria usada
            print("🕒 Usando zona horaria UTC para filtrado")

            # Obtener la fecha actual en UTC y definir los rangos
            now = datetime.utcnow()
            print(f"🔸 Ahora (UTC): {now}")

            start_datetime = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_datetime = start_datetime + timedelta(days=1)

            print(f"🗓️ Rango de búsqueda:")
            print(f"   - Desde: {start_datetime}")
            print(f"   - Hasta: {end_datetime}")
            print(f"🔍 Buscando mensajes para celular: {celular}, id_bot: {id_bot}")

            query = (self.db.collection("reactivaciones")
                    .where(field_path="id_bot", op_string="==", value=id_bot)
                    .where(field_path="celular", op_string="==", value=celular)
                    .where(field_path="fecha", op_string=">=", value=start_datetime)
                    .where(field_path="fecha", op_string="<", value=end_datetime))
            
            docs = query.stream()
            mensajes = [doc.to_dict() for doc in docs]

            print(f"📦 Mensajes recuperados: {len(mensajes)}")
            for i, msg in enumerate(mensajes, 1):
                print(f"   {i}. {msg}")

            return mensajes

        except Exception as e:
            print(f"❌ Error al recuperar mensajes de hoy: {e}")
            return []