@app.get("/api/report/csv")
def export_csv():
    u = current_user()
    if not u or u["role"] != "administrador":
        return jsonify(error="No autorizado"), 403
    
    c = db()
    rows = c.execute("""
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
    """, (u["company_id"],)).fetchall()
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
                "total_extras": 0
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

    si = io.StringIO()
    # Usamos punto y coma (;) como delimitador para compatibilidad perfecta con Excel en español
    cw = csv.writer(si, delimiter=';', quoting=csv.QUOTE_MINIMAL)
    cw.writerow(["ID", "Empleado", "Documento", "Area", "Sede", "Fecha", "Hora de ingreso", "Hora de Salida", "Ubicacion Google Maps", "Distancia (m)", "Minutos Retardo", "Total Extras"])
    
    for key, r in daily_records.items():
        lat = r["lat"]
        lon = r["lon"]
        if lat is not None and lon is not None:
            try:
                maps_url = f"https://www.google.com/maps?q={float(lat)},{float(lon)}"
                location_field = f'=HIPERVINCULO("{maps_url}"; "Ver en Mapa")'
            except Exception:
                location_field = ""
        else:
            location_field = ""

        cw.writerow([
            r["id"], r["employee_name"], r["document"], r["area_name"], r["site_name"], 
            r["date"], r["entrada_time"], r["salida_time"], location_field, 
            f"{r['distance_m']:.0f}" if r["distance_m"] is not None else "", 
            r["late_minutes"], r["total_extras"]
        ])
    
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=reporte_asistencia_consolidado.csv"
    output.headers["Content-type"] = "text/csv; charset=utf-8"
    return output
