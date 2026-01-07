# app.py — Bot de REACTIVACIONES
import os
import re
import json
from datetime import datetime
from typing import Any, Dict, Optional

from flask import Flask, request, Response, g

# LangChain / LangGraph
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_elasticsearch import ElasticsearchStore
from langchain.tools import tool, Tool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool

# Meta WhatsApp Cloud API
import requests

# ==== TUS COMPONENTES ====
from api_keys import openai_api_key
from component_postgresql import DataBasePostgreSQLManager  # ← mismo manager, distinto esquema
from component_firestore import DataBaseFirestoreManager
from component_openai import OpenAIManager   # si ya lo usas (para consistencia)
# RAG helper (igual que en código de pago)
#   Necesitas ELASTIC_URL, ELASTIC_USER, ELASTIC_PASSWORD, ELASTIC_INDEX configurados.

# Para Vercel mirror (opcional, como en tu otro bot)
import threading, time, uuid

VERCEL_WEBHOOK_URL = "https://crmreactivaciones.vercel.app/api/webhook/whatsapp"

# -------------------------------------------------------------------
# 0) CONFIG GLOBAL (ENV)
# -------------------------------------------------------------------
os.environ["OPENAI_API_KEY"] = openai_api_key

WHATSAPP_TOKEN   = "EAAaN7lCC8MIBPbE8sv5RmeZAtna9Sbx4Hh7xciNkqtAQWhW7nPuxe6H2ZAtGV8SeJqmQSXWWkdv80vrhidMjlzxjZAXZBWSBHuhZBkOvJoJZBKS1tCZBe9UQZAL5b4tH2ZAEwRJIAtZC316R8s4mSIAzPV5CgvkXSaJw08F1lXSIcnexkDePW43Jm9q1hvmDhCkgZDZD"
PHONE_NUMBER_ID  = "734525176415715"
VERIFY_TOKEN     = "token_reactiva"

# Cadena de conexión a PostgreSQL para el Checkpointer
os.environ["DB_URI"] = "postgresql://maquisistema:sayainvestments1601@34.82.84.15:5432/bdMaqui?sslmode=disable"

# Credenciales de Elasticsearch RAG
os.environ["ELASTIC_URL"]      = "http://34.83.130.207:9200"
os.environ["ELASTIC_USER"]     = "elastic"
os.environ["ELASTIC_PASSWORD"] = "P=IK-doIv668orND5FmG"
os.environ["ELASTIC_INDEX"]    = "reactivaciones-v1"

API_URL = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_TOKEN}",
    "Content-Type": "application/json",
}

# Cargar las variables de entorno a variables locales
ELASTIC_URL      = os.environ["ELASTIC_URL"]
ELASTIC_USER     = os.environ["ELASTIC_USER"]
ELASTIC_PASSWORD = os.environ["ELASTIC_PASSWORD"]
ELASTIC_INDEX    = os.environ["ELASTIC_INDEX"]
DB_URI           = os.environ["DB_URI"]


# -------------------------------------------------------------------
# 1) HELPERS META
# -------------------------------------------------------------------
# def send_whatsapp(to_number: str, message_body: str) -> str:
#     to = (to_number or "").replace("whatsapp:", "").replace("+", "").strip()
#     payload = {
#         "messaging_product": "whatsapp",
#         "to": to,
#         "type": "text",
#         "text": {"body": message_body},
#     }
#     try:
#         r = requests.post(API_URL, headers=HEADERS, json=payload, timeout=15)
#     except Exception as e:
#         print(f"[META SEND] request error: {e}", flush=True)
#         return ""

#     print(f"[META SEND] status={r.status_code} to={to}", flush=True)
#     print(f"[META SEND] resp={r.text}", flush=True)
#     return r.text


def send_whatsapp(to_number: str, message_body: str) -> str:
    to = (to_number or "").replace("whatsapp:", "").replace("+", "").strip()
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message_body},
    }
    try:
        r = requests.post(API_URL, headers=HEADERS, json=payload, timeout=15)
    except Exception as e:
        print(f"[META SEND] request error: {e}", flush=True)
        return ""

    print(f"[META SEND] status={r.status_code} to={to}", flush=True)
    print(f"[META SEND] resp={r.text}", flush=True)

    # ⬇️ NUEVO: si fue OK, registrar mensaje_out con el id de Meta
    if 200 <= r.status_code < 300:
        try:
            data = r.json()
            mid = (data.get("messages") or [{}])[0].get("id")
            if mid:
                print(f"[META SEND] message_id={mid}", flush=True)
                try:
                    # Usa un nombre genérico para respuestas del bot
                    postgresql.registrar_mensaje_out(
                        id_msg=mid,
                        phone_to=to_number,        # el manager normaliza
                        template_name="bot_reply", # si fuera plantilla real, cámbialo
                        template_lang="es",
                        campanha_id=None
                    )
                except Exception as e2:
                    print(f"[META SEND] warn: no pude registrar mensaje_out ({e2})", flush=True)
        except Exception:
            pass

    return r.text



# -------------------------------------------------------------------
# 2) INSTANCIAS DE TUS COMPONENTES
# -------------------------------------------------------------------

postgresql = DataBasePostgreSQLManager(db_uri=DB_URI, schema="reactivaciones")
firestore  = DataBaseFirestoreManager()
openai_mgr = OpenAIManager()

# 🔎 Ping inmediato a PostgreSQL al arrancar
def _pg_startup_ping():
    try:
        with postgresql.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SHOW search_path;")
                path = cur.fetchone()[0]
                cur.execute("SELECT 1;")
                ok = cur.fetchone()[0]
                print(f"[PG] Startup ping OK={ok} | search_path={path}", flush=True)
    except Exception as e:
        print(f"[PG] Startup ping FAILED: {e}", flush=True)
_pg_startup_ping()

# -------------------------------------------------------------------
# 3) RAG (Elasticsearch + OpenAI embeddings) — igual que tu otro bot
# -------------------------------------------------------------------
db_query = ElasticsearchStore(
    es_url=ELASTIC_URL,
    es_user=ELASTIC_USER,
    es_password=ELASTIC_PASSWORD,
    index_name=ELASTIC_INDEX,
    embedding=OpenAIEmbeddings(),
)
retriever = db_query.as_retriever()

_raw_rag_tool = retriever.as_tool(
    name="consultar_informacion_rag",
    description=("Busca y resume información de documentos/FAQs. "
                 "Úsala cuando el cliente pida detalles informativos (precios, pasos, canales, tiempos, etc.)."),
)

def _rag(query: str) -> str:
    print("🛠️ [TOOL] consultar_informacion_rag | query:", query, flush=True)
    return _raw_rag_tool.func(query)

consultar_informacion_rag = Tool(
    name="consultar_informacion_rag",
    func=_rag,
    description=_raw_rag_tool.description,
)

# -------------------------------------------------------------------
# 4) HERRAMIENTAS PRINCIPALES (reactivaciones)
# -------------------------------------------------------------------


# 4.1 Clasificar intención (estado) y guardar en PG (esquema reactivaciones)
@tool(
    "clasificar_intencion",
    description=(
        "Clasifica el último mensaje del cliente en uno de 6 estados y devuelve una respuesta lista para WhatsApp. "
        "Estados: 'Interesado en reactivar' | 'Fecha de Pago' | 'Indeciso' | 'Reclamo activo' | "
        "'Solicita devolucion de dinero' | 'No interesado'. Guarda el estado en BD."
    ),
)
def clasificar_intencion(payload: str) -> str:
    """
    payload: texto crudo del usuario (no JSON).
    Efecto: Guarda {estado, detalle} en Postgres asociado al número (g.sender).
    Retorno: 'reply' listo para WhatsApp (1–3 oraciones, empático y específico).
    """
    print(f"🧭 [CLASIFICAR] payload={payload!r}", flush=True)

    llm = ChatOpenAI(model="gpt-4.1-2025-04-14", temperature=0.2)

    system = SystemMessage(content="""
    Eres un servicio de CLASIFICACIÓN + RESPUESTA para reactivaciones en WhatsApp.
    DEVUELVE SOLO UN JSON VÁLIDO con la forma exacta:

    {
    "estado": "<'Interesado en reactivar' | 'Fecha de Pago' | 'Indeciso' | 'Reclamo activo' | 'Solicita devolucion de dinero' | 'No interesado'>",
    "reply": "<mensaje listo para enviar por WhatsApp en 1–3 oraciones, cordial, empático y específico al caso>",
    "detalle": "<opcional: breve resumen para CRM (motivo, fecha prometida si aplica, nro de caso, objeción clave, etc.)>",
    "faltantes": ["<dato1>", "<dato2>"],
    "derivar": <true|false>
    }

    Criterios rápidos:
    - Interesado en reactivar: expresa intención concreta de avanzar/reactivar.
    - Fecha de Pago: la persona da o solicita fijar SOLO la fecha de pago (no pidas monto).
    - Indeciso: dudas/objeciones; necesita ayuda para decidir.
    - Reclamo activo: problema/caso abierto en curso.
    - Solicita devolucion de dinero: lo pide explícitamente.
    - No interesado: rechaza o pide no insistir.

    Política específica para “Solicita devolucion de dinero”:
    - El bot **NO gestiona** devoluciones.
    - El bot **NO pregunta cuotas ni detalles**.
    - El bot **NO promete agilizar, acelerar ni priorizar** devoluciones.
    - Responde SIEMPRE indicando que debe comunicarse directamente con el **Contact Center**.
    - Opcional: puedes añadir una sola línea aclarando que, según contrato, si solo pagó una cuota no corresponde devolución.
    - Nunca vuelvas a pedir información adicional.


    Redacción del "reply":
    - Máximo 3 oraciones. Tono cercano y profesional.
    - Si 'Fecha de Pago': pide únicamente la fecha exacta (YYYY-MM-DD); **no pidas monto**.
    - Si 'Reclamo activo' o 'Solicita devolucion de dinero': ofrece derivación/contact center, sin más.
    - Si 'Interesado en reactivar': propone pase a un asesor para gestión.
    - Si 'Indeciso': ofrece 1 beneficio y una pregunta que destrabe.
    - Si 'No interesado': agradece y cierra respetuosamente.

    Reglas:
    - 1 sola pregunta focalizada cuando existan 'faltantes'.
    - 'derivar' = true cuando: (a) reclamo activo, (b) ya hay info clave para cerrar/agendar, o (c) el usuario lo pide.
    """)



    raw = llm.invoke([system, HumanMessage(content=payload or "")]).content
    print(f"🧭 [CLASIFICAR] raw_json={raw!r}", flush=True)

    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"⚠️ [CLASIFICAR] JSON inválido ({e}); fallback.", flush=True)
        # Fallback tipo respuesta-a-campaña (neutral y útil, sin pedir monto)
        return ("¡Gracias por responder! Estoy para ayudarte con tu caso. "
                "¿Podrías indicarme en una línea si prefieres fijar una fecha de pago, tienes un reclamo activo "
                "o necesitas apoyo para decidir?")

    estado    = (data.get("estado") or "").strip()
    reply     = (data.get("reply") or "").strip()
    detalle   = (data.get("detalle") or "").strip() or None
    faltantes = data.get("faltantes") or []
    derivar   = bool(data.get("derivar"))

    print(f"🧭 [CLASIFICAR] estado={estado!r} derivar={derivar} faltantes={faltantes}", flush=True)

    # Guardado en PG (tu propio método)
    sender = getattr(g, "sender", None)
    try:
        ok = postgresql.registrar_estado_reactivacion(sender, estado, detalle)
        print(f"🗃️ [CLASIFICAR] registrar_estado_reactivacion -> {ok}", flush=True)
    except Exception as e:
        print(f"❌ [CLASIFICAR] Error guardando estado en PG: {e}", flush=True)

    # Hook futuro para derivación automática si lo deseas.
    # if derivar: enqueue/webhook a asesoras

    return reply or ("Gracias por responder. Para ayudarte mejor, ¿me confirmas si deseas fijar una fecha de pago, "
                     "tienes un reclamo activo o necesitas orientación para decidir?")


# 4.2 Registrar promesa de pago (solo fecha; SIN monto)
@tool(
    "registrar_promesa_pago",
    description=(
        "Registra una promesa de pago en BD. "
        "Recibe texto libre del cliente (p.ej. 'pago el 15', 'mañana pago') y extrae fecha ISO."
    ),
)
def registrar_promesa_pago(payload: str) -> str:
    """
    payload: texto crudo del usuario (no JSON).
    Efecto: Guarda promesa {fecha, observacion} asociada al número (g.sender).
    Retorno: confirmación breve.
    """
    print(f"💳 [PROMESA] payload={payload!r}", flush=True)

    llm = ChatOpenAI(model="gpt-4.1-2025-04-14", temperature=0)

    system = SystemMessage(content="""
        Extrae de un texto de promesa de pago un JSON:

        {
        "fecha_iso": "<YYYY-MM-DD si se entiende; si no, vacío>",
        "observacion": "<opcional breve, resumir lo dicho>"
        }

        - Si dice "mañana" o una fecha parcial, intenta resolver a YYYY-MM-DD (asume zona horaria local).
        - NO infieras montos, no los pidas.
        - Devuelve SOLO el JSON.
    """)

    raw = llm.invoke([system, HumanMessage(content=payload or "")]).content
    print(f"💳 [PROMESA] raw_json={raw!r}", flush=True)

    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"⚠️ [PROMESA] JSON inválido ({e}); pido fecha.", flush=True)
        return "Para registrar tu promesa necesito la fecha (YYYY-MM-DD). ¿Cuál sería?"

    fecha_iso   = (data.get("fecha_iso") or "").strip()
    observacion = (data.get("observacion") or "").strip() or None

    if not fecha_iso:
        return "¿Me confirmas la fecha de tu pago en formato YYYY-MM-DD?"

    # Guardado en PG (tu propio método)
    sender = getattr(g, "sender", None)
    try:
        ok = postgresql.registrar_promesa_pago(sender, fecha_iso, None, observacion)  # monto=None
        print(f"🗃️ [PROMESA] registrar_promesa_pago -> {ok}", flush=True)
    except Exception as e:
        print(f"❌ [PROMESA] Error guardando promesa en PG: {e}", flush=True)
        return "Ocurrió un problema al registrar tu promesa. ¿Puedes intentar nuevamente?"

    return f"¡Listo! Registré tu promesa para el {fecha_iso}. Si deseas ajustar algo, dime."



# -------------------------------------------------------------------
# 5) PROMPT Y AGENTE
# -------------------------------------------------------------------
prompt = ChatPromptTemplate.from_messages(
    [
        ("system",
         """
Eres el asistente de **Reactivaciones (Maqui+)**. Responde en 1–3 oraciones, estilo WhatsApp, claro y empático.

Cuándo usar herramientas:
1) Si el cliente explica su situación, dudas, objeciones o intención → llama SIEMPRE a `clasificar_intencion`.
2) Si el cliente pide información (precios, pasos, canales, etc.) → usa `consultar_informacion_rag`.
3) Si el cliente fija/propone fecha de pago, o pide registrar promesa → usa `registrar_promesa_pago`.

Reglas:
- Nunca menciones herramientas ni “clasificación”.
- **Si una herramienta devuelve un 'reply', úsalo tal cual como tu respuesta final (no agregues nada).**
- Usa un tono respetuoso, cercano y proactivo.
- Si el usuario inicia con “hola” sin contexto, invítalo brevemente a contar su situación para ayudarlo a reactivar.
- En caso de **“solicitud de devolución de dinero”**, tu única respuesta válida es derivar al Contact Center, sin ofrecer agilizar ni gestionar devoluciones directamente. No pidas confirmación de cuotas ni detalles adicionales.
         """),
        ("human", "{messages}"),
    ]
)


# Memoria (checkpoint) con Postgres
connection_kwargs = {"autocommit": True, "prepare_threshold": 0}
pool = ConnectionPool(conninfo=DB_URI, max_size=20, kwargs=connection_kwargs)
checkpointer = PostgresSaver(pool)

model = ChatOpenAI(model="gpt-4.1-2025-04-14")

toolkit = [
    consultar_informacion_rag,
    clasificar_intencion,
    registrar_promesa_pago,
]

agent_executor = create_react_agent(
    model=model,
    tools=toolkit,
    checkpointer=checkpointer,
    prompt=prompt
)

# -------------------------------------------------------------------
# 6) FORWARD A VERCEL (opcional, igual que el otro bot)
# -------------------------------------------------------------------
def forward_to_vercel(raw_meta_body=None, sender=None, message_text=None):
    try:
        if raw_meta_body:
            payload = raw_meta_body
        else:
            if not sender:
                return
            payload = {
                "object": "whatsapp_business_account",
                "entry": [{
                    "id": PHONE_NUMBER_ID,
                    "changes": [{
                        "field": "messages",
                        "value": {
                            "messages": [{
                                "from": sender,
                                "id": f"local_{uuid.uuid4().hex}",
                                "timestamp": str(int(time.time())),
                                "text": {"body": message_text or ""}
                            }]
                        }
                    }]
                }]
            }

        r = requests.post(VERCEL_WEBHOOK_URL, json=payload, timeout=3)
        print(f"[VERCEL] status={r.status_code} body={r.text[:200]}", flush=True)
    except Exception as e:
        print(f"[VERCEL] forward error: {e}", flush=True)

# -------------------------------------------------------------------
# 7) FLASK APP (Webhook Meta)
# -------------------------------------------------------------------
app = Flask(__name__)

@app.route("/hello", methods=["GET", "POST"])
def main():
    # Verificación webhook
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return Response(challenge, status=200, mimetype="text/plain")
        return "Error de verificación", 403

    # Evento entrante
    if request.is_json:
        data = request.get_json()

        # =====================================================
        # 🔴 AQUÍ: LOG RAW DEL WEBHOOK (PRIMERA COSA)
        # =====================================================
        try:
            change = (data.get("entry") or [{}])[0].get("changes", [{}])[0]
            value = change.get("value", {}) or {}

            event_type = "unknown"
            if value.get("messages"):
                event_type = "message"
            elif value.get("statuses"):
                event_type = "status"

            postgresql.registrar_webhook_log(
                event_type=event_type,
                payload=data
            )
        except Exception as e:
            print(f"⚠️ [WEBHOOK_LOG] error guardando payload: {e}", flush=True)
        # =====================================================

        # envio a vercel
        threading.Thread(
            target=forward_to_vercel, args=(data, None, None), daemon=True
        ).start()
        # fin envio a vercel


        try:
            change = data["entry"][0]["changes"][0]
            value = change.get("value", {})
            
            # --- [NUEVO] Captura de estados de entrega/lectura/falla ---
            statuses = value.get("statuses", [])
            if statuses:
                for st in statuses:
                    id_msg       = st.get("id")
                    status       = st.get("status")          # sent|delivered|read|failed
                    ts_unix      = st.get("timestamp")       # str con segundos
                    recipient_id = st.get("recipient_id")
                    pricing      = st.get("pricing")
                    conversation = st.get("conversation")
                    errors       = st.get("errors")

                    # 1) Asegura existencia de mensaje_out (por si lo envió el CRM y no este bot)
                    try:
                        postgresql.registrar_mensaje_out(
                            id_msg=id_msg,
                            phone_to=recipient_id or "",
                            template_name="desconocida",   # no viene en el status
                            template_lang="",
                            campanha_id=None
                        )
                    except Exception as e:
                        print(f"[STATUS] warn upsert mensaje_out: {e}", flush=True)

                    # 2) Inserta evento de estado
                    try:
                        postgresql.registrar_status_event(
                            id_msg=id_msg,
                            estado=status,
                            ts_unix=int(ts_unix) if ts_unix else None,
                            recipient_id=recipient_id,
                            pricing_json=json.dumps(pricing) if pricing else None,
                            conversation_json=json.dumps(conversation) if conversation else None,
                            errors_json=json.dumps(errors) if errors else None
                        )
                    except Exception as e:
                        print(f"[STATUS] error insert status_event: {e}", flush=True)

                # Importante: responde 200 para que Meta no reintente
                return Response("EVENT_RECEIVED", status=200)
            # --- [FIN NUEVO] ---

            messages = value.get("messages", [])
            if not messages:
                return Response("EVENT_RECEIVED", status=200)
            msg = messages[0]
            incoming_msg = (msg.get("text") or {}).get("body", "")
            sender = msg.get("from", "")
        except Exception as e:
            print(f"❌ Parse JSON Meta: {e} | data={data}", flush=True)
            return Response("EVENT_RECEIVED", status=200)
    else:
        incoming_msg = request.form.get("Body", "").strip()
        sender       = request.form.get("From", "").replace("whatsapp:", "").strip()

    # Sender como contexto
    g.sender = sender
    print("🟢 [DEBUG] IN:", incoming_msg, "FROM:", sender, flush=True)

    # Log en Firestore
    try:
        firestore.crear_documento(sender, None, "reactivaciones", incoming_msg, True)
    except Exception as e:
        print(f"❌ Firestore IN error: {e}", flush=True)

    # Invocar agente
    try:
        thread_id = f"rc-{sender}"
        config = {"configurable": {"thread_id": thread_id}}
        result = agent_executor.invoke({"messages": [HumanMessage(content=(incoming_msg or " "))]}, config=config)
        response_text = result["messages"][-1].content
    except Exception as e:
        print(f"⚠️ Error al invocar agente: {e}", flush=True)
        response_text = "Hubo un problema al procesar tu solicitud. ¿Puedes intentar nuevamente?"

    # Log OUT y enviar por WhatsApp
    try:
        firestore.crear_documento(sender, None, "reactivaciones", response_text, False)
    except Exception as e:
        print(f"❌ Firestore OUT error: {e}", flush=True)

    send_whatsapp(sender, response_text)
    return Response("EVENT_RECEIVED", status=200)

# -------------------------------------------------------------------
# 8) RUN LOCAL
# -------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
