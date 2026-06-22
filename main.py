"""
Agente de noticias de movilidad eléctrica en Colombia.
Cada día busca noticias en Google News, escoge las 5 más importantes
y las envía por correo a uno o varios destinatarios.

Variables de entorno (se configuran como "secrets" en GitHub):
  GMAIL_USER           -> tu correo, ej. tucorreo@gmail.com
  GMAIL_APP_PASSWORD   -> la contraseña de aplicación de 16 caracteres
  RECIPIENTS           -> destinatarios separados por coma. Ej:
                          correo1@gmail.com, correo2@hotmail.com
  ANTHROPIC_API_KEY    -> (opcional) si lo agregas, la IA escoge las 5
                          MÁS importantes y explica por qué importa cada una.
"""

import os
import json
import smtplib
import datetime
import html
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote_plus

import feedparser

# ---------------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------------
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]

# Lista de destinatarios. Separa los correos con coma en el secret RECIPIENTS.
# Si no se define, se envía a ti mismo.
RECIPIENTS = [
    e.strip()
    for e in os.environ.get("RECIPIENTS", GMAIL_USER).split(",")
    if e.strip()
]

# Búsquedas que alimentan el boletín. Puedes agregar o quitar las que quieras.
QUERIES = [
    "movilidad eléctrica Colombia",
    "estaciones de carga vehículos eléctricos Colombia",
    "electrolineras Colombia",
    "infraestructura de carga eléctrica Colombia",
    "carros eléctricos Colombia",
    "buses eléctricos Colombia",
]

POOL_PER_QUERY = 8   # cuántos titulares recolectar de cada búsqueda
POOL_MAX = 30        # tope del "montón" de candidatos antes de escoger
TOP_N = 5            # cuántas noticias finales enviar
WHEN = "2d"          # ventana de tiempo: "1d" = último día, "2d" = 2 días


# ---------------------------------------------------------------------------
# 1) RECOLECTAR UN MONTÓN DE NOTICIAS (candidatas)
# ---------------------------------------------------------------------------
def fetch_pool():
    seen_links = set()
    seen_titles = set()
    pool = []

    for q in QUERIES:
        url = (
            "https://news.google.com/rss/search?q="
            + quote_plus(f"{q} when:{WHEN}")
            + "&hl=es-419&gl=CO&ceid=CO:es-419"
        )
        feed = feedparser.parse(url)

        for entry in feed.entries[:POOL_PER_QUERY]:
            link = entry.get("link", "")
            title = entry.get("title", "").strip()
            if not title or link in seen_links:
                continue

            key = title.lower()[:60]
            if key in seen_titles:
                continue

            seen_links.add(link)
            seen_titles.add(key)

            source = ""
            if "source" in entry and hasattr(entry.source, "title"):
                source = entry.source.title

            pool.append({"title": title, "link": link, "source": source})

    return pool[:POOL_MAX]


# ---------------------------------------------------------------------------
# 2) ESCOGER LAS 5 MÁS IMPORTANTES (con IA si hay API key)
# ---------------------------------------------------------------------------
def pick_top(pool):
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    # Sin API key o sin noticias -> tomamos las primeras 5 por relevancia.
    if not api_key or not pool:
        return [dict(it, why="") for it in pool[:TOP_N]]

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        listado = "\n".join(
            f"{i}. {it['title']} ({it['source']})" for i, it in enumerate(pool)
        )
        prompt = (
            "Eres analista de movilidad eléctrica en Colombia. De esta lista "
            "numerada de titulares de hoy, selecciona los 5 MÁS IMPORTANTES para "
            "alguien que trabaja en infraestructura y estaciones de carga de "
            "vehículos eléctricos. Prioriza noticias de Colombia con impacto real "
            "(inversiones, nuevas estaciones, regulación, cifras, alianzas) y "
            "evita duplicados o notas triviales.\n\n"
            "Devuelve SOLO un JSON array de 5 objetos con este formato exacto, "
            "sin texto adicional ni comillas de código:\n"
            '[{"n": 0, "razon": "frase corta de por qué importa"}]\n\n'
            "Titulares:\n" + listado
        )

        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )

        text = msg.content[0].text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)

        seleccion = []
        for obj in data[:TOP_N]:
            i = obj.get("n")
            if isinstance(i, int) and 0 <= i < len(pool):
                seleccion.append(dict(pool[i], why=obj.get("razon", "")))

        if seleccion:
            return seleccion

    except Exception as e:
        print("No se pudo escoger con IA, uso orden por relevancia:", e)

    return [dict(it, why="") for it in pool[:TOP_N]]


# ---------------------------------------------------------------------------
# 3) ARMAR EL CORREO (HTML)
# ---------------------------------------------------------------------------
def build_html(items):
    hoy = datetime.date.today().strftime("%d/%m/%Y")

    filas = ""
    for n, it in enumerate(items, start=1):
        titulo = html.escape(it["title"])
        fuente = html.escape(it["source"]) if it["source"] else "Fuente"
        why = html.escape(it.get("why", ""))
        why_html = (
            f'<div style="color:#4361ee;font-size:13px;margin-top:6px;">'
            f"💡 {why}</div>"
            if why
            else ""
        )
        filas += f"""
        <div style="padding:16px 0;border-bottom:1px solid #eee;">
          <div style="color:#aaa;font-size:12px;font-weight:700;">#{n}</div>
          <a href="{it['link']}" style="color:#1a1a2e;text-decoration:none;
             font-weight:600;font-size:15px;line-height:1.4;">{titulo}</a>
          <div style="color:#888;font-size:12px;margin-top:4px;">{fuente}</div>
          {why_html}
        </div>
        """

    if not items:
        filas = """<p style="color:#888;">Hoy no se encontraron noticias nuevas
        con los criterios actuales. Mañana vuelvo a revisar.</p>"""

    return f"""
    <html><body style="margin:0;background:#f5f5f7;font-family:Arial,Helvetica,sans-serif;">
      <div style="max-width:600px;margin:0 auto;padding:24px;">
        <div style="background:#fff;border-radius:14px;padding:28px;
                    box-shadow:0 2px 10px rgba(0,0,0,0.05);">
          <h1 style="margin:0 0 4px;font-size:20px;color:#1a1a2e;">
            ⚡ Top 5 — Movilidad Eléctrica Colombia</h1>
          <p style="margin:0 0 20px;color:#888;font-size:13px;">{hoy}</p>
          {filas}
          <p style="margin-top:24px;color:#aaa;font-size:11px;text-align:center;">
            Generado automáticamente por tu agente de noticias.
          </p>
        </div>
      </div>
    </body></html>
    """


# ---------------------------------------------------------------------------
# 4) ENVIAR EL CORREO
# ---------------------------------------------------------------------------
def send_email(html_body):
    hoy = datetime.date.today().strftime("%d/%m")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"⚡ Top 5 Movilidad Eléctrica Colombia — {hoy}"
    msg["From"] = GMAIL_USER
    msg["To"] = ", ".join(RECIPIENTS)
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, RECIPIENTS, msg.as_string())

    print(f"Correo enviado a: {', '.join(RECIPIENTS)}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    print("Buscando noticias...")
    pool = fetch_pool()
    print(f"Se recolectaron {len(pool)} noticias candidatas.")

    items = pick_top(pool)
    print(f"Se seleccionaron {len(items)} noticias finales.")

    html_body = build_html(items)
    send_email(html_body)


if __name__ == "__main__":
    main()
