
from flask import Flask, request, jsonify, render_template, session
import sqlite3, math, hashlib, secrets
from pathlib import Path
from datetime import datetime

app=Flask(__name__)
app.secret_key=secrets.token_hex(32)
DB=Path(__file__).with_name("asistencia.db")

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON"); return c

def hashpw(p): return hashlib.sha256(p.encode()).hexdigest()

def init_db():
    c=db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS sites(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER NOT NULL,name TEXT NOT NULL,latitude REAL NOT NULL,longitude REAL NOT NULL,radius_m INTEGER DEFAULT 100,FOREIGN KEY(company_id) REFERENCES companies(id));
    CREATE TABLE IF NOT EXISTS schedules(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER NOT NULL,name TEXT NOT NULL,mon TEXT DEFAULT '',tue TEXT DEFAULT '',wed TEXT DEFAULT '',thu TEXT DEFAULT '',fri TEXT DEFAULT '',sat TEXT DEFAULT '',sun TEXT DEFAULT '',lunch_start TEXT DEFAULT '',lunch_end TEXT DEFAULT '',break_minutes INTEGER DEFAULT 30,tolerance_minutes INTEGER DEFAULT 10,FOREIGN KEY(company_id) REFERENCES companies(id));
    CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER NOT NULL,site_id INTEGER,schedule_id INTEGER,name TEXT NOT NULL,document TEXT,position TEXT,status TEXT DEFAULT 'Activo',FOREIGN KEY(company_id) REFERENCES companies(id),FOREIGN KEY(site_id) REFERENCES sites(id),FOREIGN KEY(schedule_id) REFERENCES schedules(id));
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id INTEGER,employee_id INTEGER,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL,active INTEGER DEFAULT 1,FOREIGN KEY(company_id) REFERENCES companies(id),FOREIGN KEY(employee_id) REFERENCES employees(id));
    CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,event_type TEXT NOT NULL,event_time TEXT NOT NULL,latitude REAL,longitude REAL,distance_m REAL,gps_valid INTEGER DEFAULT 0,status TEXT,late_minutes INTEGER DEFAULT 0,worked_minutes INTEGER DEFAULT 0,overtime_minutes INTEGER DEFAULT 0,FOREIGN KEY(employee_id) REFERENCES employees(id));
    CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,type TEXT NOT NULL,start_date TEXT,end_date TEXT,notes TEXT,status TEXT DEFAULT 'Pendiente',FOREIGN KEY(employee_id) REFERENCES employees(id));
    """)
    if c.execute("SELECT COUNT(*) FROM companies").fetchone()[0]==0:
        c.execute("INSERT INTO companies(name) VALUES(?)",("Empresa Demo S.A.S.",)); co=c.lastrowid
        c.execute("INSERT INTO sites(company_id,name,latitude,longitude,radius_m) VALUES(?,?,?,?,?)",(co,6.2442,-75.5812,150,"Sede Principal")); site=c.lastrowid
        c.execute("""INSERT INTO schedules(company_id,name,mon,tue,wed,thu,fri,sat,sun,lunch_start,lunch_end,break_minutes,tolerance_minutes)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(co,"Administrativo","07:00-17:00","07:00-17:00","07:00-17:00","07:00-17:00","07:00-13:00","","","13:00","14:00",30,10)); sch=c.lastrowid
        demo=[("Carlos Rodríguez","1.234.567.890","Auxiliar Administrativo"),("María González","1.098.765.432","Contadora"),("Juan Pérez","1.112.223.334","Operario")]
        for n,d,p in demo:
            c.execute("INSERT INTO employees(company_id,site_id,schedule_id,name,document,position) VALUES(?,?,?,?,?,?)",(co,site,sch,n,d,p))
            eid=c.lastrowid
            c.execute("INSERT INTO users(company_id,employee_id,username,password_hash,role) VALUES(?,?,?,?,?)",(co,eid,n.split()[0].lower(),hashpw("123456"),"empleado"))
        c.execute("INSERT INTO users(company_id,username,password_hash,role) VALUES(?,?,?,?)",(co,"admin",hashpw("admin123"),"administrador"))
    c.commit(); c.close()

def current_user():
    uid=session.get("uid")
    if not uid:return None
    c=db(); u=c.execute("SELECT * FROM users WHERE id=? AND active=1",(uid,)).fetchone(); c.close()
    return dict(u) if u else None

def hav(lat1,lon1,lat2,lon2):
    R=6371000;p1=math.radians(lat1);p2=math.radians(lat2);dp=math.radians(lat2-lat1);dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R*2*math.atan2(math.sqrt(a),math.sqrt(1-a))

@app.route("/")
def index(): return render_template("index.html", user=current_user())

@app.post("/api/login")
def login():
    d=request.json or {}; u=(d.get("username") or "").strip().lower(); p=d.get("password") or ""
    c=db(); row=c.execute("SELECT * FROM users WHERE lower(username)=? AND password_hash=? AND active=1",(u,hashpw(p))).fetchone(); c.close()
    if not row:return jsonify(error="Usuario o contraseña incorrectos"),401
    session["uid"]=row["id"]; return jsonify(user=dict(row))

@app.post("/api/logout")
def logout(): session.clear(); return jsonify(ok=True)

@app.get("/api/me")
def me():
    u=current_user()
    if not u:return jsonify(authenticated=False)
    return jsonify(authenticated=True,user=u)

@app.get("/api/state")
def state():
    u=current_user()
    if not u:return jsonify(error="No autenticado"),401
    c=db(); cid=u["company_id"]
    companies=[dict(x) for x in c.execute("SELECT * FROM companies WHERE id=?",(cid,))]
    sites=[dict(x) for x in c.execute("SELECT * FROM sites WHERE company_id=?",(cid,))]
    schedules=[dict(x) for x in c.execute("SELECT * FROM schedules WHERE company_id=?",(cid,))]
    employees=[dict(x) for x in c.execute("""SELECT e.*,s.name site_name,h.name schedule_name FROM employees e LEFT JOIN sites s ON s.id=e.site_id LEFT JOIN schedules h ON h.id=e.schedule_id WHERE e.company_id=? ORDER BY e.id""",(cid,))]
    attendance=[dict(x) for x in c.execute("""SELECT a.*,e.name employee_name FROM attendance a JOIN employees e ON e.id=a.employee_id WHERE e.company_id=? ORDER BY a.id DESC LIMIT 100""",(cid,))]
    incidents=[dict(x) for x in c.execute("""SELECT i.*,e.name employee_name FROM incidents i JOIN employees e ON e.id=i.employee_id WHERE e.company_id=? ORDER BY i.id DESC""",(cid,))]
    c.close(); return jsonify(companies=companies,sites=sites,schedules=schedules,employees=employees,attendance=attendance,incidents=incidents,user=u)

@app.post("/api/company")
def company():
    u=current_user()
    if not u or u["role"]!="administrador":return jsonify(error="No autorizado"),403
    name=(request.json or {}).get("name","").strip()
    if not name:return jsonify(error="Nombre requerido"),400
    c=db(); cur=c.execute("INSERT INTO companies(name) VALUES(?)",(name,)); c.commit(); c.close(); return jsonify(id=cur.lastrowid),201

@app.post("/api/site")
def site():
    u=current_user()
    if not u or u["role"]!="administrador":return jsonify(error="No autorizado"),403
    d=request.json or {}
    try: vals=(u["company_id"],d["name"],float(d["latitude"]),float(d["longitude"]),int(d.get("radius_m",100)))
    except:return jsonify(error="Datos inválidos"),400
    c=db(); cur=c.execute("INSERT INTO sites(company_id,name,latitude,longitude,radius_m) VALUES(?,?,?,?,?)",vals); c.commit(); c.close(); return jsonify(id=cur.lastrowid),201

@app.post("/api/schedule")
def schedule():
    u=current_user()
    if not u or u["role"]!="administrador":return jsonify(error="No autorizado"),403
    d=request.json or {}
    vals=(u["company_id"],d["name"],d.get("mon",""),d.get("tue",""),d.get("wed",""),d.get("thu",""),d.get("fri",""),d.get("sat",""),d.get("sun",""),d.get("lunch_start",""),d.get("lunch_end",""),int(d.get("break_minutes",30)),int(d.get("tolerance_minutes",10)))
    c=db(); cur=c.execute("""INSERT INTO schedules(company_id,name,mon,tue,wed,thu,fri,sat,sun,lunch_start,lunch_end,break_minutes,tolerance_minutes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",vals); c.commit(); c.close(); return jsonify(id=cur.lastrowid),201

@app.post("/api/employee")
def employee():
    u=current_user()
    if not u or u["role"]!="administrador":return jsonify(error="No autorizado"),403
    d=request.json or {}
    c=db(); cur=c.execute("INSERT INTO employees(company_id,site_id,schedule_id,name,document,position) VALUES(?,?,?,?,?,?)",(u["company_id"],d.get("site_id"),d.get("schedule_id"),d["name"],d.get("document",""),d.get("position",""))); eid=cur.lastrowid
    username=(d.get("username") or d["name"].split()[0]).lower()
    c.execute("INSERT INTO users(company_id,employee_id,username,password_hash,role) VALUES(?,?,?,?,?)",(u["company_id"],eid,username,hashpw(d.get("password","123456")),"empleado"))
    c.commit(); c.close(); return jsonify(id=eid,username=username),201

@app.post("/api/incident")
def incident():
    u=current_user()
    if not u or u["role"] not in ("administrador","supervisor"):return jsonify(error="No autorizado"),403
    d=request.json or {}; c=db()
    c.execute("INSERT INTO incidents(employee_id,type,start_date,end_date,notes) VALUES(?,?,?,?,?)",(d["employee_id"],d["type"],d.get("start_date"),d.get("end_date"),d.get("notes",""))); c.commit(); c.close()
    return jsonify(ok=True),201

@app.post("/api/mark")
def mark():
    u=current_user()
    if not u or u["role"]!="empleado" or not u["employee_id"]:return jsonify(error="Solo un empleado autenticado puede marcar"),403
    d=request.json or {}; typ=d.get("event_type"); lat=d.get("latitude"); lon=d.get("longitude")
    if typ not in ("Entrada","Salida"):return jsonify(error="Tipo inválido"),400
    c=db(); row=c.execute("""SELECT e.*,si.latitude site_lat,si.longitude site_lon,si.radius_m,h.* FROM employees e
      LEFT JOIN sites si ON si.id=e.site_id LEFT JOIN schedules h ON h.id=e.schedule_id WHERE e.id=? AND e.company_id=?""",(u["employee_id"],u["company_id"])).fetchone()
    c.close()
    if not row:return jsonify(error="Empleado no encontrado"),404
    dist=None; valid=False
    if lat is not None and lon is not None:
        dist=hav(float(lat),float(lon),row["site_lat"],row["site_lon"]); valid=dist<=row["radius_m"]
        if not valid:return jsonify(error=f"Fuera de la zona autorizada ({dist:.0f} m)"),403
    dt=datetime.now(); status="Registrada"; late=0
    if typ=="Entrada":
        days=["mon","tue","wed","thu","fri","sat","sun"]; period=row[days[dt.weekday()]]
        if period:
            start=int(period[:2])*60+int(period[3:5]); actual=dt.hour*60+dt.minute; late=max(0,actual-start-int(row["tolerance_minutes"] or 0))
            status="A tiempo" if late==0 else f"Retardo {late} min"
    c=db(); cur=c.execute("""INSERT INTO attendance(employee_id,event_type,event_time,latitude,longitude,distance_m,gps_valid,status,late_minutes) VALUES(?,?,?,?,?,?,?,?,?)""",(u["employee_id"],typ,dt.isoformat(timespec="seconds"),lat,lon,dist,int(valid),status,late)); c.commit(); c.close()
    return jsonify(id=cur.lastrowid,time=dt.strftime("%H:%M"),status=status,gps_valid=valid,late_minutes=late)

if __name__=="__main__": init_db(); app.run(host="0.0.0.0",port=5000,debug=True)
