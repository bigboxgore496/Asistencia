from datetime import datetime
import hashlib
import io
import math
from pathlib import Path
import secrets
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


def init_db():
  c = db()
  c.executescript("""
    CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS sites(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER NOT NULL,name TEXT NOT NULL,latitude REAL NOT NULL,longitude REAL NOT NULL,radius_m INTEGER DEFAULT 100,FOREIGN KEY(company_id) REFERENCES companies(id));
    CREATE TABLE IF NOT EXISTS schedules(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER NOT NULL,name TEXT NOT NULL,mon TEXT DEFAULT '',tue TEXT DEFAULT '',wed TEXT DEFAULT '',thu TEXT DEFAULT '',fri TEXT DEFAULT '',sat TEXT DEFAULT '',sun TEXT DEFAULT '',lunch_start TEXT DEFAULT '',lunch_end TEXT DEFAULT '',break_minutes INTEGER DEFAULT 30,tolerance_minutes INTEGER DEFAULT 10,FOREIGN KEY(company_id) REFERENCES companies(id));
    CREATE TABLE IF NOT EXISTS areas(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER NOT NULL,name TEXT NOT NULL,schedule_id INTEGER,FOREIGN KEY(company_id) REFERENCES companies(id),FOREIGN KEY(schedule_id) REFERENCES schedules(id));
    CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER NOT NULL,site_id INTEGER,schedule_id INTEGER,area_id INTEGER,name TEXT NOT NULL,document TEXT,position TEXT,status TEXT DEFAULT 'Activo',FOREIGN KEY(company_id) REFERENCES companies(id),FOREIGN KEY(site_id) REFERENCES sites(id),FOREIGN KEY(schedule_id) REFERENCES schedules(id),FOREIGN KEY(area_id) REFERENCES areas(id));
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER,employee_id INTEGER,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL,active INTEGER DEFAULT 1,FOREIGN KEY(company_id) REFERENCES companies(id),FOREIGN KEY(employee_id) REFERENCES employees(id));
    CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,event_type TEXT NOT NULL,event_time TEXT NOT NULL,latitude REAL,longitude REAL,distance_m REAL,gps_valid INTEGER DEFAULT 0,status TEXT,late_minutes INTEGER DEFAULT 0,worked_minutes INTEGER DEFAULT 0,overtime_minutes INTEGER DEFAULT 0,FOREIGN KEY(employee_id) REFERENCES employees(id));
    CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,type TEXT NOT NULL,start_date TEXT,end_date TEXT,notes TEXT,status TEXT DEFAULT 'Pendiente',FOREIGN KEY(employee_id) REFERENCES employees(id));
    """)

  if c.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 0:
    cur = c.execute("INSERT INTO companies(name) VALUES(?)", ("Omma Group",))
    co = cur.lastrowid

    cur = c.execute(
        "INSERT INTO sites(company_id,name,latitude,longitude,radius_m)"
        " VALUES(?,?,?,?,?)",
        (co, "Sede Principal", 6.214110727151654, -75.58268995990919, 200),
    )
    site = cur.lastrowid

    cur = c.execute(
        """INSERT INTO
        schedules(company_id,name,mon,tue,wed,thu,fri,sat,sun,lunch_start,lunch_end,break_minutes,tolerance_minutes)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            co,
            "Horario General (07:00 - 15:00)",
            "07:00-15:00",
            "07:00-15:00",
            "07:00-15:00",
            "07:00-15:00",
            "07:00-15:00",
            "07:00-15:00",
            "",
            "12:00",
            "13:00",
            60,
            10,
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
        ("1035861508", "ANTONY WEPES HOYOS", "Produccion"),
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

    for pwd, nombre, area_name in personal_data:
      aid = area_ids.get(area_name)
      cur = c.execute(
          "INSERT INTO"
          " employees(company_id,site_id,schedule_id,area_id,name,document,position)"
          " VALUES(?,?,?,?,?,?,?)",
          (co, site, sch, aid, nombre, pwd, area_name),
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
  u = c.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
  c.close()
  if u and u["role"] == "administrador":
    u_dict = dict(u)
    u_dict["display_name"] = "Admin (Administrador)"
    return u_dict
  return dict(u) if u else None


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
    employee_name, document, event_type, event_time, status, late_minutes
):
  try:
    gc = gspread.service_account(filename="credentials.json")
    sh = gc.open("Nombre_De_Su_Google_Sheets")
    worksheet = sh.sheet1
    worksheet.append_row(
        [employee_name, document, event_type, event_time, status, late_minutes]
    )
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
    async function loadState() {
        let res = await fetch('/api/state');
        if (!res.ok) { renderLogin(); return; }
        let data = await res.json();
        renderDashboard(data);
    }
    
    async function renderLogin() {
        document.getElementById('user-nav').innerHTML = '';
        let employees = [];
        try {
            let res = await fetch('/api/employees/list');
            if (res.ok) { employees = await res.json(); }
        } catch (err) {
            console.error("Error cargando empleados:", err);
        }

        document.getElementById('app-container').innerHTML = `
            <div class="row justify-content-center mt-5">
                <div class="col-md-5 card p-4 shadow">
                    <h3 class="mb-3 text-center">Iniciar Sesión</h3>
                    <div id="login-error" class="alert alert-danger d-none"></div>
                    <form onsubmit="doLogin(event)">
                        <div class="mb-3">
                            <label class="form-label">Usuario o Nombre</label>
                            <input type="text" id="emp-search" class="form-control" list="employees-list" placeholder="Ingrese su nombre o usuario..." autocomplete="off" required>
                            <datalist id="employees-list">
                                <option value="admin">
                                ${employees.map(e => `<option value="${e.name}">`).join('')}
                            </datalist>
                        </div>
                        <div class="mb-3">
                            <label class="form-label">Contraseña (Número de Documento)</label>
                            <input type="password" id="password" class="form-control" placeholder="Ingrese su contraseña..." required>
                        </div>
                        <button type="submit" class="btn btn-dark w-100">Ingresar</button>
                    </form>
                </div>
            </div>`;
        window.allEmployees = employees;
    }

    async function doLogin(e) {
        e.preventDefault();
        let inputVal = document.getElementById('emp-search').value.trim();
        let p = document.getElementById('password').value.trim();

        let username = inputVal;
        if (inputVal.toLowerCase() !== 'admin' && window.allEmployees && window.allEmployees.length > 0) {
            let found = window.allEmployees.find(emp => 
                emp.name.toLowerCase() === inputVal.toLowerCase() || 
                (emp.username && emp.username.toLowerCase() === inputVal.toLowerCase())
            );
            if (found && found.username) {
                username = found.username;
            }
        }

        let res = await fetch('/api/login', {
            method: 'POST', 
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({username: username, password: p})
        });
        
        if (res.ok) { 
            loadState(); 
        } else { 
            let err = await res.json(); 
            let errorDiv = document.getElementById('login-error');
            errorDiv.innerText = err.error || 'Error de autenticación'; 
            errorDiv.classList.remove('d-none'); 
        }
    }

    async function doLogout() {
        await fetch('/api/logout', {method: 'POST'});
        loadState();
    }

    function renderDashboard(data) {
        let role = data.user.role;
        let displayName = role === 'administrador' ? 'Admin (Administrador)' : data.user.username;
        document.getElementById('user-nav').innerHTML = `<span>${displayName}</span> <button class="btn btn-outline-light btn-sm ms-3" onclick="doLogout()">Cerrar sesión</button>`;
        
        let html = `
            <div class="card p-4 shadow-sm mb-4">
                <h2>Panel de Control - Omma Group</h2>
                <p class="text-muted">Sistema de control de asistencia con validación GPS y áreas (Horario fijo: Lunes a Sábado de 7:00 AM a 3:00 PM).</p>
            </div>`;

        if (role === 'empleado') {
            html += `
                <div class="card p-4 shadow-sm text-center">
                    <h3>Registrar Asistencia</h3>
                    <p class="text-muted">Marque su entrada o salida usando GPS (Radio 200m).</p>
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
                    <a href="/api/report/csv" class="btn btn-success w-100">Descargar Reporte de Asistencia Consolidado (Excel)</a>
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
            container.innerHTML = '<li class="list-group-item text-center text-muted">No se encontraron empleados con los filtros seleccionados</li>';
            return;
        }

        container.innerHTML = filtered.map(e => `
            <li class="list-group-item d-flex justify-content-between align-items-center">
                <div>
                    <strong>${e.name}</strong><br>
                    <small class="text-muted">Doc: ${e.document || 'N/A'}</small>
                </div>
                <div>
                    <span class="badge bg-primary">${e.area_name || 'Sin área'}</span>
                    <span class="badge bg-secondary">${e.site_name || 'Sin sede'}</span>
                </div>
            </li>`).join('');
    }

    async function markAttendance(type) {
        if (!navigator.geolocation) { alert('Geolocalización no soportada'); return; }
        navigator.geolocation.getCurrentPosition(async pos => {
            let lat = pos.coords.latitude;
            let lon = pos.coords.longitude;
            let res = await fetch('/api/mark', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({event_type: type, latitude: lat, longitude: lon})
            });
            let data = await res.json();
            if (res.ok) {
                document.getElementById('mark-result').innerHTML = `<div class="alert alert-success">${type} registrada a las ${data.time} - Estado: ${data.status}</div>`;
            } else {
                document.getElementById('mark-result').innerHTML = `<div class="alert alert-danger">${data.error}</div>`;
            }
        }, err => { alert('Error obteniendo GPS: ' + err.message); });
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
  u = (d.get("username") or "").strip().lower()
  p = d.get("password") or ""
  c = db()
  row = c.execute(
      "SELECT * FROM users WHERE lower(username)=? AND password_hash=? AND"
      " active=1",
      (u, hashpw(p)),
  ).fetchone()
  c.close()
  if not row:
    return jsonify(error="Usuario o contraseña incorrectos"), 401
  session["uid"] = row["id"]
  return jsonify(user=dict(row))


@app.post("/api/logout")
def logout():
  session.clear()
  return jsonify(ok=True)


@app.get("/api/me")
def me():
  u = current_user()
  if not u:
    return jsonify(authenticated=False)
  return jsonify(authenticated=True, user=u)


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
          """SELECT e.*,s.name site_name,h.name schedule_name, ar.name area_name FROM employees e LEFT JOIN sites s ON s.id=e.site_id LEFT JOIN schedules h ON h.id=e.schedule_id LEFT JOIN areas ar ON ar.id=e.area_id WHERE e.company_id=? ORDER BY e.name ASC""",
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

    if typ not in ("Entrada", "Salida"):
      return jsonify(error="Tipo inválido"), 400

    c = db()
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
    c.close()

    if not row:
      return jsonify(error="Empleado no encontrado"), 404

    dist = 0.0
    valid = True

    if (
        lat is not None
        and lon is not None
        and row["site_lat"] is not None
        and row["site_lon"] is not None
    ):
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

    dt = datetime.now(COLOMBIA_TZ)
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
        status = f"Retardo {late} min ({h_late} horas y {m_late} minutos.)"

    elif typ == "Salida" and period:
      end = int(period[6:8]) * 60 + int(period[9:11])
      actual = dt.hour * 60 + dt.minute
      diff = actual - end
      if diff < 0:
        early_mins = abs(diff)
        h_early = early_mins // 60
        m_early = early_mins % 60
        status = (
            f"Salida anticipada {early_mins} min ({h_early} horas y"
            f" {m_early} minutos antes.)"
        )
      elif diff > 0:
        extra_mins = diff
        h_extra = extra_mins // 60
        m_extra = extra_mins % 60
        status = (
            f"Hora extra {extra_mins} min ({h_extra} horas y {m_extra}"
            " minutos.)"
        )
      else:
        status = "A tiempo"

    if not valid:
      status = f"{status} (Fuera de zona a {dist:.0f}m)"

    c = db()
    cur = c.execute(
        """INSERT INTO
        attendance(employee_id,event_type,event_time,latitude,longitude,distance_m,gps_valid,status,late_minutes,overtime_minutes)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
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
               a.event_type, a.event_time, a.latitude, a.longitude, a.distance_m, a.status, a.late_minutes, a.overtime_minutes,
               COALESCE(ar_sch.mon, h.mon) mon, COALESCE(ar_sch.tue, h.tue) tue, 
               COALESCE(ar_sch.wed, h.wed) wed, COALESCE(ar_sch.thu, h.thu) thu, 
               COALESCE(ar_sch.fri, h.fri) fri, COALESCE(ar_sch.sat, h.sat) sat, 
               COALESCE(ar_sch.sun, h.sun) sun,
               COALESCE(ar_sch.tolerance_minutes, h.tolerance_minutes) tolerance_minutes
        FROM attendance a 
        JOIN employees e ON e.id = a.employee_id 
        LEFT JOIN sites s ON s.id = e.site_id 
        LEFT JOIN schedules h ON h.id=e.schedule_id 
        LEFT JOIN areas ar ON ar.id = e.area_id
        LEFT JOIN schedules ar_sch ON ar_sch.id=ar.schedule_id
        WHERE e.company_id = ? 
        ORDER BY a.event_time ASC
    """,
      (u["company_id"],),
  ).fetchall()
  c.close()

  daily_records = {}
  days_list = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

  for r in rows:
    dt_str = r["event_time"]
    try:
      dt_obj = datetime.fromisoformat(dt_str)
    except Exception:
      try:
        dt_obj = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
      except Exception:
        continue

    day_date = dt_obj.strftime("%Y-%m-%d")
    emp_key = (r["emp_id"], day_date)

    if emp_key not in daily_records:
      daily_records[emp_key] = {
          "id": r["id"],
          "employee_name": r["employee_name"],
          "document": r["document"],
          "area_name": r["area_name"] or "",
          "site_name": r["site_name"] or "",
          "date": dt_obj.strftime("%d-%b").lower(),
          "entrada_time": "",
          "salida_time": "",
          "lat": None,
          "lon": None,
          "distance_m": None,
          "late_minutes": 0,
          "total_extras": 0,
      }

    rec = daily_records[emp_key]
    time_formatted = dt_obj.strftime("%I:%M:%S %p").lower()

    weekday_idx = dt_obj.weekday()
    day_key = days_list[weekday_idx]
    period = r[day_key] if day_key in r.keys() else ""

    if r["event_type"] == "Entrada":
      rec["entrada_time"] = time_formatted
      if r["latitude"] is not None:
        rec["lat"] = r["latitude"]
        rec["lon"] = r["longitude"]
        rec["distance_m"] = r["distance_m"]

      if period and "-" in period:
        try:
          start_str = period.split("-")[0]
          sched_hour = int(start_str[:2])
          sched_min = int(start_str[3:5])
          sched_total = sched_hour * 60 + sched_min
          actual_total = dt_obj.hour * 60 + dt_obj.minute
          tolerance = int(r["tolerance_minutes"] or 10)
          calc_late = max(0, actual_total - sched_total - tolerance)
          rec["late_minutes"] = max(r["late_minutes"] or 0, calc_late)
        except Exception:
          rec["late_minutes"] = r["late_minutes"] or 0
      else:
        rec["late_minutes"] = r["late_minutes"] or 0

    elif r["event_type"] == "Salida":
      rec["salida_time"] = time_formatted
      if r["latitude"] is not None and rec["lat"] is None:
        rec["lat"] = r["latitude"]
        rec["lon"] = r["longitude"]
        rec["distance_m"] = r["distance_m"]

      if period and "-" in period:
        try:
          end_str = period.split("-")[1]
          end_hour = int(end_str[:2])
          end_min = int(end_str[3:5])
          sched_end_total = end_hour * 60 + end_min
          actual_end_total = dt_obj.hour * 60 + dt_obj.minute
          diff = actual_end_total - sched_end_total
          calc_extras = max(0, diff)
          rec["total_extras"] = max(r["overtime_minutes"] or 0, calc_extras)
        except Exception:
          rec["total_extras"] = r["overtime_minutes"] or 0
      else:
        rec["total_extras"] = r["overtime_minutes"] or 0

  wb = openpyxl.Workbook()
  ws = wb.active
  ws.title = "Reporte Asistencia"

  headers = [
      "ID",
      "Empleado",
      "Documento",
      "Area",
      "Sede",
      "Fecha",
      "Hora de ingreso",
      "Hora de Salida",
      "Ubicacion Google Maps",
      "Distancia (m)",
      "Minutos Retardo",
      "Total Extras",
  ]
  ws.append(headers)

  for key, r in daily_records.items():
    lat = r["lat"]
    lon = r["lon"]
    if lat is not None and lon is not None:
      try:
        maps_url = f"https://www.google.com/maps?q={float(lat)},{float(lon)}"
        location_field = f'=HYPERLINK("{maps_url}"; "Ver en Mapa")'
      except Exception:
        location_field = ""
    else:
      location_field = ""

    ws.append([
        r["id"],
        r["employee_name"],
        r["document"],
        r["area_name"],
        r["site_name"],
        r["date"],
        r["entrada_time"],
        r["salida_time"],
        location_field,
        f"{r['distance_m']:.0f}" if r["distance_m"] is not None else "",
        r["late_minutes"],
        r["total_extras"],
    ])

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


# Inicialización de la base de datos para entornos de producción con Gunicorn
init_db()

if __name__ == "__main__":
  app.run(debug=True, host="0.0.0.0", port=5000)
```[cite: 2]
