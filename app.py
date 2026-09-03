import os
from datetime import datetime, time, date, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
import pytz
import openpyxl
from io import BytesIO
from flask import send_file

app = Flask(__name__)
app.secret_key = 'clave_secreta_super_segura_multiempresa'

# Configuración de la Base de Datos SQLite (Soporte multi-empresa integrado)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'asistencia.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Zona horaria de Colombia
TZ_COLOMBIA = pytz.timezone('America/Bogota')

def obtener_ahora_colombia():
    return datetime.now(TZ_COLOMBIA)

# ==========================================
# MODELOS DE BASE DE DATOS (CON MULTI-TENANCY)
# ==========================================

class Company(db.Model):
    __tablename__ = 'companies'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    nit = db.Column(db.String(50), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)

class Employee(db.Model):
    __tablename__ = 'employees'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False, default=1)
    cedula = db.Column(db.String(50), unique=True, nullable=False)
    nombre = db.Column(db.String(150), nullable=False)
    area = db.Column(db.String(100), nullable=False)
    device_uuid = db.Column(db.String(255), nullable=True) # Control de seguridad por dispositivo

class Attendance(db.Model):
    __tablename__ = 'attendances'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False, default=1)
    cedula = db.Column(db.String(50), nullable=False)
    nombre = db.Column(db.String(150), nullable=False)
    fecha = db.Column(db.String(20), nullable=False)
    hora_entrada = db.Column(db.String(20), nullable=True)
    lat_entrada = db.Column(db.Float, nullable=True)
    lon_entrada = db.Column(db.Float, nullable=True)
    ubicacion_valida_entrada = db.Column(db.Boolean, default=True)
    nota_entrada = db.Column(db.String(255), nullable=True)
    
    hora_salida = db.Column(db.String(20), nullable=True)
    lat_salida = db.Column(db.Float, nullable=True)
    lon_salida = db.Column(db.Float, nullable=True)
    ubicacion_valida_salida = db.Column(db.Boolean, default=True)
    nota_salida = db.Column(db.String(255), nullable=True)
    
    horas_ordinarias = db.Column(db.Float, default=0.0)
    horas_extras_diurnas = db.Column(db.Float, default=0.0)
    horas_extras_nocturnas = db.Column(db.Float, default=0.0)
    horas_extras_festivas = db.Column(db.Float, default=0.0)

class ScheduleConfig(db.Model):
    __tablename__ = 'schedule_configs'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False, default=1)
    area = db.Column(db.String(100), unique=True, nullable=False)
    hora_entrada = db.Column(db.String(10), nullable=False, default="08:00")
    hora_salida = db.Column(db.String(10), nullable=False, default="18:00")
    tolerancia_minutos = db.Column(db.Integer, nullable=False, default=15)

# Coordenadas de la empresa principal y radio permitido en metros
LAT_EMPRESA = 6.2442
LON_EMPRESA = -75.5812
RADIO_MAXIMO_METROS = 100.0

def calcular_distancia_metros(lat1, lon1, lat2, lon2):
    import math
    R = 6371000  # Radio de la tierra en metros
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

with app.app_context():
    db.create_all()
    # Crear empresa por defecto si la base de datos está vacía para asegurar retrocompatibilidad total
    if not Company.query.first():
        empresa_defecto = Company(id=1, name="Empresa Principal Demo", nit="900000000-1", is_active=True)
        db.session.add(empresa_defecto)
        db.session.commit()

# ==========================================
# RUTAS DEL SISTEMA
# ==========================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin')
def admin():
    company_id = 1 # Por defecto empresa principal
    empleados = Employee.query.filter_by(company_id=company_id).all()
    configuraciones = ScheduleConfig.query.filter_by(company_id=company_id).all()
    empresas = Company.query.all()
    return render_template('admin.html', empleados=empleados, configuraciones=configuraciones, empresas=empresas)

@app.route('/api/marcar', methods=['POST'])
def marcar_asistencia():
    data = request.json
    cedula = data.get('cedula')
    lat = data.get('lat')
    lon = data.get('lon')
    device_uuid = data.get('device_uuid')
    company_id = data.get('company_id', 1) # Soporte multiempresa

    if not cedula:
        return jsonify({'success': False, 'message': 'Debe ingresar la cédula.'}), 400

    empleado = Employee.query.filter_by(cedula=cedula, company_id=company_id).first()
    if not empleado:
        return jsonify({'success': False, 'message': 'Empleado no encontrado en esta empresa.'}), 404

    # Validación de seguridad por dispositivo (Device UUID)
    if empleado.device_uuid and empleado.device_uuid != device_uuid:
        return jsonify({
            'success': False, 
            'message': 'Dispositivo no autorizado. Este usuario ya se encuentra vinculado a otro teléfono celular.'
        }), 403
    elif not empleado.device_uuid and device_uuid:
        empleado.device_uuid = device_uuid
        db.session.commit()

    # Validación de geolocalización
    ubicacion_valida = True
    nota_ubicacion = None
    if lat is not None and lon is not None:
        distancia = calcular_distancia_metros(lat, lon, LAT_EMPRESA, LON_EMPRESA)
        if distancia > RADIO_MAXIMO_METROS:
            ubicacion_valida = False
            nota_ubicacion = f"Fuera de rango permitído ({round(distancia, 2)} metros de la sede)."
    else:
        ubicacion_valida = False
        nota_ubicacion = "No se proveyeron coordenadas de GPS."

    ahora = obtener_ahora_colombia()
    fecha_hoy = ahora.strftime('%Y-%m-%d')
    hora_actual_str = ahora.strftime('%H:%M:%S')

    asistencia = Attendance.query.filter_by(company_id=company_id, cedula=cedula, fecha=fecha_hoy).first()

    if not asistencia:
        # Registro de ENTRADA
        asistencia = Attendance(
            company_id=company_id,
            cedula=cedula,
            nombre=empleado.nombre,
            fecha=fecha_hoy,
            hora_entrada=hora_actual_str,
            lat_entrada=lat,
            lon_entrada=lon,
            ubicacion_valida_entrada=ubicacion_valida,
            nota_entrada=nota_ubicacion
        )
        db.session.add(asistencia)
        db.session.commit()
        return jsonify({
            'success': True,
            'tipo': 'entrada',
            'message': f'¡Entrada registrada con éxito para {empleado.nombre}!',
            'hora': hora_actual_str
        })
    else:
        if asistencia.hora_salida:
            return jsonify({'success': False, 'message': 'Ya se ha registrado la entrada y salida para el día de hoy.'}), 400
        
        # Registro de SALIDA y cálculo de horas
        asistencia.hora_salida = hora_actual_str
        asistencia.lat_salida = lat
        asistencia.lon_salida = lon
        asistencia.ubicacion_valida_salida = ubicacion_valida
        asistencia.nota_salida = nota_ubicacion

        # Motor de cálculo de horas ordinarias y extras
        try:
            FMT = '%H:%M:%S'
            t_entrada = datetime.strptime(asistencia.hora_entrada, FMT)
            t_salida = datetime.strptime(asistencia.hora_salida, FMT)
            diferencia_horas = (t_salida - t_entrada).total_seconds() / 3600.0
            
            if diferencia_horas < 0:
                diferencia_horas = 0

            # Obtener configuración de horario para el área
            cfg = ScheduleConfig.query.filter_by(company_id=company_id, area=empleado.area).first()
            horas_jornada = 8.0 # Estándar por defecto
            if cfg:
                h_ent = datetime.strptime(cfg.hora_entrada, '%H:%M')
                h_sal = datetime.strptime(cfg.hora_salida, '%H:%M')
                horas_jornada = max(1.0, (h_sal - h_ent).total_seconds() / 3600.0)

            if diferencia_horas <= horas_jornada:
                asistencia.horas_ordinarias = round(diferencia_horas, 2)
                asistencia.horas_extras_diurnas = 0.0
            else:
                asistencia.horas_ordinarias = round(horas_jornada, 2)
                extras = diferencia_horas - horas_jornada
                # Asignación simplificada de extras diurnas base
                asistencia.horas_extras_diurnas = round(extras, 2)
        except Exception as e:
            print("Error calculando horas:", e)

        db.session.commit()
        return jsonify({
            'success': True,
            'tipo': 'salida',
            'message': f'¡Salida registrada con éxito para {empleado.nombre}!',
            'hora': hora_actual_str,
            'ordinarias': asistencia.horas_ordinarias,
            'extras_diurnas': asistencia.horas_extras_diurnas
        })

@app.route('/api/empleados', methods=['POST'])
def gestionar_empleados():
    data = request.json
    accion = data.get('accion')
    company_id = data.get('company_id', 1)

    if accion == 'crear':
        cedula = data.get('cedula')
        nombre = data.get('nombre')
        area = data.get('area')
        
        if not cedula or not nombre or not area:
            return jsonify({'success': False, 'message': 'Todos los campos son obligatorios.'}), 400
            
        existe = Employee.query.filter_by(cedula=cedula).first()
        if existe:
            return jsonify({'success': False, 'message': 'Ya existe un empleado con esta cédula.'}), 400
            
        nuevo = Employee(company_id=company_id, cedula=cedula, nombre=nombre, area=area)
        db.session.add(nuevo)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Empleado creado correctamente.'})

    elif accion == 'eliminar':
        cedula = data.get('cedula')
        emp = Employee.query.filter_by(cedula=cedula, company_id=company_id).first()
        if emp:
            db.session.delete(emp)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Empleado eliminado correctamente.'})
        return jsonify({'success': False, 'message': 'Empleado no encontrado.'}), 404

    return jsonify({'success': False, 'message': 'Acción no válida.'}), 400

@app.route('/api/horarios', methods=['POST'])
def guardar_horario():
    data = request.json
    company_id = data.get('company_id', 1)
    area = data.get('area')
    hora_entrada = data.get('hora_entrada')
    hora_salida = data.get('hora_salida')
    tolerancia = data.get('tolerancia_minutos', 15)

    if not area:
        return jsonify({'success': False, 'message': 'El área es obligatoria.'}), 400

    cfg = ScheduleConfig.query.filter_by(company_id=company_id, area=area).first()
    if not cfg:
        cfg = ScheduleConfig(company_id=company_id, area=area)
        db.session.add(cfg)

    cfg.hora_entrada = hora_entrada
    cfg.hora_salida = hora_salida
    cfg.tolerancia_minutos = int(tolerancia)
    db.session.commit()

    return jsonify({'success': True, 'message': 'Configuración de horario guardada con éxito.'})

@app.route('/api/reporte/excel', methods=['GET'])
def descargar_reporte_excel():
    company_id = request.args.get('company_id', 1, type=int)
    registros = Attendance.query.filter_by(company_id=company_id).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Reporte Asistencia"

    # Encabezados
    headers = [
        "Cédula", "Nombre", "Fecha", "Hora Entrada", "Ubicación Válida Entrada", 
        "Nota Entrada", "Hora Salida", "Ubicación Válida Salida", "Nota Salida", 
        "H. Ordinarias", "H. Extras Diurnas", "H. Extras Nocturnas", "H. Extras Festivas"
    ]
    ws.append(headers)

    for r in registros:
        # Conversión de la cédula a entero (int) para solucionar el formato de texto en Excel
        try:
            cedula_val = int(r.cedula)
        except ValueError:
            cedula_val = r.cedula

        ws.append([
            cedula_val, r.nombre, r.fecha, r.hora_entrada or "", 
            "Sí" if r.ubicacion_valida_entrada else "No", r.nota_entrada or "",
            r.hora_salida or "", "Sí" if r.ubicacion_valida_salida else "No", r.nota_salida or "",
            r.horas_ordinarias, r.horas_extras_diurnas, r.horas_extras_nocturnas, r.horas_extras_festivas
        ])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'Reporte_Asistencia_Empresa_{company_id}.xlsx'
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
