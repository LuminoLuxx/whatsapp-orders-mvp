import os
import json
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

app = FastAPI()

# Environment variables
SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
TIMEZONE = os.getenv("TIMEZONE", "UTC")

def get_sheets_service():
    credentials_info = json.loads(SERVICE_ACCOUNT_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=credentials)
    return service

def get_business_config():
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="BusinessConfig!A2:F2"
    ).execute()

    values = result.get("values", [])
    if not values:
        return None

    row = values[0]
    return {
        "business_name": row[0],
        "order_mode": row[1],
        "currency_symbol": row[2],
        "hours": row[3],
        "address": row[4],
        "menu_page_size": int(row[5])
    }

def get_products():
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="Products!A2:H"
    ).execute()

    rows = result.get("values", [])
    products = []

    for row in rows:
        if len(row) < 4:
            continue
        product = {
            "product_id": row[0],
            "number": row[1],
            "name": row[2],
            "price": float(row[3]),
            "active": row[4].lower() == "true"
        }
        if product["active"]:
            products.append(product)

    return products

def save_order(phone, items, total):
    service = get_sheets_service()
    now = datetime.utcnow().isoformat()

    body = {
        "values": [[
            str(datetime.utcnow().timestamp()),
            phone,
            json.dumps(items),
            total,
            "new",
            "",
            "",
            now
        ]]
    }

    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="Orders!A2",
        valueInputOption="RAW",
        body=body
    ).execute()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/webhook/twilio")
async def whatsapp_webhook(request: Request):
    form = await request.form()
    incoming_msg = form.get("Body", "").strip().lower()
    phone = form.get("From", "")

    response = MessagingResponse()
    config = get_business_config()

    if not config:
        response.message("Error de configuración del negocio.")
        return PlainTextResponse(str(response))

    if "menu" in incoming_msg or "qué venden" in incoming_msg or "hola" in incoming_msg:
        products = get_products()
        menu_text = f"👋 Bienvenido a {config['business_name']}\n\nMenú:\n\n"

        for p in products:
            menu_text += f"{p['number']}) {p['name']} - {config['currency_symbol']}{p['price']}\n"

        menu_text += "\nEscribe por ejemplo: 2 x 1"
        response.message(menu_text)
        return PlainTextResponse(str(response))

    # Simple order format: "2 x 1"
    if "x" in incoming_msg:
        try:
            number, qty = incoming_msg.split("x")
            number = number.strip()
            qty = int(qty.strip())

            products = get_products()
            selected = next((p for p in products if p["number"] == number), None)

            if not selected:
                response.message("Producto no encontrado. Escribe MENU para ver opciones.")
                return PlainTextResponse(str(response))

            total = selected["price"] * qty
            items = [{
                "product_id": selected["product_id"],
                "name": selected["name"],
                "qty": qty,
                "price": selected["price"]
            }]

            save_order(phone, items, total)

            response.message(
                f"✅ Pedido confirmado:\n{qty} x {selected['name']}\nTotal: {config['currency_symbol']}{total}\n\nTe avisaremos cuando esté listo."
            )
            return PlainTextResponse(str(response))

        except:
            response.message("Formato inválido. Usa por ejemplo: 2 x 1")
            return PlainTextResponse(str(response))

    response.message("Escribe MENU para ver opciones.")
    return PlainTextResponse(str(response))
