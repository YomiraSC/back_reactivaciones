from openai import OpenAI
from help_prompt import (
    prompt_intencionces_codPago,
    prompt_cliente_dni_ruc,
    prompt_obtener_dniv2,
    prompt_respuesta_codigo_pago,
    prompt_respuesta_intencion_1,
    prompt_inicio_conversacion,
    prompt_pedir_eleccion_contrato,
    prompt_extraer_codigo_asociado
)
import pytz
import json
import re
from datetime import datetime
from api_keys import openai_api_key

class OpenAIManager:
    def __init__(self):
        self.client = OpenAI(api_key=openai_api_key)
        self.tz = pytz.timezone("America/Lima")

    def clasificar_intencion_botPago(self, mensaje_cliente):
        """
        Clasifica la intención del mensaje del usuario usando OpenAI.
        """
        try:
            fecha_actual = datetime.now().strftime("%Y-%m-%d")
            prompt_sistema = prompt_intencionces_codPago(fecha_actual) + mensaje_cliente

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "system", "content": prompt_sistema}],
                max_tokens=50,
            )

            respuesta_texto = response.choices[0].message.content.strip()
            print("Respuesta del modelo:", respuesta_texto)

            # Intentar limpiar y cargar como JSON
            respuesta_texto = respuesta_texto.replace("```json", "").replace("```", "").strip()
            respuesta_json = json.loads(respuesta_texto)

            # Validar que se obtuvo una intención
            if "intencion" in respuesta_json:
                return respuesta_json

            return {"error": "Respuesta inválida del modelo"}

        except Exception as e:
            print("Error en la clasificación de intención:", str(e))
            return {"error": "Error en la clasificación"}


    def clasificar_intencion_botPago_old(self, mensaje_cliente):
        """
        Clasifica la intención del mensaje del cliente.
        """
        try:
            if not mensaje_cliente:
                return {"error": "No se recibió un mensaje válido"}

            fecha_actual = datetime.now(self.tz).strftime("%Y-%m-%d")
            print("Fecha actual:", fecha_actual)

            prompt_sistema = prompt_intencionces_codPago(fecha_actual) + mensaje_cliente

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "system", "content": prompt_sistema}],
                max_tokens=100,
            )

            respuesta_texto = response.choices[0].message.content.strip()
            print("Respuesta del modelo:", respuesta_texto)

            try:
                respuesta_texto = re.sub(r"```json|```", "", respuesta_texto).strip()
                respuesta_json = json.loads(respuesta_texto)

                if "intencion" in respuesta_json:
                    return respuesta_json
                else:
                    return {"error": "Respuesta inválida del modelo"}
            
            except json.JSONDecodeError:
                return {"error": "Formato incorrecto de la respuesta"}

        except Exception as e:
            print("Error al clasificar la intención:", str(e))
            return {"error": "Error en la clasificación"}

    def consulta_dni_ruc_botPago(self, mensaje_cliente, cliente, response_message=None):
        """
        Obtiene el DNI/RUC del cliente basado en su mensaje.
        """
        if not mensaje_cliente:
            return "No se recibió un mensaje válido"

        response = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": prompt_cliente_dni_ruc(cliente, response_message, mensaje_cliente)},
            ],
            max_tokens=150,
        )
        return response.choices[0].message.content.strip()

    def obtener_dni_brindado(self, mensaje_cliente):
        """
        Extrae el DNI o RUC desde el mensaje del cliente.
        """
        if not mensaje_cliente:
            return None

        prompt = prompt_obtener_dniv2(mensaje_cliente)
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "system", "content": prompt}],
                max_tokens=50
            )

            respuesta_texto = response.choices[0].message.content.strip()
            print("Respuesta OpenAI:", respuesta_texto)

            dni_data = json.loads(respuesta_texto)

            if isinstance(dni_data, dict) and "tipo" in dni_data and "numero" in dni_data:
                if dni_data["tipo"] in ["DNI", "RUC", "CE"] and dni_data["numero"].isdigit():
                    return dni_data
            
            return None

        except Exception as e:
            print("Error al procesar el DNI/RUC/CE:", str(e))
            return None



    def generar_respuesta_codigo_pago(self, codigo, tipo):
        """
        Usa OpenAI para generar un mensaje de respuesta basado en el código de pago y su tipo.
        """
        prompt = prompt_respuesta_codigo_pago(codigo, tipo)

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "system", "content": prompt}],
                max_tokens=100
            )

            respuesta_texto = response.choices[0].message.content.strip()
            print(f"📝 Respuesta generada: {respuesta_texto}")  # Log para verificar

            return respuesta_texto

        except Exception as e:
            print(f"❌ Error al generar respuesta del código de pago: {e}")
            return "Lo siento, hubo un problema al generar tu respuesta. Inténtalo nuevamente."


    def generar_respuesta_intencion_1(self, mensaje_cliente):
        """
        Usa OpenAI para generar una respuesta a dudas sobre el proceso de pago.
        """

        try:
            # Generar el prompt usando la función de help_prompt.py
            prompt = prompt_respuesta_intencion_1(mensaje_cliente)

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "system", "content": prompt}],
                max_tokens=200
            )

            respuesta_texto = response.choices[0].message.content.strip()
            print("🔹 Respuesta OpenAI (intención 1):", respuesta_texto)  # Log de depuración

            return respuesta_texto

        except Exception as e:
            print("❌ Error en la generación de respuesta (intención 1):", str(e))
            return "Lo siento, no puedo responder a tu consulta en este momento."

    def generar_respuesta_inicio_conversacion(self):
        """
        Genera un mensaje de bienvenida cuando el usuario solo dice "Hola".
        """
        return prompt_inicio_conversacion()


    def obtener_codigo_asociado(self, mensaje_cliente, lista_contratos):
        """
        Usa OpenAI para identificar cuál es el código de contrato (Codigo_Asociado)
        elegido por el cliente basándose en su respuesta y la lista de contratos mostrados.
        """
        prompt = prompt_extraer_codigo_asociado(mensaje_cliente, lista_contratos)

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "system", "content": prompt}],
                max_tokens=50
            )

            texto = response.choices[0].message.content.strip()
            print("🎯 Elección del cliente (GPT):", texto)

            return texto  # Se espera que OpenAI devuelva directamente el Código_Asociado como texto
        except Exception as e:
            print(f"❌ Error al obtener código asociado: {e}")
            return None


    def consultar_eleccion_contrato(self, dni, contratos):
        """
        Genera un mensaje para pedirle al cliente que elija un contrato entre los que tiene activos.
        """
        prompt = prompt_pedir_eleccion_contrato(dni, contratos)

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "system", "content": prompt}],
                max_tokens=300
            )
            respuesta_texto = response.choices[0].message.content.strip()
            print("📨 Mensaje para elegir contrato generado por OpenAI:", respuesta_texto)
            return respuesta_texto

        except Exception as e:
            print("❌ Error en generación de mensaje para elección de contrato:", str(e))
            return "Por favor, indícanos con cuál de tus contratos deseas generar el código de pago."

