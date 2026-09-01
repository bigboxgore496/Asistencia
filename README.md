# ASISTENCIA OMMA

V5 del sistema genérico de asistencia.

## Novedades V5
- PWA: puede instalarse desde el navegador en el celular.
- Service Worker básico para carga de recursos.
- Interfaz móvil conservada y optimizada.
- Acciones rápidas para actualizar GPS y refrescar.
- Exportación CSV de las marcaciones visibles.
- Base V4 conservada: login, roles, multiempresa, sedes, horarios, GPS y novedades.

## Demo
Administrador: admin / admin123
Empleado: carlos / 123456
Empleado: maria / 123456
Empleado: juan / 123456

## Ejecutar
pip install -r requirements.txt
python app.py

Abrir:
http://localhost:5000

Para probar la experiencia móvil:
1. Abrir desde un celular o usar las herramientas de dispositivo del navegador.
2. Conceder permiso de ubicación.
3. Iniciar sesión como empleado.
4. Marcar entrada/salida.
5. En un navegador compatible, usar "Agregar a pantalla de inicio".

## Producción
Antes de uso real deben incorporarse HTTPS, PostgreSQL, cookies seguras, CSRF, rate limiting,
auditoría, recuperación de contraseña, cifrado/gestión de secretos y políticas de retención de ubicación.
