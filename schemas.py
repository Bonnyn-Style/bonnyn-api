import datetime
import re
import pytz
from typing import Optional, List
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, Date
from sqlalchemy.ext.declarative import declarative_base
from pydantic import BaseModel, EmailStr, field_validator

Base = declarative_base()

# ==========================================
# LÓGICA DE VALIDACIÓN Y NORMALIZACIÓN DE PAGOS
# ==========================================

def limpiar_y_validar_referencia(ref: str, metodo: str = "movil") -> str:
    """
    Limpia espacios, elimina caracteres extraños y valida la longitud 
    y el formato según el tipo de método de pago.
    """
    if not ref:
        raise ValueError("La referencia de pago no puede estar vacía.")
    
    # Eliminar espacios y convertir a mayúsculas para estandarizar
    ref_limpia = ref.strip().upper()
    
    # Validaciones según el método
    if metodo in ["movil", "nacional", "banco"]:
        # Solo números para referencias bancarias nacionales (ej. Pago Móvil / BNC)
        # Exigimos un mínimo de 4 dígitos numéricos puros.
        solo_numeros = re.sub(r'\D', '', ref_limpia)
        if len(solo_numeros) < 4:
            raise ValueError("La referencia bancaria nacional debe contener al menos 4 dígitos numéricos válidos.")
        return solo_numeros
    else:
        # Para pasarelas internacionales (Binance Pay ID, Airtm, PayPal Txn ID)
        # Exigimos un mínimo de 6 caracteres alfanuméricos.
        ref_alfa = re.sub(r'[^A-Z0-9_-]', '', ref_limpia)
        if len(ref_alfa) < 6:
            raise ValueError("La referencia internacional o de pasarela debe tener al menos 6 caracteres alfanuméricos.")
        return ref_alfa


# ==========================================
# MODELOS DE BASE DE DATOS (SQLALCHEMY)
# ==========================================

class Profesional(Base):
    __tablename__ = "profesionales"
    
    id = Column(Integer, primary_key=True, index=True)
    nombre_negocio = Column(String, index=True)
    slug = Column(String, unique=True, index=True)
    plan = Column(String, default="gratuito")  # gratuito, profesional, elite, suspendido_multa
    correo = Column(String, unique=True, index=True)
    password_hash = Column(String)
    telefono_whatsapp = Column(String)
    direccion = Column(String, nullable=True)
    
    # --- Configuración Internacional y Multipaís ---
    logo_url = Column(String, nullable=True)
    pais = Column(String, default="VE")         # Código ISO (VE, CO, EC, PE, CL, AR, PA, MX, US)
    moneda = Column(String, default="USD")       # USD, COP, MXN, PEN, CLP, ARS, VES
    simbolo_moneda = Column(String, default="$") # $, S/, bs, etc.
    idioma = Column(String, default="es")        # "es" para Español, "en" para Inglés
    timezone = Column(String, default="America/Caracas") # Zona horaria del negocio
    
    # Datos de Pago Tradicionales / Venezuela (Pago Móvil)
    banco = Column(String, nullable=True)
    cedula_rif = Column(String, nullable=True)
    telefono_pago_movil = Column(String, nullable=True)
    
    # --- Nuevos Campos para Métodos de Pago Nacionales e Internacionales (Multipaís) ---
    metodo_pago_nacional_tipo = Column(String, nullable=True)     # Ej: Yape, Nequi, CBU, Transferencia Local, Zelle
    banco_local = Column(String, nullable=True)                   # Banco destino para transferencias locales
    numero_cuenta_local = Column(String, nullable=True)           # Número de cuenta / CBU / CVU / CLABE
    titular_cuenta_local = Column(String, nullable=True)          # Titular de la cuenta local
    documento_titular_local = Column(String, nullable=True)       # Cédula / RUT / DNI del titular
    telefono_pago_local = Column(String, nullable=True)           # Teléfono vinculado (Yape, Plin, Nequi, DaviPlata, Yappy)
    
    # Pasarelas Globales Universales (Para Todos los Países)
    binance_pay_id = Column(String, nullable=True)                # Binance Pay ID o correo asociado
    airtm_correo = Column(String, nullable=True)                  # Correo electrónico o username de Airtm
    paypal_correo = Column(String, nullable=True)                 # Correo electrónico de PayPal
    mostrar_pagos_internacionales = Column(Boolean, default=True) # Interruptor para mostrar/ocultar pasarelas globales en el portal

    latitud = Column(Float, default=10.4534)
    longitud = Column(Float, default=-64.1705)
    estado_cuenta = Column(String, default="activo") # activo, baneado
    fecha_vencimiento = Column(DateTime, nullable=True)

class Servicio(Base):
    __tablename__ = "servicios"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"))
    categoria_id = Column(Integer)
    nombre = Column(String)
    duracion_minutos = Column(Integer)
    precio_usd = Column(Float)

class CategoriaServicio(Base):
    __tablename__ = "categorias_servicio"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String)
    icono = Column(String)

class Personal(Base):
    __tablename__ = "personal"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"))
    nombre = Column(String)
    especialidad = Column(String)
    comision_porcentaje = Column(Float, default=50.0)

class HorarioProfesional(Base):
    __tablename__ = "horarios_profesional"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"))
    dia_semana = Column(String)
    hora_apertura = Column(String)
    hora_cierre_manana = Column(String)
    hora_apertura_tarde = Column(String)
    hora_cierre = Column(String)
    activo = Column(Boolean, default=True)

class DiaBloqueado(Base):
    __tablename__ = "dias_bloqueados"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"))
    personal_id = Column(Integer, ForeignKey("personal.id"), nullable=True)  # Si es NULL, bloquea todo el salón; si tiene ID, bloquea a un especialista específico (Plan Elite)
    fecha = Column(String, index=True)  # Formato "YYYY-MM-DD"
    motivo = Column(String, default="Falta / Reposo / Vacaciones") # Motivo del bloqueo o falta médica

class MultaEspecialista(Base):
    __tablename__ = "multas_especialistas"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"))
    personal_id = Column(Integer, ForeignKey("personal.id"))
    monto_original = Column(Float, default=0.0)
    moneda = Column(String, default="USD")
    motivo = Column(String)
    fecha_registro = Column(DateTime, default=datetime.datetime.now)
    estado_pago = Column(String, default="pendiente") # "pendiente", "pagada", "cancelada"
    fecha_pago = Column(DateTime, nullable=True)

class Cita(Base):
    __tablename__ = "citas"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"))
    servicio_id = Column(Integer, ForeignKey("servicios.id"))
    personal_id = Column(Integer, ForeignKey("personal.id"), nullable=True)
    nombre_clienta = Column(String)
    whatsapp_clienta = Column(String)
    fecha_hora_inicio = Column(DateTime)
    fecha_hora_fin = Column(DateTime)
    
    # --- Datos de Pago de la Cita y Soporte para Pagos Parciales ---
    referencia_pago_movil = Column(String, nullable=True)       # Referencia o comprobante del primer pago o pago completo
    referencia_segundo_pago = Column(String, nullable=True)     # Referencia del saldo pendiente si se paga online posteriormente
    metodo_pago_utilizado = Column(String, default="pago_movil") # p.ej. pago_movil, yape, nequi, cbu, binance_pay, airtm, efectivo
    
    monto_total_usd = Column(Float, default=0.0)                # Valor total real del servicio (para contabilidad y comisiones)
    monto_abonado_usd = Column(Float, default=0.0)              # Monto abonado/pagado online inicialmente
    saldo_pendiente_usd = Column(Float, default=0.0)            # Saldo pendiente por cancelar en el salón o online
    modalidad_pago = Column(String, default="completo")         # "completo" (100%) o "mitad" (50% abono)
    estado_financiero_pago = Column(String, default="pendiente") # "pendiente", "parcial", "pagado", "saldado_en_salon"

    monto_pagado = Column(Float)                                # Mantenido por compatibilidad retroactiva
    tasa_bcv_historica = Column(Float, nullable=True)
    monto_bs = Column(Float, nullable=True)
    estado = Column(String, default="pendiente")
    
    notif_2h_enviada = Column(Boolean, default=False)
    notif_30m_enviada = Column(Boolean, default=False)

class PagoSuscripcion(Base):
    __tablename__ = "pagos_suscripcion"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"))
    nuevo_plan = Column(String)
    monto_usd = Column(Float)
    monto_bs = Column(Float)
    referencia_pago = Column(String)
    estado_pago = Column(String, default="verificando")
    fecha_pago = Column(DateTime, default=datetime.datetime.now)

class ConfiguracionPlataforma(Base):
    __tablename__ = "configuracion_plataforma"

    id = Column(Integer, primary_key=True, index=True)
    banco = Column(String, default="Banco de Venezuela")
    cedula_rif = Column(String, default="V-00000000")
    telefono = Column(String, default="04120000000")
    binance_pay_id = Column(String, nullable=True)
    airtm_correo = Column(String, nullable=True)
    paypal_correo = Column(String, nullable=True)

class NotaClinicaPaciente(Base):
    __tablename__ = "notas_clinicas_paciente"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"))
    whatsapp_clienta = Column(String, index=True)
    nombre_clienta = Column(String)
    diagnostico_notas = Column(Text)
    fecha_actualizacion = Column(DateTime, default=datetime.datetime.now)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"))
    name = Column(String, index=True)
    stock = Column(Integer, default=0)
    price = Column(Float, default=0.0)

class ServiceInventory(Base):
    __tablename__ = "service_inventory"
    id = Column(Integer, primary_key=True, index=True)
    servicio_id = Column(Integer, ForeignKey("servicios.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity_used = Column(Integer, default=1)

class LoyaltyAccount(Base):
    __tablename__ = "loyalty_accounts"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"))
    client_whatsapp = Column(String, index=True)
    points = Column(Integer, default=0)
    cashback_balance = Column(Float, default=0.0)

class CodigoRecuperacion(Base):
    __tablename__ = "codigos_recuperacion"
    id = Column(Integer, primary_key=True, index=True)
    correo = Column(String, index=True)
    codigo = Column(String(6))
    expira_at = Column(DateTime)


# ==========================================
# ESQUEMAS DE PYDANTIC PARA VALIDACIÓN
# ==========================================

class ProfesionalRegistro(BaseModel):
    nombre_negocio: str
    slug: str
    email: str
    password: str
    telefono_whatsapp: str
    direccion: Optional[str] = ""
    country: Optional[str] = "VE"
    currency: Optional[str] = "USD"
    currency_symbol: Optional[str] = "$"
    language: Optional[str] = "es"
    timezone: Optional[str] = "America/Caracas"
    banco: Optional[str] = "N/A"
    cedula_rif: Optional[str] = "N/A"
    telefono_pago_movil: Optional[str] = "N/A"
    latitud: Optional[float] = 10.4534
    longitud: Optional[float] = -64.1705

class ProfesionalLogin(BaseModel):
    correo: str
    password: str

class ProfesionalUpdate(BaseModel):
    nombre_negocio: str
    telefono_whatsapp: str
    direccion: Optional[str] = ""
    banco: Optional[str] = ""
    cedula_rif: str = ""
    telefono_pago_movil: str = ""
    latitud: Optional[float] = 10.4534
    longitud: Optional[float] = -64.1705
    pais: Optional[str] = "VE"
    moneda: Optional[str] = "USD"
    metodo_pago_nacional_tipo: Optional[str] = ""
    banco_local: Optional[str] = ""
    numero_cuenta_local: Optional[str] = ""
    titular_cuenta_local: Optional[str] = ""
    binance_pay_id: Optional[str] = ""
    airtm_correo: Optional[str] = ""
    paypal_correo: Optional[str] = ""
    mostrar_pagos_internacionales: Optional[bool] = True

class ServicioCreate(BaseModel):
    profesional_id: int
    categoria_id: int
    nombre: str
    duracion_minutos: int
    precio_usd: float

class PersonalCreate(BaseModel):
    profesional_id: int
    nombre: str
    especialidad: str
    comision_porcentaje: float = 50.0

class HorarioItem(BaseModel):
    dia_semana: str
    hora_apertura: str
    hora_cierre_manana: str
    hora_apertura_tarde: str
    hora_cierre: str
    activo: bool

class HorariosUpdate(BaseModel):
    horarios: List[HorarioItem]

class DiaBloqueadoCreate(BaseModel):
    profesional_id: int
    personal_id: Optional[int] = None
    fecha: str
    motivo: Optional[str] = "Falta / Reposo / Vacaciones"
    aplicar_multa: Optional[bool] = False
    monto_multa: Optional[float] = 0.0

class CitaCreate(BaseModel):
    profesional_id: int
    servicio_id: int
    personal_id: Optional[int] = None
    nombre_clienta: str
    whatsapp_clienta: str
    fecha_hora_inicio_str: str
    referencia_pago_movil: str
    monto_pagado: float
    tasa_bcv_actual: float = 0.0
    modalidad_pago: Optional[str] = "completo"

    @field_validator('referencia_pago_movil')
    @classmethod
    def validar_ref_cita(cls, v: str) -> str:
        return limpiar_y_validar_referencia(v, metodo="movil")

class CitaReprogramar(BaseModel):
    nueva_fecha_hora_str: str

class PagoSuscripcionReport(BaseModel):
    profesional_id: int
    nuevo_plan: str
    monto_usd: float
    referencia_pago: str

    @field_validator('referencia_pago')
    @classmethod
    def validar_ref_suscripcion(cls, v: str) -> str:
        return limpiar_y_validar_referencia(v, metodo="movil")