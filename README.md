# Revachol Radiobot

Bot configurado con el dataset de los diálogos del juego [Disco Elysium](https://discoelysium.com/)

Pensado para correr desatendido en cualquier máquina Linux. Solo hace falta tener instaladas las
dependencias (Python y Git) y el código de este repositorio.

Nació para [@discoelysium_es](https://x.com/discoelysium_es)

> [!WARNING]
> Usar la API interna de X **no está autorizado** por sus condiciones de uso y la cuenta puede ser
> limitada o suspendida. Úsalo con cuentas dedicadas marcadas como *automatizadas*
> (Ajustes → Tu cuenta → Información de la cuenta → Automatización)
> Este proyecto no está afiliado a X Corp.

- Publica a través de la API interna de x.com con [twifork](https://github.com/PawiX25/twifork)
  (fork mantenido de *twikit*) usando las cookies de una sesión real del navegador.
- No repite entradas: elige al azar entre las que aún no se han publicado y lleva el estado en SQLite.
- Cuenta caracteres como X, los textos de más de 280 se publican en dos tuits
  `(1/2)` `(2/2)`, cortando entre frases; solo si no es posible corta en una coma o entre palabras.
- Un **timer de systemd** (`revachol-radiobot@discoelysium_es.timer`): un tuit cada 90 minutos con un
  desfase aleatorio de hasta 10 minutos.
- Se ejecuta con un **usuario de servicio** sin login, que solo puede escribir en la carpeta del bot.
- Avisa por **Telegram** (chat, grupo o tema de un supergrupo) cuando X bloquea la cuenta, caducan las
  cookies, X la limita o algo falla, con el motivo y qué hacer, sin repetir el aviso cada 90 minutos.
  Cuando vuelve a publicar con normalidad, avisa con un ✅.

## Estructura en el servidor

```
/opt/revachol-radiobot/                 root:root                     0755
├── app/                                el repositorio (git clone)
├── venv/                               entorno virtual de Python
├── common.env                          root:revachol-radiobot        0640  avisos por Telegram
└── bots/                               root:revachol-radiobot        0750
    └── discoelysium_es/                revachol-radiobot             0750  carpeta del bot (su arroba)
        ├── bot.env                     root:revachol-radiobot        0640  config del bot
        ├── dataset.json                revachol-radiobot             0640
        ├── cookies.json                revachol-radiobot             0600  sesión de X
        └── state.db                    revachol-radiobot             0600  qué se ha publicado

/etc/systemd/system/revachol-radiobot@.service   servicio (la instancia es la arroba del bot)
/etc/systemd/system/revachol-radiobot@.timer     un tuit cada 90 minutos
/usr/local/bin/revachol-radiobot                 revachol-radiobot discoelysium_es <comando>
```

El servicio corre como el usuario `revachol-radiobot` (de sistema, sin contraseña ni shell). systemd monta
todo el sistema en solo lectura (`ProtectSystem=strict`) excepto la carpeta del bot
(`ReadWritePaths=/opt/revachol-radiobot/bots/%i`). El código y el venv pertenecen a root, así que el
servicio no puede modificarlos.

## Instalación

### Requisitos

- Linux con systemd y `apt` (Debian, Ubuntu o derivadas).
- Python 3.10 o superior y Git.
- El código de este repositorio.

El resto de dependencias (entorno virtual, librerías de Python, usuario de servicio y unidades de
systemd) las instala `deploy/install.sh`.

**1. Dependencias** (como root):

```bash
apt-get update
apt-get install -y git python3 python3-venv ca-certificates
```

Opcional: la rejilla de 90 minutos sigue la zona horaria del sistema
(`timedatectl set-timezone Europe/Madrid`).

**2. Descargar el código y revisar el instalador:**

```bash
git clone https://github.com/enkirro/revachol-radiobot.git /opt/revachol-radiobot/app
less /opt/revachol-radiobot/app/deploy/install.sh
```

`install.sh` se ejecuta como root, así que léelo antes de lanzarlo (como cualquier script que te pidan
ejecutar con privilegios). Este es todo lo que hace, en orden:

| Paso | Qué hace |
|---|---|
| Comprobaciones | Valida el nombre de la cuenta (`--bot`), que se ejecuta como root y que el sistema tiene `apt` y systemd. Si algo falla, se para sin tocar nada |
| Paquetes del sistema | `apt-get install` de `python3`, `python3-venv`, `python3-pip`, `ca-certificates`, `git` y `sqlite3` |
| Usuario de servicio | Si no existe, crea el usuario de sistema `revachol-radiobot`: sin contraseña, sin shell (`/usr/sbin/nologin`) y sin carpeta personal |
| Carpetas | Crea `/opt/revachol-radiobot` (de root) y `/opt/revachol-radiobot/bots` (root, grupo del servicio, `0750`) |
| Entorno de Python | Crea `/opt/revachol-radiobot/venv` e instala en él este proyecto y sus dependencias desde PyPI (sobre todo `twifork`). Si la versión de `twifork` publicada en PyPI no carga, instala la última de [su repositorio](https://github.com/PawiX25/twifork) |
| Configuración | Copia las plantillas a `/opt/revachol-radiobot/common.env` y `bots/<cuenta>/bot.env` **solo si no existen**; nunca sobrescribe tu configuración |
| Carpeta del bot | Crea `bots/<cuenta>/` con dueño `revachol-radiobot` y permisos `0750`. Avisa si falta el dataset |
| Comando | Copia `deploy/revachol-radiobot-cli.sh` a `/usr/local/bin/revachol-radiobot` |
| systemd | Copia `revachol-radiobot@.service` y `revachol-radiobot@.timer` a `/etc/systemd/system/` y recarga systemd. **No activa el timer** salvo que pases `--enable` |

No abre puertos ni modifica otros servicios. Aparte de los paquetes de `apt` y del usuario de servicio,
solo escribe en `/opt/revachol-radiobot`, `/usr/local/bin/revachol-radiobot` y
`/etc/systemd/system/revachol-radiobot@.*`. Se puede ejecutar
varias veces: actualiza el código y las unidades sin tocar la configuración, las cookies, el dataset
ni el estado.

**3. Instalar y crear el bot:**

```bash
/opt/revachol-radiobot/app/deploy/install.sh --bot discoelysium_es
```

**4. Copiar el dataset** (no se incluye en el repositorio, ver [Dataset](#dataset)):

```bash
install -o revachol-radiobot -g revachol-radiobot -m 0640 dataset.json \
  /opt/revachol-radiobot/bots/discoelysium_es/dataset.json
```

**5. Guardar las cookies de la sesión** (ver [Cookies de sesión](#cookies-de-sesión)):

```bash
revachol-radiobot discoelysium_es cookies set
```

**6. Probar y activar:**

```bash
revachol-radiobot discoelysium_es check            # dataset + cookies + sesión de X
revachol-radiobot discoelysium_es preview -n 5     # cómo quedarían 5 entradas al azar
revachol-radiobot discoelysium_es post --dry-run   # simula una ejecución sin publicar ni tocar el estado
systemctl start revachol-radiobot@discoelysium_es.service      # un tuit real
journalctl -u revachol-radiobot@discoelysium_es -n 20
systemctl enable --now revachol-radiobot@discoelysium_es.timer
systemctl list-timers 'revachol-radiobot@*'
```

### Actualizar

```bash
cd /opt/revachol-radiobot/app && git pull && ./deploy/install.sh
```

No toca la configuración, las cookies, el dataset ni el estado del bot. Si quieres revisar los cambios
antes de aplicarlos: `git fetch && git log -p HEAD..origin/main`, y después `git merge && ./deploy/install.sh`.

### Desinstalar

```bash
systemctl disable --now revachol-radiobot@discoelysium_es.timer
rm -f /etc/systemd/system/revachol-radiobot@.service /etc/systemd/system/revachol-radiobot@.timer \
      /usr/local/bin/revachol-radiobot
systemctl daemon-reload
userdel revachol-radiobot
rm -rf /opt/revachol-radiobot    # borra también dataset, cookies y estado
```

## Cookies de sesión

X retiró el login por usuario y contraseña para clientes no oficiales, así que revachol-radiobot usa las cookies
de una sesión iniciada en el navegador. Solo hacen falta dos: `auth_token` y `ct0`.

1. Inicia sesión en <https://x.com> con la cuenta del bot. Usa una ventana o perfil aparte y **no cierres
   sesión** después, porque cerrarla invalida las cookies.
2. Abre las DevTools (F12) → *Application* (Chrome) o *Almacenamiento* (Firefox) → *Cookies* → `https://x.com`.
3. Copia los valores de `auth_token` y `ct0`.
4. En el servidor, ejecuta `revachol-radiobot discoelysium_es cookies set` y pégalos. No se muestran en pantalla.

Si X rota `ct0`, el bot guarda el valor nuevo tras cada publicación correcta. Cuando la sesión caduca, el
bot sale con código 2 y envía un aviso; basta con repetir estos pasos.

## Configuración

Todo se configura con variables `RADIOBOT_*` en dos ficheros:

- **`/opt/revachol-radiobot/common.env`**: los avisos por Telegram. Ver
  [`deploy/common.env.example`](deploy/common.env.example).
- **`/opt/revachol-radiobot/bots/discoelysium_es/bot.env`**: la configuración del bot. Tiene prioridad
  sobre `common.env`. Ver [`.env.example`](.env.example).

| Variable | Por defecto | Descripción |
|---|---|---|
| `RADIOBOT_TEMPLATE` | `{author}: {text}` | Formato del tuit |
| `RADIOBOT_AUTHOR_FIELD` / `RADIOBOT_TEXT_FIELD` | `Languages` / `Translation` | Campos del JSON |
| `RADIOBOT_MAX_THREAD_PARTS` | `2` | Máximo de tuits por entrada; las que no caben se omiten |
| `RADIOBOT_THREAD_NUMBERING` | `true` | Añadir `(1/2)` a cada parte |
| `RADIOBOT_RECYCLE` | `true` | Al agotar el dataset, empezar otra vuelta |
| `RADIOBOT_IMPERSONATE` | `chrome124` | Huella TLS de navegador (evita 403 de Cloudflare) |
| `RADIOBOT_PROXY` | — | Proxy HTTP/SOCKS opcional |
| `RADIOBOT_TELEGRAM_BOT_TOKEN` / `RADIOBOT_TELEGRAM_CHAT_ID` | — | Avisos por Telegram |
| `RADIOBOT_NOTIFY_COOLDOWN_HOURS` | `12` | Horas mínimas entre dos avisos del mismo tipo |

Para cambiar la frecuencia: `systemctl edit revachol-radiobot@discoelysium_es.timer`.

### Avisos por Telegram

Funciona con un chat privado, un grupo o un **supergrupo con temas**.

1. Crea un bot con [@BotFather](https://t.me/BotFather) (`/newbot`), copia el token y añádelo al
   grupo con permiso para escribir (en el tema que quieras, si tiene temas).
2. Obtén los IDs: copia el enlace de cualquier mensaje del tema
   (clic derecho → *Copiar enlace*), por ejemplo `https://t.me/c/1234567890/55/123`:
   - `chat_id` = `-100` + el primer número → `-1001234567890`
   - `thread_id` = el segundo número → `55` (si el mensaje está en *General*, el enlace solo tiene
     dos números y no hace falta `thread_id`)
3. Añade a `/opt/revachol-radiobot/common.env`:
   ```bash
   RADIOBOT_TELEGRAM_BOT_TOKEN=123456789:AA...
   RADIOBOT_TELEGRAM_CHAT_ID=-1001234567890
   RADIOBOT_TELEGRAM_THREAD_ID=55
   ```
4. Prueba: `revachol-radiobot discoelysium_es notify-test`. Si falla, el mensaje indica la causa (chat o
   tema incorrecto, falta de permisos, grupo migrado a supergrupo...).

| Aviso | Cuándo |
|---|---|
| ⚠️ X no acepta la sesión del bot | Captcha/bloqueo, suspensión, cookies caducadas o de otra cuenta. Incluye el motivo y qué hacer en cada caso |
| ⚠️ X ha rechazado la sesión al publicar | Lo mismo, pero detectado al enviar el tuit |
| ⚠️ No se puede iniciar el cliente de X | Falta o está mal `cookies.json` |
| ⚠️ X ha limitado la cuenta | Error 429 o límite diario, con la hora de fin si X la indica |
| ⚠️ Error publicando | X rechaza el tuit o fallan los 3 intentos (red o servidor de X) |
| ⚠️ Hilo publicado a medias | Sale la parte 1 de una frase larga pero falla la 2 |
| ⚠️ No se pudo publicar | X rechaza 3 frases seguidas como duplicadas |
| ✅ Vuelve a publicar con normalidad | Primer tuit correcto tras cualquiera de los anteriores, con enlace |

Cada tipo de aviso se repite como mucho cada `RADIOBOT_NOTIFY_COOLDOWN_HOURS` (12 h por defecto).
No se avisa de fallos que se resuelven solos con el reintento (p. ej. un 503 de X).

## Dataset

**El repositorio no incluye ningún dataset.** Los textos de *Disco Elysium* son propiedad de ZA/UM, así
que cada uno debe extraerlos de su propia copia del juego.

### Formato

Una lista JSON de objetos. Solo se usan los dos campos configurados (`RADIOBOT_AUTHOR_FIELD` y
`RADIOBOT_TEXT_FIELD`); el resto se ignora:

```json
[
  {
    "actorId": 395,
    "Languages": "Kim Kitsuragi",
    "dialogLong": "\"Right. Now let's get to it,\" the lieutenant nods.",
    "Translation": "\"Bien. Ahora, en marcha\", el teniente asiente."
  }
]
```

| Campo | Uso |
|---|---|
| `Languages` | Nombre del personaje que habla (antes de los dos puntos del tuit) |
| `Translation` | Frase en español, que es lo que se publica |
| `dialogLong` | Original en inglés. No se usa |
| `actorId` | ID del personaje en el juego. No se usa |

El tuit queda como `Kim Kitsuragi: "Bien. Ahora, en marcha", el teniente asiente.` Las entradas con el
mismo texto final se publican una sola vez. Hay un ejemplo en
[`data/dataset.example.json`](data/dataset.example.json).

### Cómo extraer los diálogos del juego

El dataset de @discoelysium_es se generó siguiendo la serie *Disco Narrator* de
[152334H](https://152334h.github.io/), con los scripts adaptados para quedarse con los textos en español:

1. **Extraer la base de datos de diálogos** —
   [Disco Narrator: Data Scraping](https://152334h.github.io/blog/dn-1/). Con
   [Il2CppDumper](https://github.com/Perfare/Il2CppDumper) se generan las DLL que necesita
   [AssetStudio](https://github.com/Perfare/AssetStudio) para leer los *MonoBehaviour*, y con AssetStudio
   se exporta a JSON el asset `Assets/Dialogue Databases/Disco Elysium.asset`, que contiene todos los
   textos del juego (diálogos, pensamientos, descripciones...).
2. **Procesar el JSON** — [Disco Narrator, parte 2](https://152334h.github.io/blog/dn-2/). Scripts en
   Python con pandas que normalizan las entradas de diálogo y las cruzan con los personajes
   (`actors.json`, exportado con
   [disco-courier](https://web.archive.org/web/20250718131516/https://github.com/htmlbanjo/disco-courier);
   el repositorio original ya no existe, el enlace apunta a la copia de Web Archive).
3. **Adaptarlo a este formato** — en lugar de quedarse solo con el inglés, extraer también la traducción
   al español de cada línea y el nombre del personaje, y guardar el resultado como la lista JSON de
   arriba.

Los pasos dependen de la versión del juego y de las herramientas, así que puede que haya que ajustarlos.
Ninguno de esos proyectos está relacionado con este repositorio; se enlazan como referencia.

## Uso diario

```bash
revachol-radiobot --help                               # comandos disponibles
revachol-radiobot discoelysium_es status               # resumen + resultados de las últimas 24 h
revachol-radiobot discoelysium_es status 72            # ... de las últimas 72 h
revachol-radiobot discoelysium_es stats                # progreso, último tuit, autonomía
revachol-radiobot discoelysium_es notify-test          # comprobar que llegan los avisos
journalctl -u revachol-radiobot@discoelysium_es -f     # logs
systemctl list-timers 'revachol-radiobot@*'            # próxima ejecución
```

| Código de salida | Significado |
|---|---|
| 0 | Publicado |
| 1 | Error (aviso enviado) |
| 2 | Sesión inválida: hay que renovar las cookies (aviso enviado) |
| 3 | No quedan entradas (con `RADIOBOT_RECYCLE=false`) |
| 75 | Límite de X alcanzado; se reintenta en el siguiente turno |

## Desarrollo

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q && ruff check .

# probar con el dataset de ejemplo sin publicar
mkdir -p bots/prueba && cp data/dataset.example.json bots/prueba/dataset.json
RADIOBOT_HOME=bots/prueba revachol-radiobot post --dry-run
```

```
src/revachol_radiobot/
  cli.py        comandos (post, check, stats, preview, cookies, import-log)
  bot.py        una ejecución: elegir, publicar, registrar, avisar
  publisher.py  única capa que habla con X (twifork) + modo dry-run
  text.py       longitud ponderada de X y división en tuits por frases
  dataset.py    carga, normalización y deduplicación del JSON
  state.py      estado en SQLite
  legacy.py     importación del log de un bot anterior
  notify.py     avisos por Telegram
deploy/
  install.sh                 instalador idempotente (Debian 13)
  revachol-radiobot-cli.sh   comando /usr/local/bin/revachol-radiobot
  common.env.example         plantilla de /opt/revachol-radiobot/common.env
  systemd/                   revachol-radiobot@.service + revachol-radiobot@.timer
```

## Licencia

MIT para el código. Los datasets no se incluyen: los textos de *Disco Elysium* pertenecen a ZA/UM y
cada uno es responsable de tener derecho a publicar el contenido que use.
