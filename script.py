"""
Bot per il monitoraggio dei richiami alimentari del Ministero della Salute.

Cosa fa:
1. Legge il feed RSS ufficiale dei richiami alimentari.
2. Confronta gli avvisi con quelli già notificati in precedenza (seen.json).
3. Per ogni nuovo avviso, apre la pagina collegata e cerca i PDF allegati.
4. Invia su Telegram un messaggio + i PDF scaricati.
5. Aggiorna seen.json (il workflow di GitHub Actions lo ricommitta nel repo).

Nota tecnica:
Il sito salute.gov.it è protetto da un sistema anti-bot che richiede
JavaScript. Per questo motivo ogni richiesta passa prima da un client HTTP
"normale" e, se bloccata, da un client che imita l'impronta TLS di un
browser reale (curl_cffi). Se anche questo dovesse smettere di funzionare,
il passo successivo è passare a un browser headless (Playwright) - vedi
il README.
"""

import os
import re
import sys
import json
import time
from urllib.parse import urljoin

import feedparser
import requests as std_requests
from curl_cffi import requests as cffi_requests

RSS_URL = "https://www.salute.gov.it/portale/news/RSS_avvisi_richiami_osa.xml"
STATE_FILE = os.path.join(os.path.dirname(__file__), "seen.json")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

BLOCK_MARKERS = ("Please Enable JavaScript", "Please Enable Cookies", "Site verification")


def _looks_blocked(text: str) -> bool:
    return any(marker in text for marker in BLOCK_MARKERS)


def fetch(url: str, binary: bool = False):
    """Prova prima una richiesta normale, poi una che imita un browser reale."""
    try:
        r = std_requests.get(url, headers=HEADERS, timeout=30)
        content_check = r.text if not binary else ""
        if r.status_code == 200 and not _looks_blocked(content_check):
            return r
    except Exception as e:
        print(f"  richiesta normale fallita ({e}), provo il fallback...")

    try:
        r = cffi_requests.get(url, headers=HEADERS, impersonate="chrome124", timeout=30)
        return r
    except Exception as e:
        print(f"  anche il fallback e' fallito: {e}")
        raise


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
        print("Il sito ha bloccato anche il fallback (protezione anti-bot). "
              "Vedi il README per l'opzione Playwright.")
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
