from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, Date
from sqlalchemy.ext.declarative import declarative_base
import datetime
import pytz

Base = declarative_base()

class Profesional(Base):
    __tablename__ = "profesionales"
    
    id = Column(Integer, primary_key=True, index=True)
    nombre_negocio = Column(String, index=True)
    slug = Column(String, unique=True, index=True) # 👈 Indexado para velocidad máxima en landing
    plan = Column(String, default="gratuito")  # gratuito, profesional, elite, suspendido_multa
    correo = Column(String, unique=True, index=True) # 👈 Indexado para autenticación y login rápido
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
    estado_cuenta = Column(String, default="activo", index=True) # 👈 Indexado para filtros de cuentas activas/suspendidas
    fecha_vencimiento = Column(DateTime, nullable=True, index=True) # 👈 Indexado para control de vigencia de planes
    
    # --- Control de Alertas de Vencimiento de Suscripción ---
    notif_vencimiento_24h_enviada = Column(Boolean, default=False)

    # --- Nuevos Campos para la Lógica de Transición y Multas por Citas Pendientes ---
    monto_multa_pendiente = Column(Float, default=0.0)
    motivo_multa = Column(String, nullable=True)

class Servicio(Base):
    __tablename__ = "servicios"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"), index=True) # 👈 Indexado para listados rápidos por salón
    categoria_id = Column(Integer, index=True)
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
    profesional_id = Column(Integer, ForeignKey("profesionales.id"), index=True) # 👈 Indexado
    nombre = Column(String)
    especialidad = Column(String)
    comision_porcentaje = Column(Float, default=50.0)

class HorarioProfesional(Base):
    __tablename__ = "horarios_profesional"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"), index=True)
    dia_semana = Column(String)
    hora_apertura = Column(String)
    hora_cierre_manana = Column(String)
    hora_apertura_tarde = Column(String)
    hora_cierre = Column(String)
    activo = Column(Boolean, default=True)

class DiaBloqueado(Base):
    __tablename__ = "dias_bloqueados"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"), index=True)
    personal_id = Column(Integer, ForeignKey("personal.id"), nullable=True, index=True)  # Si es NULL, bloquea todo el salón; si tiene ID, bloquea a un especialista específico (Plan Elite)
    fecha = Column(String, index=True)  # Formato "YYYY-MM-DD"
    motivo = Column(String, default="Falta / Reposo / Vacaciones") # Motivo del bloqueo o falta médica

class MultaEspecialista(Base):
    __tablename__ = "multas_especialistas"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"), index=True)
    personal_id = Column(Integer, ForeignKey("personal.id"), index=True)
    monto_original = Column(Float, default=0.0)
    moneda = Column(String, default="USD")
    motivo = Column(String)
    fecha_registro = Column(DateTime, default=datetime.datetime.now)
    estado_pago = Column(String, default="pendiente", index=True) # 👈 Indexado para filtrado de multas
    fecha_pago = Column(DateTime, nullable=True)

class Cita(Base):
    __tablename__ = "citas"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"), index=True) # 👈 Crucial para consultas de agenda
    servicio_id = Column(Integer, ForeignKey("servicios.id"))
    personal_id = Column(Integer, ForeignKey("personal.id"), nullable=True, index=True)
    nombre_clienta = Column(String)
    whatsapp_clienta = Column(String, index=True) # 👈 Indexado para historial rápido de clientas
    fecha_hora_inicio = Column(DateTime, index=True) # 👈 Indexado para ordenamiento y filtros de tiempo
    fecha_hora_fin = Column(DateTime)
    
    # --- Datos de Pago de la Cita ---
    referencia_pago_movil = Column(String, nullable=True, index=True) # 👈 Indexado para webhooks y comprobación de duplicados
    metodo_pago_utilizado = Column(String, default="pago_movil") # p.ej. pago_movil, yape, nequi, cbu, binance_pay, airtm, efectivo
    
    monto_pagado = Column(Float)
    monto_total_usd = Column(Float, nullable=True)     
    monto_abonado_usd = Column(Float, nullable=True)
    saldo_pendiente_usd = Column(Float, nullable=True)   # <-- Agregado y asegurado para sincronización perfecta
    modalidad_pago = Column(String, nullable=True) 
    estado_financiero_pago = Column(String, nullable=True, index=True) # 👈 Indexado para reportes contables
    tasa_bcv_historica = Column(Float, nullable=True)
    monto_bs = Column(Float, nullable=True)
    estado = Column(String, default="pendiente", index=True) # 👈 Indexado para filtros masivos de estado de citas
    
    notif_2h_enviada = Column(Boolean, default=False)
    notif_30m_enviada = Column(Boolean, default=False)

class PagoSuscripcion(Base):
    __tablename__ = "pagos_suscripcion"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"), index=True)
    nuevo_plan = Column(String)
    monto_usd = Column(Float)
    monto_bs = Column(Float)
    referencia_pago = Column(String, index=True) # 👈 Indexado para validación de idempotencia y webhooks de pago
    estado_pago = Column(String, default="verificando", index=True) # 👈 Indexado para reportes de pagos pendientes
    fecha_pago = Column(DateTime, default=datetime.datetime.now, index=True)

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
    profesional_id = Column(Integer, ForeignKey("profesionales.id"), index=True)
    whatsapp_clienta = Column(String, index=True)
    nombre_clienta = Column(String)
    diagnostico_notas = Column(Text)
    fecha_actualizacion = Column(DateTime, default=datetime.datetime.now)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"), index=True)
    name = Column(String, index=True)
    stock = Column(Integer, default=0)
    price = Column(Float, default=0.0)

class ServiceInventory(Base):
    __tablename__ = "service_inventory"
    id = Column(Integer, primary_key=True, index=True)
    servicio_id = Column(Integer, ForeignKey("servicios.id"), index=True)
    product_id = Column(Integer, ForeignKey("products.id"), index=True)
    quantity_used = Column(Integer, default=1)

# --- NUEVOS MODELOS DE INVENTARIO Y STOCK (PLAN ELITE) ---
class ProductoInsumo(Base):
    __tablename__ = "productos_insumos"
    
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"), nullable=False, index=True)
    nombre = Column(String(100), nullable=False, index=True)
    stock_actual = Column(Float, default=0.0)
    stock_minimo = Column(Float, default=5.0)  # Alerta de stock bajo
    precio_venta = Column(Float, default=0.0)  # Para venta al detal directa
    costo = Column(Float, default=0.0)         # Costo de adquisición

class ServicioInsumoRelacion(Base):
    __tablename__ = "servicio_insumos"
    
    id = Column(Integer, primary_key=True, index=True)
    servicio_id = Column(Integer, ForeignKey("servicios.id"), nullable=False, index=True)
    producto_id = Column(Integer, ForeignKey("productos_insumos.id"), nullable=False, index=True)
    cantidad_consumida = Column(Float, nullable=False) # Ej: 15ml o 1 par

class VentaProducto(Base):
    __tablename__ = "ventas_productos"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"), index=True)
    producto_id = Column(Integer, ForeignKey("productos_insumos.id"), index=True)
    cantidad = Column(Float, default=1.0)
    total_usd = Column(Float, default=0.0)
    fecha_venta = Column(DateTime, default=datetime.datetime.now, index=True)

# --- MODELO DE REGISTRO CONTABLE DE VENTA DIRECTA (HÍBRIDO) ---
class VentaDirectaRegistro(Base):
    __tablename__ = "ventas_directas_registros"
    
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"), nullable=False, index=True)
    producto_id = Column(Integer, nullable=False, index=True)
    nombre_producto = Column(String(100), nullable=False)
    cantidad = Column(Float, nullable=False)
    precio_unitario = Column(Float, nullable=False)
    total_usd = Column(Float, nullable=False)
    fecha_venta = Column(DateTime, default=datetime.datetime.now, index=True)

class LoyaltyAccount(Base):
    __tablename__ = "loyalty_accounts"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"), index=True)
    client_whatsapp = Column(String, index=True)
    points = Column(Integer, default=0)
    cashback_balance = Column(Float, default=0.0)

class CodigoRecuperacion(Base):
    __tablename__ = "codigos_recuperacion"
    id = Column(Integer, primary_key=True, index=True)
    correo = Column(String, index=True)
    codigo = Column(String(6))
    expira_at = Column(DateTime, index=True)

class GastoLogistica(Base):
    __tablename__ = "gastos_logistica"
    id = Column(Integer, primary_key=True, index=True)
    profesional_id = Column(Integer, ForeignKey("profesionales.id"), index=True)
    monto_usd = Column(Float, default=0.0)
    detalle = Column(String(255), nullable=False)
    categoria = Column(String(50), default="Insumos", index=True) # Insumos, Logística, Alquiler, etc.
    fecha_gasto = Column(DateTime, default=datetime.datetime.now, index=True)

# --- NUEVO MODELO DE CIERRES DE CAJA DIARIOS POR ESPECIALISTA ---
class CierreCajaDiario(Base):
    __tablename__ = "cierres_caja_diarios"
    
    id = Column(Integer, primary_key=True, index=True)
    fecha = Column(String, index=True)  # Formato "YYYY-MM-DD"
    profesional_id = Column(Integer, ForeignKey("profesionales.id"), nullable=False, index=True)
    personal_id = Column(Integer, ForeignKey("personal.id"), nullable=False, index=True)
    citas_completadas_count = Column(Integer, default=0)
    total_bruto = Column(Float, default=0.0)
    total_iva = Column(Float, default=0.0)
    comision_especialista = Column(Float, default=0.0)
    ganancia_salon = Column(Float, default=0.0)
    tasa_bcv_cierre = Column(Float, default=0.0)
    monto_bs_total = Column(Float, default=0.0)
    fecha_registro = Column(DateTime, default=datetime.datetime.now, index=True)