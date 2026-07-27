# grid-watch — diseño

Fecha: 2026-07-26
Estado: aprobado, pendiente de plan de implementación

## 1. Problema

Un inversor solar off-grid (Growatt SPF 5000 ES) sigue alimentando la casa
cuando se corta la red pública, así que **el corte es invisible**: las luces
siguen encendidas y nadie se entera de que se está consumiendo batería. Hace
falta un aviso al celular cuando la red pública cae, y otro cuando vuelve.

## 2. Objetivo y alcance

Un servicio pequeño y autónomo que vigila el estado de la red pública tal como
lo reporta el inversor, y notifica al celular cuatro eventos:

1. Corte de red pública
2. Restablecimiento de la red (con la duración del corte)
3. Batería crítica durante un corte
4. El inversor dejó de reportar datos nuevos

**Fuera de alcance:** dashboards, históricos, gráficas, control del inversor,
integración con Home Assistant. Este proyecto avisa; no gestiona.

## 3. Decisiones y sus razones

| Decisión | Alternativa descartada | Por qué |
|---|---|---|
| Datos de la nube de Growatt (`server.growatt.com`) | Modbus RTU local por el puerto BMS | Cero hardware nuevo; latencia de 5-10 min aceptada como requisito |
| Ejecución en un VPS externo | En la casa (PC o Raspberry Pi) | Independiente del emplazamiento: si cae el internet de la casa, el VPS lo detecta como "el inversor dejó de reportar" en vez de quedarse ciego |
| Notificación por ntfy | Telegram, Pushover, SMS | Gratis, sin cuenta, prioridad máxima que salta el modo silencio, y autohospedable más adelante |
| Daemon único con máquina de estados | Script bajo cron/timer; Prometheus + Alertmanager | La lógica de detección merece ser código propio y testeable; el cron impide reintentos finos y Alertmanager es desproporcionado para un solo dato |

La casa mantiene el router alimentado por el inversor, así que durante un corte
normal sigue habiendo internet y el dongle sigue publicando en la nube. El caso
en que también cae el internet queda cubierto por el evento 4.

## 4. Arquitectura

```
grid_watch/
├── config.py      # carga TOML + variables de entorno, valida y falla temprano
├── source.py      # GrowattCloudSource.read() → Reading
├── detector.py    # función pura: (estado, Reading, ahora) → (estado, eventos)
├── notifier.py    # NtfySink.send(Event) → POST HTTP con reintentos
├── state.py       # persistencia atómica del estado en JSON
└── __main__.py    # loop: poll → detect → notify → persist, con backoff
```

**Flujo:** el loop pide una lectura a la fuente, se la pasa al detector junto
con el estado cargado de disco y la hora actual, recibe cero o más eventos, los
envía al sink y persiste el estado nuevo. Los eventos que el sink no consigue
entregar quedan encolados dentro del estado persistido y se reintentan.

### Fronteras

- **`Source`** — protocolo con un único método `read() -> Reading`. Hoy lo
  implementa `GrowattCloudSource`; una futura `ModbusSource` entra sin tocar el
  resto. Interfaz de un método: no es especulación cara.
- **`detect()`** — función pura. No abre sockets, no lee el reloj ni el disco;
  la hora entra como parámetro. Toda la lógica sutil (histéresis, flapping,
  arranque en frío) es testeable sin red.
- **`Sink`** — protocolo `send(Event)`. Hoy `NtfySink`.

## 5. Modelo de datos

```python
@dataclass(frozen=True)
class Reading:
    sample_time: datetime | None   # timestamp del lado del dispositivo
    grid_v: float
    grid_hz: float
    grid_power: float              # + import, - export
    bat_soc: float | None
    load_power: float
    pv_power: float
    ok: bool
    error: str | None
```

`Event` lleva `kind` (`grid_down`, `grid_restored`, `battery_critical`,
`inverter_silent`, `inverter_reporting`, `monitor_blind`), el `Reading` que lo
provocó, el instante en que se generó y un `id` derivado de tipo + timestamp de
muestra, para no duplicar avisos tras un reinicio.

El estado persistido contiene: estado de la red, marca de la muestra que lo
confirmó, inicio del corte en curso, si ya se avisó de batería crítica, si se
está en modo silencio, y la cola de eventos pendientes de entrega.

## 6. Lógica de detección

Estados: `UNKNOWN` (arranque), `GRID_OK`, `GRID_DOWN`; más un indicador
ortogonal de silencio.

**Señal primaria:** `grid_v` (el campo `vGrid` de `storageDetailBean`), con
histéresis: se declara caída por debajo de `grid_down_below` y sólo se declara
vuelta por encima de `grid_ok_above`. Por defecto, 68% y 82% de un
`grid_nominal_v` configurable (220 V en la instalación de referencia; 230 o 120
en otras).

**Confirmación por muestras distintas, no por lecturas.** La nube sirve la
misma muestra durante unos cinco minutos, así que exigir "dos lecturas
consecutivas" no confirma nada: sería leer dos veces el mismo dato. La
condición se confirma cuando se cumple **cualquiera de estas dos, la primera
que ocurra**:

- se ha observado en **dos muestras con `sample_time` distinto**, o
- han pasado `min_sustain_s` desde la primera muestra que la vio.

La segunda regla existe para el caso en que el `sample_time` no avance (ver
§7.2): sin ella, un inversor que repite la misma muestra bloquearía la
confirmación para siempre.

Un micro-corte resuelto antes de la confirmación no genera aviso, pero queda
contado en el estado.

**Latencia esperada.** Con `poll_interval_s` de 180 s y muestras de la nube
cada ~5 min, un corte se notifica típicamente entre 5 y 12 minutos después de
ocurrir. Sondear más rápido no adelanta nada — el dato no existe todavía — y
gasta cuota arriesgando el bloqueo de Cloudflare.

**Un fallo de la API nunca emite "corte".** Si la lectura falla, el estado de la
red no se toca; sólo avanza el reloj de obsolescencia. "No sé" y "no hay luz"
no se confunden nunca.

**Batería crítica** sólo dentro de un corte, cuando el SOC baja de
`soc_critical` (20% por defecto). Se emite una vez por apagón y se re-arma al
volver la red o cuando el SOC sube por encima de `soc_critical + 10`.

**Silencio del inversor:** si la muestra más reciente tiene más de
`stale_after_s` (20 min por defecto), se emite `inverter_silent` una sola vez;
al llegar datos nuevos, `inverter_reporting` con la duración del silencio.

**Arranque en frío:** si al iniciar ya hay corte, se notifica marcándolo como
estado inicial, salvo que el estado persistido indique que ese aviso ya salió.
Es preferible avisar de más que arrancar en mitad de un apagón y callar.

## 7. Dos supuestos que hay que validar contra el inversor real

Ambos tienen decidido su plan alternativo; se verifican antes de escribir el
detector, con el modo `--diagnose` (sondea e imprime lecturas crudas sin
notificar nada).

1. **`vGrid` puede valer 0 legítimamente.** Al ser off-grid, según el modo de
   salida configurado en el LCD el inversor puede ignorar la red y reportar
   `vGrid = 0` con la calle energizada; el detector avisaría entonces de un
   apagón perpetuo. Salvaguarda incorporada: si nunca ha observado `grid_v` por
   encima de `grid_ok_above`, no emite corte y registra que la señal no parece
   utilizable. Señales de respaldo, en orden: `freqGrid` y `pAcInPut`.
2. **El silencio exige un timestamp del dispositivo.** Si `storageDetailBean`
   no expone `lastUpdateTime` o equivalente, no se distingue "mudo" de "reporta
   lo mismo". Plan alternativo: considerar muestra nueva cualquier cambio en un
   contador monótono (energía acumulada del día).

## 8. Notificaciones

El topic de ntfy funciona como contraseña: quien lo conoce puede leer y
publicar. Será un nombre largo y aleatorio, vivirá sólo en el entorno del
servidor y nunca en el repositorio. Se soportan además token de autenticación y
URL de servidor propia, para autohospedar ntfy más adelante.

| Evento | Prioridad | Contenido |
|---|---|---|
| Corte de red | 5 (urgent) | hora de la muestra, SOC, consumo, PV |
| Vuelve la red | 3 (normal) | duración del corte, SOC final |
| Batería crítica | 5 (urgent) | SOC, consumo, tiempo transcurrido |
| Inversor mudo | 4 (high) | minutos sin datos, última lectura conocida |
| Vuelve a reportar | 3 (normal) | duración del silencio |
| Monitor ciego | 4 (high) | tiempo sin lecturas válidas y último error |

Si la entrega falla, el evento se encola en el estado persistido y se reintenta
con backoff. Cuando por fin sale, el mensaje indica el retraso en lugar de
fingir que acaba de ocurrir: un aviso de hace seis horas presentado como
reciente sería peor que no enviarlo.

## 9. Configuración y secretos

`config.toml` versionado con umbrales y tiempos (nada sensible). Los secretos
llegan sólo por variables de entorno: `GROWATT_USER`, `GROWATT_PASSWORD`,
`NTFY_TOPIC`, `NTFY_TOKEN`. El repositorio incluye `config.example.toml` y
`.env.example`; `.gitignore` cubre `.env`, `state.json` y `config.local.toml`.
La contraseña se redacta en logs y trazas: un traceback filtrando credenciales
en un issue de GitHub es un accidente demasiado fácil.

Parámetros configurables, con sus valores por defecto:

| Parámetro | Defecto | Significado |
|---|---|---|
| `poll_interval_s` | 180 | cada cuánto se consulta la nube |
| `grid_nominal_v` | 220 | tensión nominal de la instalación |
| `grid_down_below` | 68% del nominal | umbral de caída |
| `grid_ok_above` | 82% del nominal | umbral de recuperación (histéresis) |
| `min_sustain_s` | 300 | tiempo mínimo sosteniendo la condición |
| `soc_critical` | 20 | % de batería que dispara el aviso crítico |
| `stale_after_s` | 1200 | sin muestra nueva ⇒ inversor mudo |
| `blind_after_s` | 3600 | sin lectura válida ⇒ monitor ciego |
| `timezone` | `America/Lima` | zona horaria de los mensajes |

## 10. Errores

Tres familias, tratadas de forma distinta:

- **Fallos de la nube** (login rechazado, sesión caducada, respuesta no-JSON,
  403 de Cloudflare, timeout): re-login y backoff exponencial con techo de
  30 min. Nunca alteran el estado de la red. Si se acumulan más de
  `blind_after_s` sin una lectura válida, se envía `monitor_blind` una sola vez:
  un vigilante averiado que calla es peor que no tener vigilante.
- **Fallos de entrega**: encolar y reintentar.
- **Fallos de configuración**: abortar al arrancar con un mensaje claro, en
  lugar de correr degradado.

Tres detalles de la API de Growatt ya descubiertos y que hay que portar:
User-Agent de navegador (con el de la librería, Cloudflare responde 403),
timeout inyectado en la sesión de `requests` (sin él, un socket a medias cuelga
el proceso para siempre y el backoff nunca llega a activarse), y re-login
forzado cuando la respuesta deja de ser JSON.

## 11. Pruebas

El detector concentra la cobertura útil, con tablas de lecturas sintéticas y
sin red ni relojes: corte limpio, restablecimiento, micro-corte que no debe
alertar, flapping en el umbral, arranque en frío durante un apagón, batería
cruzando el umbral y re-armándose, muestra repetida por la nube que no debe
contar como confirmación, y transición a silencio y vuelta.

El loop se prueba con fuente y sink falsos, verificando lo que importa: que un
sink caído no pierde eventos y que un reinicio no los duplica.

CI mínima en GitHub Actions ejecutando pytest en cada push.

## 12. Despliegue

Servidor VPS con Debian: venv en `/opt/grid-watch`, unidad systemd con
`Restart=always` y `EnvironmentFile` en modo 0600, logs al journal.

## 13. Futuro

Cuando exista el hardware ESP32 + MAX485 sobre el puerto BMS del inversor,
`ModbusSource` sustituye a `GrowattCloudSource` sin tocar el detector, las
notificaciones ni el despliegue, y la latencia baja de minutos a segundos.
