#!/usr/bin/env python3
"""Genera pronostici.json per la PWA.
Value bet = miglior quota disponibile vs probabilità di mercato de-vig (Pinnacle, o media dei book).
Uso: ODDS_API_KEY=xxx python pronostici.py   (MARKETS=h2h,totals per aggiungere i totali)"""
import os, json, datetime as dt, requests

KEY = os.environ["ODDS_API_KEY"]
BASE = "https://api.the-odds-api.com/v4"
MARKETS = os.getenv("MARKETS", "h2h")
SPORTS = {  # chiave API: (etichetta, fonti statistiche)
    "soccer_italy_serie_a": ("Serie A", [["FBref", "https://fbref.com/en/comps/11/"], ["Understat", "https://understat.com/league/Serie_A"]]),
    "soccer_uefa_nations_league": ("Nations League", [["UEFA", "https://www.uefa.com/uefanationsleague/"]]),
    "americanfootball_nfl": ("NFL", [["Pro Football Reference", "https://www.pro-football-reference.com/"], ["ESPN injuries", "https://www.espn.com/nfl/injuries"]]),
    "basketball_nba": ("NBA", [["NBA Stats", "https://www.nba.com/stats"], ["ESPN injuries", "https://www.espn.com/nba/injuries"]]),
    "baseball_mlb": ("MLB", [["Baseball Savant", "https://baseballsavant.mlb.com/"]]),
    "mma_mixed_martial_arts": ("UFC", [["UFCStats", "http://ufcstats.com/"]]),
}

def get(path, **p):
    r = requests.get(BASE + path, params={"apiKey": KEY, **p}, timeout=30)
    r.raise_for_status()
    return r.json()

def devig(prices):
    inv = [1 / x for x in prices]; s = sum(inv)
    return [i / s for i in inv]

def analizza(ev, label, fonti):
    groups = {}  # (mercato, linea) -> {book: {esito: quota}}
    for b in ev.get("bookmakers", []):
        for m in b["markets"]:
            by_pt = {}
            for o in m["outcomes"]:
                by_pt.setdefault(o.get("point"), {})[o["name"]] = o["price"]
            for pt, d in by_pt.items():
                groups.setdefault((m["key"], pt), {})[b["key"]] = d
    best = None
    for (mk, pt), books in groups.items():
        if len(books) < 3:  # evita quote isolate o non aggiornate
            continue
        sharp = "pinnacle" in books
        src = [books["pinnacle"]] if sharp else list(books.values())
        names = sorted(src[0])
        src = [d for d in src if sorted(d) == names]
        if len(names) < 2 or not src:
            continue
        dv = [devig([d[k] for k in names]) for d in src]
        p = {n: sum(x[i] for x in dv) / len(dv) for i, n in enumerate(names)}
        for n in names:
            odds = max(d[n] for d in books.values() if n in d)
            e = p[n] * odds - 1
            if 1.4 <= odds <= 3.2 and e > 0.02 and (best is None or e > best[0]):
                best = (e, pt, n, odds, p, "Pinnacle" if sharp else f"media di {len(books)} book")
    if not best:
        return None
    e, pt, n, odds, p, fonte = best
    pn = p[n]
    conf = round(min(95, 100 * pn * 0.8 + min(e, 0.1) * 200))
    strat = ("Singola, stake Kelly ¼." if conf >= 70 else
             "Singola a stake ridotto o gamba di combo." if conf >= 60 else "Solo come gamba di combo.")
    return {
        "id": ev["id"], "league": label, "match": f'{ev["home_team"]} – {ev["away_team"]}',
        "time": ev["commence_time"], "pick": n if pt is None else f"{n} {pt}",
        "odds": odds, "p": round(pn * 100, 1), "conf": conf,
        "probs": [[k, round(v * 100)] for k, v in sorted(p.items(), key=lambda x: -x[1])],
        "strategy": strat,
        "insight": f"Quota migliore {odds:.2f} contro probabilità di mercato de-vig {pn:.1%} ({fonte}): EV {e:+.1%}. "
                   "Confidenza = 80% della probabilità + bonus EV (max 20 punti).",
        "risk": "Il modello coincide con il prezzo di mercato: controlla infortuni dell'ultima ora, meteo e riposo. "
                "Una quota isolata può essere un prezzo non aggiornato.",
        "sources": [["The Odds API", "https://the-odds-api.com"]] + fonti,
    }

def main():
    now = dt.datetime.now(dt.timezone.utc)
    attivi = {s["key"] for s in get("/sports") if s["active"]}  # MLB si attiva da sola in stagione
    picks = []
    for key, (label, fonti) in SPORTS.items():
        if key not in attivi:
            continue
        try:
            evs = get(f"/sports/{key}/odds", regions="eu", markets=MARKETS, oddsFormat="decimal")
        except requests.HTTPError as err:
            print(key, err); continue
        for ev in evs:
            t = dt.datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if now < t < now + dt.timedelta(days=3):
                if (x := analizza(ev, label, fonti)):
                    picks.append(x)
    picks.sort(key=lambda k: -(k["conf"] * (k["p"] / 100 * k["odds"] - 1)))
    picks = picks[:15]
    top = [k for k in picks if k["conf"] >= 60]
    combos = []
    if len(top) >= 2:
        a, b = top[0], top[1]
        combos.append({"t": "Doppia a basso rischio", "legs": [a["id"], b["id"]], "odds": round(a["odds"] * b["odds"], 2),
                       "note": "Le due giocate con confidenza più alta, eventi diversi."})
    boosts = json.load(open("boosts.json")) if os.path.exists("boosts.json") else []  # super quote inserite a mano
    with open("pronostici.json", "w", encoding="utf-8") as f:
        json.dump({"updated": now.isoformat(), "picks": picks, "combos": combos, "boosts": boosts}, f, ensure_ascii=False, indent=1)
    print(f"{len(picks)} giocate salvate")

if __name__ == "__main__":
    main()
