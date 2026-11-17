# Informes diarios de retiros

Bot exclusivo de informes, independiente de `TELEGRAM_BOT_TOKEN` y de las alertas existentes.
Se ejecuta todos los días a las **23:59 de Ecuador (UTC−5)**, incluso el domingo, cuando puede indicar que no hubo ingresos.

## Configuración en Render

- `INFORMES_TELEGRAM_ACTIVO=true`
- `INFORMES_TELEGRAM_BOT_TOKEN`: secreto del bot nuevo, únicamente en Environment.
- `INFORMES_TELEGRAM_CHAT_IDS`: IDs autorizados, separados por coma. Cada destinatario debe iniciar el bot.
- `INFORMES_TELEGRAM_DESDE`: primera fecha autorizada, formato `AAAA-MM-DD`.
- Opcionales: `INFORMES_TELEGRAM_DIR` (por defecto `/var/data/informes_telegram`) e `INFORMES_TELEGRAM_UPLOADS` (por defecto `/var/data/uploads`).

Requiere el disco persistente del servicio y un proceso en ejecución. No requiere un cron de pago adicional ni que el PC del usuario esté encendido. El inicio está desactivado salvo configuración explícita. Cada proceso puede iniciar su hilo; un bloqueo del sistema operativo permite un solo emisor por disco.

## Contenido y criterios

1. Resumen de ingresos del día, separado por estado; montos conservados por el sistema al momento de consultar.
2. Informe HTML legible y CSV con usuario, ID, transacción, fecha, banco, monto, estado, cobrador, celular del retiro, banco de cobro, liquidación, motivo, historial y referencias de comprobantes.
3. ZIP con el informe, CSV y comprobantes originales separados por ID. Si hace falta se divide en partes inferiores a 45 MB; se extraen juntas en una carpeta.
4. Sección separada para retiros anteriores cuyo historial tuvo movimientos ese día. No se mezclan sus montos con los ingresos nuevos.
5. Papelera aparte, fuera de totales. Pruebas excluidas. No se cuenta un saldado o fusionado como un retiro completado nuevo. Las liquidaciones de caja se muestran aparte.

La selección consulta PostgreSQL mediante una transacción independiente de solo lectura y consistente. No importa `app.py` desde la CLI ni modifica registros financieros. No reconstruye registros ausentes de PostgreSQL ni deduce estados a partir de imágenes. Un envío retrasado muestra la hora real de la consulta; no finge recuperar el estado histórico de medianoche.

El informe no añade PIN, claves, contraseñas ni tokens a sus columnas. Los comprobantes originales se incluyen tal como están guardados. Las rutas se validan dentro de uploads. Las imágenes ausentes o demasiado grandes se identifican expresamente; no se ocultan.

## Entregas y recuperación

En el disco se conserva una instantánea por fecha, hashes de los adjuntos y `entregas.sqlite3`, independiente de la base del negocio. Un reinicio recupera fechas pendientes desde la fecha configurada. Las partes con respuesta exitosa de Telegram no se reenvían. Un destinatario bloqueado no impide entregar al otro.

Las copias de adjuntos generadas para enviar se conservan siete días después de su creación y solo se limpian cuando todos los destinatarios configurados las recibieron. Se mantienen el JSON del corte, el resumen, el manifiesto y la constancia de entrega. Los comprobantes originales de uploads no se borran. Antes de generar archivos se reserva un mínimo de 50 MB libres para el sistema. Los nuevos destinatarios deben configurarse con una fecha de inicio vigente si se desea evitar intentar recuperar adjuntos ya vencidos.

Un timeout o caída después de iniciar una petición puede haber entregado el mensaje aunque no haya llegado la respuesta. Esa parte queda `incierto` o `enviando` y **no se reenvía automáticamente**: verificar primero el chat, porque Telegram no ofrece una clave de idempotencia para estos envíos. Un rechazo explícito se puede reintentar después de cinco minutos. Los errores se registran sin el token.

## Verificación operativa

```bash
python -m reportes_diarios --comprobar
python -m reportes_diarios --prueba
```

`--comprobar` consulta el bot y los destinatarios; no envía mensajes. `--prueba` sí envía los datos actuales a los destinatarios configurados y los identifica como PRUEBA / CORTE PARCIAL. Tiene un lote separado y no sustituye el informe automático de las 23:59.

Antes de reenviar una parte incierta, consultar el chat y el registro de entregas. No borrar el historial de entregas para forzar un reenvío.

Pruebas sin red ni producción: `python -B -m unittest discover -s tests -p test_reportes_diarios.py -v`.
