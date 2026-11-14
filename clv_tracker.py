#!/usr/bin/env python3
"""Edge Desk - tracciamento CLV (Closing Line Value) contro Pinnacle.

Da codice (quando il bot genera una giocata):
    from clv_tracker import log_pick
    log_pick(event_id, sport_key, commence_time, "h2h", "Inter", 2.10,
             label="Inter - Milan")
    log_pick(event_id, sport_key, commence_time, "totals", "Over", 1.95,
             point=2.5, label="Inter - Milan")

Da workflow (ogni 5 minuti):
    python clv_tracker.py update

Mercati supportati: h2h e totals. Variabile d'ambiente: ODDS_API_KEY.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import urlopen

LOG_PATH = os.environ.get("CLV_LOG", "data/clv_log.json")
API_KEY = os.environ.get("ODDS_API_KEY", "")
API_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"
WINDOW = timedelta(minutes=30)  # da quando seguire la linea Pinnacle
GIVE_UP = timedelta(hours=3)    # oltre questo tempo senza snapshot: abbandona


def _now():
    return datetime.now(timezone.utc)


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def devig(odds):
    """Quote decimali -> probabilità senza margine (normalizzazione proporzionale)."""
    inv = [1.0 / o for o in odds]
    tot = sum(inv)
    return [x / tot for x in inv]


def clv_pct(odds_taken, p_close):
    """CLV % = quota presa / quota di chiusura equa - 1 (positivo = hai battuto la chiusura)."""
    return (odds_taken * p_close - 1.0) * 100.0


def load(path=LOG_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save(picks, path=LOG_PATH):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(picks, f, ensure_ascii=False, indent=1)


def log_pick(event_id, sport_key, commence_time, market, selection, odds_taken,
             point=None, label=None, path=LOG_PATH):
    """Registra una giocata da seguire. Ignora i duplicati."""
    if isinstance(commence_time, datetime):
        commence_time = _iso(commence_time.astimezone(timezone.utc))
    picks = load(path)
    pid = f"{event_id}|{market}|{selection}|{point}"
    if any(p["id"] == pid for p in picks):
        return pid
    picks.append({
        "id": pid, "event_id": event_id, "sport_key": sport_key,
        "label": label or str(event_id), "market": market,
        "selection": selection, "point": point,
        "odds_taken": float(odds_taken), "taken_at": _iso(_now()),
        "commence_time": commence_time, "status": "open",
    })
    save(picks, path)
    return pid


def fetch_pinnacle(sport, event_ids, markets):
    q = urlencode({
        "apiKey": API_KEY, "bookmakers": "pinnacle",
        "markets": ",".join(markets), "oddsFormat": "decimal",
        "eventIds": ",".join(event_ids),
    })
    with urlopen(API_URL.format(sport=sport) + "?" + q, timeout=30) as r:
        return json.load(r)


def pinnacle_prob(event, market, selection, point=None):
    """Probabilità senza margine di Pinnacle per la selezione, o None."""
    for bk in event.get("bookmakers", []):
        if bk["key"] != "pinnacle":
            continue
        for m in bk["markets"]:
            if m["key"] != market:
                continue
            outs = [o for o in m["outcomes"]
                    if point is None or o.get("point") == point]
            if len(outs) < 2:
                return None
            for o, p in zip(outs, devig([o["price"] for o in outs])):
                if o["name"] == selection:
                    return p
    return None


def update(path=LOG_PATH):
    """Aggiorna lo snapshot Pinnacle e chiude le giocate già iniziate.

    L'ultimo snapshot prima del fischio diventa la quota di chiusura, quindi
    un cron in ritardo di qualche minuto non rovina il dato. Senza giocate
    nella finestra non fa nessuna chiamata API (zero crediti).
    """
    picks = load(path)
    now = _now()
    changed = False

    follow = {}
    for p in picks:
        if p["status"] == "open":
            start = _ts(p["commence_time"])
            if start - WINDOW <= now < start:
                follow.setdefault(p["sport_key"], []).append(p)

    for sport, group in follow.items():
        ids = sorted({p["event_id"] for p in group})
        markets = sorted({p["market"] for p in group})
        try:
            events = {e["id"]: e for e in fetch_pinnacle(sport, ids, markets)}
        except Exception as exc:  # rete, quota API esaurita, ecc.
            print(f"[clv] errore API {sport}: {exc}", file=sys.stderr)
            continue
        for p in group:
            ev = events.get(p["event_id"])
            prob = pinnacle_prob(ev, p["market"], p["selection"], p.get("point")) if ev else None
            if prob is None:
                continue
            p["close_prob"] = round(prob, 5)
            p["close_odds"] = round(1.0 / prob, 3)
            p["snap_at"] = _iso(now)
            changed = True

    for p in picks:
        if p["status"] != "open":
            continue
        start = _ts(p["commence_time"])
        if now >= start:
            if "close_prob" in p:
                p["clv"] = round(clv_pct(p["odds_taken"], p["close_prob"]), 2)
                p["status"] = "closed"
                changed = True
            elif now - start > GIVE_UP:
                p["status"] = "no_close"
                changed = True

    if changed:
        save(picks, path)
    return picks


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "update":
        update()
    else:
        print(__doc__)
