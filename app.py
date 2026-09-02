from datetime import timedelta

def es_festivo_colombia(fecha):
    fijos = [(1, 1), (5, 1), (7, 20), (8, 7), (12, 8), (12, 25)]
    return (fecha.month, fecha.day) in fijos or fecha.weekday() >= 5

@app.post("/api/admin/simulate-30-days/<int:employee_id>")
def simulate_30_days_safe(employee_id):
    u = current_user()
    if not u or u.get("role") != "administrador":
        return jsonify(error="No autorizado"), 403

    conn = db()
    cursor = conn.cursor()
    dt_base = datetime.now(COLOMBIA_TZ)
    
    try:
        for i in range(30):
            d_sim = dt_base - timedelta(days=i)
            date_str = d_sim.strftime("%Y-%m-%d")
            
            cursor.execute(
                """INSERT INTO attendance(employee_id, event_type, event_time, latitude, longitude, distance_m, gps_valid, status, late_minutes, overtime_minutes, project_code)
                   VALUES(?, 'Entrada', ?, 6.2141, -75.5826, 10.0, 1, 'A tiempo', 0, 0, '')""",
                (employee_id, f"{date_str}T07:00:00")
            )
            cursor.execute(
                """INSERT INTO attendance(employee_id, event_type, event_time, latitude, longitude, distance_m, gps_valid, status, late_minutes, overtime_minutes, project_code)
                   VALUES(?, 'Salida', ?, 6.2141, -75.5826, 10.0, 1, 'Hora extra', 0, 120, 'DA 149')""",
                (employee_id, f"{date_str}T18:00:00")
            )
        conn.commit()
        return jsonify(ok=True, message="Simulación de 30 días agregada correctamente.")
    except Exception as e:
        conn.rollback()
        return jsonify(error=str(e)), 500
    finally:
        conn.close()


init_db()

if __name__ == "__main__":
  app.run(debug=True, host="0.0.0.0", port=5000)





from datetime import datetime, date
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
            "Horario Normal OMMA (07:00 - 15:00)",
            "07:00-15:00",
            "07:00-15:00",
            "07:00-15:00",
            "07:00-15:00",
            "07:00-15:00",
            "07:00-15:00",
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
    <div class="container" id="app-container">
        <!-- Contenido dinámico -->
    </div>
    <script>
    function getDeviceUUID() {
        let name = "device_uuid=";
        let decodedCookie = decodeURIComponent(document.cookie);
        let ca = decodedCookie.split(';');
        for(let i = 0; i < ca.length; i++) {
            let c = ca[i];
            while (c.charAt(0) == ' ') {
                c = c.substring(1);
            }
            if (c.indexOf(name) == 0) {
                return c.substring(name.length, c.length);
            }
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
                            <input type="text" id="emp-search" class="form-control" placeholder="Escriba su nombre para autocompletar..." autocomplete="off" list="employees-datalist" required>
                            <datalist id="employees-datalist"></datalist>
                        </div>
                        <div class="mb-3">
                            <label class="form-label">Contraseña</label>
                            <input type="password" id="password" class="form-control" placeholder="Ingrese su contraseña..." required>
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
        } catch (e) {
            console.error("No se pudo cargar la lista para autocompletar", e);
        }
    }

    async function doLogin(e) {
        e.preventDefault();
        let searchEl = document.getElementById('emp-search');
        let passEl = document.getElementById('password');
        let errorDiv = document.getElementById('login-error');
        
        if (!searchEl || !passEl) return;

        let inputVal = searchEl.value.trim();
        let p = passEl.value.trim();
        let deviceToken = getDeviceUUID();

        try {
            let res = await fetch('/api/login', {
                method: 'POST', 
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username: inputVal, password: p, device_token: deviceToken})
            });
            let data = await res.json();

            if (res.ok) { 
                loadState(); 
            } else { 
                if (errorDiv) {
                    errorDiv.innerText = data.error || 'Error de autenticación'; 
                    errorDiv.classList.remove('d-none'); 
                } else {
                    alert(data.error || 'Error de autenticación');
                }
            }
        } catch (err) {
            console.error("Error en login:", err);
            alert("Ocurrió un error al conectar con el servidor.");
        }
    }

    async function doLogout() {
        await fetch('/api/logout', {method: 'POST'});
        loadState();
    }

    function renderDashboard(data) {
        let displayName = data.user.display_name || data.user.username;
        document.getElementById('user-nav').innerHTML = `<span>${displayName}</span> <button class="btn btn-outline-light btn-sm ms-3" onclick="doLogout()">Cerrar sesión</button>`;
        
        let html = `
            <div class="card p-4 shadow-sm mb-4">
                <h2>Panel de Control - Omma Group</h2>
                <p class="text-muted">Control de doble registro de ubicación (Ingreso y Salida independientes con GPS).</p>
            </div>`;

        if (data.user.role === 'empleado') {
            html += `
                <div class="card p-4 shadow-sm text-center">
                    <h3>Registro de Asistencia GPS</h3>
                    <p class="text-muted">Debe realizar de forma independiente el registro de su <strong>Entrada</strong> y su <strong>Salida</strong> (Radio 200m).</p>
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
                    <h4>Reportes del Sistema</h4>
                    <a href="/api/report/csv" class="btn btn-success w-100">Descargar Reporte de Asistencia (Excel)</a>
                </div>
                
                <div class="card p-3 shadow-sm mb-4">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h4 class="mb-0">Empleados Registrados (<span id="emp-count-badge">${data.employees.length}</span>)</h4>
                    </div>
                    
                    <div class="row g-2 mb-3">
                        <div class="col-md-4">
                            <input type="text" id="filter-search" class="form-control" placeholder="Buscar por nombre o cédula..." oninput="filterEmployees()">
                        </div>
                        <div class="col-md-4">
                            <select id="filter-area" class="form-select" onchange="filterEmployees()">
                                <option value="">Todas las Áreas</option>
                                ${data.areas.map(a => `<option value="${a.id}">${a.name}</option>`).join('')}
                            </select>
                        </div>
                        <div class="col-md-4">
                            <select id="filter-sort" class="form-select" onchange="filterEmployees()">
                                <option value="az">Orden Alfabético (A - Z)</option>
                                <option value="za">Orden Alfabético (Z - A)</option>
                            </select>
                        </div>
                    </div>

                    <ul class="list-group list-group-flush" id="employees-list-container" style="max-height: 450px; overflow-y: auto;">
                        <!-- Renderizado dinámico -->
                    </ul>
                </div>`;
            setTimeout(filterEmployees, 50);
        }
        document.getElementById('app-container').innerHTML = html;
    }

    function filterEmployees() {
        if (!window.allSysEmployees) return;
        let search = document.getElementById('filter-search').value.toLowerCase();
        let areaId = document.getElementById('filter-area').value;
        let sortOrder = document.getElementById('filter-sort').value;

        let filtered = window.allSysEmployees.filter(e => {
            let matchSearch = e.name.toLowerCase().includes(search) || (e.document && e.document.includes(search));
            let matchArea = areaId === "" || e.area_id == areaId;
            return matchSearch && matchArea;
        });

        filtered.sort((a, b) => {
            if (sortOrder === 'az') return a.name.localeCompare(b.name);
            if (sortOrder === 'za') return b.name.localeCompare(a.name);
            return 0;
        });

        let container = document.getElementById('employees-list-container');
        document.getElementById('emp-count-badge').innerText = filtered.length;

        if (filtered.length === 0) {
            container.innerHTML = '<li class="list-group-item text-center text-muted">No se encontraron empleados</li>';
            return;
        }

        container.innerHTML = filtered.map(e => `
            <li class="list-group-item d-flex justify-content-between align-items-center">
                <div>
                    <strong>${e.name}</strong><br>
                    <small class="text-muted">Doc: ${e.document || 'N/A'}</small>
                </div>
                <div class="d-flex align-items-center gap-2">
                    <span class="badge bg-primary">${e.area_name || 'Sin área'}</span>
                    <span class="badge bg-secondary">${e.site_name || 'Sin sede'}</span>
                    ${e.user_id ? `<button class="btn btn-outline-warning btn-sm" onclick="resetDevice(${e.user_id})">Reiniciar Celular</button>` : ''}
                </div>
            </li>`).join('');
    }

    async function resetDevice(userId) {
        if (!confirm('¿Está seguro de desvincular el dispositivo de este empleado?')) return;
        let res = await fetch(`/api/admin/reset-device/${userId}`, {method: 'POST'});
        if (res.ok) {
            alert('Dispositivo desvinculado con éxito.');
            loadState();
        } else {
            let data = await res.json();
            alert(data.error || 'Error al desvincular dispositivo');
        }
    }

    async function markAttendance(type) {
        if (!navigator.geolocation) { alert('Geolocalización no soportada'); return; }
        
        let projectCode = '';
        if (type === 'Salida') {
            let inputCode = prompt('Ingrese el Código del o los Proyectos en los que Laboró Ej: DA 149', '');
            if (inputCode === null) return;
            projectCode = inputCode.trim().toUpperCase();
            let regex = /^[A-Z]{2}\s?\d{3}$/;
            if (!regex.test(projectCode)) {
                alert('Formato de código de proyecto inválido. Debe ser 2 letras y 3 números (Ej: DA 149).');
                return;
            }
        }

        navigator.geolocation.getCurrentPosition(async pos => {
            let lat = pos.coords.latitude;
            let lon = pos.coords.longitude;
            let res = await fetch('/api/mark', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({event_type: type, latitude: lat, longitude: lon, project_code: projectCode})
            });
            let data = await res.json();
            if (res.ok) {
                let projText = projectCode ? ` | Proyecto: ${projectCode}` : '';
                document.getElementById('mark-result').innerHTML = `<div class="alert alert-success">Ubicación GPS registrada para ${type} a las ${data.time}${projText} - Estado: ${data.status}</div>`;
            } else {
                document.getElementById('mark-result').innerHTML = `<div class="alert alert-danger">${data.error}</div>`;
            }
        }, err => { alert('Para marcar ' + type + ' es obligatorio permitir el acceso a la ubicación GPS: ' + err.message); }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
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
    uname = (r["username"] or "").strip()
    ename = (r["emp_name"] or "").strip()
    edoc = (r["emp_doc"] or "").strip()

    if input_norm == strip_accents(ename).lower() or input_norm == strip_accents(
        uname
    ).lower():
      if r["password_hash"] == p_hash:
        matched_user = dict(r)
        break

  if not matched_user:
    return jsonify(error="Nombre o contraseña incorrectos"), 401

  if matched_user["role"] == "empleado":
    c = db()
    if not matched_user["device_token"]:
      if client_device_token:
        c.execute(
            "UPDATE users SET device_token = ? WHERE id = ?",
            (client_device_token, matched_user["id"]),
        )
        c.commit()
        matched_user["device_token"] = client_device_token
    elif matched_user["device_token"] != client_device_token:
      c.close()
      return jsonify(
          error=(
              "Este usuario ya está vinculado a otro dispositivo móvil."
              " Contacte al administrador para reiniciar el dispositivo."
          )
      ), 403
    c.close()

  session["uid"] = matched_user["id"]
  return jsonify(user=matched_user)


@app.post("/api/admin/reset-device/<int:user_id>")
def reset_device(user_id):
  u = current_user()
  if not u or u["role"] != "administrador":
    return jsonify(error="No autorizado"), 403
  c = db()
  c.execute("UPDATE users SET device_token = NULL WHERE id = ?", (user_id,))
  c.commit()
  c.close()
  return jsonify(ok=True)


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
  companies = [
      dict(x) for x in c.execute("SELECT * FROM companies WHERE id=?", (cid,))
  ]
  sites = [
      dict(x) for x in c.execute("SELECT * FROM sites WHERE company_id=?", (cid,))
  ]
  schedules = [
      dict(x)
      for x in c.execute("SELECT * FROM schedules WHERE company_id=?", (cid,))
  ]
  areas = [
      dict(x)
      for x in c.execute(
          """
        SELECT a.*, s.name schedule_name, s.mon, s.tue, s.wed, s.thu, s.fri, s.sat, s.sun 
        FROM areas a LEFT JOIN schedules s ON s.id=a.schedule_id WHERE a.company_id=?
    """,
          (cid,),
      )
  ]
  employees = [
      dict(x)
      for x in c.execute(
          """SELECT e.*,s.name site_name,h.name schedule_name, ar.name area_name, u.id as user_id, u.device_token FROM employees e LEFT JOIN sites s ON s.id=e.site_id LEFT JOIN schedules h ON h.id=e.schedule_id LEFT JOIN areas ar ON ar.id=e.area_id LEFT JOIN users u ON u.employee_id=e.id WHERE e.company_id=? ORDER BY e.name ASC""",
          (cid,),
      )
  ]
  attendance = [
      dict(x)
      for x in c.execute(
          """SELECT a.*,e.name employee_name FROM attendance a JOIN employees e ON e.id=a.employee_id WHERE e.company_id=? ORDER BY a.id DESC LIMIT 100""",
          (cid,),
      )
  ]
  incidents = [
      dict(x)
      for x in c.execute(
          """SELECT i.*,e.name employee_name FROM incidents i JOIN employees e ON e.id=i.employee_id WHERE e.company_id=? ORDER BY i.id DESC""",
          (cid,),
      )
  ]
  c.close()
  return jsonify(
      companies=companies,
      sites=sites,
      schedules=schedules,
      areas=areas,
      employees=employees,
      attendance=attendance,
      incidents=incidents,
      user=u,
  )


@app.post("/api/mark")
def mark():
  try:
    u = current_user()
    if not u or u["role"] != "empleado" or not u["employee_id"]:
      return jsonify(error="Solo un empleado autenticado puede marcar"), 403

    d = request.json or {}
    typ = d.get("event_type")
    lat = d.get("latitude")
    lon = d.get("longitude")
    project_code = (d.get("project_code") or "").strip().upper()

    if typ not in ("Entrada", "Salida"):
      return jsonify(error="Tipo inválido"), 400

    if lat is None or lon is None:
      return jsonify(
          error=(
              f"La ubicación GPS es obligatoria para registrar la {typ.lower()}."
          )
      ), 400

    if typ == "Salida" and not project_code:
      return jsonify(
          error="El código de proyecto es obligatorio al marcar salida."
      ), 400

    dt = datetime.now(COLOMBIA_TZ)
    today_str = dt.date().isoformat()

    c = db()
    existing_mark = c.execute(
        """SELECT id FROM attendance 
           WHERE employee_id = ? AND event_type = ? AND DATE(event_time) = ?""",
        (u["employee_id"], typ, today_str),
    ).fetchone()

    if existing_mark:
      c.close()
      return jsonify(
          error=(
              f"Ya ha registrado su ubicación de {typ} el día de hoy. No se"
              " permiten registros duplicados del mismo evento."
          )
      ), 400

    row = c.execute(
        """SELECT e.*, si.latitude site_lat, si.longitude site_lon, si.radius_m, 
                            COALESCE(ar_sch.mon, h.mon) mon, COALESCE(ar_sch.tue, h.tue) tue, 
                            COALESCE(ar_sch.wed, h.wed) wed, COALESCE(ar_sch.thu, h.thu) thu, 
                            COALESCE(ar_sch.fri, h.fri) fri, COALESCE(ar_sch.sat, h.sat) sat, 
                            COALESCE(ar_sch.sun, h.sun) sun,
                            COALESCE(ar_sch.tolerance_minutes, h.tolerance_minutes) tolerance_minutes
                            FROM employees e
                            LEFT JOIN sites si ON si.id=e.site_id 
                            LEFT JOIN schedules h ON h.id=e.schedule_id 
                            LEFT JOIN areas ar ON ar.id=e.area_id
                            LEFT JOIN schedules ar_sch ON ar_sch.id=ar.schedule_id
                            WHERE e.id=? AND e.company_id=?""",
        (u["employee_id"], u["company_id"]),
    ).fetchone()

    if not row:
      c.close()
      return jsonify(error="Empleado no encontrado"), 404

    dist = 0.0
    valid = True

    if row["site_lat"] is not None and row["site_lon"] is not None:
      try:
        dist = hav(
            float(lat),
            float(lon),
            float(row["site_lat"]),
            float(row["site_lon"]),
        )
        radius = row["radius_m"] if row["radius_m"] is not None else 200
        valid = dist <= radius
      except Exception as e:
        print(f"Aviso cálculo GPS: {e}")

    status = "Registrada"
    late = 0
    extra_mins = 0

    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    day_key = days[dt.weekday()]
    period = row[day_key] if day_key in row.keys() else ""

    if typ == "Entrada" and period:
      start = int(period[:2]) * 60 + int(period[3:5])
      actual = dt.hour * 60 + dt.minute
      late = max(0, actual - start - int(row["tolerance_minutes"] or 0))
      if late == 0:
        status = "A tiempo"
      else:
        h_late = late // 60
        m_late = late % 60
        status = f"Retardo {late} min ({h_late}h {m_late}m)"

    elif typ == "Salida" and period:
      end = int(period[6:8]) * 60 + int(period[9:11])
      actual = dt.hour * 60 + dt.minute
      diff = actual - end
      if diff < 0:
        early_mins = abs(diff)
        h_early = early_mins // 60
        m_early = early_mins % 60
        status = f"Salida anticipada {early_mins} min ({h_early}h {m_early}m antes)"
      elif diff > 0:
        extra_mins = diff
        h_extra = extra_mins // 60
        m_extra = extra_mins % 60
        status = f"Hora extra {extra_mins} min ({h_extra}h {m_extra}m)"
      else:
        status = "A tiempo"

    if not valid:
      status = f"{status} (Fuera de zona a {dist:.0f}m)"

    cur = c.execute(
        """INSERT INTO
        attendance(employee_id,event_type,event_time,latitude,longitude,distance_m,gps_valid,status,late_minutes,overtime_minutes,project_code)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            u["employee_id"],
            typ,
            dt.isoformat(timespec="seconds"),
            lat,
            lon,
            dist,
            int(valid),
            status,
            late,
            extra_mins,
            project_code,
        ),
    )

    emp_info = c.execute(
        "SELECT name, document FROM employees WHERE id=?", (u["employee_id"],)
    ).fetchone()
    c.commit()
    c.close()

    if emp_info:
      try:
        sync_to_sheets(
            emp_info["name"],
            emp_info["document"],
            typ,
            dt.strftime("%Y-%m-%d %H:%M:%S"),
            status,
            late,
            project_code,
        )
      except Exception as sheet_err:
        print(f"Aviso Google Sheets: {sheet_err}")

    time_ampm = dt.strftime("%I:%M%p")
    return jsonify(
        id=cur.lastrowid,
        time=time_ampm,
        status=status,
        gps_valid=valid,
        late_minutes=late,
        project_code=project_code,
    )
  except Exception as e:
    return jsonify(error=f"Excepción interna: {str(e)}"), 500


@app.get("/api/report/csv")
def export_csv():
  u = current_user()
  if not u or u["role"] != "administrador":
    return jsonify(error="No autorizado"), 403

  c = db()
  rows = c.execute(
      """
        SELECT a.id, e.id as emp_id, e.name as employee_name, e.document, s.name as site_name, ar.name as area_name,
               a.event_type, a.event_time, a.latitude, a.longitude, a.distance_m, a.status, a.late_minutes, a.overtime_minutes, a.project_code
        FROM attendance a 
        JOIN employees e ON e.id = a.employee_id 
        LEFT JOIN sites s ON s.id = e.site_id 
        LEFT JOIN areas ar ON ar.id = e.area_id
        WHERE e.company_id = ? 
        ORDER BY a.event_time ASC
    """,
      (u["company_id"],),
  ).fetchall()
  c.close()

  daily_records = {}
  for r in rows:
    dt_str = r["event_time"]
    try:
      dt_obj = datetime.fromisoformat(dt_str)
    except Exception:
      try:
        dt_obj = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
      except Exception:
        continue

    date_key = dt_obj.strftime("%Y-%m-%d")
    emp_id = r["emp_id"]
    key = (emp_id, date_key)

    if key not in daily_records:
      daily_records[key] = {
          "employee_name": r["employee_name"],
          "document": r["document"],
          "area_name": r["area_name"],
          "site_name": r["site_name"],
          "date": dt_obj.strftime("%d-%b-%Y").lower(),
          "entrada": None,
          "salida": None,
      }

    event_info = {
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

  headers = [
      "Empleado",
      "Documento",
      "Área",
      "Sede",
      "Fecha",
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
      "Total Extras",
  ]
  ws.append(headers)

  for key, rec in sorted(
      daily_records.items(), key=lambda x: (x[0][1], x[1]["employee_name"])
  ):
    ent = rec["entrada"] or {}
    sal = rec["salida"] or {}

    ent_maps = ""
    if ent.get("lat") is not None and ent.get("lon") is not None:
      try:
        ent_maps = (
            f'=HYPERLINK("https://www.google.com/maps?q={float(ent["lat"])},'
            f'{float(ent["lon"])}", "Ver Entrada")'
        )
      except Exception:
        pass
 
    sal_maps = ""
    if sal.get("lat") is not None and sal.get("lon") is not None:
      try:
        sal_maps = (
            f'=HYPERLINK("https://www.google.com/maps?q={float(sal["lat"])},'
            f'{float(sal["lon"])}", "Ver Salida")'
        )
      except Exception:
        pass

    ent_dist = (
        int(round(ent["distance"])) if ent.get("distance") is not None else ""
    )
    sal_dist = (
        int(round(sal["distance"])) if sal.get("distance") is not None else ""
    )

    row_data = [
        rec["employee_name"],
        str(rec["document"] or ""),
        rec["area_name"] or "",
        rec["site_name"] or "",
        rec["date"],
        ent.get("time", ""),
        ent_maps,
        ent_dist,
        ent.get("status", ""),
        sal.get("time", ""),
        sal_maps,
        sal_dist,
        sal.get("project_code", ""),
        sal.get("status", ""),
        int(ent.get("late", 0) or 0),
        int(sal.get("overtime", 0) or 0),
    ]
    ws.append(row_data)

  file_stream = io.BytesIO()
  wb.save(file_stream)
  file_stream.seek(0)

  output = make_response(file_stream.getvalue())
  output.headers["Content-Disposition"] = (
      "attachment; filename=reporte_asistencia.xlsx"
  )
  output.headers["Content-Type"] = (
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
  )
  return output


init_db()

if __name__ == "__main__":
  app.run(debug=True, host="0.0.0.0", port=5000)
