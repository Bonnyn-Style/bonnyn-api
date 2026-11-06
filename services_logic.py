import requests
from sqlalchemy.orm import Session
import models

WHATSAPP_TOKEN = "TU_TOKEN_PERMANENTE_DE_META"
PHONE_NUMBER_ID = "TU_PHONE_NUMBER_ID"

def send_whatsapp_reminder(to_phone: str, client_name: str, appointment_time: str):
    """Envía notificación automática mediante WhatsApp Cloud API con timeout de seguridad."""
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "template",
        "template": {
            "name": "recordatorio_cita_2h",
            "language": {"code": "es"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": client_name},
                        {"type": "text", "text": appointment_time}
                    ]
                }
            ]
        }
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5.0)
        return response.status_code == 200
    except Exception as e:
        print(f"Error enviando WhatsApp: {e}")
        return False

def deduct_inventory_for_service(db: Session, servicio_id: int):
    consumptions = db.query(models.ServiceInventory).filter(models.ServiceInventory.servicio_id == servicio_id).all()
    for item in consumptions:
        product = db.query(models.Product).filter(models.Product.id == item.product_id).first()
        if product:
            product.stock = max(0, product.stock - item.quantity_used)
    db.commit()

def apply_loyalty_and_cashback(db: Session, profesional_id: int, client_whatsapp: str, service_price: float):
    loyalty = db.query(models.LoyaltyAccount).filter(
        models.LoyaltyAccount.profesional_id == profesional_id,
        models.LoyaltyAccount.client_whatsapp == client_whatsapp
    ).first()
    
    earned_points = int(service_price * 0.10)
    earned_cashback = service_price * 0.05

    if not loyalty:
        loyalty = models.LoyaltyAccount(
            profesional_id=profesional_id,
            client_whatsapp=client_whatsapp,
            points=earned_points,
            cashback_balance=earned_cashback
        )
        db.add(loyalty)
    else:
        loyalty.points += earned_points
        loyalty.cashback_balance += earned_cashback
        
    db.commit()
    db.refresh(loyalty)
    return {
        "points_added": earned_points,
        "cashback_added": earned_cashback,
        "total_points": loyalty.points,
        "total_cashback_balance": loyalty.cashback_balance
    }