import os
import json
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import Response
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv

from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

app = FastAPI()

# Environment variables
SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
TIMEZONE = os.getenv("TIMEZONE", "UTC")


def get_sheets_service():
    if not SERVICE_ACCOUNT_JSON:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON env var.")
    if not SPREADSHEET_ID:
        raise RuntimeError("Missing GOOGLE_SHEETS_SPREADSHEET_ID env var.")

    credentials_info = json.loads(SERVICE_ACCOUNT_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    service = build("sheets", "v4", credentials=credentials)
    return service


def get_business_config():
    service = get_sheets_service()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SPREADSHEET_ID, range="BusinessConfig!A2:F2")
        .execute()
    )

    values = result.get("values", [])
    if not values:
        return None

    row = values[0]
    # Ensure row has at least 6 columns
    while len(row) < 6:
        row.append("")

    menu_page_size = 8
    try:
        menu_page_size = int(row[5]) if row[5] else 8
    except ValueError:
        menu_page_size = 8

    return {
        "business_name": row[0],
        "order_mode": (row[1] or "").strip().lower(),  # pickup / delivery / both
        "currency_symbol": row[2] or "$",
        "hours": row[3] or "",
        "address": row[4] or "",
        "menu_page_size": menu_page_size,
    }


def get_products():
    service = get_sheets_service()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SPREADSHEET_ID, range="Products!A2:H")
        .execute()
    )

    rows = result.get("values", [])
    products = []

    for row in rows:
        # Expected:
        # product_id, number, name, price, active, keywords, unit, featured
        if len(row) < 5:
            continue

        product_id = row[0]
        number = row[1]
        name = row[2]
        price_raw = row[3]
        active_raw = row[4]

        try:
            price = float(price_raw)
        except (ValueError, TypeError):
            continue

        active = str(active_raw).strip().lower() == "true"
        if not active:
            continue

        products.append(
            {
                "product_id": product_id,
                "number": str(number).strip(),
                "name": str(name).strip(),
                "price": price,
            }
        )

    return products


def save_order(phone, items, total):
    service = get_sheets_service()
    now = datetime.utcnow().isoformat()

    # Keep it simple for MVP
    order_id = str(int(datetime.utcnow().timestamp()))

    body = {
        "values": [
            [
                order_id,
                phone,
                json.dumps(items, ensure_ascii=False),
                total,
                "new",
                "",  # order_type (pickup/delivery) later
                "",  # address later
                now,
            ]
        ]
    }

    (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=SPREADSHEET_ID,
            range="Orders!A2",
            valueInputOption="RAW",
            body=body,
        )
        .execute()
    )

    return order_id


@app.get("/health")
def health():
    return {"status": "ok"}


def twiml_response(msg: str) -> Response:
    """
    Return proper TwiML XML so WhatsApp doesn't show the XML tags.
    """
    tw = MessagingResponse()
    tw.message(msg)
    return Response(content=str(tw), media_type="application/xml")


@app.post("/webhook/twilio")
async def whatsapp_webhook(request: Request):
    form = await request.form()
    incoming_msg = (form.get("Body") or "").strip().lower()
    phone = form.get("From") or ""

    # Load config
    config = get_business_config()
    if not config:
        return twiml_response("⚠️ Error de configuración del negocio. Revisa BusinessConfig.")

    # Basic intents
    if (
        incoming_msg in ["hola", "buenas", "buenos dias", "buenas tardes", "buenas noches"]
        or "menu" in incoming_msg
        or "qué venden" in incoming_msg
        or "que venden" in incoming_msg
    ):
        products = get_products()
        if not products:
            return twiml_response("⚠️ No hay productos activos en la hoja Products.")

        # Simple conversational menu (no XML visible now)
        text = f"👋 Hola, bienvenido a {config['business_name']}.\n\n"
        text += "Esto es lo que tenemos hoy:\n\n"

        for p in products:
            text += f"- {p['name']} — {config['currency_symbol']}{p['price']}\n"

        text += "\nPara ordenar, escribe por ejemplo: 2001 x 2"
        return twiml_response(text)

    # Simple order format: "2001 x 2"
    if "x" in incoming_msg:
        try:
            left, right = incoming_msg.split("x", 1)
            number = left.strip()
            qty = int(right.strip())

            if qty <= 0:
                return twiml_response("La cantidad debe ser mayor a 0. Ejemplo: 2001 x 2")

            products = get_products()
            selected = next((p for p in products if p["number"] == number), None)

            if not selected:
                return twiml_response("Producto no encontrado. Escribe MENU para ver opciones.")

            total = selected["price"] * qty
            items = [
                {
                    "product_id": selected["product_id"],
                    "name": selected["name"],
                    "qty": qty,
                    "price": selected["price"],
                }
            ]

            order_id = save_order(phone, items, total)

            msg = (
                f"✅ ¡Pedido recibido!\n"
                f"{qty} x {selected['name']}\n"
                f"Total: {config['currency_symbol']}{total}\n"
                f"Pedido: #{order_id}\n\n"
                "Te avisaremos cuando esté listo 🙌"
            )
            return twiml_response(msg)

        except ValueError:
            return twiml_response("Formato inválido. Usa: 2001 x 2")
        except Exception:
            return twiml_response("Ocurrió un error procesando tu pedido. Intenta de nuevo.")

    return twiml_response("Escribe MENU para ver opciones, o envía tu pedido (ej: 2001 x 2).")
