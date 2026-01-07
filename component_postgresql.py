# component_postgresql.py — Reactivaciones
import os
import re
import json
from datetime import datetime
from typing import Optional, Dict, Any
from psycopg_pool import ConnectionPool


class DataBasePostgreSQLManager:
    """
    Estilo 'código de pago':
    - Usa psycopg_pool.ConnectionPool con DB_URI
    - En cada método: SET search_path TO <schema>;
    - Placeholders %s y tuplas de parámetros
    - Manejo dentro de with (commit al salir)
    """

    def __init__(self, db_uri: Optional[str] = None, schema: str = "reactivaciones"):
        self.db_uri = db_uri or os.environ.get("DB_URI")
        if not self.db_uri:
            raise RuntimeError("DB_URI no definido")
        self.schema = schema
        self.pool = ConnectionPool(conninfo=self.db_uri)

    # ---------- helpers ----------
    def _set_schema(self, cur) -> None:
        cur.execute(f"SET search_path TO {self.schema};")

    def _row_to_dict(self, cur, row) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        cols = [d.name for d in cur.description]
        return {k: v for k, v in zip(cols, row)}

    def _norm_tel(self, raw: Optional[str]) -> str:
        """
        Normaliza el teléfono a formato E164 con '+':
        - El app.py puede mandar 'whatsapp:...', '5199...', '+5199...'
        - Siempre devolverá '+5199...'
        """
        s = "" if raw is None else str(raw)
        s = s.replace("whatsapp:", "")
        s = re.sub(r"\D+", "", s)  # deja solo dígitos
        if not s.startswith("51"):
            # si no empieza con 51, lo asume como Perú y lo antepone
            s = "51" + s
        return f"+{s}" if s else ""


    # ---------- utilidades sobre cliente ----------
    def _buscar_cliente_id_por_celular(self, cur, celular_norm: str) -> Optional[int]:
        """Busca cliente_id por celular normalizado (E164 con '+')."""
        cur.execute("SELECT cliente_id FROM cliente WHERE celular = %s LIMIT 1;", (celular_norm,))
        row = cur.fetchone()
        return row[0] if row else None


    def _insertar_cliente_min(self, cur, celular_norm: str) -> int:
        """Inserta cliente mínimo con timestamps; devuelve cliente_id."""
        cur.execute(
            """
            INSERT INTO cliente (celular, fecha_creacion, fecha_ultimo_estado,
                                 fecha_ultima_interaccion, fecha_ultima_interaccion_bot)
            VALUES (%s, NOW(), NOW(), NOW(), NOW())
            RETURNING cliente_id;
            """,
            (celular_norm,)
        )
        return cur.fetchone()[0]

    # ---------- métodos públicos usados por app.py ----------

    def registrar_estado_reactivacion(self, celular: str, estado: str, detalle: Optional[str]) -> bool:
        print(f"📝 [PG][registrar_estado_reactivacion] tel_raw={celular!r} estado={estado!r} detalle={detalle!r}", flush=True)
        celular_norm = self._norm_tel(celular)
        print(f"📱 [PG] celular_normalizado={celular_norm!r}", flush=True)

        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)

                    # 1) obtener/crear cliente
                    cliente_id = self._buscar_cliente_id_por_celular(cur, celular_norm)
                    if cliente_id is None:
                        print("➕ [PG] cliente no existe, insertando...", flush=True)
                        cliente_id = self._insertar_cliente_min(cur, celular_norm)

                    # 2) actualizar estado + timestamps (+ motivo si hay detalle)
                    if detalle and detalle.strip():
                        cur.execute(
                            """
                            UPDATE cliente
                            SET estado = %s,
                                motivo = %s,
                                fecha_ultimo_estado = NOW(),
                                fecha_ultima_interaccion = NOW(),
                                fecha_ultima_interaccion_bot = NOW()
                            WHERE cliente_id = %s;
                            """,
                            (estado, detalle.strip(), cliente_id)
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE cliente
                            SET estado = %s,
                                fecha_ultimo_estado = NOW(),
                                fecha_ultima_interaccion = NOW(),
                                fecha_ultima_interaccion_bot = NOW()
                            WHERE cliente_id = %s;
                            """,
                            (estado, cliente_id)
                        )
                    print(f"✅ [PG] update cliente OK (motivo {'SET' if detalle else 'SKIP'})", flush=True)

                    # 3) histórico
                    cur.execute(
                        """
                        INSERT INTO historico_estado (cliente_id, estado, fecha_estado, detalle)
                        VALUES (%s, %s, NOW(), NULLIF(%s,''));
                        """,
                        (cliente_id, estado, detalle or "")
                    )
                    print("📚 [PG] historico_estado insert OK", flush=True)

                    return True
        except Exception as e:
            print(f"❌ [PG][registrar_estado_reactivacion] Error: {e}", flush=True)
            return False

    def registrar_promesa_pago(self, celular: str, fecha_iso: str, monto: Optional[float], observacion: Optional[str]) -> bool:
        print(f"💳 [PG][registrar_promesa_pago] tel_raw={celular!r} fecha={fecha_iso!r} monto={monto!r} obs={observacion!r}", flush=True)
        celular_norm = self._norm_tel(celular)
        print(f"📱 [PG] celular_normalizado={celular_norm!r}", flush=True)

        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)

                    # 1) obtener/crear cliente
                    cliente_id = self._buscar_cliente_id_por_celular(cur, celular_norm)
                    if cliente_id is None:
                        print("➕ [PG] cliente no existe, insertando...", flush=True)
                        cliente_id = self._insertar_cliente_min(cur, celular_norm)

                    # 2) insertar pago prometido (monto puede ser NULL)
                    cur.execute(
                        """
                        INSERT INTO pago (cliente_id, fecha_pago, monto, metodo_pago, estado_pago)
                        VALUES (%s, %s::timestamp, %s, %s, %s);
                        """,
                        (cliente_id, fecha_iso, monto, 'N/A', 'pendiente')
                    )
                    print("💾 [PG] pago insert OK", flush=True)

                    # 3) actualizar estado del cliente a 'Fecha de Pago' + timestamps + posible observación
                    cur.execute(
                        """
                        UPDATE cliente
                        SET estado = 'Fecha de Pago',
                            observacion = COALESCE(NULLIF(%s,''), observacion),
                            fecha_ultimo_estado = NOW(),
                            fecha_ultima_interaccion = NOW(),
                            fecha_ultima_interaccion_bot = NOW()
                        WHERE cliente_id = %s;
                        """,
                        (observacion or "", cliente_id)
                    )
                    print(f"✅ [PG] update cliente a 'Fecha de Pago' OK", flush=True)

                    # 4) histórico
                    cur.execute(
                        """
                        INSERT INTO historico_estado (cliente_id, estado, fecha_estado, detalle)
                        VALUES (%s, 'Fecha de Pago', NOW(), CONCAT('Promesa ', %s::text));
                        """,
                        (cliente_id, fecha_iso)
                    )
                    print("📚 [PG] historico_estado insert OK", flush=True)

                    return True
        except Exception as e:
            print(f"❌ [PG][registrar_promesa_pago] Error: {e}", flush=True)
            return False



    # ---------- contactabilidad: envíos ----------
    def registrar_mensaje_out(
        self,
        id_msg: str,
        phone_to: str,
        template_name: str,
        template_lang: str,
        campanha_id: str = None,
    ) -> bool:
        """
        Crea (o ignora si existe) el envío saliente asociado al id_msg de Meta.
        Usa idempotencia con ON CONFLICT DO NOTHING para no duplicar.
        """
        if not id_msg:
            print("❌ [PG][registrar_mensaje_out] id_msg vacío", flush=True)
            return False

        phone_norm = self._norm_tel(phone_to)
        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)
                    cur.execute(
                        """
                        INSERT INTO mensaje_out (id_msg, phone_to, template_name, template_lang, campanha_id)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (id_msg) DO NOTHING;
                        """,
                        (id_msg, phone_norm, template_name, template_lang, campanha_id)
                    )
                    print(f"📨 [PG] mensaje_out upsert OK id_msg={id_msg}", flush=True)
                    return True
        except Exception as e:
            print(f"❌ [PG][registrar_mensaje_out] Error: {e}", flush=True)
            return False


    # ---------- contactabilidad: estados ----------
    def registrar_status_event(
        self,
        id_msg: str,
        estado: str,                 # 'sent' | 'delivered' | 'read' | 'failed'
        ts_unix: int = None,
        recipient_id: str = None,
        pricing_json: Optional[str] = None,
        conversation_json: Optional[str] = None,
        errors_json: Optional[str] = None,
    ) -> bool:
        """
        Inserta un evento de estado para el id_msg. No actualiza nada más;
        la vista v_mensaje_estado_actual te da el último estado por mensaje.
        """
        if not id_msg or not estado:
            print("❌ [PG][registrar_status_event] id_msg/estado vacío", flush=True)
            return False

        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)
                    cur.execute(
                        """
                        INSERT INTO mensaje_status_event
                            (id_msg, estado, ts_unix, recipient_id, pricing_json, conversation_json, errors_json)
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb);
                        """,
                        (
                            id_msg,
                            estado,
                            ts_unix,
                            self._norm_tel(recipient_id) if recipient_id else None,
                            pricing_json,
                            conversation_json,
                            errors_json,
                        )
                    )
                    print(f"🧩 [PG] status_event insert OK id_msg={id_msg} estado={estado}", flush=True)
                    return True
        except Exception as e:
            print(f"❌ [PG][registrar_status_event] Error: {e}", flush=True)
            return False

    def registrar_webhook_log(self, event_type: str, payload: Dict[str, Any]) -> Optional[int]:
        """
        Inserta el payload RAW del webhook de Meta en reactivaciones.webhook_logs.
        Retorna el id generado si todo OK, si no retorna None.
        """
        try:
            et = (event_type or "unknown")[:100]
            payload_json = json.dumps(payload or {}, ensure_ascii=False)

            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)
                    cur.execute(
                        """
                        INSERT INTO webhook_logs (event_type, payload)
                        VALUES (%s, %s::jsonb)
                        RETURNING id;
                        """,
                        (et, payload_json)
                    )
                    row = cur.fetchone()
                    new_id = row[0] if row else None

            print(f"✅ [PG][registrar_webhook_log] OK id={new_id} event_type={et!r}", flush=True)
            return new_id

        except Exception as e:
            print(f"❌ [PG][registrar_webhook_log] Error: {e}", flush=True)
            return None
