<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ASISTENCIA V5</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
    <nav class="navbar navbar-dark bg-dark mb-4">
        <div class="container-fluid">
            <span class="navbar-brand mb-0 h1">ASISTENCIA V5</span>
            <div id="user-info" class="text-white"></div>
        </div>
    </nav>

    <div class="container pb-5">
        <div id="app"></div>
    </div>

    <script>
        let stateData = {};

        async function init() {
            const res = await fetch('/api/me');
            const data = await res.json();
            if (!data.authenticated) {
                renderLogin();
            } else {
                loadState();
            }
        }

        async function loadState() {
            const res = await fetch('/api/state');
            if (res.ok) {
                stateData = await res.json();
                renderDashboard();
            }
        }

        function renderLogin() {
            document.getElementById('user-info').innerHTML = '';
            document.getElementById('app').innerHTML = `
                <div class="row justify-content-center">
                    <div class="col-md-5">
                        <div class="card shadow-sm p-4">
                            <h2 class="mb-3">Bienvenido</h2>
                            <p class="text-muted">Acceso seguro a la plataforma</p>
                            <form id="login-form" onsubmit="handleLogin(event)">
                                <div class="mb-3">
                                    <label class="form-label">Usuario</label>
                                    <input type="text" id="username" class="form-control" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">Contraseña</label>
                                    <input type="password" id="password" class="form-control" required>
                                </div>
                                <button type="submit" class="btn btn-primary w-100">Iniciar sesión</button>
                            </form>
                            <div class="mt-3 small text-muted">
                                Demo administrador: admin / admin123<br>
                                Empleados demo: carlos / 123456 - maria / 123456 - juan / 123456
                            </div>
                        </div>
                    </div>
                </div>`;
        }

        async function handleLogin(e) {
            e.preventDefault();
            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;
            const res = await fetch('/api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password })
            });
            if (res.ok) {
                init();
            } else {
                const err = await res.json();
                alert(err.error || 'Error al iniciar sesión');
            }
        }

        async function handleLogout() {
            await fetch('/api/logout', { method: 'POST' });
            init();
        }

        async function registrarAsistencia(eventType) {
            // Intenta capturar la geolocalización del dispositivo si está disponible
            if (navigator.geolocation) {
                navigator.geolocation.getCurrentPosition(async (position) => {
                    await enviarMarcacion(eventType, position.coords.latitude, position.coords.longitude);
                }, async (error) => {
                    console.warn('GPS no disponible, enviando marcación sin coordenadas.');
                    await enviarMarcacion(eventType, null, null);
                }, { timeout: 10000 });
            } else {
                await enviarMarcacion(eventType, null, null);
            }
        }

        async function enviarMarcacion(eventType, lat, lon) {
            const res = await fetch('/api/mark', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ event_type: eventType, latitude: lat, longitude: lon })
            });
            if (res.ok) {
                const data = await res.json();
                alert(`¡Marcación exitosa! (${data.status} a las ${data.time})`);
                loadState();
            } else {
                const err = await res.json();
                alert(err.error || 'Error al registrar asistencia');
            }
        }

        function renderDashboard() {
            const u = stateData.user;
            document.getElementById('user-info').innerHTML = `
                <span>${u.username} (${u.role})</span>
                <button onclick="handleLogout()" class="btn btn-outline-light btn-sm ms-3">Cerrar sesión</button>
            `;

            let html = `<div class="card shadow-sm p-4 mb-4">
                <h3>Panel de Control - ${stateData.companies[0]?.name || 'Empresa'}</h3>
                <p class="text-muted">Bienvenido al sistema de control de asistencia.</p>
            </div>`;

            if (u.role === 'administrador') {
                html += `
                <div class="row">
                    <div class="col-md-6 mb-4">
                        <div class="card shadow-sm p-3 h-100">
                            <h4>Empleados Registrados</h4>
                            <ul class="list-group mt-3">
                                ${stateData.employees.map(e => `<li class="list-group-item d-flex justify-content-between align-items-center">${e.name} <span class="badge bg-primary">${e.position || 'Empleado'}</span></li>`).join('')}
                            </ul>
                        </div>
                    </div>
                    <div class="col-md-6 mb-4">
                        <div class="card shadow-sm p-3 h-100">
                            <h4>Sedes</h4>
                            <ul class="list-group mt-3">
                                ${stateData.sites.map(s => `<li class="list-group-item d-flex justify-content-between align-items-center">${s.name} <span class="badge bg-secondary">${s.radius_m}m radio</span></li>`).join('')}
                            </ul>
                        </div>
                    </div>
                </div>`;
            } else {
                html += `
                <div class="card shadow-sm p-4 mb-4 text-center bg-white">
                    <h4>Registro de Asistencia</h4>
                    <p class="text-muted">Haga clic en el botón correspondiente para marcar su jornada.</p>
                    <div class="d-flex justify-content-center gap-3 mt-3">
                        <button onclick="registrarAsistencia('Entrada')" class="btn btn-success btn-lg px-4">Registrar Entrada</button>
                        <button onclick="registrarAsistencia('Salida')" class="btn btn-danger btn-lg px-4">Registrar Salida</button>
                    </div>
                </div>`;
            }

            html += `
            <div class="card shadow-sm p-3">
                <h4>Últimos Registros de Asistencia</h4>
                <div class="table-responsive mt-3">
                    <table class="table table-striped">
                        <thead><tr><th>Empleado</th><th>Evento</th><th>Hora</th><th>Estado</th></tr></thead>
                        <tbody>
                            ${stateData.attendance && stateData.attendance.length > 0 ? stateData.attendance.map(a => `<tr><td>${a.employee_name}</td><td>${a.event_type}</td><td>${a.event_time}</td><td><span class="badge bg-success">${a.status || 'Registrada'}</span></td></tr>`).join('') : '<tr><td colspan="4" class="text-center text-muted">No hay registros recientes</td></tr>'}
                        </tbody>
                    </table>
                </div>
            </div>`;

            document.getElementById('app').innerHTML = html;
        }

        document.addEventListener('DOMContentLoaded', init);
    </script>
</body>
</html>
