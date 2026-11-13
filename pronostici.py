#!/usr/bin/env python3
"""Genera pronostici.json per la PWA.
Value bet = miglior quota disponibile vs probabilità di mercato de-vig (Pinnacle, o media dei book).
Uso: ODDS_API_KEY=xxx python pronostici.py   (MARKETS=h2h,totals per aggiungere i totali)"""
import os, json, math, re, unicodedata, difflib, datetime as dt, requests

KEY = os.environ["ODDS_API_KEY"]
BASE = "https://api.the-odds-api.com/v4"
MARKETS = os.getenv("MARKETS", "h2h")
FD_KEY = os.getenv("FOOTBALL_DATA_KEY")           # football-data.org (gratis)
FD_CODE = {"Serie A": "SA", "Champions League": "CL"}
W_MODELLO = 0.4                                   # peso del modello Poisson nel blend (il resto è il mercato)
ALIAS = {"inter milan": "inter"}                  # nomi che The Odds API scrive diversamente
SPORTS = {  # chiave API: (etichetta, fonti statistiche)
    "soccer_italy_serie_a": ("Serie A", [["FBref", "https://fbref.com/en/comps/11/"], ["Understat", "https://understat.com/league/Serie_A"]]),
    "soccer_uefa_champs_league": ("Champions League", [["UEFA", "https://www.uefa.com/uefachampionsleague/"]]),
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

def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    skip = {"fc", "ac", "as", "ss", "ssc", "bc", "afc", "cf", "sc", "calcio", "club", "de"}
    return " ".join(w for w in re.sub(r"[^a-z ]", " ", s).split() if w not in skip)

def trova(nome, squadre):
    """Abbina il nome di The Odds API a quello di football-data.org; None se ambiguo."""
    n = norm(nome); n = ALIAS.get(n, n)
    if n in squadre:
        return squadre[n]
    best = {}
    for k, v in squadre.items():
        best[v] = max(best.get(v, 0), difflib.SequenceMatcher(None, n, k).ratio())
    r = sorted(((s, v) for v, s in best.items()), reverse=True)
    ok = r and r[0][0] >= .85 and (len(r) < 2 or r[0][0] - r[1][0] > .05)
    return r[0][1] if ok else None

def costruisci_modello(code):
    """Poisson su gol fatti/subiti (ultime 10, peso decrescente, ridotti verso la media)."""
    r = requests.get(f"https://api.football-data.org/v4/competitions/{code}/matches",
                     params={"status": "FINISHED"}, headers={"X-Auth-Token": FD_KEY}, timeout=30)
    r.raise_for_status()
    squadre, log, hg, ag, crest = {}, {}, [], [], {}
    for m in sorted(r.json()["matches"], key=lambda m: m["utcDate"]):
        h, a, g = m["homeTeam"], m["awayTeam"], m["score"]["fullTime"]
        if g["home"] is None:
            continue
        for t in (h, a):
            crest[t["name"]] = t.get("crest")
            for k in (t.get("name"), t.get("shortName")):
                if k:
                    squadre[norm(k)] = t["name"]
        log.setdefault(h["name"], []).append((g["home"], g["away"]))
        log.setdefault(a["name"], []).append((g["away"], g["home"]))
        hg.append(g["home"]); ag.append(g["away"])
    if len(hg) < 10:
        return None
    mh, ma = sum(hg) / len(hg), sum(ag) / len(ag)
    mu, k = (mh + ma) / 2, (5 if code == "CL" else 3)   # k = partite "virtuali" nella media

    def forza(nome):
        v = log.get(nome, [])[-10:]
        w = [0.9 ** (len(v) - 1 - i) for i in range(len(v))]
        n = sum(w)
        f = sum(wi * x[0] for wi, x in zip(w, v)); s = sum(wi * x[1] for wi, x in zip(w, v))
        return (f + k * mu) / ((n + k) * mu), (s + k * mu) / ((n + k) * mu)

    def prob(casa, trasf):
        a_h, d_h = forza(casa); a_a, d_a = forza(trasf)
        lh, la = mh * a_h * d_a, ma * a_a * d_h
        ph = [math.exp(-lh) * lh ** i / math.factorial(i) for i in range(11)]
        pa = [math.exp(-la) * la ** i / math.factorial(i) for i in range(11)]
        H = sum(ph[i] * pa[j] for i in range(11) for j in range(i))
        D = sum(ph[i] * pa[i] for i in range(11))
        A = sum(ph[i] * pa[j] for i in range(11) for j in range(i + 1, 11))
        s = H + D + A
        return H / s, D / s, A / s, lh, la
    return squadre, prob, crest

def prob_modello(ev, label, modelli):
    m = modelli.get(label)
    if not m:
        return None
    squadre, prob, crest = m
    c, t = trova(ev["home_team"], squadre), trova(ev["away_team"], squadre)
    if not c or not t:
        print("senza modello:", ev["home_team"], "-", ev["away_team"]); return None
    h, d, a, lh, la = prob(c, t)
    return {"p": {ev["home_team"]: h, "Draw": d, ev["away_team"]: a}, "lh": lh, "la": la, "logos": [crest.get(c), crest.get(t)]}

def analizza(ev, label, fonti, pm=None):
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
        fonte = "Pinnacle" if sharp else f"media di {len(books)} book"
        if mk == "h2h" and pm and set(pm) == set(names):
            p = {n: (1 - W_MODELLO) * p[n] + W_MODELLO * pm[n] for n in names}
            fonte += " + modello Poisson"
        for n in names:
            quote = {b: d[n] for b, d in books.items() if n in d}
            book = max(quote, key=quote.get); odds = quote[book]
            e = p[n] * odds - 1
            if 1.4 <= odds <= 3.2 and e > 0.02 and (best is None or e > best[0]):
                best = (e, pt, n, odds, p, fonte, book)
    if not best:
        return None
    e, pt, n, odds, p, fonte, book = best
    pn = p[n]
    conf = round(min(95, 100 * pn * 0.8 + min(e, 0.1) * 200))
    strat = ("Singola, stake Kelly ¼." if conf >= 70 else
             "Singola a stake ridotto o gamba di combo." if conf >= 60 else "Solo come gamba di combo.")
    return {
        "id": ev["id"], "league": label, "match": f'{ev["home_team"]} – {ev["away_team"]}',
        "time": ev["commence_time"], "pick": n if pt is None else f"{n} {pt}",
        "odds": odds, "book": book.replace("_", " ").title(), "p": round(pn * 100, 1), "conf": conf,
        "probs": [[k, round(v * 100)] for k, v in sorted(p.items(), key=lambda x: -x[1])],
        "strategy": strat,
        "insight": f"Quota migliore {odds:.2f} contro probabilità stimata {pn:.1%} ({fonte}): EV {e:+.1%}. "
                   "Confidenza = 80% della probabilità + bonus EV (max 20 punti).",
        "risk": "Il modello usa solo gol e quote, non gli infortuni: controlla infortuni dell'ultima ora, meteo e riposo. "
                "Una quota isolata può essere un prezzo non aggiornato.",
        "sources": [["The Odds API", "https://the-odds-api.com"]] + fonti,
    }

def main():
    now = dt.datetime.now(dt.timezone.utc)
    attivi = {s["key"] for s in get("/sports") if s["active"]}  # MLB si attiva da sola in stagione
    modelli = {}
    if FD_KEY:
        for lab, code in FD_CODE.items():
            try:
                modelli[lab] = costruisci_modello(code)
            except Exception as err:
                print("modello", lab, err)
    picks, partite, errori = [], [], 0
    for key, (label, fonti) in SPORTS.items():
        if key not in attivi:
            continue
        try:
            evs = get(f"/sports/{key}/odds", regions="eu", markets=MARKETS, oddsFormat="decimal")
        except requests.HTTPError as err:
            print(key, err); errori += 1; continue
        for ev in evs:
            t = dt.datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if now < t < now + dt.timedelta(days=3):
                full = prob_modello(ev, label, modelli)
                if full:
                    partite.append({"id": ev["id"], "league": label, "match": f'{ev["home_team"]} – {ev["away_team"]}',
                                    "time": ev["commence_time"], "lh": round(full["lh"], 3), "la": round(full["la"], 3)})
                if (x := analizza(ev, label, fonti, full and full["p"])):
                    x["home"], x["away"] = ev["home_team"], ev["away_team"]
                    x["logos"] = full["logos"] if full else [None, None]
                    picks.append(x)
    if errori and not picks and os.path.exists("pronostici.json"):
        print("quote non disponibili: tengo il file precedente"); return
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
        json.dump({"updated": now.isoformat(), "picks": picks, "combos": combos, "boosts": boosts, "partite": partite}, f, ensure_ascii=False, indent=1)
    try:
        st = json.load(open("storico.json"))
    except Exception:
        st = []
    visti = {x["id"] for x in st}
    st += [{"id": k["id"], "ts": now.isoformat(), "match": k["match"], "league": k["league"], "pick": k["pick"],
            "odds": k["odds"], "p": k["p"], "conf": k["conf"], "time": k["time"]} for k in picks if k["id"] not in visti]
    with open("storico.json", "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    print(f"{len(picks)} giocate salvate")

if __name__ == "__main__":
    main()
