from flask import Flask, request, jsonify, render_template, session, make_response
import sqlite3, math, hashlib, secrets, csv, io, gspread
from pathlib import Path
from datetime import datetime

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
DB = Path(__file__).with_name("asistencia.db")

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON"); return c

def hashpw(p): return hashlib.sha256(p.encode()).hexdigest()

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS sites(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER NOT NULL,name TEXT NOT NULL,latitude REAL NOT NULL,longitude REAL NOT NULL,radius_m INTEGER DEFAULT 100,FOREIGN KEY(company_id) REFERENCES companies(id));
    CREATE TABLE IF NOT EXISTS schedules(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER NOT NULL,name TEXT NOT NULL,mon TEXT DEFAULT '',tue TEXT DEFAULT '',wed TEXT DEFAULT '',thu TEXT DEFAULT '',fri TEXT DEFAULT '',sat TEXT DEFAULT '',sun TEXT DEFAULT '',lunch_start TEXT DEFAULT '',lunch_end TEXT DEFAULT '',break_minutes INTEGER DEFAULT 30,tolerance_minutes INTEGER DEFAULT 10,FOREIGN KEY(company_id) REFERENCES companies(id));
    CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER NOT NULL,site_id INTEGER,schedule_id INTEGER,name TEXT NOT NULL,document TEXT,position TEXT,status TEXT DEFAULT 'Activo',FOREIGN KEY(company_id) REFERENCES companies(id),FOREIGN KEY(site_id) REFERENCES sites(id),FOREIGN KEY(schedule_id) REFERENCES schedules(id));
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER,employee_id INTEGER,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL,active INTEGER DEFAULT 1,FOREIGN KEY(company_id) REFERENCES companies(id),FOREIGN KEY(employee_id) REFERENCES employees(id));
    CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,event_type TEXT NOT NULL,event_time TEXT NOT NULL,latitude REAL,longitude REAL,distance_m REAL,gps_valid INTEGER DEFAULT 0,status TEXT,late_minutes INTEGER DEFAULT 0,worked_minutes INTEGER DEFAULT 0,overtime_minutes INTEGER DEFAULT 0,FOREIGN KEY(employee_id) REFERENCES employees(id));
    CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,type TEXT NOT NULL,start_date TEXT,end_date TEXT,notes TEXT,status TEXT DEFAULT 'Pendiente',FOREIGN KEY(employee_id) REFERENCES employees(id));
    """)
    if c.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 0:
        cur = c.execute("INSERT INTO companies(name) VALUES(?)", ("Empresa Demo S.A.S.",))
        co = cur.lastrowid
        
        cur = c.execute("INSERT INTO sites(company_id,name,latitude,longitude,radius_m) VALUES(?,?,?,?,?)", (co, "Sede Principal", 6.214110727151654, -75.58268995990919, 200))
        site = cur.lastrowid
        
        cur = c.execute("""INSERT INTO schedules(company_id,name,mon,tue,wed,thu,fri,sat,sun,lunch_start,lunch_end,break_minutes,tolerance_minutes)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (co, "Administrativo", "07:00-17:00", "07:00-17:00", "07:00-17:00", "07:00-17:00", "07:00-13:00", "", "", "13:00", "14:00", 30, 10))
        sch = cur.lastrowid
        
        demo = [("Carlos Rodríguez", "1.234.567.890", "Auxiliar Administrativo"), ("María González", "1.098.765.432", "Contadora"), ("Juan Pérez", "1.112.223.334", "Operario")]
        for n, d, p in demo:
            cur = c.execute("INSERT INTO employees(company_id,site_id,schedule_id,name,document,position) VALUES(?,?,?,?,?,?)", (co, site, sch, n, d, p))
            eid = cur.lastrowid
            c.execute("INSERT INTO users(company_id,employee_id,username,password_hash,role) VALUES(?,?,?,?,?)", (co, eid, n.split()[0].lower(), hashpw("123456"), "empleado"))
        c.execute("INSERT INTO users(company_id,username,password_hash,role) VALUES(?,?,?,?)", (co, "admin", hashpw("admin123"), "administrador"))
    c.commit(); c.close()

def current_user():
    uid = session.get("uid")
    if not uid: return None
    c = db(); u = c.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone(); c.close()
    return dict(u) if u else None

def hav(lat1, lon1, lat2, lon2):
    R = 6371000; p1 = math.radians(lat1); p2 = math.radians(lat2); dp = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def sync_to_sheets(employee_name, document, event_type, event_time, status, late_minutes):
    try:
        gc = gspread.service_account(filename="credentials.json")
        sh = gc.open("Nombre_De_Su_Google_Sheets")
        worksheet = sh.sheet1
        worksheet.append_row([employee_name, document, event_type, event_time, status, late_minutes])
    except Exception as e:
        print(f"Error al sincronizar con Google Sheets: {e}")

@app.route("/")
def index(): return render_template("index.html", user=current_user())

@app.post("/api/login")
def login():
    d = request.json or {}; u = (d.get("username") or "").strip().lower(); p = d.get("password") or ""
    c = db(); row = c.execute("SELECT * FROM users WHERE lower(username)=? AND password_hash=? AND active=1", (u, hashpw(p))).fetchone(); c.close()
    if not row: return jsonify(error="Usuario o contraseña incorrectos"), 401
    session["uid"] = row["id"]; return jsonify(user=dict(row))

@app.post("/api/logout")
def logout(): session.clear(); return jsonify(ok=True)

@app.get("/api/me")
def me():
    u = current_user()
    if not u: return jsonify(authenticated=False)
    return jsonify(authenticated=True, user=u)

@app.get("/api/state")
def state():
    u = current_user()
    if not u: return jsonify(error="No autenticado"), 401
    c = db(); cid = u["company_id"]
    companies = [dict(x) for x in c.execute("SELECT * FROM companies WHERE id=?", (cid,))]
    sites = [dict(x) for x in c.execute("SELECT * FROM sites WHERE company_id=?", (cid,))]
    schedules = [dict(x) for x in c.execute("SELECT * FROM schedules WHERE company_id=?", (cid,))]
    employees = [dict(x) for x in c.execute("""SELECT e.*,s.name site_name,h.name schedule_name FROM employees e LEFT JOIN sites s ON s.id=e.site_id LEFT JOIN schedules h ON h.id=e.schedule_id WHERE e.company_id=? ORDER BY e.id""", (cid,))]
    attendance = [dict(x) for x in c.execute("""SELECT a.*,e.name employee_name FROM attendance a JOIN employees e ON e.id=a.employee_id WHERE e.company_id=? ORDER BY a.id DESC LIMIT 100""", (cid,))]
    incidents = [dict(x) for x in c.execute("""SELECT i.*,e.name employee_name FROM incidents i JOIN employees e ON e.id=i.employee_id WHERE e.company_id=? ORDER BY i.id DESC""", (cid,))]
    c.close(); return jsonify(companies=companies, sites=sites, schedules=schedules, employees=employees, attendance=attendance, incidents=incidents, user=u)

@app.post("/api/company")
def company():
    u = current_user()
    if not u or u["role"] != "administrador": return jsonify(error="No autorizado"), 403
    name = (request.json or {}).get("name", "").strip()
    if not name: return jsonify(error="Nombre requerido"), 400
    c = db(); cur = c.execute("INSERT INTO companies(name) VALUES(?)", (name,)); c.commit(); c.close(); return jsonify(id=cur.lastrowid), 201

@app.post("/api/site")
def site():
    u = current_user()
    if not u or u["role"] != "administrador": return jsonify(error="No autorizado"), 403
    d = request.json or {}
    try: vals = (u["company_id"], d["name"], float(d["latitude"]), float(d["longitude"]), int(d.get("radius_m", 100)))
    except: return jsonify(error="Datos inválidos"), 400
    c = db(); cur = c.execute("INSERT INTO sites(company_id,name,latitude,longitude,radius_m) VALUES(?,?,?,?,?)", vals); c.commit(); c.close(); return jsonify(id=cur.lastrowid), 201

@app.post("/api/schedule")
def schedule():
    u = current_user()
    if not u or u["role"] != "administrador": return jsonify(error="No autorizado"), 403
    d = request.json or {}
    vals = (u["company_id"], d["name"], d.get("mon", ""), d.get("tue", ""), d.get("wed", ""), d.get("thu", ""), d.get("fri", ""), d.get("sat", ""), d.get("sun", ""), d.get("lunch_start", ""), d.get("lunch_end", ""), int(d.get("break_minutes", 30)), int(d.get("tolerance_minutes", 10)))
    c = db(); cur = c.execute("""INSERT INTO schedules(company_id,name,mon,tue,wed,thu,fri,sat,sun,lunch_start,lunch_end,break_minutes,tolerance_minutes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", vals); c.commit(); c.close(); return jsonify(id=cur.lastrowid), 201

@app.post("/api/employee")
def employee():
    u = current_user()
    if not u or u["role"] != "administrador": return jsonify(error="No autorizado"), 403
    d = request.json or {}
    c = db(); cur = c.execute("INSERT INTO employees(company_id,site_id,schedule_id,name,document,position) VALUES(?,?,?,?,?,?)", (u["company_id"], d.get("site_id"), d.get("schedule_id"), d["name"], d.get("document", ""), d.get("position", ""))); eid = cur.lastrowid
    username = (d.get("username") or d["name"].split()[0]).lower()
    c.execute("INSERT INTO users(company_id,employee_id,username,password_hash,role) VALUES(?,?,?,?,?)", (u["company_id"], eid, username, hashpw(d.get("password", "123456")), "empleado"))
    c.commit(); c.close(); return jsonify(id=eid, username=username), 201

@app.post("/api/incident")
def incident():
    u = current_user()
    if not u or u["role"] not in ("administrador", "supervisor"): return jsonify(error="No autorizado"), 403
    d = request.json or {}; c = db()
    c.execute("INSERT INTO incidents(employee_id,type,start_date,end_date,notes) VALUES(?,?,?,?,?)", (d["employee_id"], d["type"], d.get("start_date"), d.get("end_date"), d.get("notes", ""))); c.commit(); c.close()
    return jsonify(ok=True), 201

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
        row = c.execute("""SELECT e.*,si.latitude site_lat,si.longitude site_lon,si.radius_m,h.* FROM employees e
          LEFT JOIN sites si ON si.id=e.site_id LEFT JOIN schedules h ON h.id=e.schedule_id WHERE e.id=? AND e.company_id=?""", (u["employee_id"], u["company_id"])).fetchone()
        c.close()
        
        if not row: 
            return jsonify(error="Empleado no encontrado"), 404
            
        dist = 0.0
        valid = True
        
        if lat is not None and lon is not None and row["site_lat"] is not None and row["site_lon"] is not None:
            try:
                dist = hav(float(lat), float(lon), float(row["site_lat"]), float(row["site_lon"]))
                radius = row["radius_m"] if row["radius_m"] is not None else 200
                valid = dist <= radius
            except Exception as e:
                print(f"Aviso cálculo GPS: {e}")

        dt = datetime.now()
        status = "Registrada"
        late = 0
        
        if typ == "Entrada":
            days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
            period = row[days[dt.weekday()]] if days[dt.weekday()] in row.keys() else ""
            if period:
                start = int(period[:2]) * 60 + int(period[3:5])
                actual = dt.hour * 60 + dt.minute
                late = max(0, actual - start - int(row["tolerance_minutes"] or 0))
                status = "A tiempo" if late == 0 else f"Retardo {late} min"
        
        if not valid:
            status = f"{status} (Fuera de zona a {dist:.0f}m)"
        
        c = db()
        cur = c.execute("""INSERT INTO attendance(employee_id,event_type,event_time,latitude,longitude,distance_m,gps_valid,status,late_minutes) VALUES(?,?,?,?,?,?,?,?,?)""", (u["employee_id"], typ, dt.isoformat(timespec="seconds"), lat, lon, dist, int(valid), status, late))
        
        emp_info = c.execute("SELECT name, document FROM employees WHERE id=?", (u["employee_id"],)).fetchone()
        c.commit()
        c.close()

        if emp_info:
            try:
                sync_to_sheets(emp_info["name"], emp_info["document"], typ, dt.strftime("%Y-%m-%d %H:%M:%S"), status, late)
            except Exception as sheet_err:
                print(f"Aviso Google Sheets: {sheet_err}")

        return jsonify(id=cur.lastrowid, time=dt.strftime("%H:%M"), status=status, gps_valid=valid, late_minutes=late)
    except Exception as e:
        return jsonify(error=f"Excepción interna: {str(e)}"), 500

@app.get("/api/report/csv")
def export_csv():
    u = current_user()
    if not u or u["role"] != "administrador":
        return jsonify(error="No autorizado"), 403
    
    c = db()
    # Se incluyen a.latitude, a.longitude y a.distance_m en la consulta del reporte CSV
    rows = c.execute("""
        SELECT a.id, e.name as employee_name, e.document, s.name as site_name, 
               a.event_type, a.event_time, a.latitude, a.longitude, a.distance_m, a.status, a.late_minutes 
        FROM attendance a 
        JOIN employees e ON e.id = a.employee_id 
        LEFT JOIN sites s ON s.id = e.site_id 
        WHERE e.company_id = ? 
        ORDER BY a.id DESC
    """, (u["company_id"],)).fetchall()
    c.close()
    
    si = io.StringIO()
    cw = csv.writer(si)
    # Se añaden las columnas correspondientes en las cabeceras del CSV
    cw.writerow(["ID", "Empleado", "Documento", "Sede", "Evento", "Fecha y Hora", "Latitud", "Longitud", "Distancia (m)", "Estado", "Minutos Retardo"])
    for r in rows:
        cw.writerow([
            r["id"], r["employee_name"], r["document"], r["site_name"], 
            r["event_type"], r["event_time"], r["latitude"], r["longitude"], 
            f"{r['distance_m']:.1f}" if r["distance_m"] is not None else "", 
            r["status"], r["late_minutes"]
        ])
    
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=reporte_asistencia.csv"
    output.headers["Content-type"] = "text/csv; charset=utf-8"
    return output

init_db()

if __name__ == "__main__": 
    app.run(host="0.0.0.0", port=5000, debug=True)    
