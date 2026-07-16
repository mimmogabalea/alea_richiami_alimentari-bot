# Bot richiami alimentari Ministero della Salute → Telegram

Controlla ogni giorno il feed RSS ufficiale dei richiami alimentari del
Ministero della Salute e invia su Telegram un messaggio + i PDF dei nuovi
avvisi.

## ⚠️ Avviso importante

Il sito **salute.gov.it è protetto da un sistema anti-bot** che normalmente
richiede JavaScript per essere visitato. Lo script prova prima una
richiesta HTTP normale e poi, se bloccata, un client che imita l'impronta
di un browser reale (`curl_cffi`). Questo funziona nella maggior parte dei
casi analoghi, ma **non è garantito al 100%**: se il Ministero rafforza la
protezione, lo script potrebbe smettere di funzionare e sarebbe necessario
passare a un browser headless (es. Playwright) dentro GitHub Actions.
Consiglio di fare un primo test manuale (`workflow_dispatch`, vedi sotto)
prima di fidarsi ciecamente della schedulazione automatica.

## 1. Crea il bot Telegram

1. Scrivi a [@BotFather](https://t.me/BotFather) su Telegram.
2. Invia `/newbot` e segui le istruzioni: ti darà un **token** tipo
   `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.
3. Avvia una conversazione col tuo nuovo bot (cercalo per nome e premi
   "Avvia"), altrimenti non potrà scriverti.

## 2. Trova il tuo chat_id

Metodo semplice: scrivi a [@userinfobot](https://t.me/userinfobot), ti
risponderà con il tuo ID numerico (es. `987654321`). Usa quello come
`TELEGRAM_CHAT_ID`.

Se preferisci un canale/gruppo, aggiungi il bot come admin del
canale/gruppo e usa l'ID del canale (di solito inizia con `-100`).

## 3. Crea il repository GitHub

1. Crea un nuovo repository (anche privato va bene) e carica tutti i file
   di questa cartella (`script.py`, `requirements.txt`, `seen.json`, la
   cartella `.github/workflows/`).
2. Vai su **Settings → Secrets and variables → Actions → New repository
   secret** e aggiungi:
   - `TELEGRAM_BOT_TOKEN` → il token ottenuto da BotFather
   - `TELEGRAM_CHAT_ID` → il tuo chat_id

## 4. Test manuale

Vai su **Actions → Controllo richiami alimentari → Run workflow** per
lanciarlo subito senza aspettare il cron, e controlla i log per vedere se
tutto funziona (o se il sito ha bloccato le richieste).

## 5. Schedulazione

Il workflow è già impostato per girare ogni giorno alle 7:00 UTC. Puoi
cambiare l'orario modificando la riga `cron` in
`.github/workflows/richiami.yml` (sintassi cron standard, orario UTC).

## Come funziona lo stato (evitare messaggi duplicati)

Il file `seen.json` tiene traccia degli avvisi già notificati. Dopo ogni
esecuzione, il workflow lo ricommitta automaticamente nel repository, così
il giorno dopo lo script sa quali avvisi ha già mandato.

## Se lo script smette di funzionare (blocco anti-bot più severo)

Alternative da valutare, in ordine di complessità crescente:
1. Aumentare i tentativi/retry con backoff nello script attuale.
2. Sostituire `curl_cffi` con **Playwright** (browser headless reale) —
   richiede installare i binari del browser nel workflow
   (`playwright install chromium`), più lento ma più difficile da bloccare.
3. Verificare se il Ministero ha cambiato l'URL dell'RSS o della pagina.
