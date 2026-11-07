from fastapi import FastAPI, Depends, HTTPException, Request, Response, status, UploadFile, File, Form, Cookie, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
import models
from database import engine, get_db
import schemas
import datetime
import io
import httpx
import requests
import os
import html
import jwt
import threading
import time
import random
import smtplib
import logging
import pytz
import json
import csv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pydantic import BaseModel
from passlib.context import CryptContext
from PIL import Image
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from starlette.status import HTTP_413_CONTENT_TOO_LARGE

# Importaciones para Rate Limiting (SlowAPI)
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Cargar variables de entorno desde el archivo .env
load_dotenv()

# ==========================================
# CONFIGURACIÓN DE LOGS Y BASE DE DATOS
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("BonnynApp")

models.Base.metadata.create_all(bind=engine)

# 🛡️ CONFIGURACIÓN DE RATE LIMITING POR IP
limiter = Limiter(key_func=get_remote_address)

# 🚀 GESTIÓN DE HILOS Y CICLO DE VIDA (LIFESPAN) CON SESIONES AISLADAS
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Arranque de hilos en segundo plano al iniciar el servidor
    threading.Thread(target=background_whatsapp_reminder_loop, daemon=True).start()
    threading.Thread(target=tarea_sondeo_bcv, daemon=True).start()
    logger.info("🟢 [Bonnyn] Hilos en segundo plano (WhatsApp y BCV) iniciados correctamente.")
    
    yield
    
    logger.info("🔴 [Bonnyn] Servidor apagándose...")

app = FastAPI(
    title="Bonnyn - SaaS de Citas, Finanzas & B2B", 
    version="14.22.0",
    lifespan=lifespan
)

# Registrar el limitador y su manejador de excepciones (HTTP 429 Too Many Requests)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# 🛡️ PROTECCIÓN CONTRA ATAQUES DOS / LÍMITE DE SUBIDA (5 MB)
MAX_UPLOAD_SIZE = 5 * 1024 * 1024 

@app.middleware("http")
async def limit_upload_size(request: Request, call_next):
    if request.method in ["POST", "PUT", "PATCH"]:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=HTTP_413_CONTENT_TOO_LARGE,
                detail="⚠️ El archivo o carga enviada es demasiado pesado. El límite máximo permitido es de 5 MB para proteger el servidor."
            )
    response = await call_next(request)
    return response

# BLINDAJE DE SEGURIDAD: Eliminación del fallback inseguro para SECRET_KEY
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("⚠️ ERROR CRÍTICO: La variable de entorno SECRET_KEY no está configurada. El servidor no puede iniciar por motivos de seguridad.")
ALGORITHM = "HS256"

# CREDENCIALES EXCLUSIVAS DE SUPER ADMIN (VARIABLES DE ENTORNO)
SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "admin@bonnyn.com")
SUPER_ADMIN_PASSWORD = os.getenv("SUPER_ADMIN_PASSWORD", "Bonnyn#2026$MasterKey!")
MASTER_TOKEN_SECRET = os.getenv("MASTER_TOKEN_SECRET", "llave_ultra_secreta_bonnyn_master_2026")

# BLINDAJE DE SEGURIDAD: Credenciales de WhatsApp obligatorias por entorno
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
if not WHATSAPP_TOKEN:
    raise RuntimeError("⚠️ ERROR CRÍTICO: La variable de entorno WHATSAPP_TOKEN no está configurada.")

PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
if not PHONE_NUMBER_ID:
    raise RuntimeError("⚠️ ERROR CRÍTICO: La variable de entorno PHONE_NUMBER_ID no está configurada.")

# Configuración SMTP de Gmail protegida por entorno
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER", "bonnynsoporte@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "tpbtwosanzmrjnpf")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "bonnynsoporte@gmail.com")

# Carpeta estática para logos e imágenes públicas optimizadas
OS_UPLOAD_DIR = "static/logos"
os.makedirs(OS_UPLOAD_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ==========================================
# HELPER: ZONAS HORARIAS Y LIMPIEZA DE LOGOS
# ==========================================

def get_salon_now(timezone_str: str = "America/Caracas") -> datetime.datetime:
    """Retorna la fecha y hora actual según la zona horaria del salón."""
    try:
        tz = pytz.timezone(timezone_str)
        return datetime.datetime.now(tz)
    except Exception:
        tz = pytz.timezone("America/Caracas")
        return datetime.datetime.now(tz)

def remove_old_logo(logo_url: str):
    """Elimina el archivo físico de un logo anterior si existe en el disco."""
    if logo_url and logo_url.startswith("/static/logos/"):
        filename = logo_url.replace("/static/logos/", "")
        file_path = os.path.join(OS_UPLOAD_DIR, filename)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Logo antiguo eliminado: {file_path}")
            except Exception as e:
                logger.error(f"Error al eliminar logo antiguo {file_path}: {e}")


class SolicitarRecuperacionSchema(BaseModel):
    correo: str

class VerificarRecuperacionSchema(BaseModel):
    correo: str
    codigo: str
    nueva_password: str

class NotaClinicaSchema(BaseModel):
    profesional_id: int
    whatsapp_clienta: str
    nombre_clienta: str
    diagnostico_notas: str

class WebhookPagoInput(BaseModel):
    referencia: str
    monto_usd: float
    banco_emisor: str = "N/A"

class ConfiguracionPagoUpdate(BaseModel):
    banco: str | None = None
    cedula_rif: str | None = None
    telefono: str | None = None
    binance_pay_id: str | None = None
    airtm_correo: str | None = None
    paypal_correo: str | None = None

class LiquidarCitaSchema(BaseModel):
    referencia_segundo_pago: str | None = "EfectivoEnSalon"
    metodo_pago: str | None = "efectivo"

class GastoLogisticaCreate(BaseModel):
    profesional_id: int
    monto_usd: float
    detalle: str
    categoria: str = "Insumos"

class ProductoInsumoCreate(BaseModel):
    profesional_id: int
    nombre: str
    stock_actual: float = 0.0
    stock_minimo: float = 5.0
    precio_venta: float = 0.0
    costo: float = 0.0

class ServicioInsumoRelacionCreate(BaseModel):
    servicio_id: int
    producto_id: int
    cantidad_consumida: float

class VentaDirectaSchema(BaseModel):
    producto_id: int
    cantidad: float

class VersionSchema(BaseModel):
    version: str

class MantenimientoSchema(BaseModel):
    activo: bool


# ==========================================
# GESTIÓN DE ESTADO GLOBAL DE PLATAFORMA
# ==========================================
CONFIG_ESTADO_PATH = "estado_plataforma.json"

def obtener_estado_plataforma():
    if not os.path.exists(CONFIG_ESTADO_PATH):
        return {"version": "v14.22.0", "modo_mantenimiento": False, "build_timestamp": int(time.time())}
    try:
        with open(CONFIG_ESTADO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"version": "v14.22.0", "modo_mantenimiento": False, "build_timestamp": int(time.time())}

def guardar_estado_plataforma(datos):
    try:
        with open(CONFIG_ESTADO_PATH, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=4)
    except Exception as e:
        logger.error(f"Error al guardar estado de plataforma: {e}")


@app.middleware("http")
async def agregar_encabezados_seguridad(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.middleware("http")
async def verificar_modo_mantenimiento(request: Request, call_next):
    rutas_exentas = ["/super-admin", "/super_admin", "/api/super-admin", "/static", "/favicon.ico", "/login"]
    if not any(request.url.path.startswith(ruta) for ruta in rutas_exentas):
        estado = obtener_estado_plataforma()
        if estado.get("modo_mantenimiento", False):
            if request.url.path.startswith("/api/"):
                raise HTTPException(status_code=503, detail="⚠️ Plataforma en mantenimiento temporal por actualización ejecutiva.")
    response = await call_next(request)
    return response


templates = Jinja2Templates(directory="templates")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
TASA_FILE = "tasa_segura_bcv.txt"

CACHE_TASA_MEMORIA = {
    "valor": 889.26,
    "ultima_actualizacion": None
}

def sanitizar_texto(texto: str) -> str:
    if not texto:
        return ""
    return html.escape(str(texto).strip())

def leer_tasa_local():
    global CACHE_TASA_MEMORIA
    ahora = datetime.datetime.now()
    if CACHE_TASA_MEMORIA["ultima_actualizacion"] and (ahora - CACHE_TASA_MEMORIA["ultima_actualizacion"]).total_seconds() < 1800:
        return CACHE_TASA_MEMORIA["valor"]

    if os.path.exists(TASA_FILE):
        try:
            with open(TASA_FILE, "r", encoding="utf-8") as f:
                val = float(f.read().strip())
                CACHE_TASA_MEMORIA["valor"] = val
                CACHE_TASA_MEMORIA["ultima_actualizacion"] = ahora
                return val
        except Exception:
            pass
    return CACHE_TASA_MEMORIA["valor"]

def guardar_tasa_local(valor):
    global CACHE_TASA_MEMORIA
    CACHE_TASA_MEMORIA["valor"] = float(valor)
    CACHE_TASA_MEMORIA["ultima_actualizacion"] = datetime.datetime.now()
    try:
        with open(TASA_FILE, "w", encoding="utf-8") as f:
            f.write(str(valor))
    except Exception:
        pass

def tarea_sondeo_bcv():
    while True:
        try:
            ahora = datetime.datetime.now()
            hora = ahora.hour
            es_ventana_tarde = (16 <= hora < 19)
            es_ventana_madrugada = (4 <= hora < 5)

            if es_ventana_tarde or es_ventana_madrugada:
                response = httpx.get("https://ve.dolarapi.com/v1/dolares/oficial", timeout=5.0)
                if response.status_code == 200:
                    data = response.json()
                    precio = data.get("promedio") or data.get("precio")
                    if precio and float(precio) > 0:
                        nuevo_valor = float(precio)
                        tasa_actual = leer_tasa_local()
                        if nuevo_valor != tasa_actual:
                            guardar_tasa_local(nuevo_valor)
                            logger.info(f"[SONDA BCV] Tasa actualizada: {nuevo_valor}")
                            if es_ventana_tarde:
                                time.sleep(1800)
                        else:
                            time.sleep(180)
                            continue
            time.sleep(1200)
        except Exception as e:
            logger.error(f"Error en sonda BCV: {e}")
            time.sleep(300)

def background_whatsapp_reminder_loop():
    logger.info("Iniciando servicio de notificaciones de WhatsApp y barrido automático de citas con sesiones independientes...")
    from database import SessionLocal
    while True:
        db = None
        try:
            db = SessionLocal()
            ahora = datetime.datetime.now()

            expirar_citas_pendientes_antiguas(db)
            completar_citas_pasadas_automaticamente(db)

            t_2h_min = ahora + datetime.timedelta(hours=1, minutes=55)
            t_2h_max = ahora + datetime.timedelta(hours=2, minutes=5)

            citas_2h = db.query(models.Cita).join(models.Profesional).filter(
                models.Profesional.plan == "elite",
                models.Cita.estado.in_(["pendiente", "confirmada"]),
                models.Cita.notif_2h_enviada == False,
                models.Cita.fecha_hora_inicio >= t_2h_min,
                models.Cita.fecha_hora_inicio <= t_2h_max
            ).all()

            for c in citas_2h:
                enviar_mensaje_whatsapp(c.whatsapp_clienta, "recordatorio_cita_2h", [c.nombre_clienta, c.fecha_hora_inicio.strftime("%I:%M %p")])
                c.notif_2h_enviada = True
                db.commit()

            t_30m_min = ahora + datetime.timedelta(minutes=25)
            t_30m_max = ahora + datetime.timedelta(minutes=35)

            citas_30m = db.query(models.Cita).join(models.Profesional).filter(
                models.Profesional.plan == "elite",
                models.Cita.estado.in_(["pendiente", "confirmada"]),
                models.Cita.notif_30m_enviada == False,
                models.Cita.fecha_hora_inicio >= t_30m_min,
                models.Cita.fecha_hora_inicio <= t_30m_max
            ).all()

            for c in citas_30m:
                enviar_mensaje_whatsapp(c.whatsapp_clienta, "recordatorio_cita_30m", [c.nombre_clienta, c.fecha_hora_inicio.strftime("%I:%M %p")])
                c.notif_30m_enviada = True
                db.commit()
        except Exception as e:
            logger.error(f"Error en bucle de WhatsApp: {e}")
        finally:
            if db:
                db.close()
        time.sleep(60)

def enviar_correo_codigo_bonnyn(destinatario: str, codigo: str):
    asunto = f"Código de recuperación: {codigo} - Bonnyn"
    html_content = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head><meta charset="UTF-8"></head>
    <body style="font-family: sans-serif; background: #f8fafc; padding: 20px;">
        <div style="max-width: 480px; margin: 0 auto; background: #fff; border-radius: 24px; padding: 32px; text-align: center; border: 1px solid #fbcfe8;">
            <h1 style="color: #0f172a;">Recuperación de Contraseña</h1>
            <p style="color: #64748b;">Tu código de verificación es:</p>
            <div style="background: #fff1f2; border: 2px dashed #f43f5e; border-radius: 16px; font-size: 32px; font-weight: 900; color: #e11d48; padding: 16px; margin: 24px 0;">{codigo}</div>
            <p style="font-size: 12px; color: #475569;">Válido por 80 segundos.</p>
        </div>
    </body>
    </html>
    """
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"] = f"Bonnyn Soporte <{SENDER_EMAIL}>"
        msg["To"] = destinatario
        msg.attach(MIMEText(html_content, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SENDER_EMAIL, destinatario, msg.as_string())
        logger.info(f"Correo de recuperación enviado con éxito a {destinatario}")
        return True
    except Exception as e:
        logger.error(f"Error correo: {e}")
        return False

def verificar_vencimiento_socio(prof: models.Profesional, db: Session = None):
    if prof.plan not in ["gratuito", "suspendido_multa"] and prof.fecha_vencimiento:
        ahora = datetime.datetime.now()
        if ahora > prof.fecha_vencimiento:
            if db:
                citas_futuras_count = db.query(models.Cita).filter(
                    models.Cita.profesional_id == prof.id,
                    models.Cita.fecha_hora_inicio >= ahora,
                    models.Cita.estado.in_(["pendiente", "confirmada"])
                ).count()
                
                if citas_futuras_count == 0:
                    prof.plan = "gratuito"
                    prof.estado_cuenta = "activo"
                    prof.monto_multa_pendiente = 0.0
                    prof.motivo_multa = None
                else:
                    if citas_futuras_count <= 5:
                        prof.monto_multa_pendiente = 5.00
                    else:
                        prof.monto_multa_pendiente = 10.00
                        
                    prof.plan = "suspendido_multa"
                    prof.estado_cuenta = "suspendido"
                    prof.motivo_multa = f"Transición por vencimiento con {citas_futuras_count} citas pendientes en agenda."
            else:
                prof.plan = "suspendido_multa"
                prof.estado_cuenta = "suspendido"

def expirar_citas_pendientes_antiguas(db: Session, profesional_id: int = None):
    limite_tiempo = datetime.datetime.now() - datetime.timedelta(minutes=30)
    query = db.query(models.Cita).filter(models.Cita.estado == "pendiente", models.Cita.fecha_hora_inicio < limite_tiempo)
    if profesional_id:
        query = query.filter(models.Cita.profesional_id == profesional_id)
    for c in query.all():
        c.estado = "cancelada"
    db.commit()

def completar_citas_pasadas_automaticamente(db: Session, profesional_id: int = None):
    ahora = datetime.datetime.now()
    query = db.query(models.Cita).filter(models.Cita.estado == "confirmada", models.Cita.fecha_hora_fin < ahora)
    if profesional_id:
        query = query.filter(models.Cita.profesional_id == profesional_id)
    for c in query.all():
        c.estado = "completada"
    db.commit()

def enviar_mensaje_whatsapp(to_phone: str, template_name: str, parameters: list):
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "es"},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in parameters]}]
        }
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5.0)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error WhatsApp: {e}")
        return False

def crear_datos_iniciales():
    db = next(get_db())
    try:
        cats = [
            {"id": 1, "nombre": "Uñas", "icono": "💅"},
            {"id": 2, "nombre": "Cejas y Pestañas", "icono": "👁️"},
            {"id": 3, "nombre": "Maquilladora Profesional", "icono": "💄"},
            {"id": 4, "nombre": "Barbería", "icono": "✂️"},
            {"id": 5, "nombre": "Cabello", "icono": "💇‍♀️"},
            {"id": 6, "nombre": "Esteticista Cosmética", "icono": "🌿"},
            {"id": 7, "nombre": "Especialista en Micropigmentación", "icono": "✨"},
            {"id": 8, "nombre": "Spa y Estética Corporal", "icono": "🧴"},
            {"id": 9, "nombre": "Fisioterapeuta", "icono": "🦴"},
            {"id": 10, "nombre": "Masajista / Quiromasajista", "icono": "💆‍♂️"},
            {"id": 11, "nombre": "Psicólogo / Terapeuta", "icono": "🧠"}
        ]
        for c in cats:
            if not db.query(models.CategoriaServicio).filter(models.CategoriaServicio.id == c["id"]).first():
                db.add(models.CategoriaServicio(**c))
        db.commit()

        if not db.query(models.ConfiguracionPlataforma).first():
            db.add(models.ConfiguracionPlataforma(
                banco="Banco de Venezuela", 
                cedula_rif="V-15111111", 
                telefono="04121111111",
                binance_pay_id="987654321",
                airtm_correo="bonnynsoporte@gmail.com",
                paypal_correo="bonnynsoporte@gmail.com"
            ))
            db.commit()

        if not db.query(models.Profesional).filter(models.Profesional.slug == "studio-maria").first():
            socio_prueba = models.Profesional(
                id=1,
                nombre_negocio="Studio Maria",
                slug="studio-maria",
                plan="gratuito",
                correo="maria@studio.com",
                password_hash=pwd_context.hash("123456"),
                telefono_whatsapp="04121234567",
                direccion="Av. Principal, C.C. Las Flores, Local 4",
                pais="VE",
                moneda="USD",
                simbolo_moneda="$",
                idioma="es",
                timezone="America/Caracas",
                banco="Banco de Venezuela",
                cedula_rif="V-15111111",
                telefono_pago_movil="04121111111",
                metodo_pago_nacional_tipo="Transferencia / Pago Local",
                banco_local="Banco de Venezuela",
                numero_cuenta_local="01020000000000000000",
                titular_cuenta_local="Studio Maria C.A.",
                binance_pay_id="maria_binance_pay",
                airtm_correo="maria@airtm.com",
                paypal_correo="maria@paypal.com",
                mostrar_pagos_internacionales=True,
                latitud=10.4534,
                longitud=-64.1705,
                estado_cuenta="activo"
            )
            db.add(socio_prueba)
            db.commit()
    finally:
        db.close()

crear_datos_iniciales()


# ==========================================
# GASTOS Y REPORTES CONTABLES
# ==========================================

@app.get("/api/profesional/{profesional_id}/gastos")
def obtener_gastos_logistica(profesional_id: int, db: Session = Depends(get_db)):
    if hasattr(models, 'GastoLogistica'):
        gastos = db.query(models.GastoLogistica).filter(models.GastoLogistica.profesional_id == profesional_id).all()
        return [{"id": g.id, "monto_usd": g.monto_usd, "detalle": g.detalle, "categoria": g.categoria, "fecha": g.fecha_gasto.strftime("%Y-%m-%d %I:%M %p") if g.fecha_gasto else ""} for g in gastos]
    return []

@app.post("/api/profesional/gastos")
def registrar_gasto_logistica(data: GastoLogisticaCreate, db: Session = Depends(get_db)):
    prof = db.query(models.Profesional).filter(models.Profesional.id == data.profesional_id).first()
    if not prof or (prof.plan or "").lower() != "elite":
        raise HTTPException(status_code=403, detail="⚠️ El registro de gastos de logística e insumos es exclusivo del Plan Elite ($29).")
    
    if hasattr(models, 'GastoLogistica'):
        gasto = models.GastoLogistica(
            profesional_id=data.profesional_id,
            monto_usd=float(data.monto_usd),
            detalle=sanitizar_texto(data.detalle),
            categoria=sanitizar_texto(data.categoria)
        )
        db.add(gasto)
        db.commit()
        return {"mensaje": "Gasto registrado con éxito en la contabilidad."}
    raise HTTPException(status_code=500, detail="Módulo de gastos no disponible en modelos.")


# ==========================================
# INVENTARIO Y STOCK (PLAN ELITE)
# ==========================================

@app.get("/api/profesional/{profesional_id}/inventario")
def obtener_inventario(profesional_id: int, db: Session = Depends(get_db)):
    if hasattr(models, 'ProductoInsumo'):
        productos = db.query(models.ProductoInsumo).filter(models.ProductoInsumo.profesional_id == profesional_id).all()
        resultado = []
        for p in productos:
            relaciones = []
            if hasattr(models, 'ServicioInsumoRelacion'):
                rels = db.query(models.ServicioInsumoRelacion).filter(models.ServicioInsumoRelacion.producto_id == p.id).all()
                for r in rels:
                    serv = db.query(models.Servicio).filter(models.Servicio.id == r.servicio_id).first()
                    relaciones.append({
                        "id": r.id,
                        "servicio_id": r.servicio_id,
                        "nombre_servicio": serv.nombre if serv else "Desconocido",
                        "cantidad_consumida": r.cantidad_consumida
                    })
            resultado.append({
                "id": p.id,
                "nombre": p.nombre,
                "stock_actual": p.stock_actual,
                "stock_minimo": p.stock_minimo,
                "precio_venta": p.precio_venta,
                "costo": p.costo,
                "relaciones": relaciones
            })
        return resultado
    return []

@app.post("/api/inventario/productos")
def crear_producto_insumo(data: ProductoInsumoCreate, db: Session = Depends(get_db)):
    prof = db.query(models.Profesional).filter(models.Profesional.id == data.profesional_id).first()
    if not prof or (prof.plan or "").lower() != "elite":
        raise HTTPException(status_code=403, detail="⚠️ El control avanzado de inventario y stock es exclusivo del Plan Elite ($29).")
    
    if hasattr(models, 'ProductoInsumo'):
        nuevo = models.ProductoInsumo(
            profesional_id=data.profesional_id,
            nombre=sanitizar_texto(data.nombre),
            stock_actual=float(data.stock_actual),
            stock_minimo=float(data.stock_minimo),
            precio_venta=float(data.precio_venta),
            costo=float(data.costo)
        )
        db.add(nuevo)
        db.commit()
        db.refresh(nuevo)
        return {"mensaje": "Producto o insumo registrado con éxito.", "id": nuevo.id}
    raise HTTPException(status_code=500, detail="Modelo de inventario no disponible.")

@app.put("/api/inventario/productos/{producto_id}")
def actualizar_producto_insumo(producto_id: int, data: ProductoInsumoCreate, db: Session = Depends(get_db)):
    if hasattr(models, 'ProductoInsumo'):
        prod = db.query(models.ProductoInsumo).filter(models.ProductoInsumo.id == producto_id).first()
        if not prod:
            raise HTTPException(status_code=404, detail="Producto no encontrado.")
        prod.nombre = sanitizar_texto(data.nombre)
        prod.stock_actual = float(data.stock_actual)
        prod.stock_minimo = float(data.stock_minimo)
        prod.precio_venta = float(data.precio_venta)
        prod.costo = float(data.costo)
        db.commit()
        return {"mensaje": "Producto actualizado con éxito."}
    raise HTTPException(status_code=500, detail="Modelo no disponible.")

@app.delete("/api/inventario/productos/{producto_id}")
def eliminar_producto_insumo(producto_id: int, db: Session = Depends(get_db)):
    if hasattr(models, 'ProductoInsumo'):
        prod = db.query(models.ProductoInsumo).filter(models.ProductoInsumo.id == producto_id).first()
        if prod:
            if hasattr(models, 'ServicioInsumoRelacion'):
                db.query(models.ServicioInsumoRelacion).filter(models.ServicioInsumoRelacion.producto_id == producto_id).delete()
            db.delete(prod)
            db.commit()
        return {"mensaje": "Producto eliminado con éxito."}
    raise HTTPException(status_code=500, detail="Modelo no disponible.")

@app.post("/api/inventario/servicio-insumos")
def vincular_servicio_insumo(data: ServicioInsumoRelacionCreate, db: Session = Depends(get_db)):
    if hasattr(models, 'ServicioInsumoRelacion'):
        existente = db.query(models.ServicioInsumoRelacion).filter(
            models.ServicioInsumoRelacion.servicio_id == data.servicio_id,
            models.ServicioInsumoRelacion.producto_id == data.producto_id
        ).first()
        if existente:
            existente.cantidad_consumida = float(data.cantidad_consumida)
        else:
            rel = models.ServicioInsumoRelacion(
                servicio_id=data.servicio_id,
                producto_id=data.producto_id,
                cantidad_consumida=float(data.cantidad_consumida)
            )
            db.add(rel)
        db.commit()
        return {"mensaje": "Insumo vinculado al servicio correctamente."}
    raise HTTPException(status_code=500, detail="Módulo no disponible.")

@app.delete("/api/inventario/servicio-insumos/{relacion_id}")
def desvincular_servicio_insumo(relacion_id: int, db: Session = Depends(get_db)):
    if hasattr(models, 'ServicioInsumoRelacion'):
        rel = db.query(models.ServicioInsumoRelacion).filter(models.ServicioInsumoRelacion.id == relacion_id).first()
        if rel:
            db.delete(rel)
            db.commit()
        return {"mensaje": "Vinculación eliminada."}
    raise HTTPException(status_code=500, detail="Módulo no disponible.")

@app.post("/api/inventario/venta-directa")
def registrar_venta_directa(data: VentaDirectaSchema, db: Session = Depends(get_db)):
    if hasattr(models, 'ProductoInsumo'):
        producto = db.query(models.ProductoInsumo).filter(models.ProductoInsumo.id == data.producto_id).first()
        if not producto:
            raise HTTPException(status_code=404, detail="Producto no encontrado.")
        if producto.stock_actual < data.cantidad:
            raise HTTPException(status_code=400, detail="⚠️ Stock insuficiente para registrar la venta directa.")
        
        producto.stock_actual -= float(data.cantidad)
        db.commit()
        return {"mensaje": "Venta directa registrada y stock descontado con éxito."}
    raise HTTPException(status_code=500, detail="Módulo no disponible.")


@app.get("/api/profesional/{profesional_id}/exportar-reporte-contable")
def exportar_reporte_contable(
    profesional_id: int, 
    formato: str = "csv", 
    especialista: str = "todos", 
    periodo: str = "mes", 
    db: Session = Depends(get_db)
):
    prof = db.query(models.Profesional).filter(models.Profesional.id == profesional_id).first()
    if not prof:
        raise HTTPException(status_code=404, detail="Salón no encontrado")
    
    plan = (prof.plan or "gratuito").lower()
    if plan == "gratuito":
        raise HTTPException(status_code=403, detail="⚠️ El Plan Gratuito no incluye la exportación de reportes contables.")
    
    if plan == "profesional" and especialista != "todos":
        raise HTTPException(status_code=403, detail="⚠️ En el Plan Profesional solo puedes descargar el reporte global. Actualiza a Elite ($29) para reportes por especialista.")

    citas = db.query(models.Cita).filter(models.Cita.profesional_id == profesional_id).all()
    
    if especialista != "todos":
        citas = [c for c in citas if c.personal_id and str(c.personal_id) == str(especialista)]

    hoy = datetime.datetime.now()
    citas_filtradas = []
    for c in citas:
        if not c.fecha_hora_inicio: continue
        f_cita = c.fecha_hora_inicio
        if periodo == "dia" and f_cita.date() != hoy.date(): continue
        if periodo == "semana" and (hoy - f_cita).days > 7: continue
        if periodo == "mes" and (f_cita.month != hoy.month or f_cita.year != hoy.year): continue
        citas_filtradas.append(c)

    gastos = []
    if hasattr(models, 'GastoLogistica'):
        gastos = db.query(models.GastoLogistica).filter(models.GastoLogistica.profesional_id == profesional_id).all()

    total_bruto = sum([c.monto_pagado for c in citas_filtradas if c.estado != "cancelada"])
    total_gastos = sum([g.monto_usd for g in gastos])
    neto_final = total_bruto - total_gastos

    if formato == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID Cita", "Clienta", "WhatsApp", "Servicio", "Especialista ID", "Fecha", "Estado", "Referencia", "Monto Bruto ($)"])
        for c in citas_filtradas:
            writer.writerow([c.id, c.nombre_clienta, c.whatsapp_clienta, c.servicio_id, c.personal_id or "General", c.fecha_hora_inicio, c.estado, c.referencia_pago_movil, c.monto_pagado])
        
        writer.writerow([])
        writer.writerow(["--- RESUMEN CONTABLE FINAL ---"])
        writer.writerow(["Total Bruto Recaudado ($):", total_bruto])
        writer.writerow(["Total Gastos de Logística ($):", total_gastos])
        writer.writerow(["Balance Neto Real ($):", neto_final])
        
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=corte_contable_{especialista}_{periodo}.csv"}
        )
    else:
        html_content = f"""
        <html>
        <head><title>Reporte Contable - {prof.nombre_negocio}</title>
        <style>body{{font-family:sans-serif;padding:20px;color:#1e293b;}} table{{width:100%;border-collapse:collapse;margin-top:15px;}} th,td{{border:1px solid #cbd5e1;padding:8px;text-align:left;font-size:12px;}} th{{background:#f1f5f9;}}</style>
        </head>
        <body>
            <h2>Reporte Contable: {prof.nombre_negocio}</h2>
            <p>Filtro Especialista ID: <strong>{especialista}</strong> | Período: <strong>{periodo}</strong></p>
            <table>
                <thead><tr><th>ID</th><th>Clienta</th><th>Espec. ID</th><th>Fecha</th><th>Estado</th><th>Monto ($)</th></tr></thead>
                <tbody>
        """
        for c in citas_filtradas:
            html_content += f"<tr><td>{c.id}</td><td>{c.nombre_clienta}</td><td>{c.personal_id}</td><td>{c.fecha_hora_inicio}</td><td>{c.estado}</td><td>${c.monto_pagado:.2f}</td></tr>"
        
        html_content += f"""
                </tbody>
            </table>
            <div style="margin-top:20px;padding:15px;background:#f8fafc;border-radius:8px;">
                <h3>Resumen Financiero Redondo</h3>
                <p><strong>Total Bruto Recaudado:</strong> ${total_bruto:.2f}</p>
                <p><strong>Total Gastos / Insumos:</strong> ${total_gastos:.2f}</p>
                <p><strong>Balance Neto Real:</strong> ${neto_final:.2f}</p>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)


# ==========================================
# RUTAS PWA Y CONFIGURACIÓN GENERAL
# ==========================================
@app.get("/manifest.json")
@app.get("/static/manifest.json")
def get_pwa_manifest():
    manifest_path = os.path.join("static", "manifest.json")
    if os.path.exists(manifest_path):
        return FileResponse(manifest_path, media_type="application/json")
    raise HTTPException(status_code=404, detail="Manifest no encontrado")

@app.get("/sw.js")
@app.get("/static/sw.js")
def get_service_worker():
    sw_path = os.path.join("static", "sw.js")
    if os.path.exists(sw_path):
        return FileResponse(sw_path, media_type="application/javascript")
    raise HTTPException(status_code=404, detail="Service Worker no encontrado")

@app.get("/", response_class=HTMLResponse)
@app.get("/landing", response_class=HTMLResponse)
def pagina_principal(request: Request):
    return templates.TemplateResponse(request, "landing.html", {})

@app.get("/api/tasa-bcv")
def obtener_tasa_bcv():
    return {"tasa": leer_tasa_local(), "fecha": str(datetime.date.today())}

@app.get("/api/vitrina-elite")
def obtener_vitrina_elite(db: Session = Depends(get_db)):
    tiendas_elite = db.query(models.Profesional).filter(models.Profesional.plan == "elite", models.Profesional.estado_cuenta == "activo").all()
    return [{"nombre": t.nombre_negocio, "slug": t.slug, "direccion": t.direccion, "whatsapp": t.telefono_whatsapp} for t in tiendas_elite]

@app.get("/api/configuracion-pago")
def obtener_configuracion_pago(db: Session = Depends(get_db)):
    config = db.query(models.ConfiguracionPlataforma).first()
    if not config:
        return {
            "banco": "Banco de Venezuela", 
            "cedula_rif": "V-00000000", 
            "telefono": "04120000000",
            "binance_pay_id": "",
            "airtm_correo": "",
            "paypal_correo": ""
        }
    return {
        "banco": config.banco, 
        "cedula_rif": config.cedula_rif, 
        "telefono": config.telefono,
        "binance_pay_id": getattr(config, "binance_pay_id", ""),
        "airtm_correo": getattr(config, "airtm_correo", ""),
        "paypal_correo": getattr(config, "paypal_correo", "")
    }

@app.put("/api/super-admin/configuracion-pago")
def actualizar_configuracion_pago(data: ConfiguracionPagoUpdate, db: Session = Depends(get_db)):
    config = db.query(models.ConfiguracionPlataforma).first()
    if not config:
        config = models.ConfiguracionPlataforma()
        db.add(config)
    
    if data.banco is not None: config.banco = sanitizar_texto(data.banco)
    if data.cedula_rif is not None: config.cedula_rif = sanitizar_texto(data.cedula_rif)
    if data.telefono is not None: config.telefono = sanitizar_texto(data.telefono)
    if hasattr(config, "binance_pay_id") and data.binance_pay_id is not None: config.binance_pay_id = sanitizar_texto(data.binance_pay_id)
    if hasattr(config, "airtm_correo") and data.airtm_correo is not None: config.airtm_correo = sanitizar_texto(data.airtm_correo)
    if hasattr(config, "paypal_correo") and data.paypal_correo is not None: config.paypal_correo = sanitizar_texto(data.paypal_correo)
    
    db.commit()
    return {"mensaje": "Configuración de pagos actualizada con éxito"}


# ==========================================
# CONTROL DE VERSIONES Y MANTENIMIENTO GLOBAL
# ==========================================
@app.get("/api/super-admin/version")
def api_obtener_version():
    return obtener_estado_plataforma()

@app.post("/api/super-admin/version")
def api_actualizar_version(data: VersionSchema):
    estado = obtener_estado_plataforma()
    estado["version"] = data.version
    estado["build_timestamp"] = int(time.time())
    guardar_estado_plataforma(estado)
    return {"status": "ok", "mensaje": f"Versión actualizada a {data.version}"}

@app.post("/api/super-admin/mantenimiento")
def api_toggle_mantenimiento(data: MantenimientoSchema):
    estado = obtener_estado_plataforma()
    estado["modo_mantenimiento"] = data.activo
    guardar_estado_plataforma(estado)
    return {"status": "ok", "modo_mantenimiento": data.activo}

@app.post("/api/super-admin/broadcast-update")
def api_broadcast_update():
    estado = obtener_estado_plataforma()
    estado["build_timestamp"] = int(time.time())
    guardar_estado_plataforma(estado)
    return {"status": "ok", "mensaje": "Señal de actualización emitida con éxito"}


# ==========================================
# AUTENTICACIÓN Y REGISTRO
# ==========================================
@app.post("/api/registro")
@limiter.limit("5/minute")
def registrar_profesional(request: Request, data: schemas.ProfesionalRegistro, response: Response, db: Session = Depends(get_db)):
    data_slug = sanitizar_texto(data.slug).lower()
    data_correo = sanitizar_texto(data.email).lower()
    data_whatsapp = sanitizar_texto(data.telefono_whatsapp)

    if db.query(models.Profesional).filter(models.Profesional.slug == data_slug).first():
        raise HTTPException(status_code=400, detail="El link personal (slug) ya está en uso.")
    if db.query(models.Profesional).filter(models.Profesional.correo == data_correo).first():
        raise HTTPException(status_code=400, detail="El correo ya está registrado.")

    nuevo_prof = models.Profesional(
        nombre_negocio=sanitizar_texto(data.nombre_negocio),
        slug=data_slug,
        plan="gratuito",
        correo=data_correo,
        password_hash=pwd_context.hash(data.password),
        telefono_whatsapp=data_whatsapp,
        direccion=sanitizar_texto(data.direccion),
        pais=getattr(data, 'country', None) or getattr(data, 'pais', 'VE') or 'VE',
        moneda=getattr(data, 'currency', None) or getattr(data, 'moneda', 'USD') or 'USD',
        simbolo_moneda=getattr(data, 'currency_symbol', '$') or '$',
        idioma=getattr(data, 'language', 'es') or 'es',
        timezone=getattr(data, 'timezone', 'America/Caracas') or 'America/Caracas',
        banco=sanitizar_texto(data.banco or "N/A"),
        cedula_rif=sanitizar_texto(data.cedula_rif or "N/A"),
        telefono_pago_movil=sanitizar_texto(data.telefono_pago_movil or "N/A"),
        latitud=data.latitud,
        longitud=data.longitud,
        estado_cuenta="activo"
    )
    db.add(nuevo_prof)
    db.commit()
    db.refresh(nuevo_prof)
    
    db.add(models.Personal(profesional_id=nuevo_prof.id, nombre=nuevo_prof.nombre_negocio, especialidad="Especialista Principal", comision_porcentaje=100.0))
    db.commit()

    token = jwt.encode({"sub": str(nuevo_prof.id), "slug": nuevo_prof.slug, "exp": datetime.datetime.utcnow() + datetime.timedelta(days=7)}, SECRET_KEY, algorithm=ALGORITHM)
    
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=604800,
        path="/"
    )

    return {"mensaje": "Registro exitoso", "slug": nuevo_prof.slug, "profesional_id": nuevo_prof.id}

@app.post("/api/login")
@limiter.limit("5/minute")
def login_profesional(request: Request, data: schemas.ProfesionalLogin, response: Response, db: Session = Depends(get_db)):
    prof = db.query(models.Profesional).filter(models.Profesional.correo == sanitizar_texto(data.correo).lower()).first()
    if not prof or not pwd_context.verify(data.password, prof.password_hash):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas.")
    
    verificar_vencimiento_socio(prof, db)
    db.commit()
    
    token = jwt.encode({"sub": str(prof.id), "slug": prof.slug, "exp": datetime.datetime.utcnow() + datetime.timedelta(days=7)}, SECRET_KEY, algorithm=ALGORITHM)
    
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=604800,
        path="/"
    )

    return {"mensaje": "Login exitoso", "profesional_id": prof.id, "nombre_negocio": prof.nombre_negocio, "slug": prof.slug}

@app.post("/api/logout")
def logout_profesional(response: Response):
    response.delete_cookie(
        key="access_token",
        path="/",
        secure=True,
        samesite="strict",
        httponly=True
    )
    return {"mensaje": "Sesión cerrada correctamente"}

@app.post("/api/recuperar-password/solicitar")
@limiter.limit("3/minute")
def solicitar_recuperacion(request: Request, data: SolicitarRecuperacionSchema, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    correo = sanitizar_texto(data.correo).lower()
    prof = db.query(models.Profesional).filter(models.Profesional.correo == correo).first()
    if not prof:
        raise HTTPException(status_code=404, detail="El correo no se encuentra registrado.")
    
    codigo = str(random.randint(100000, 999999))
    expira_dt = datetime.datetime.now() + datetime.timedelta(seconds=80)
    
    if hasattr(models, 'CodigoRecuperacion'):
        db.query(models.CodigoRecuperacion).filter(models.CodigoRecuperacion.correo == correo).delete()
        db.add(models.CodigoRecuperacion(correo=correo, codigo=codigo, expira_at=expira_dt))
        db.commit()

    background_tasks.add_task(enviar_correo_codigo_bonnyn, correo, codigo)
    
    return {"mensaje": "Código enviado con éxito."}

@app.post("/api/recuperar-password/verificar-y-cambiar")
@limiter.limit("5/minute")
def verificar_y_cambiar_password(request: Request, data: VerificarRecuperacionSchema, db: Session = Depends(get_db)):
    correo = sanitizar_texto(data.correo).lower()
    valido = False
    
    if hasattr(models, 'CodigoRecuperacion'):
        reg = db.query(models.CodigoRecuperacion).filter(
            models.CodigoRecuperacion.correo == correo,
            models.CodigoRecuperacion.codigo == data.codigo,
            models.CodigoRecuperacion.expira_at >= datetime.datetime.now()
        ).first()
        if reg:
            valido = True
            db.delete(reg)
            db.commit()

    if not valido:
        raise HTTPException(status_code=400, detail="Código inválido o expirado.")
    
    prof = db.query(models.Profesional).filter(models.Profesional.correo == correo).first()
    if prof:
        prof.password_hash = pwd_context.hash(data.nueva_password)
        db.commit()
    return {"mensaje": "Contraseña actualizada exitosamente."}


# ==========================================
# NOTAS CLÍNICAS Y LOGOS
# ==========================================
@app.get("/api/notas-clinicas/{profesional_id}")
def obtener_notas_clinicas(profesional_id: int, db: Session = Depends(get_db)):
    if hasattr(models, 'NotaClinicaPaciente'):
        notas = db.query(models.NotaClinicaPaciente).filter(models.NotaClinicaPaciente.profesional_id == profesional_id).all()
        return [{"id": n.id, "whatsapp_clienta": n.whatsapp_clienta, "nombre_clienta": n.nombre_clienta, "diagnostico_notas": n.diagnostico_notas, "fecha_actualizacion": n.fecha_actualizacion.strftime("%Y-%m-%d %I:%M %p") if hasattr(n, 'fecha_actualizacion') and n.fecha_actualizacion else ""} for n in notas]
    return []

@app.post("/api/notas-clinicas/")
def guardar_nota_clinica(data: NotaClinicaSchema, db: Session = Depends(get_db)):
    if hasattr(models, 'NotaClinicaPaciente'):
        wsp = sanitizar_texto(data.whatsapp_clienta)
        existente = db.query(models.NotaClinicaPaciente).filter(models.NotaClinicaPaciente.profesional_id == data.profesional_id, models.NotaClinicaPaciente.whatsapp_clienta == wsp).first()
        if existente:
            existente.nombre_clienta = sanitizar_texto(data.nombre_clienta)
            existente.diagnostico_notas = sanitizar_texto(data.diagnostico_notas)
        else:
            db.add(models.NotaClinicaPaciente(profesional_id=data.profesional_id, whatsapp_clienta=wsp, nombre_clienta=sanitizar_texto(data.nombre_clienta), diagnostico_notas=sanitizar_texto(data.diagnostico_notas)))
        db.commit()
        return {"mensaje": "Ficha guardada"}
    return {"mensaje": "No disponible"}

@app.post("/api/profesional/{profesional_id}/logo", response_model=dict)
async def upload_salon_logo(
    profesional_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    prof = db.query(models.Profesional).filter(models.Profesional.id == profesional_id).first()
    if not prof:
        raise HTTPException(status_code=404, detail="Salón no encontrado")
    
    allowed_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Formato de imagen no soportado. Usa JPG, PNG o WebP.")

    remove_old_logo(prof.logo_url)
    
    try:
        image_data = file.file.read()
        img = Image.open(io.BytesIO(image_data))
        
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
            
        img.thumbnail((300, 300), Image.Resampling.LANCZOS)
        
        filename = f"logo_{profesional_id}_{int(time.time())}.webp"
        filepath = os.path.join(OS_UPLOAD_DIR, filename)
        
        img.save(filepath, "WEBP", quality=80, optimize=True)
        
        logo_url = f"/static/logos/{filename}"
        prof.logo_url = logo_url
        db.commit()
        db.refresh(prof)
        
        return {"message": "Logo actualizado con éxito", "logo_url": logo_url}
    except Exception as e:
        logger.error(f"Error al procesar la imagen: {e}")
        raise HTTPException(status_code=500, detail="Error interno al procesar e optimizar el logo")


# ==========================================
# GESTIÓN DE NEGOCIOS Y CLIENTES
# ==========================================
@app.get("/api/profesional/{profesional_id}")
def obtener_profesional_by_id(profesional_id: int, db: Session = Depends(get_db)):
    prof = db.query(models.Profesional).filter(models.Profesional.id == profesional_id).first()
    if not prof:
        raise HTTPException(status_code=404, detail="No encontrado")
    verificar_vencimiento_socio(prof, db)
    db.commit()
    return {
        "id": prof.id, "nombre_negocio": prof.nombre_negocio, "slug": prof.slug,
        "telefono_whatsapp": prof.telefono_whatsapp, "direccion": prof.direccion,
        "pais": prof.pais or "VE", "moneda": prof.moneda or "USD", "simbolo_moneda": prof.simbolo_moneda or "$",
        "timezone": getattr(prof, 'timezone', 'America/Caracas') or 'America/Caracas',
        "logo_url": prof.logo_url,
        "banco": prof.banco, "cedula_rif": prof.cedula_rif, "telefono_pago_movil": prof.telefono_pago_movil,
        "metodo_pago_nacional_tipo": getattr(prof, 'metodo_pago_nacional_tipo', 'Transferencia / Pago Local'),
        "banco_local": getattr(prof, 'banco_local', prof.banco),
        "numero_cuenta_local": getattr(prof, 'numero_cuenta_local', prof.telefono_pago_movil),
        "titular_cuenta_local": getattr(prof, 'titular_cuenta_local', prof.nombre_negocio),
        "binance_pay_id": getattr(prof, 'binance_pay_id', ''),
        "airtm_correo": getattr(prof, 'airtm_correo', ''),
        "paypal_correo": getattr(prof, 'paypal_correo', ''),
        "mostrar_pagos_internacionales": getattr(prof, 'mostrar_pagos_internacionales', True),
        "latitud": prof.latitud, "longitud": prof.longitud, "plan": prof.plan,
        "estado_cuenta": prof.estado_cuenta, "fecha_vencimiento": prof.fecha_vencimiento.strftime("%Y-%m-%d") if prof.fecha_vencimiento else "Sin límite",
        "monto_multa_pendiente": getattr(prof, "monto_multa_pendiente", 0.0),
        "motivo_multa": getattr(prof, "motivo_multa", None)
    }

@app.get("/api/negocio-slug/{slug}")
def obtener_profesional_por_slug(slug: str, db: Session = Depends(get_db)):
    prof = db.query(models.Profesional).filter(models.Profesional.slug == slug).first()
    if not prof or prof.estado_cuenta == "baneado" or prof.plan == "suspendido_multa":
        raise HTTPException(status_code=403, detail="Salón suspendido o en proceso de transición.")
    verificar_vencimiento_socio(prof, db)
    db.commit()

    servicios = db.query(models.Servicio).filter(models.Servicio.profesional_id == prof.id).all()
    cat_ids = list(set([s.categoria_id for s in servicios]))
    categorias = db.query(models.CategoriaServicio).filter(models.CategoriaServicio.id.in_(cat_ids)).all()
    personal = db.query(models.Personal).filter(models.Personal.profesional_id == prof.id).all()
    plan = (prof.plan or "gratuito").lower()

    if plan == "gratuito" and len(personal) > 1:
        personal = sorted(personal, key=lambda p: p.id)[:1]
    elif plan == "profesional" and len(personal) > 3:
        personal = sorted(personal, key=lambda p: p.id)[:3]

    return {
        "id": prof.id, 
        "nombre_negocio": prof.nombre_negocio, 
        "slug": prof.slug,
        "telefono_whatsapp": prof.telefono_whatsapp, 
        "direccion": prof.direccion,
        "pais": prof.pais or "VE", 
        "moneda": prof.moneda or "USD", 
        "simbolo_moneda": prof.simbolo_moneda or "$",
        "timezone": getattr(prof, 'timezone', 'America/Caracas') or 'America/Caracas',
        "logo_url": prof.logo_url,
        "banco": prof.banco, 
        "cedula_rif": prof.cedula_rif, 
        "telefono_pago_movil": prof.telefono_pago_movil,
        "metodo_pago_nacional_tipo": getattr(prof, 'metodo_pago_nacional_tipo', 'Transferencia / Pago Local'),
        "banco_local": getattr(prof, 'banco_local', prof.banco),
        "numero_cuenta_local": getattr(prof, 'numero_cuenta_local', prof.telefono_pago_movil),
        "titular_cuenta_local": getattr(prof, 'titular_cuenta_local', prof.nombre_negocio),
        "binance_pay_id": getattr(prof, 'binance_pay_id', ''),
        "airtm_correo": getattr(prof, 'airtm_correo', ''),
        "paypal_correo": getattr(prof, 'paypal_correo', ''),
        "mostrar_pagos_internacionales": getattr(prof, 'mostrar_pagos_internacionales', True),
        "latitud": prof.latitud, 
        "longitud": prof.longitud, 
        "plan": prof.plan,
        "estado_cuenta": prof.estado_cuenta, 
        "fecha_vencimiento": prof.fecha_vencimiento.strftime("%Y-%m-%d") if prof.fecha_vencimiento else "Sin límite",
        "categorias": [{"id": c.id, "nombre": c.nombre, "icono": c.icono} for c in categorias],
        "personal": [{"id": p.id, "nombre": p.nombre, "especialidad": p.especialidad} for p in personal]
    }

@app.get("/api/negocio-slug/{slug}/servicios")
def obtener_servicios_slug(slug: str, categoria_id: int = None, db: Session = Depends(get_db)):
    prof = db.query(models.Profesional).filter(models.Profesional.slug == slug).first()
    if not prof or prof.estado_cuenta == "baneado" or prof.plan == "suspendido_multa":
        raise HTTPException(status_code=403, detail="Salón suspendido.")
    q = db.query(models.Servicio).filter(models.Servicio.profesional_id == prof.id)
    if categoria_id:
        q = q.filter(models.Servicio.categoria_id == categoria_id)
    servicios = q.all()
    plan = (prof.plan or "gratuito").lower()
    if plan == "gratuito" and len(servicios) > 3:
        servicios = sorted(servicios, key=lambda s: s.id)[:3]
    elif plan == "profesional" and len(servicios) > 8:
        servicios = sorted(servicios, key=lambda s: s.id)[:8]
    return servicios

@app.get("/api/servicios/")
def obtener_servicios_por_profesional(profesional_id: int = None, db: Session = Depends(get_db)):
    q = db.query(models.Servicio)
    if profesional_id:
        q = q.filter(models.Servicio.profesional_id == profesional_id)
    return q.all()

@app.post("/api/servicios/")
def crear_servicio(serv: schemas.ServicioCreate, db: Session = Depends(get_db)):
    prof = db.query(models.Profesional).filter(models.Profesional.id == serv.profesional_id).first()
    plan = (prof.plan or "gratuito").lower() if prof else "gratuito"
    actuales = db.query(models.Servicio).filter(models.Servicio.profesional_id == serv.profesional_id).count()
    if plan == "gratuito" and actuales >= 3:
        raise HTTPException(status_code=400, detail="⚠️ Plan Gratuito: Máximo 3 servicios permitidos.")
    if plan == "profesional" and actuales >= 8:
        raise HTTPException(status_code=400, detail="⚠️ Plan Profesional: Máximo 8 servicios permitidos.")

    nuevo = models.Servicio(profesional_id=serv.profesional_id, categoria_id=serv.categoria_id, nombre=sanitizar_texto(serv.nombre), duracion_minutos=serv.duracion_minutos, precio_usd=serv.precio_usd)
    db.add(nuevo)
    db.commit()
    return {"mensaje": "Servicio creado"}

@app.put("/api/servicios/{servicio_id}")
def actualizar_servicio(servicio_id: int, serv: schemas.ServicioCreate, db: Session = Depends(get_db)):
    s = db.query(models.Servicio).filter(models.Servicio.id == servicio_id).first()
    if s:
        s.categoria_id = serv.categoria_id
        s.nombre = sanitizar_texto(serv.nombre)
        s.duracion_minutos = serv.duracion_minutos
        s.precio_usd = serv.precio_usd
        db.commit()
    return {"mensaje": "Actualizado"}

@app.delete("/api/servicios/{servicio_id}")
def eliminar_servicio(servicio_id: int, db: Session = Depends(get_db)):
    s = db.query(models.Servicio).filter(models.Servicio.id == servicio_id).first()
    if s:
        db.delete(s)
        db.commit()
    return {"mensaje": "Eliminado"}


# ==========================================
# PAGOS Y WEBHOOKS CON IDEMPOTENCIA
# ==========================================
@app.get("/api/profesional/{profesional_id}/historial-pagos")
def obtener_historial_pagos(profesional_id: int, db: Session = Depends(get_db)):
    pagos = db.query(models.PagoSuscripcion).filter(models.PagoSuscripcion.profesional_id == profesional_id).order_by(models.PagoSuscripcion.fecha_pago.desc()).all()
    return [{"id": p.id, "monto_usd": p.monto_usd, "monto_bs": p.monto_bs, "referencia": p.referencia_pago, "nuevo_plan": p.nuevo_plan, "fecha": p.fecha_pago.strftime("%Y-%m-%d %I:%M %p"), "estado": p.estado_pago} for p in pagos]

@app.post("/api/suscripciones/reportar")
def reportar_pago_suscripcion(data: schemas.PagoSuscripcionReport, db: Session = Depends(get_db)):
    ref_limpia = sanitizar_texto(data.referencia_pago)
    if not ref_limpia or len(ref_limpia) < 4:
        raise HTTPException(status_code=400, detail="⚠️ La referencia de pago es inválida o muy corta (mínimo 4 dígitos).")
    
    duplicado_pago = db.query(models.PagoSuscripcion).filter(
        models.PagoSuscripcion.referencia_pago == ref_limpia,
        models.PagoSuscripcion.estado_pago.in_(["verificando", "aprobado"])
    ).first()
    if duplicado_pago:
        raise HTTPException(status_code=400, detail="⚠️ Esta referencia de pago ya fue reportada o utilizada anteriormente.")

    tasa = leer_tasa_local()
    monto_usd = float(data.monto_usd)
    db.add(models.PagoSuscripcion(
        profesional_id=data.profesional_id, 
        nuevo_plan=sanitizar_texto(data.nuevo_plan), 
        monto_usd=monto_usd, 
        monto_bs=monto_usd * tasa, 
        referencia_pago=ref_limpia, 
        estado_pago="verificando"
    ))
    db.commit()
    return {"mensaje": "Reportado"}

@app.post("/api/webhook/auto-pago")
def webhook_auto_aprobar_pago(datos: WebhookPagoInput, db: Session = Depends(get_db)):
    ref_limpia = datos.referencia.strip()

    pago_suscripcion_ya_aprobado = db.query(models.PagoSuscripcion).filter(
        models.PagoSuscripcion.referencia_pago == ref_limpia,
        models.PagoSuscripcion.estado_pago == "aprobado"
    ).first()
    if pago_suscripcion_ya_aprobado:
        return {
            "status": "success",
            "tipo": "idempotente",
            "message": f"⚠️ La referencia {ref_limpia} ya había sido procesada anteriormente con éxito."
        }

    pago_suscripcion = db.query(models.PagoSuscripcion).filter(
        models.PagoSuscripcion.referencia_pago == ref_limpia,
        models.PagoSuscripcion.estado_pago == "verificando"
    ).first()

    if pago_suscripcion:
        pago_suscripcion.estado_pago = "aprobado"
        profesional = db.query(models.Profesional).filter(models.Profesional.id == pago_suscripcion.profesional_id).first()
        
        if profesional:
            if pago_suscripcion.nuevo_plan == "multa_reactivacion" or profesional.plan == "suspendido_multa":
                profesional.plan = "gratuito"
                profesional.estado_cuenta = "activo"
                profesional.monto_multa_pendiente = 0.0
                profesional.motivo_multa = None
            else:
                hoy = datetime.datetime.now()
                nueva_fecha = hoy + datetime.timedelta(days=30)
                profesional.plan = pago_suscripcion.nuevo_plan
                profesional.estado_cuenta = "activo"
                profesional.fecha_vencimiento = nueva_fecha
                profesional.monto_multa_pendiente = 0.0
                profesional.motivo_multa = None

        db.commit()
        return {
            "status": "success",
            "tipo": "suscripcion_socio",
            "message": f"¡Pago/Multa con referencia {ref_limpia} procesado con éxito!",
            "nuevo_plan": profesional.plan if profesional else "N/A",
            "negocio_activado": profesional.nombre_negocio if profesional else "Desconocido"
        }

    cita_pendiente = db.query(models.Cita).filter(
        models.Cita.referencia_pago_movil == ref_limpia,
        models.Cita.estado == "pendiente"
    ).first()

    if cita_pendiente:
        cita_pendiente.estado = "confirmada"
        db.commit()

        try:
            enviar_mensaje_whatsapp(
                cita_pendiente.whatsapp_clienta, 
                "cita_confirmada_admin", 
                [cita_pendiente.nombre_clienta, cita_pendiente.fecha_hora_inicio.strftime("%Y-%m-%d %I:%M %p")]
            )
        except Exception:
            pass

        return {
            "status": "success",
            "tipo": "cita_clienta",
            "message": f"¡Cita de la clienta {cita_pendiente.nombre_clienta} confirmada automáticamente con la referencia {ref_limpia}!",
            "cita_id": cita_pendiente.id
        }

    raise HTTPException(
        status_code=404, 
        detail=f"No se encontró ninguna suscripción, multa o cita pendiente con la referencia '{ref_limpia}'."
    )


# ==========================================
# HORARIOS, CITAS Y PERSONAL
# ==========================================
@app.get("/api/profesional/{profesional_id}/horarios")
def obtener_horarios(profesional_id: int, db: Session = Depends(get_db)):
    h = db.query(models.HorarioProfesional).filter(models.HorarioProfesional.profesional_id == profesional_id).all()
    if not h:
        for d in ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]:
            db.add(models.HorarioProfesional(profesional_id=profesional_id, dia_semana=d, hora_apertura="08:00", hora_cierre_manana="12:00", hora_apertura_tarde="13:00", hora_cierre="18:00", activo=(d != "Domingo")))
        db.commit()
        h = db.query(models.HorarioProfesional).filter(models.HorarioProfesional.profesional_id == profesional_id).all()
    return h

@app.put("/api/profesional/{profesional_id}/horarios")
def actualizar_horarios(profesional_id: int, data: schemas.HorariosUpdate, db: Session = Depends(get_db)):
    db.query(models.HorarioProfesional).filter(models.HorarioProfesional.profesional_id == profesional_id).delete()
    for item in data.horarios:
        db.add(models.HorarioProfesional(profesional_id=profesional_id, **item.dict()))
    db.commit()
    return {"mensaje": "Actualizado"}

@app.get("/api/profesional/{profesional_id}/dias-bloqueados")
def obtener_dias_bloqueados(profesional_id: int, db: Session = Depends(get_db)):
    bloqueos = db.query(models.DiaBloqueado).filter(models.DiaBloqueado.profesional_id == profesional_id).all()
    return [{"id": b.id, "fecha": b.fecha, "personal_id": b.personal_id, "motivo": b.motivo} for b in bloqueos]

@app.post("/api/profesional/dias-bloqueados")
def bloquear_dia_db(data: schemas.DiaBloqueadoCreate, db: Session = Depends(get_db)):
    prof = db.query(models.Profesional).filter(models.Profesional.id == data.profesional_id).first()
    if not prof:
        raise HTTPException(status_code=404, detail="Salón no encontrado.")

    plan_actual = (prof.plan or "gratuito").lower()

    if data.personal_id is not None or data.aplicar_multa:
        if plan_actual != "elite":
            raise HTTPException(status_code=403, detail="⚠️ El bloqueo por especialista y sanciones son exclusivos del Plan Elite ($29).")

    nuevo_bloqueo = models.DiaBloqueado(
        profesional_id=data.profesional_id,
        personal_id=data.personal_id,
        fecha=data.fecha,
        motivo=sanitizar_texto(data.motivo or "Falta / Vacaciones")
    )
    db.add(nuevo_bloqueo)

    if data.aplicar_multa and data.personal_id and data.monto_multa > 0:
        nueva_multa = models.MultaEspecialista(
            profesional_id=data.profesional_id,
            personal_id=data.personal_id,
            monto_original=float(data.monto_multa),
            moneda=prof.moneda or "USD",
            motivo=sanitizar_texto(f"Multa por ausencia: {data.motivo}"),
            estado_pago="pendiente"
        )
        db.add(nueva_multa)

    db.commit()
    return {"mensaje": "Día bloqueado e incidencia registrada con éxito."}

@app.delete("/api/profesional/{profesional_id}/dias-bloqueados/{bloqueo_id}")
def eliminar_dia_bloqueado_db(profesional_id: int, bloqueo_id: int, db: Session = Depends(get_db)):
    b = db.query(models.DiaBloqueado).filter(models.DiaBloqueado.id == bloqueo_id, models.DiaBloqueado.profesional_id == profesional_id).first()
    if b:
        db.delete(b)
        db.commit()
    return {"mensaje": "Desbloqueado"}

@app.get("/api/profesional/{profesional_id}/multas")
def obtener_multas_especialistas(profesional_id: int, db: Session = Depends(get_db)):
    multas = db.query(models.MultaEspecialista).filter(models.MultaEspecialista.profesional_id == profesional_id).all()
    resultado = []
    for m in multas:
        personal = db.query(models.Personal).filter(models.Personal.id == m.personal_id).first()
        resultado.append({
            "id": m.id,
            "personal_id": m.personal_id,
            "nombre_personal": personal.nombre if personal else "Espec. Eliminado",
            "monto": m.monto_original,
            "moneda": m.moneda,
            "motivo": m.motivo,
            "estado": m.estado_pago,
            "fecha": m.fecha_registro.strftime("%Y-%m-%d %I:%M %p") if m.fecha_registro else ""
        })
    return resultado

@app.patch("/api/multas/{multa_id}/estado")
def actualizar_estado_multa(multa_id: int, estado: str, db: Session = Depends(get_db)):
    multa = db.query(models.MultaEspecialista).filter(models.MultaEspecialista.id == multa_id).first()
    if not multa:
        raise HTTPException(status_code=404, detail="Multa no encontrada.")
    
    multa.estado_pago = sanitizar_texto(estado).lower()
    if multa.estado_pago == "pagada":
        multa.fecha_pago = datetime.datetime.now()
    db.commit()
    return {"mensaje": f"Multa marcada como {multa.estado_pago}"}

@app.put("/api/profesional/{profesional_id}")
def actualizar_profesional(profesional_id: int, data: schemas.ProfesionalUpdate, db: Session = Depends(get_db)):
    prof = db.query(models.Profesional).filter(models.Profesional.id == profesional_id).first()
    if prof:
        prof.nombre_negocio = sanitizar_texto(data.nombre_negocio)
        prof.telefono_whatsapp = sanitizar_texto(data.telefono_whatsapp)
        prof.direccion = sanitizar_texto(data.direccion)
        prof.banco = sanitizar_texto(data.banco)
        prof.cedula_rif = sanitizar_texto(data.cedula_rif)
        prof.telefono_pago_movil = sanitizar_texto(data.telefono_pago_movil)
        prof.latitud = data.latitud
        prof.longitud = data.longitud
        if hasattr(data, 'pais') and data.pais: prof.pais = sanitizar_texto(data.pais)
        if hasattr(data, 'moneda') and data.moneda: prof.moneda = sanitizar_texto(data.moneda)
        if hasattr(data, 'metodo_pago_nacional_tipo') and data.metodo_pago_nacional_tipo: prof.metodo_pago_nacional_tipo = sanitizar_texto(data.metodo_pago_nacional_tipo)
        if hasattr(data, 'banco_local') and data.banco_local: prof.banco_local = sanitizar_texto(data.banco_local)
        if hasattr(data, 'numero_cuenta_local') and data.numero_cuenta_local: prof.numero_cuenta_local = sanitizar_texto(data.numero_cuenta_local)
        if hasattr(data, 'titular_cuenta_local') and data.titular_cuenta_local: prof.titular_cuenta_local = sanitizar_texto(data.titular_cuenta_local)
        if hasattr(data, 'binance_pay_id') and data.binance_pay_id is not None: prof.binance_pay_id = sanitizar_texto(data.binance_pay_id)
        if hasattr(data, 'airtm_correo') and data.airtm_correo is not None: prof.airtm_correo = sanitizar_texto(data.airtm_correo)
        if hasattr(data, 'paypal_correo') and data.paypal_correo is not None: prof.paypal_correo = sanitizar_texto(data.paypal_correo)
        if hasattr(data, 'mostrar_pagos_internacionales') and data.mostrar_pagos_internacionales is not None: prof.mostrar_pagos_internacionales = data.mostrar_pagos_internacionales
        db.commit()
    return {"mensaje": "Actualizado"}

@app.get("/api/personal/{profesional_id}")
def obtener_personal(profesional_id: int, db: Session = Depends(get_db)):
    return db.query(models.Personal).filter(models.Personal.profesional_id == profesional_id).all()

@app.post("/api/personal/")
def crear_personal(data: schemas.PersonalCreate, db: Session = Depends(get_db)):
    prof = db.query(models.Profesional).filter(models.Profesional.id == data.profesional_id).first()
    plan = (prof.plan or "gratuito").lower() if prof else "gratuito"
    actuales = db.query(models.Personal).filter(models.Personal.profesional_id == data.profesional_id).count()
    if plan == "gratuito" and actuales >= 1:
        raise HTTPException(status_code=400, detail="⚠️ Plan Gratuito: 1 especialista máximo.")
    if plan == "profesional" and actuales >= 3:
        raise HTTPException(status_code=400, detail="⚠️ Plan Profesional: 3 especialistas máximo.")

    p = models.Personal(profesional_id=data.profesional_id, nombre=sanitizar_texto(data.nombre), especialidad=sanitizar_texto(data.especialidad), comision_porcentaje=data.comision_porcentaje)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p

@app.put("/api/personal/{personal_id}")
def actualizar_personal(personal_id: int, data: schemas.PersonalCreate, db: Session = Depends(get_db)):
    p = db.query(models.Personal).filter(models.Personal.id == personal_id).first()
    if p:
        p.nombre = sanitizar_texto(data.nombre)
        p.especialidad = sanitizar_texto(data.especialidad)
        p.comision_porcentaje = data.comision_porcentaje
        db.commit()
    return {"mensaje": "Actualizado"}

@app.delete("/api/personal/{personal_id}")
def eliminar_personal(personal_id: int, db: Session = Depends(get_db)):
    p = db.query(models.Personal).filter(models.Personal.id == personal_id).first()
    if p:
        db.delete(p)
        db.commit()
    return {"mensaje": "Eliminado"}

@app.post("/api/citas/verificar-disponibilidad")
def verificar_disponibilidad(data: schemas.CitaCreate, db: Session = Depends(get_db)):
    prof = db.query(models.Profesional).filter(models.Profesional.id == data.profesional_id).first()
    if not prof or prof.estado_cuenta == "baneado" or prof.plan == "suspendido_multa":
        raise HTTPException(status_code=403, detail="⚠️ Salón suspendido.")

    verificar_vencimiento_socio(prof, db)
    db.commit()

    try:
        dt_inicio = datetime.datetime.strptime(data.fecha_hora_inicio_str, "%Y-%m-%d %H:%M")
    except Exception:
        raise HTTPException(status_code=400, detail="Formato de fecha inválido")

    fecha_str = dt_inicio.strftime("%Y-%m-%d")

    if db.query(models.DiaBloqueado).filter(
        models.DiaBloqueado.profesional_id == prof.id, 
        models.DiaBloqueado.personal_id.is_(None),
        models.DiaBloqueado.fecha == fecha_str
    ).first():
        return {"disponible": False, "personal_asignado_id": None, "mensaje": "⚠️ Salón cerrado por vacaciones o día bloqueado."}

    if dt_inicio < datetime.datetime.now():
        return {"disponible": False, "personal_asignado_id": None, "mensaje": "⚠️ No puedes agendar citas en el pasado."}

    serv = db.query(models.Servicio).filter(models.Servicio.id == data.servicio_id).first()
    if not serv:
        raise HTTPException(status_code=404, detail="Servicio no válido")
    
    dt_fin = dt_inicio + datetime.timedelta(minutes=serv.duracion_minutos)
    
    dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    nombre_dia_str = dias_semana[dt_inicio.weekday()]

    horario = db.query(models.HorarioProfesional).filter(
        models.HorarioProfesional.profesional_id == prof.id, 
        models.HorarioProfesional.dia_semana == nombre_dia_str
    ).first()
    
    if not horario or not horario.activo:
        return {"disponible": False, "personal_asignado_id": None, "mensaje": f"⚠️ El salón no labora el día {nombre_dia_str}."}

    def a_minutos(h_s):
        if not h_s: return 0
        try:
            p = h_s.strip().split(":")
            return int(p[0]) * 60 + int(p[1])
        except Exception:
            return 0

    t_inicio_min = a_minutos(dt_inicio.strftime("%H:%M"))
    t_fin_min = t_inicio_min + serv.duracion_minutos

    aper_min = a_minutos(horario.hora_apertura or "08:00")
    cierre_max = a_minutos(horario.hora_cierre or "18:00")
    pausa_ini = a_minutos(horario.hora_cierre_manana or "12:00")
    pausa_fin = a_minutos(horario.hora_apertura_tarde or "13:00")

    en_horario_general = (aper_min <= t_inicio_min and t_fin_min <= cierre_max)
    cae_en_pausa = (t_inicio_min < pausa_fin and t_fin_min > pausa_ini)

    if not en_horario_general or cae_en_pausa:
        return {"disponible": False, "personal_asignado_id": None, "mensaje": f"⚠️ Fuera de la jornada laboral."}

    personal_bloqueado_ids = [
        b.personal_id for b in db.query(models.DiaBloqueado).filter(
            models.DiaBloqueado.profesional_id == prof.id,
            models.DiaBloqueado.personal_id.isnot(None),
            models.DiaBloqueado.fecha == fecha_str
        ).all()
    ]

    empleados = db.query(models.Personal).filter(
        models.Personal.profesional_id == prof.id,
        ~models.Personal.id.in_(personal_bloqueado_ids) if personal_bloqueado_ids else True
    ).all()

    if not empleados:
        return {"disponible": False, "personal_asignado_id": None, "mensaje": "⚠️ No hay especialistas disponibles en esta fecha." }

    if data.personal_id:
        if data.personal_id in personal_bloqueado_ids:
            return {"disponible": False, "personal_asignado_id": None, "mensaje": "⚠️ El especialista seleccionado se encuentra ausente en esta fecha."}

        conflicto = db.query(models.Cita).filter(
            models.Cita.personal_id == data.personal_id,
            models.Cita.estado.in_(["pendiente", "confirmada"]),
            models.Cita.fecha_hora_inicio < dt_fin,
            models.Cita.fecha_hora_fin > dt_inicio
        ).first()

        if conflicto:
            return {"disponible": False, "personal_asignado_id": None, "mensaje": "⚠️ Especialista ocupado en este horario."}
        return {"disponible": True, "personal_asignado_id": data.personal_id}

    ids_ocupados = db.query(models.Cita.personal_id).filter(
        models.Cita.profesional_id == prof.id,
        models.Cita.personal_id.isnot(None),
        models.Cita.estado.in_(["pendiente", "confirmada"]),
        models.Cita.fecha_hora_inicio < dt_fin,
        models.Cita.fecha_hora_fin > dt_inicio
    ).distinct().all()
    
    ids_ocupados_set = {row[0] for row in ids_ocupados}
    emp_disponible = next((emp for emp in empleados if emp.id not in ids_ocupados_set), None)

    if emp_disponible:
        return {"disponible": True, "personal_asignado_id": emp_disponible.id, "mensaje": f"✅ Disponible con {emp_disponible.nombre}"}

    return {"disponible": False, "personal_asignado_id": None, "mensaje": "⚠️ Todos los especialistas están ocupados en este horario."}

@app.post("/api/citas/")
@limiter.limit("15/minute")
def agendar_cita(request: Request, cita: schemas.CitaCreate, db: Session = Depends(get_db)):
    ref_movil = sanitizar_texto(cita.referencia_pago_movil)
    if not ref_movil or len(ref_movil) < 4:
        raise HTTPException(status_code=400, detail="⚠️ La referencia de pago es obligatoria (mínimo 4 dígitos).")
    
    duplicado_cita = db.query(models.Cita).filter(
        models.Cita.referencia_pago_movil == ref_movil,
        models.Cita.estado.in_(["pendiente", "confirmada"])
    ).first()
    if duplicado_cita:
        raise HTTPException(status_code=400, detail="⚠️ Esta referencia de pago móvil ya fue utilizada en otra cita activa.")

    verif = verificar_disponibilidad(cita, db)
    if not verif.get("disponible"):
        raise HTTPException(status_code=400, detail=verif.get("mensaje", "Horario no disponible."))

    prof = db.query(models.Profesional).filter(models.Profesional.id == cita.profesional_id).first()
    serv = db.query(models.Servicio).filter(models.Servicio.id == cita.servicio_id).first()

    dt_inicio = datetime.datetime.strptime(cita.fecha_hora_inicio_str, "%Y-%m-%d %H:%M")
    dt_fin = dt_inicio + datetime.timedelta(minutes=serv.duracion_minutos)
    personal_id_final = verif.get("personal_asignado_id") or cita.personal_id

    tasa = cita.tasa_bcv_actual if (prof.pais or "VE") == "VE" and cita.tasa_bcv_actual > 0 else leer_tasa_local()

    monto_total_real = serv.precio_usd if serv else float(cita.monto_pagado or 0.0)
    modalidad = getattr(cita, "modalidad_pago", "completo") or "completo"

    if modalidad == "mitad":
        monto_abonado = monto_total_real / 2.0
        saldo_pendiente = monto_total_real / 2.0
        estado_financiero = "parcial"
    else:
        modalidad = "completo"
        monto_abonado = monto_total_real
        saldo_pendiente = 0.0
        estado_financiero = "pagado"

    nueva = models.Cita(
        profesional_id=cita.profesional_id, 
        servicio_id=cita.servicio_id, 
        personal_id=personal_id_final,
        nombre_clienta=sanitizar_texto(cita.nombre_clienta), 
        whatsapp_clienta=sanitizar_texto(cita.whatsapp_clienta),
        fecha_hora_inicio=dt_inicio, 
        fecha_hora_fin=dt_fin, 
        referencia_pago_movil=ref_movil,
        monto_total_usd=monto_total_real,
        monto_abonado_usd=monto_abonado,
        saldo_pendiente_usd=saldo_pendiente,
        modalidad_pago=modalidad,
        estado_financiero_pago=estado_financiero,
        monto_pagado=monto_abonado, 
        tasa_bcv_historica=tasa, 
        monto_bs=monto_abonado * tasa, 
        estado="pendiente"
    )
    db.add(nueva)
    db.commit()
    db.refresh(nueva)

    enviar_mensaje_whatsapp(nueva.whatsapp_clienta, "cita_programada_detalles", [nueva.nombre_clienta, dt_inicio.strftime("%Y-%m-%d %I:%M %p"), serv.nombre])
    return {"mensaje": "Agendado con éxito.", "cita_id": nueva.id, "monto_abonado": monto_abonado, "saldo_pendiente": saldo_pendiente}

@app.put("/api/citas/{cita_id}/liquidar")
def liquidar_saldo_cita(cita_id: int, data: LiquidarCitaSchema, db: Session = Depends(get_db)):
    c = db.query(models.Cita).filter(models.Cita.id == cita_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Cita no encontrada.")
    
    if getattr(c, 'saldo_pendiente_usd', 0.0) <= 0:
        return {"mensaje": "La cita ya se encuentra totalmente pagada."}

    c.saldo_pendiente_usd = 0.0
    c.estado_financiero_pago = "pagado"
    c.referencia_segundo_pago = sanitizar_texto(data.referencia_segundo_pago or "EfectivoEnSalon")
    db.commit()
    return {"mensaje": "¡Saldo pendiente liquidado y contabilidad actualizada con éxito!"}

@app.get("/api/citas/")
def obtener_citas(profesional_id: int = None, db: Session = Depends(get_db)):
    q = db.query(models.Cita)
    if profesional_id:
        q = q.filter(models.Cita.profesional_id == profesional_id)
    citas = q.order_by(models.Cita.fecha_hora_inicio.desc()).all()
    res = []
    for c in citas:
        serv = db.query(models.Servicio).filter(models.Servicio.id == c.servicio_id).first()
        pers = db.query(models.Personal).filter(models.Personal.id == c.personal_id).first() if c.personal_id else None
        res.append({
            "id": c.id, 
            "nombre_clienta": c.nombre_clienta, 
            "whatsapp_clienta": c.whatsapp_clienta,
            "servicio": serv.nombre if serv else "Desconocido", 
            "personal": pers.nombre if pers else "General",
            "precio_total_usd": c.monto_total_usd if getattr(c, 'monto_total_usd', None) is not None else (serv.precio_usd if serv else 0.0),
            "monto_abonado_usd": c.monto_abonado_usd if getattr(c, 'monto_abonado_usd', None) is not None else c.monto_pagado,
            "saldo_pendiente_usd": c.saldo_pendiente_usd if getattr(c, 'saldo_pendiente_usd', None) is not None else 0.0,
            "modalidad_pago": getattr(c, 'modalidad_pago', 'completo'),
            "estado_financiero_pago": getattr(c, 'estado_financiero_pago', 'pagado'),
            "fecha_hora_inicio": c.fecha_hora_inicio.strftime("%Y-%m-%d %I:%M %p"),
            "referencia_pago_movil": c.referencia_pago_movil, 
            "referencia_segundo_pago": getattr(c, 'referencia_segundo_pago', None),
            "monto_pagado": c.monto_pagado, 
            "monto_bs": c.monto_bs, 
            "estado": c.estado
        })
    return res

@app.get("/api/citas/cliente/{whatsapp}")
def consultar_citas_cliente(whatsapp: str, profesional_id: int = None, db: Session = Depends(get_db)):
    q = db.query(models.Cita).filter(models.Cita.whatsapp_clienta == whatsapp, models.Cita.estado.in_(["pendiente", "confirmada"]))
    if profesional_id:
        q = q.filter(models.Cita.profesional_id == profesional_id)
    res = []
    for c in q.order_by(models.Cita.fecha_hora_inicio.desc()).all():
        serv = db.query(models.Servicio).filter(models.Servicio.id == c.servicio_id).first()
        pers = db.query(models.Personal).filter(models.Personal.id == c.personal_id).first() if c.personal_id else None
        res.append({
            "id": c.id, 
            "nombre_clienta": c.nombre_clienta, 
            "whatsapp_clienta": c.whatsapp_clienta,
            "servicio": serv.nombre if serv else "Desconocido", 
            "personal": pers.nombre if pers else "General",
            "precio_total_usd": c.monto_total_usd if getattr(c, 'monto_total_usd', None) is not None else (serv.precio_usd if serv else 0.0),
            "monto_abonado_usd": c.monto_abonado_usd if getattr(c, 'monto_abonado_usd', None) is not None else c.monto_pagado,
            "saldo_pendiente_usd": c.saldo_pendiente_usd if getattr(c, 'saldo_pendiente_usd', None) is not None else 0.0,
            "modalidad_pago": getattr(c, 'modalidad_pago', 'completo'),
            "estado_financiero_pago": getattr(c, 'estado_financiero_pago', 'pagado'),
            "fecha_hora_inicio": c.fecha_hora_inicio.strftime("%Y-%m-%d %I:%M %p"), 
            "monto_pagado": c.monto_pagado, 
            "estado": c.estado
        })
    return res

@app.patch("/api/citas/{cita_id}/estado")
def cambiar_estado_cita(cita_id: int, nuevo_estado: str, db: Session = Depends(get_db)):
    c = db.query(models.Cita).filter(models.Cita.id == cita_id).first()
    if c:
        ant = c.estado
        nuevo_est_limpio = sanitizar_texto(nuevo_estado)
        c.estado = nuevo_est_limpio
        
        if nuevo_est_limpio == "completada" and ant != "completada":
            if hasattr(models, 'ServicioInsumoRelacion') and hasattr(models, 'ProductoInsumo'):
                relaciones = db.query(models.ServicioInsumoRelacion).filter(models.ServicioInsumoRelacion.servicio_id == c.servicio_id).all()
                for rel in relaciones:
                    prod = db.query(models.ProductoInsumo).filter(models.ProductoInsumo.id == rel.producto_id).first()
                    if prod:
                        prod.stock_actual = max(0.0, prod.stock_actual - rel.cantidad_consumida)

        db.commit()

        if ant != "confirmada" and c.estado == "confirmada":
            enviar_mensaje_whatsapp(c.whatsapp_clienta, "cita_confirmada_admin", [c.nombre_clienta, c.fecha_hora_inicio.strftime("%Y-%m-%d %I:%M %p")])
        elif c.estado == "cancelada":
            enviar_mensaje_whatsapp(c.whatsapp_clienta, "cita_cancelada_admin", [c.nombre_clienta, c.fecha_hora_inicio.strftime("%Y-%m-%d %I:%M %p")])
    return {"mensaje": "Actualizado"}

@app.put("/api/citas/{cita_id}/reprogramar")
def reprogramar_cita(cita_id: int, data: schemas.CitaReprogramar, db: Session = Depends(get_db)):
    c = db.query(models.Cita).filter(models.Cita.id == cita_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="No encontrada")
    dt = datetime.datetime.strptime(data.nueva_fecha_hora_str, "%Y-%m-%d %H:%M")
    serv = db.query(models.Servicio).filter(models.Servicio.id == c.servicio_id).first()
    c.fecha_hora_inicio = dt
    c.fecha_hora_fin = dt + datetime.timedelta(minutes=serv.duracion_minutos if serv else 30)
    c.estado = "confirmada"
    db.commit()
    enviar_mensaje_whatsapp(c.whatsapp_clienta, "cita_confirmada_admin", [c.nombre_clienta, dt.strftime("%Y-%m-%d %I:%M %p")])
    return {"mensaje": "Reprogramada"}


# ==========================================
# SUPER ADMIN Y SEGURIDAD ESTRICTA MASTER
# ==========================================
def verificar_sesion_master(request: Request):
    token_sesion = request.cookies.get("master_session_token")
    if not token_sesion or token_sesion != MASTER_TOKEN_SECRET:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso Denegado: Credenciales de Administrador Global requeridas."
        )
    return True

@app.get("/super-admin/login", response_class=HTMLResponse)
def vista_login_master(request: Request):
    return templates.TemplateResponse(request, "login_admin.html", {})

@app.post("/api/super-admin/login")
@limiter.limit("5/minute")
def procesar_login_master(request: Request, email: str = Form(...), password: str = Form(...)):
    if email.strip().lower() == SUPER_ADMIN_EMAIL.lower() and password == SUPER_ADMIN_PASSWORD:
        resp = RedirectResponse(url="/super-admin", status_code=status.HTTP_302_FOUND)
        resp.set_cookie(
            key="master_session_token",
            value=MASTER_TOKEN_SECRET,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=86400,
            path="/"
        )
        return resp
    else:
        raise HTTPException(status_code=401, detail="Credenciales Master incorrectas.")

@app.get("/super-admin/logout")
def logout_master():
    resp = RedirectResponse(url="/super-admin/login", status_code=status.HTTP_302_FOUND)
    resp.delete_cookie("master_session_token", path="/")
    return resp

@app.get("/api/super-admin/metricas")
def admin_metricas_globales(request: Request, db: Session = Depends(get_db)):
    verificar_sesion_master(request)
    total_salones = db.query(models.Profesional).count()
    salones_activos = db.query(models.Profesional).filter(models.Profesional.estado_cuenta == "activo", models.Profesional.plan != "suspendido_multa").count()
    total_citas = db.query(models.Cita).count()
    
    pagos_aprobados = db.query(models.PagoSuscripcion).filter(models.PagoSuscripcion.estado_pago == "aprobado").all()
    total_ingresos_usd = sum([p.monto_usd for p in pagos_aprobados])
    total_ingresos_bs = sum([p.monto_bs for p in pagos_aprobados])

    profesionales = db.query(models.Profesional).filter(models.Profesional.estado_cuenta == "activo", models.Profesional.plan != "suspendido_multa").all()
    mrr = sum([15.0 if p.plan == "profesional" else (29.0 if p.plan == "elite" else 0.0) for p in profesionales])

    return {
        "total_salones": total_salones,
        "salones_activos": salones_activos,
        "total_citas": total_citas,
        "mrr": mrr,
        "total_ingresos_usd": total_ingresos_usd,
        "total_ingresos_bs": total_ingresos_bs
    }

@app.get("/api/super-admin/socios")
def admin_obtener_socios(request: Request, db: Session = Depends(get_db)):
    verificar_sesion_master(request)
    socios = db.query(models.Profesional).all()
    resultado = []
    for s in socios:
        citas_total = db.query(models.Cita).filter(models.Cita.profesional_id == s.id).count()
        citas_activas = db.query(models.Cita).filter(models.Cita.profesional_id == s.id, models.Cita.estado.in_(["pendiente", "confirmada"])).count()
        citas_completadas = db.query(models.Cita).filter(models.Cita.profesional_id == s.id, models.Cita.estado == "completada").count()
        citas_canceladas = db.query(models.Cita).filter(models.Cita.profesional_id == s.id, models.Cita.estado == "cancelada").count()

        resultado.append({
            "id": s.id,
            "nombre_negocio": s.nombre_negocio,
            "slug": s.slug,
            "correo": s.correo,
            "whatsapp": s.telefono_whatsapp,
            "pais": s.pais or "VE",
            "moneda": s.moneda or "USD",
            "plan": s.plan,
            "estado": s.estado_cuenta,
            "citas_total": citas_total,
            "citas_activas": citas_activas,
            "citas_completadas": citas_completadas,
            "citas_canceladas": citas_canceladas,
            "fecha_vencimiento": s.fecha_vencimiento.strftime("%Y-%m-%d") if s.fecha_vencimiento else "Sin fecha",
            "monto_multa_pendiente": getattr(s, "monto_multa_pendiente", 0.0)
        })
    return resultado

@app.get("/api/super-admin/pagos-pendientes")
def admin_pagos_pendientes(request: Request, db: Session = Depends(get_db)):
    verificar_sesion_master(request)
    pagos = db.query(models.PagoSuscripcion).filter(models.PagoSuscripcion.estado_pago == "verificando").all()
    resultado = []
    for p in pagos:
        prof = db.query(models.Profesional).filter(models.Profesional.id == p.profesional_id).first()
        resultado.append({
            "pago_id": p.id,
            "profesional_id": p.profesional_id,
            "nombre_negocio": prof.nombre_negocio if prof else "Desconocido",
            "slug": prof.slug if prof else "",
            "nuevo_plan": p.nuevo_plan,
            "monto_usd": p.monto_usd,
            "monto_bs": p.monto_bs,
            "referencia": p.referencia_pago,
            "fecha": p.fecha_pago.strftime("%Y-%m-%d %I:%M %p")
        })
    return resultado

@app.get("/api/super-admin/todos-los-pagos")
def admin_todos_los_pagos(request: Request, db: Session = Depends(get_db)):
    verificar_sesion_master(request)
    pagos = db.query(models.PagoSuscripcion).order_by(models.PagoSuscripcion.fecha_pago.desc()).limit(200).all()
    resultado = []
    for p in pagos:
        prof = db.query(models.Profesional).filter(models.Profesional.id == p.profesional_id).first()
        resultado.append({
            "pago_id": p.id,
            "nombre_negocio": prof.nombre_negocio if prof else "Desconocido",
            "nuevo_plan": p.nuevo_plan,
            "monto_usd": p.monto_usd,
            "monto_bs": p.monto_bs,
            "referencia": p.referencia_pago,
            "estado_pago": p.estado_pago,
            "fecha": p.fecha_pago.strftime("%Y-%m-%d %I:%M %p")
        })
    return resultado

@app.post("/api/super-admin/pagos/{pago_id}/aprobar")
def admin_aprobar_pago(pago_id: int, request: Request, db: Session = Depends(get_db)):
    verificar_sesion_master(request)
    p = db.query(models.PagoSuscripcion).filter(models.PagoSuscripcion.id == pago_id).first()
    if p:
        prof = db.query(models.Profesional).filter(models.Profesional.id == p.profesional_id).first()
        if prof:
            if p.nuevo_plan == "multa_reactivacion" or prof.plan == "suspendido_multa":
                prof.plan = "gratuito"
                prof.estado_cuenta = "activo"
                prof.monto_multa_pendiente = 0.0
                prof.motivo_multa = None
            else:
                prof.plan = p.nuevo_plan
                prof.estado_cuenta = "activo"
                prof.fecha_vencimiento = datetime.datetime.now() + datetime.timedelta(days=30)
                prof.monto_multa_pendiente = 0.0
                prof.motivo_multa = None
        p.estado_pago = "aprobado"
        db.commit()
    return {"mensaje": "Aprobado"}

@app.post("/api/super-admin/pagos/{pago_id}/rechazar")
def admin_rechazar_pago(pago_id: int, request: Request, db: Session = Depends(get_db)):
    verificar_sesion_master(request)
    p = db.query(models.PagoSuscripcion).filter(models.PagoSuscripcion.id == pago_id).first()
    if p:
        p.estado_pago = "rechazado"
        db.commit()
    return {"mensaje": "Rechazado"}

@app.patch("/api/super-admin/socios/{socio_id}/estado")
def admin_cambiar_estado_socio(socio_id: int, estado: str, request: Request, db: Session = Depends(get_db)):
    verificar_sesion_master(request)
    s = db.query(models.Profesional).filter(models.Profesional.id == socio_id).first()
    if s:
        s.estado_cuenta = sanitizar_texto(estado).lower()
        db.commit()
    return {"mensaje": "Actualizado"}

@app.put("/api/super-admin/socios/{socio_id}/plan")
def admin_cambiar_plan_socio(socio_id: int, data: dict, request: Request, db: Session = Depends(get_db)):
    verificar_sesion_master(request)
    s = db.query(models.Profesional).filter(models.Profesional.id == socio_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Socio no encontrado")
    
    if "plan" in data:
        s.plan = sanitizar_texto(data["plan"]).lower()
    if "fecha_vencimiento" in data and data["fecha_vencimiento"]:
        try:
            s.fecha_vencimiento = datetime.datetime.strptime(data["fecha_vencimiento"], "%Y-%m-%d")
        except Exception:
            raise HTTPException(status_code=400, detail="Formato de fecha inválido (YYYY-MM-DD)")
    
    db.commit()
    return {"mensaje": "Plan y vencimiento actualizados con éxito"}


# ==========================================
# RUTAS DE VISTAS HTML
# ==========================================
@app.get("/reserva/{slug}", response_class=HTMLResponse)
def vista_reserva(request: Request, slug: str): return templates.TemplateResponse(request, "index.html", {"slug": slug})
@app.get("/registro", response_class=HTMLResponse)
def vista_registro(request: Request): return templates.TemplateResponse(request, "registro.html", {})
@app.get("/login", response_class=HTMLResponse)
def vista_login(request: Request): return templates.TemplateResponse(request, "login.html", {})
@app.get("/admin", response_class=HTMLResponse)
def vista_admin(request: Request): return templates.TemplateResponse(request, "admin.html", {})

@app.get("/super-admin", response_class=HTMLResponse)
@app.get("/super_admin", response_class=HTMLResponse)
def vista_super_admin(request: Request):
    try:
        verificar_sesion_master(request)
        return templates.TemplateResponse(request, "super_admin.html", {})
    except HTTPException:
        return RedirectResponse(url="/super-admin/login", status_code=status.HTTP_302_FOUND)