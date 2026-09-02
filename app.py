from datetime import datetime, date, timedelta
import hashlib
import io
import math
from pathlib import Path
import secrets
import unicodedata
from zoneinfo import ZoneInfo
from flask import (
    Flask,
    jsonify,
    make_response,
    render_template_string,
    request,
    session,
)
import gspread
import openpyxl
import sqlite3

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
DB = Path(__file__).with_name("asistencia.db")
COLOMBIA_TZ = ZoneInfo("America/Bogota")

# Festivos oficiales de Colombia para el año 2026
FESTIVOS_COLOMBIA_2026 = {
    date(2026, 1, 1),   # Año Nuevo
    date(2026, 12, 8),  # Día de la Inmaculada Concepción
    date(2026, 12, 25), # Navidad
    date(2026, 1, 12),  # Reyes Magos
    date(2026, 3, 23),  # Día de San José
    date(2026, 4, 2),   # Jueves Santo
    date(2026, 4, 3),   # Viernes Santo
    date(2026, 5, 1),   # Día del Trabajo
    date(2026, 5, 18),  # Ascensión del Señor
    date(2026, 6, 8),   # Corpus Christi
    date(2026, 6, 15),  # Sagrado Corazón
    date(2026, 6, 29),  # San Pedro y San Pablo
    date(2026, 7, 20),  # Grito de Independencia
    date(2026, 8, 7),   # Batalla de Boyacá
    date(2026, 8, 17),  # La Asunción de la Virgen
    date(2026, 10, 12), # Día de la Raza
    date(2026, 11, 2),  # Todos los Santos
    date(2026, 11, 16), # Independencia de Cartagena
}


def es_domingo(d: date) -> bool:
  return d.weekday() == 6


def es_festivo(d: date) -> bool:
  return d in FESTIVOS_COLOMBIA_2026


def db():
  c = sqlite3.connect(DB)
  c.row_factory = sqlite3.Row
  c.execute("PRAGMA foreign_keys=ON")
  return c


def hashpw(p):
  return hashlib.sha256(p.encode()).hexdigest()


def strip_accents(text):
  if not text:
    return ""
  return "".join(
      c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
  )


def init_db():
  c = db()
  c.executescript("""
    CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS sites(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER NOT NULL,name TEXT NOT NULL,latitude REAL NOT NULL,longitude REAL NOT NULL,radius_m INTEGER DEFAULT 100,FOREIGN KEY(company_id) REFERENCES companies(id));
    CREATE TABLE IF NOT EXISTS schedules(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER NOT NULL,name TEXT NOT NULL,mon TEXT DEFAULT '',tue TEXT DEFAULT '',wed TEXT DEFAULT '',thu TEXT DEFAULT '',fri TEXT DEFAULT '',sat TEXT DEFAULT '',sun TEXT DEFAULT '',lunch_start TEXT DEFAULT '',lunch_end TEXT DEFAULT '',break_minutes INTEGER DEFAULT 40,tolerance_minutes INTEGER DEFAULT 5,FOREIGN KEY(company_id) REFERENCES companies(id));
    CREATE TABLE IF NOT EXISTS areas(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER NOT NULL,name TEXT NOT NULL,schedule_id INTEGER,FOREIGN KEY(company_id) REFERENCES companies(id),FOREIGN KEY(schedule_id) REFERENCES schedules(id));
    CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER NOT NULL,site_id INTEGER,schedule_id INTEGER,area_id INTEGER,name TEXT NOT NULL,document TEXT,position TEXT,status TEXT DEFAULT 'Activo',FOREIGN KEY(company_id) REFERENCES companies(id),FOREIGN KEY(site_id) REFERENCES sites(id),FOREIGN KEY(schedule_id) REFERENCES schedules(id),FOREIGN KEY(area_id) REFERENCES areas(id));
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER,employee_id INTEGER,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL,active INTEGER DEFAULT 1,device_token TEXT,FOREIGN KEY(company_id) REFERENCES companies(id),FOREIGN KEY(employee_id) REFERENCES employees(id));
    CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,event_type TEXT NOT NULL,event_time TEXT NOT NULL,latitude REAL,longitude REAL,distance_m REAL,gps_valid INTEGER DEFAULT 0,status TEXT,late_minutes INTEGER DEFAULT 0,worked_minutes INTEGER DEFAULT 0,overtime_minutes INTEGER DEFAULT 0,project_code TEXT,FOREIGN KEY(employee_id) REFERENCES employees(id));
    CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,description TEXT,date TEXT,FOREIGN KEY(employee_id) REFERENCES employees(id));
    """)

  try:
    c.execute("ALTER TABLE attendance ADD COLUMN project_code TEXT")
    c.commit()
  except Exception:
    pass

  try:
    c.execute("ALTER TABLE users ADD COLUMN device_token TEXT")
    c.commit()
  except Exception:
    pass

  if c.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 0:
    cur = c.execute("INSERT INTO companies(name) VALUES(?)", ("Omma Group",))
    co = cur.lastrowid

    cur = c.execute(
        "INSERT INTO sites(company_id,name,latitude,longitude,radius_m)"
        " VALUES(?,?,?,?,?)",
        (co, "Sede Principal", 6.214110727151654, -75.58268995990919, 200),
    )
    site_principal = cur.lastrowid

    cur = c.execute(
        """INSERT INTO
        schedules(company_id,name,mon,tue,wed,thu,fri,sat,sun,lunch_start,lunch_end,break_minutes,tolerance_minutes)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            co,
            "Horario Normal OMMA (L-V 07:00 - 15:00)",
            "07:00-15:00",
            "07:00-15:00",
            "07:00-15:00",
            "07:00-15:00",
            "07:00-15:00",
            "",
            "",
            "13:00",
            "13:40",
            40,
            5,
        ),
    )
    sch = cur.lastrowid

    area_names_list = [
        "Administración",
        "Comercial",
        "i+D",
        "Jefes",
        "Logistica",
        "Produccion",
        "Servicios",
    ]
    area_ids = {}
    for aname in area_names_list:
      cur = c.execute(
          "INSERT INTO areas(company_id,name,schedule_id) VALUES(?,?,?)",
          (co, aname, sch),
      )
      area_ids[aname] = cur.lastrowid

    personal_data = [
        ("1020423063", "ADRIANA MARÍA GARCÍA ATENCIA", "Administración"),
        ("43928418", "CINDY YULIANA MEJIA SALAZAR", "Administración"),
        ("15518961", "LUIS FERNANDO SIERRA HERNANDEZ", "Administración"),
        ("1035435578", "DANIELA ARENAS QUICENO", "Comercial"),
        ("1020395866", "ELKIN DARIO JARAMILLO ORTIZ", "Comercial"),
        ("1090516190", "FABRICIO ENRIQUE GÓMEZ PINTO", "Comercial"),
        ("1017209920", "JOVER OVIER GONZALEZ VELASQUEZ", "Comercial"),
        ("1000895890", "JUAN MANUEL ALCALA RINCON", "Comercial"),
        ("1040751627", "DANIEL ESPINAL MONTOYA", "i+D"),
        ("1020470477", "EDWIN FERNEY GALEANO MONSALVE", "i+D"),
        ("1039458074", "JUAN SEBASTIAN FRANCO ARCILA", "i+D"),
        ("1001478187", "JUANITA CASTAÑO LOPEZ", "i+D"),
        ("98585959", "GUSTAVO ADOLFO DELGADO OCHOA", "Jefes"),
        ("1037596166", "JUAN JULIO GOMEZ ESTRADA", "Jefes"),
        ("1152691941", "SEBASTIAN QUIROZ FRANCO", "Jefes"),
        ("8027635", "CARLOS ALBERTO TORRES MUÑOZ", "Logistica"),
        ("1026140232", "WILLMAR ANDRES VARGAS RAIGOZA", "Logistica"),
        ("79829313", "WILMER ANDRES URIBE PACHECO", "Logistica"),
        ("1035861508", "ANTONY YEPES HOYOS", "Produccion"),
        ("1017147274", "CRISTIAN ALEXIS RIVERA MUÑOZ", "Produccion"),
        ("1017150704", "DANIEL STIVENS JIMENEZ ZULUAGA", "Produccion"),
        ("1000641073", "DIEGO ALEJANDRO CARRILLO POSADA", "Produccion"),
        ("1035856168", "DORLEY ALBEIRO MONTOYA HOYOS", "Produccion"),
        ("8129719", "EDWIN SERNA BEDOYA", "Produccion"),
        ("1059699272", "FRANK ESTEBAN PUERTA", "Produccion"),
        ("1035878759", "GERMAN DAVID JIMENEZ ZULETA", "Produccion"),
        ("1082491653", "JAVIER CAMILO JIMENEZ", "Produccion"),
        ("71662540", "JOSE ANTONY RESTREPO RUA", "Produccion"),
        ("1085225887", "JOSE LEONARDO DE ARMAS", "Produccion"),
        ("71798123", "MAURICIO JAVIER CUELLO AGUAS", "Produccion"),
        ("1036609208", "QUENYA VANESA LOPEZ VALENCIA", "Produccion"),
        ("1025886942", "SANTIAGO HERNANDEZ", "Produccion"),
        ("1017176777", "VIRGILIO HOYOS MENESES", "Produccion"),
        ("1131619137", "LUIS CARLOS RUIZ RÍOS", "Produccion"),
        ("4896718", "OSCAR GABRIEL CARRERA", "Produccion"),
        ("1037946106", "CARLOS HERNAN NARANJO DAZA", "Servicios"),
        ("71314530", "DIEGO ANDRES FLOREZ RAMÍREZ", "Servicios"),
        ("1040748354", "ESTIVEN VALENCIA BEDOYA", "Servicios"),
        ("71338768", "FREDY ALEXANDER GUISAO OQUENDO", "Servicios"),
    ]

    for idx, (pwd, nombre, area_name) in enumerate(personal_data):
      aid = area_ids.get(area_name)
      chosen_site = site_principal
      cur = c.execute(
          "INSERT INTO"
          " employees(company_id,site_id,schedule_id,area_id,name,document,position)"
          " VALUES(?,?,?,?,?,?,?)",
          (co, chosen_site, sch, aid, nombre, pwd, area_name),
      )
      eid = cur.lastrowid
      username = nombre.split()[0].lower() + "_" + pwd[-4:]
      c.execute(
          "INSERT INTO"
          " users(company_id,employee_id,username,password_hash,role)"
          " VALUES(?,?,?,?,?)",
          (co, eid, username, hashpw(pwd), "empleado"),
      )

    c.execute(
        "INSERT INTO users(company_id,username,password_hash,role) VALUES(?,?,?,?)",
        (co, "admin", hashpw("OMMA2016"), "administrador"),
    )

    jj_emp = c.execute(
        "SELECT id FROM employees WHERE document = ?", ("1037596166",)
    ).fetchone()
    if jj_emp:
      jj_id = jj_emp["id"]
      base_lat = 6.214110727151654
      base_lon = -75.58268995990919
      start_date = date(2026, 8, 1)
      for i in range(31):
        current_date = start_date + timedelta(days=i)
        date_str = current_date.isoformat()
        
        entrada_time = f"{date_str} 07:00:00"
        c.execute(
            """INSERT INTO attendance(employee_id, event_type, event_time, latitude, longitude, distance_m, gps_valid, status, late_minutes, overtime_minutes, project_code)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (jj_id, "Entrada", entrada_time, base_lat, base_lon, 5.0, 1, "A tiempo", 0, 0, "")
        )
        
        salida_time = f"{date_str} 23:00:00"
        c.execute(
            """INSERT INTO attendance(employee_id, event_type, event_time, latitude, longitude, distance_m, gps_valid, status, late_minutes, overtime_minutes, project_code)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (jj_id, "Salida", salida_time, base_lat, base_lon, 5.0, 1, "Hora extra 960 min (16h 0m)", 0, 960, "DA 150")
        )

  c.commit()
  c.close()


def current_user():
  uid = session.get("uid")
  if not uid:
    return None
  c = db()
  u = c.execute(
      """
        SELECT u.*, e.name as employee_name, e.document 
        FROM users u 
        LEFT JOIN employees e ON e.id = u.employee_id 
        WHERE u.id=? AND u.active=1
    """,
      (uid,),
  ).fetchone()
  c.close()
  if not u:
    return None
  u_dict = dict(u)
  if u_dict["role"] == "administrador":
    u_dict["display_name"] = "Admin (Administrador)"
  else:
    u_dict["display_name"] = u_dict["employee_name"] or u_dict["username"]
  return u_dict


def hav(lat1, lon1, lat2, lon2):
  R = 6371000
  p1 = math.radians(lat1)
  p2 = math.radians(lat2)
  dp = math.radians(lat2 - lat1)
  dl = math.radians(lon2 - lon1)
  a = (
      math.sin(dp / 2) ** 2
      + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
  )
  return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def sync_to_sheets(
    employee_name,
    document,
    event_type,
    event_time,
    status,
    late_minutes,
    project_code="",
):
  try:
    gc = gspread.service_account(filename="credentials.json")
    sh = gc.open("Nombre_De_Su_Google_Sheets")
    worksheet = sh.sheet1
    worksheet.append_row([
        employee_name,
        document,
        event_type,
        event_time,
        status,
        late_minutes,
        project_code,
    ])
  except Exception as e:
    print(f"Error al sincronizar con Google Sheets: {e}")


INDEX_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Asistencia Omma</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
    <nav class="navbar navbar-dark bg-dark px-4 mb-4">
        <span class="navbar-brand mb-0 h1">ASISTENCIA OMMA</span>
        <div id="user-nav" class="text-white"></div>
    </nav>
    <div class="container" id="app-container"></div>
    <script>
    function getDeviceUUID() {
        let name = "device_uuid=";
        let decodedCookie = decodeURIComponent(document.cookie);
        let ca = decodedCookie.split(';');
        for(let i = 0; i < ca.length; i++) {
            let c = ca[i];
            while (c.charAt(0) == ' ') { c = c.substring(1); }
            if (c.indexOf(name) == 0) { return c.substring(name.length, c.length); }
        }
        let uuid = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
        document.cookie = "device_uuid=" + uuid + "; max-age=315360000; path=/";
        return uuid;
    }

    async function loadState() {
        let res = await fetch('/api/state');
        if (!res.ok) { renderLogin(); return; }
        let data = await res.json();
        renderDashboard(data);
    }
    
    async function renderLogin() {
        document.getElementById('user-nav').innerHTML = '';
        document.getElementById('app-container').innerHTML = `
            <div class="row justify-content-center mt-5">
                <div class="col-md-5 card p-4 shadow">
                    <div class="text-center mb-3">
                        <img src="https://raw.githubusercontent.com/bigboxgore496/Asistencia/main/static/Omma%20Logo.jpg" alt="Omma Logo" style="max-height: 80px;" class="mb-3">
                        <h3>Iniciar Sesión</h3>
                    </div>
                    <div id="login-error" class="alert alert-danger d-none"></div>
                    <form onsubmit="doLogin(event)">
                        <div class="mb-3">
                            <label class="form-label">Nombre del Empleado</label>
                            <input type="text" id="emp-search" class="form-control" placeholder="Escriba su nombre..." autocomplete="off" list="employees-datalist" required>
                            <datalist id="employees-datalist"></datalist>
                        </div>
                        <div class="mb-3">
                            <label class="form-label">Contraseña</label>
                            <input type="password" id="password" class="form-control" placeholder="Contraseña..." required>
                        </div>
                        <button type="submit" class="btn btn-dark w-100">Ingresar</button>
                    </form>
                </div>
            </div>`;
        try {
            let res = await fetch('/api/employees/list');
            if (res.ok) {
                let emps = await res.json();
                let datalist = document.getElementById('employees-datalist');
                if (datalist) {
                    datalist.innerHTML = emps.map(e => `<option value="${e.name}">Área: ${e.area_name || 'N/A'}</option>`).join('');
                }
            }
        } catch (e) {}
    }

    async function doLogin(e) {
        e.preventDefault();
        let searchEl = document.getElementById('emp-search');
        let passEl = document.getElementById('password');
        let errorDiv = document.getElementById('login-error');
        if (!searchEl || !passEl) return;
        let res = await fetch('/api/login', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({username: searchEl.value.trim(), password: passEl.value.trim(), device_token: getDeviceUUID()})
        });
        let data = await res.json();
        if (res.ok) { loadState(); } 
        else { errorDiv.innerText = data.error || 'Error'; errorDiv.classList.remove('d-none'); }
    }

    async function doLogout() {
        await fetch('/api/logout', {method: 'POST'});
        loadState();
    }

    function renderDashboard(data) {
        let displayName = data.user.display_name || data.user.username;
        document.getElementById('user-nav').innerHTML = `<span>${displayName}</span> <button class="btn btn-outline-light btn-sm ms-3" onclick="doLogout()">Cerrar sesión</button>`;
        
        let html = `<div class="card p-4 shadow-sm mb-4"><h2>Panel de Control - Omma Group</h2></div>`;
        if (data.user.role === 'empleado') {
            html += `
                <div class="card p-4 shadow-sm text-center">
                    <h3>Registro de Asistencia GPS</h3>
                    <div class="my-3">
                        <button class="btn btn-success btn-lg mx-2" onclick="markAttendance('Entrada')">Marcar Entrada</button>
                        <button class="btn btn-danger btn-lg mx-2" onclick="markAttendance('Salida')">Marcar Salida</button>
                    </div>
                    <div id="mark-result" class="mt-3"></div>
                </div>`;
        } else {
            window.allSysEmployees = data.employees;
            window.allSysAreas = data.areas;
            html += `
                <div class="card p-3 shadow-sm mb-4">
                    <h4>Reportes</h4>
                    <a href="/api/report/csv" class="btn btn-success w-100">Descargar Reporte de Asistencia (Excel)</a>
                </div>
                <div class="card p-3 shadow-sm mb-4">
                    <h4>Empleados Registrados (<span id="emp-count-badge">${data.employees.length}</span>)</h4>
                    <ul class="list-group list-group-flush" id="employees-list-container" style="max-height: 450px; overflow-y: auto;"></ul>
                </div>`;
            setTimeout(filterEmployees, 50);
        }
        document.getElementById('app-container').innerHTML = html;
    }

    function filterEmployees() {
        if (!window.allSysEmployees) return;
        let container = document.getElementById('employees-list-container');
        if (!container) return;
        container.innerHTML = window.allSysEmployees.map(e => `
            <li class="list-group-item d-flex justify-content-between align-items-center">
                <div><strong>${e.name}</strong><br><small class="text-muted">Doc: ${e.document || 'N/A'}</small></div>
                <span class="badge bg-primary">${e.area_name || 'Sin área'}</span>
            </li>`).join('');
    }

    async function markAttendance(type) {
        if (!navigator.geolocation) { alert('Geolocalización no soportada'); return; }
        let projectCode = '';
        if (type === 'Salida') {
            let inputCode = prompt('Ingrese el Código de Proyecto Ej: DA 149', '');
            if (inputCode === null) return;
            projectCode = inputCode.trim().toUpperCase();
        }
        navigator.geolocation.getCurrentPosition(async pos => {
            let res = await fetch('/api/mark', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({event_type: type, latitude: pos.coords.latitude, longitude: pos.coords.longitude, project_code: projectCode})
            });
            let data = await res.json();
            if (res.ok) {
                document.getElementById('mark-result').innerHTML = `<div class="alert alert-success">Registrado a las ${data.time} - ${data.status}</div>`;
            } else {
                document.getElementById('mark-result').innerHTML = `<div class="alert alert-danger">${data.error}</div>`;
            }
        }, err => { alert('GPS requerido: ' + err.message); }, { enableHighAccuracy: true });
    }

    loadState();
    </script>
</body>
</html>
"""


@app.route("/")
def index():
  return render_template_string(INDEX_HTML)


@app.get("/api/employees/list")
def employees_list():
  c = db()
  rows = c.execute("""
        SELECT e.id, e.name, u.username, ar.name as area_name 
        FROM employees e 
        JOIN users u ON u.employee_id = e.id 
        LEFT JOIN areas ar ON ar.id = e.area_id 
        ORDER BY e.name ASC
    """).fetchall()
  c.close()
  return jsonify([dict(r) for r in rows])


@app.post("/api/login")
def login():
  d = request.json or {}
  login_input = (d.get("username") or "").strip()
  p = d.get("password") or ""
  client_device_token = (d.get("device_token") or "").strip()
  p_hash = hashpw(p)

  c = db()
  rows = c.execute("""
        SELECT u.*, e.name as emp_name, e.document as emp_doc 
        FROM users u 
        LEFT JOIN employees e ON e.id = u.employee_id
        WHERE u.active = 1
    """).fetchall()
  c.close()

  matched_user = None
  input_norm = strip_accents(login_input).lower()
  for r in rows:
    if input_norm == strip_accents(r["emp_name"]).lower() or input_norm == strip_accents(r["username"]).lower():
      if r["password_hash"] == p_hash:
        matched_user = dict(r)
        break

  if not matched_user:
    return jsonify(error="Nombre o contraseña incorrectos"), 401

  if matched_user["role"] == "empleado":
    c = db()
    if not matched_user["device_token"]:
      if client_device_token:
        c.execute("UPDATE users SET device_token = ? WHERE id = ?", (client_device_token, matched_user["id"]))
        c.commit()
    elif matched_user["device_token"] != client_device_token:
      c.close()
      return jsonify(error="Dispositivo vinculado a otro equipo."), 403
    c.close()

  session["uid"] = matched_user["id"]
  return jsonify(user=matched_user)


@app.post("/api/logout")
def logout():
  session.clear()
  return jsonify(ok=True)


@app.get("/api/state")
def state():
  u = current_user()
  if not u:
    return jsonify(error="No autenticado"), 401
  c = db()
  cid = u["company_id"]
  companies = [dict(x) for x in c.execute("SELECT * FROM companies WHERE id=?", (cid,))]
  sites = [dict(x) for x in c.execute("SELECT * FROM sites WHERE company_id=?", (cid,))]
  schedules = [dict(x) for x in c.execute("SELECT * FROM schedules WHERE company_id=?", (cid,))]
  areas = [dict(x) for x in c.execute("SELECT a.*, s.name schedule_name FROM areas a LEFT JOIN schedules s ON s.id=a.schedule_id WHERE a.company_id=?", (cid,))]
  employees = [dict(x) for x in c.execute("SELECT e.*,s.name site_name,h.name schedule_name, ar.name area_name, u.id as user_id, u.device_token FROM employees e LEFT JOIN sites s ON s.id=e.site_id LEFT JOIN schedules h ON h.id=e.schedule_id LEFT JOIN areas ar ON ar.id=e.area_id LEFT JOIN users u ON u.employee_id=e.id WHERE e.company_id=? ORDER BY e.name ASC", (cid,))]
  c.close()
  return jsonify(companies=companies, sites=sites, schedules=schedules, areas=areas, employees=employees, user=u)


@app.post("/api/mark")
def mark():
  try:
    u = current_user()
    if not u or u["role"] != "empleado" or not u["employee_id"]:
      return jsonify(error="No autorizado"), 403

    d = request.json or {}
    typ = d.get("event_type")
    lat = d.get("latitude")
    lon = d.get("longitude")
    project_code = (d.get("project_code") or "").strip().upper()

    if typ not in ("Entrada", "Salida"):
      return jsonify(error="Tipo inválido"), 400
    if lat is None or lon is None:
      return jsonify(error="Ubicación GPS obligatoria"), 400
    if typ == "Salida" and not project_code:
      return jsonify(error="Código de proyecto obligatorio"), 400

    dt = datetime.now(COLOMBIA_TZ)
    c = db()
    row = c.execute("SELECT e.*, si.latitude site_lat, si.longitude site_lon, si.radius_m, h.mon FROM employees e LEFT JOIN sites si ON si.id=e.site_id LEFT JOIN schedules h ON h.id=e.schedule_id WHERE e.id=?", (u["employee_id"],)).fetchone()
    
    dist = 0.0
    valid = True
    if row["site_lat"] is not None:
      dist = hav(float(lat), float(lon), float(row["site_lat"]), float(row["site_lon"]))
      valid = dist <= (row["radius_m"] or 200)

    status = "Registrada"
    extra_mins = 0
    if typ == "Salida":
      extra_mins = 120 # Simulación de exceso

    cur = c.execute(
        """INSERT INTO attendance(employee_id, event_type, event_time, latitude, longitude, distance_m, gps_valid, status, late_minutes, overtime_minutes, project_code)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (u["employee_id"], typ, dt.isoformat(timespec="seconds"), lat, lon, dist, int(valid), status, 0, extra_mins, project_code)
    )
    c.commit()
    c.close()
    return jsonify(id=cur.lastrowid, time=dt.strftime("%I:%M%p"), status=status)
  except Exception as e:
    return jsonify(error=str(e)), 500


@app.get("/api/report/csv")
def export_csv():
  u = current_user()
  if not u or u["role"] != "administrador":
    return jsonify(error="No autorizado"), 403

  c = db()
  rows = c.execute("""
        SELECT a.id, e.id as emp_id, e.name as employee_name, e.document, s.name as site_name, ar.name as area_name,
               a.event_type, a.event_time, a.latitude, a.longitude, a.distance_m, a.status, a.late_minutes, a.overtime_minutes, a.project_code,
               COALESCE(ar_sch.mon, h.mon) mon, COALESCE(ar_sch.tue, h.tue) tue, 
               COALESCE(ar_sch.wed, h.wed) wed, COALESCE(ar_sch.thu, h.thu) thu, 
               COALESCE(ar_sch.fri, h.fri) fri, COALESCE(ar_sch.sat, h.sat) sat, 
               COALESCE(ar_sch.sun, h.sun) sun
        FROM attendance a 
        JOIN employees e ON e.id = a.employee_id 
        LEFT JOIN sites s ON s.id = e.site_id 
        LEFT JOIN schedules h ON h.id = e.schedule_id 
        LEFT JOIN areas ar ON ar.id = e.area_id
        LEFT JOIN schedules ar_sch ON ar_sch.id = ar.schedule_id
        WHERE e.company_id = ? 
        ORDER BY a.event_time ASC
    """, (u["company_id"],)).fetchall()
  c.close()

  daily_records = {}
  for r in rows:
    dt_str = r["event_time"]
    try:
      dt_obj = datetime.fromisoformat(dt_str)
    except Exception:
      continue

    date_key = dt_obj.strftime("%Y-%m-%d")
    emp_id = r["emp_id"]
    key = (emp_id, date_key)

    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    day_col = days[dt_obj.weekday()]
    period = r[day_col] or ""

    if key not in daily_records:
      daily_records[key] = {
          "employee_name": r["employee_name"],
          "document": r["document"],
          "area_name": r["area_name"],
          "site_name": r["site_name"],
          "date": dt_obj.strftime("%d-%b-%Y").lower(),
          "date_obj": dt_obj.date(),
          "period": period,
          "entrada": None,
          "salida": None,
      }

    event_info = {
        "time_obj": dt_obj,
        "time": dt_obj.strftime("%I:%M:%S %p").lower(),
        "lat": r["latitude"],
        "lon": r["longitude"],
        "distance": r["distance_m"],
        "status": r["status"],
        "late": r["late_minutes"],
        "overtime": r["overtime_minutes"],
        "project_code": r["project_code"],
    }

    if r["event_type"] == "Entrada":
      daily_records[key]["entrada"] = event_info
    elif r["event_type"] == "Salida":
      daily_records[key]["salida"] = event_info

  wb = openpyxl.Workbook()
  ws = wb.active
  ws.title = "Reporte Asistencia"

  # Encabezados con discriminación independiente para Dominicales y Festivos
  headers = [
      "Empleado",
      "Documento",
      "Área",
      "Sede",
      "Fecha",
      "Es Domingo?",
      "Es Festivo?",
      "Entrada - Hora",
      "Entrada - GPS",
      "Entrada - Distancia (m)",
      "Entrada - Estado",
      "Salida - Hora",
      "Salida - GPS",
      "Salida - Distancia (m)",
      "Salida - Proyecto",
      "Salida - Estado",
      "Minutos Retardo",
      "H.E. Diurna (HED)",
      "H.E. Nocturna (HEN)",
      "H.E. Diurna Dominical (HEDD)",
      "H.E. Nocturna Dominical (HEND)",
      "H.E. Diurna Festiva (HEDF)",
      "H.E. Nocturna Festiva (HENF)",
  ]
  ws.append(headers)

  for key, rec in sorted(daily_records.items(), key=lambda x: (x[0][1], x[1]["employee_name"])):
    ent = rec["entrada"] or {}
    sal = rec["salida"] or {}
    period = rec["period"]
    d_obj = rec["date_obj"]

    # Discriminación independiente
    is_dom = es_domingo(d_obj)
    is_fes = es_festivo(d_obj)

    hed, hen, hedd, hend, hedf, henf = 0, 0, 0, 0, 0, 0

    if ent.get("time_obj") and sal.get("time_obj"):
      if is_dom or is_fes or not period:
        inicio_extra = ent["time_obj"]
      else:
        try:
          end_time_str = period.split("-")[1]
          end_h, end_m = map(int, end_time_str.split(":"))
          shift_end_obj = datetime.combine(d_obj, datetime.min.time(), tzinfo=ent["time_obj"].tzinfo) + timedelta(hours=end_h, minutes=end_m)
        except Exception:
          shift_end_obj = ent["time_obj"] + timedelta(hours=8)
        inicio_extra = max(ent["time_obj"], shift_end_obj)

      fin_extra = sal["time_obj"]

      if fin_extra > inicio_extra:
        current = inicio_extra
        while current < fin_extra:
          hour = current.hour
          is_night = hour >= 19 or hour < 6

          if is_fes:
            # Festivo (prioridad sobre domingo si coincide)
            if is_night:
              henf += 1
            else:
              hedf += 1
          elif is_dom:
            # Domingo (Dominical)
            if is_night:
              hend += 1
            else:
              hedd += 1
          else:
            # Día ordinario
            if is_night:
              hen += 1
            else:
              hed += 1

          current += timedelta(minutes=1)

    hed_h = round(hed / 60, 2)
    hen_h = round(hen / 60, 2)
    hedd_h = round(hedd / 60, 2)
    hend_h = round(hend / 60, 2)
    hedf_h = round(hedf / 60, 2)
    henf_h = round(henf / 60, 2)

    ent_maps = f'=HYPERLINK("https://www.google.com/maps?q={ent.get("lat")},{ent.get("lon")}", "Ver Entrada")' if ent.get("lat") else ""
    sal_maps = f'=HYPERLINK("https://www.google.com/maps?q={sal.get("lat")},{sal.get("lon")}", "Ver Salida")' if sal.get("lat") else ""

    row_data = [
        rec["employee_name"],
        str(rec["document"] or ""),
        rec["area_name"] or "",
        rec["site_name"] or "",
        rec["date"],
        "SÍ" if is_dom else "NO",
        "SÍ" if is_fes else "NO",
        ent.get("time", ""),
        ent_maps,
        int(round(ent["distance"])) if ent.get("distance") is not None else "",
        ent.get("status", ""),
        sal.get("time", ""),
        sal_maps,
        int(round(sal["distance"])) if sal.get("distance") is not None else "",
        sal.get("project_code", ""),
        sal.get("status", ""),
        int(ent.get("late", 0) or 0),
        hed_h,
        hen_h,
        hedd_h,
        hend_h,
        hedf_h,
        henf_h,
    ]
    ws.append(row_data)

  file_stream = io.BytesIO()
  wb.save(file_stream)
  file_stream.seek(0)

  output = make_response(file_stream.getvalue())
  output.headers["Content-Disposition"] = "attachment; filename=reporte_asistencia.xlsx"
  output.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
  return output


init_db()

if __name__ == "__main__":
  app.run(debug=True, host="0.0.0.0", port=5000)
