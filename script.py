"""
Bot per il monitoraggio dei richiami alimentari del Ministero della Salute.

Cosa fa:
1. Legge il feed RSS ufficiale dei richiami alimentari.
2. Confronta gli avvisi con quelli già notificati in precedenza (seen.json).
3. Per ogni nuovo avviso, apre la pagina collegata e cerca i PDF allegati.
4. Invia su Telegram un messaggio + i PDF scaricati.
5. Aggiorna seen.json (il workflow di GitHub Actions lo ricommitta nel repo).

Nota tecnica:
Il sito salute.gov.it blocca le richieste dirette provenienti da GitHub
Actions (blocco per reputazione dell'IP). Per questo passiamo attraverso
ScraperAPI, che fa la richiesta da un altro indirizzo IP e ci restituisce
il contenuto.
"""

import os
import re
import sys
import json
import time
from urllib.parse import urljoin

import feedparser
import requests as std_requests

RSS_URL = "https://www.salute.gov.it/new/rss/RSS_avvisi_sicurezza_alimentare.xml"
STATE_FILE = os.path.join(os.path.dirname(__file__), "seen.json")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY")

SCRAPERAPI_ENDPOINT = "https://api.scraperapi.com/"

BLOCK_MARKERS = ("Please Enable JavaScript", "Please Enable Cookies", "Site verification")


def _looks_blocked(text: str) -> bool:
    return any(marker in text for marker in BLOCK_MARKERS)


def fetch(url: str, binary: bool = False, render: bool = False):
    """
    Recupera una pagina passando attraverso ScraperAPI, perché il sito del
    Ministero blocca le richieste dirette provenienti dai server di GitHub
    Actions (blocco per reputazione dell'IP, non solo anti-bot via JS).
    """
    if not SCRAPERAPI_KEY:
        raise RuntimeError("SCRAPERAPI_KEY non impostata: aggiungi il secret su GitHub.")

    params = {"api_key": SCRAPERAPI_KEY, "url": url}
    if render:
        params["render"] = "true"

    r = std_requests.get(SCRAPERAPI_ENDPOINT, params=params, timeout=90)

    if r.status_code == 200 and not binary and _looks_blocked(r.text) and not render:
        # Riprova chiedendo a ScraperAPI di eseguire il rendering JavaScript
        print("  risposta bloccata, riprovo con il rendering JavaScript...")
        return fetch(url, binary=binary, render=True)

    return r


def load_seen() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


def find_pdf_links(html: str) -> list:
    return sorted(set(re.findall(r'href="([^"]+\.pdf)"', html, re.IGNORECASE)))


def send_telegram_message(text: str):
    if not TELEGRAM_TOKEN:
        print("[TELEGRAM_BOT_TOKEN mancante, salto invio messaggio]")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = std_requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"  errore invio messaggio Telegram: {resp.status_code} {resp.text}")


def send_telegram_document(filepath: str, caption: str = ""):
    if not TELEGRAM_TOKEN:
        print("[TELEGRAM_BOT_TOKEN mancante, salto invio documento]")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    with open(filepath, "rb") as f:
        resp = std_requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024]},
            files={"document": f},
            timeout=60,
        )
    if resp.status_code != 200:
        print(f"  errore invio PDF Telegram: {resp.status_code} {resp.text}")


def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("ATTENZIONE: variabili TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID non impostate.")

    seen = load_seen()

    print("Recupero il feed RSS...")
    resp = fetch(RSS_URL)
    if resp.status_code != 200:
        print(f"Errore nel recupero dell'RSS: HTTP {resp.status_code}")
        sys.exit(1)
    if _looks_blocked(resp.text):
        print("Il sito ha bloccato anche ScraperAPI. Serve indagare ulteriormente.")
        sys.exit(1)

    print(f"  DEBUG status={resp.status_code} content-type={resp.headers.get('Content-Type')} "
          f"lunghezza={len(resp.content)} bytes")
    print(f"  DEBUG primi 500 caratteri della risposta:\n{resp.text[:500]!r}")

    feed = feedparser.parse(resp.content)
    if feed.bozo:
        print(f"  DEBUG feedparser ha segnalato un problema di parsing: {feed.bozo_exception}")
    print(f"Trovate {len(feed.entries)} voci nel feed.")

    new_items = [e for e in feed.entries if e.get("id", e.link) not in seen]

    if not new_items:
        print("Nessun nuovo richiamo.")
        return

    # Notifica dal piu' vecchio al piu' nuovo
    for entry in reversed(new_items):
        guid = entry.get("id", entry.link)
        title = entry.title
        link = entry.link
        published = entry.get("published", "")

        print(f"Nuovo avviso: {title}")

        pdf_links = []
        try:
            article_resp = fetch(link)
            if article_resp.status_code == 200 and not _looks_blocked(article_resp.text):
                pdf_links = find_pdf_links(article_resp.text)
        except Exception as e:
            print(f"  impossibile aprire la pagina dell'avviso: {e}")

        message = (
            f"🚨 <b>Nuovo richiamo alimentare</b>\n\n"
            f"{title}\n"
            f"📅 {published}\n"
            f"🔗 {link}"
        )
        send_telegram_message(message)

        for pdf_href in pdf_links:
            pdf_url = urljoin(link, pdf_href)
            try:
                pdf_resp = fetch(pdf_url, binary=True)
                if pdf_resp.status_code == 200:
                    fname = pdf_url.split("/")[-1].split("?")[0]
                    with open(fname, "wb") as f:
                        f.write(pdf_resp.content)
                    send_telegram_document(fname, caption=title)
                    os.remove(fname)
                    print(f"  inviato PDF: {fname}")
                else:
                    send_telegram_message(f"⚠️ PDF non scaricabile ({pdf_resp.status_code}): {pdf_url}")
            except Exception as e:
                send_telegram_message(f"⚠️ Errore scaricando il PDF: {pdf_url}\n{e}")

            time.sleep(1)

        seen.add(guid)

    save_seen(seen)
    print("Fatto. Stato aggiornato.")


if __name__ == "__main__":
    main()
