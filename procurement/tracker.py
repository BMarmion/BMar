"""
Purchase Intelligence — Tracker hebdomadaire
Lit les shortlists verrouillées, interroge Claude sur les prix courants,
met à jour price_history.json, envoie un rapport Telegram.

Usage:
  python tracker.py --all              # toutes les missions
  python tracker.py --mission refrigerateur
  python tracker.py --all --dry-run    # sans Telegram ni écriture
"""

import argparse, json, os, re, sys, time, urllib.request, urllib.error
from datetime import date, datetime

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
MISSIONS_DIR = os.path.join(SCRIPT_DIR, "missions")
DATA_DIR     = os.path.join(SCRIPT_DIR, "data")
REPORTS_DIR  = os.path.join(SCRIPT_DIR, "reports")


# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    """Charge les clés API depuis env ou config.local.json."""
    cfg = {}
    # 1. Variables d'environnement (GitHub Actions)
    cfg["anthropic"]  = os.environ.get("ANTHROPIC_API_KEY", "")
    cfg["tg_token"]   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    cfg["tg_chat_id"] = os.environ.get("TELEGRAM_CHAT_ID", "")

    # 2. Fichier local (dev)
    if not all([cfg["anthropic"], cfg["tg_token"], cfg["tg_chat_id"]]):
        for cfg_path in [
            os.path.join(SCRIPT_DIR, "config.json"),
            os.path.join(SCRIPT_DIR, "..", "feeds", "config.local.json"),
        ]:
            if os.path.exists(cfg_path):
                with open(cfg_path, encoding="utf-8") as f:
                    local = json.load(f)
                cfg["anthropic"]  = cfg["anthropic"]  or local.get("anthropic_api_key", "")
                cfg["tg_token"]   = cfg["tg_token"]   or local.get("telegram_bot_token", "")
                cfg["tg_chat_id"] = cfg["tg_chat_id"] or local.get("telegram_chat_id", "")
    return cfg


# ── Fichiers ──────────────────────────────────────────────────────────────────

def load_json(path, default=None):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def mission_paths(slug):
    data_dir = os.path.join(DATA_DIR, slug)
    os.makedirs(data_dir, exist_ok=True)
    return {
        "shortlist": os.path.join(data_dir, "shortlist.json"),
        "history":   os.path.join(data_dir, "price_history.json"),
    }

def load_all_slugs():
    """Retourne tous les slugs ayant une shortlist verrouillée."""
    slugs = []
    if not os.path.exists(DATA_DIR):
        return slugs
    for slug in os.listdir(DATA_DIR):
        sl_path = os.path.join(DATA_DIR, slug, "shortlist.json")
        if os.path.exists(sl_path):
            try:
                sl = load_json(sl_path)
                if sl and sl.get("locked"):
                    slugs.append(slug)
            except Exception:
                pass
    return sorted(slugs)

def load_mission_meta(slug):
    """Charge les métadonnées de la mission (nom, icône, contexte)."""
    path = os.path.join(MISSIONS_DIR, f"{slug}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ── Claude ────────────────────────────────────────────────────────────────────

def claude(api_key, prompt, system=None, max_tokens=3000):
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())["content"][0]["text"]
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Claude error {e.code}: {e.read().decode()[:300]}")

def parse_json(text):
    cleaned = re.sub(r'^```(?:json)?\s*', '', text.strip())
    cleaned = re.sub(r'\s*```\s*$', '', cleaned).strip()
    for candidate in [cleaned, text.strip()]:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    m = re.search(r'(\{.*\}|\[.*\])', cleaned, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    raise ValueError(f"JSON non parseable: {text[:300]}")


def check_prices(api_key, slug, models, mission_meta):
    """
    Demande à Claude les prix courants observés en France pour chaque modèle.
    Retourne une liste de {key, current_price_eur, low_price_eur, notes, trend}.
    """
    product_type = mission_meta.get("product_type", "produit")
    domain       = mission_meta.get("domain", "grande consommation")

    system = (f"Tu es un expert {domain} marche francais. Tu connais les prix "
              f"pratiques actuels sur Darty, Fnac, Boulanger, Amazon.fr, CDiscount. "
              f"REGLE : reponds UNIQUEMENT avec du JSON brut. Debut {{ ou [, fin }} ou ]")

    models_list = "\n".join(
        f"- {m['brand']} {m['model']} (cible client: {m.get('target_purchase_price','?')} EUR)"
        for m in models
    )
    today = date.today().strftime("%d/%m/%Y")

    prompt = f"""Date : {today}. Marché FR.

Pour chaque {product_type} ci-dessous, donne :
- Le prix courant typique observé en France (pas le prix catalogue, le prix réel)
- Le prix le plus bas observé en ce moment (promo, destockage, marketplace)
- Une note courte sur la tendance (hausse/baisse/stable, promo en cours ?)
- Confidence : 0-100 sur ta certitude des prix

MODELES :
{models_list}

JSON :
[
  {{"key": "Brand Model", "current_price_eur": 799, "low_price_eur": 749,
    "notes": "Promo Darty -10% jusqu'au 15/08", "trend": "stable", "confidence": 75}},
  ...
]"""

    text = claude(api_key, prompt, system=system, max_tokens=2000)
    return parse_json(text)


# ── Historique prix ───────────────────────────────────────────────────────────

def update_history(history, model_key, price_data):
    """Met à jour price_history.json avec les données du jour."""
    today = date.today().isoformat()
    if model_key not in history:
        history[model_key] = {"daily": {}, "checks": []}

    history[model_key]["daily"][today] = {
        "min":     price_data.get("low_price_eur"),
        "typical": price_data.get("current_price_eur"),
    }
    history[model_key]["checks"].append({
        "date":       today,
        "typical":    price_data.get("current_price_eur"),
        "low":        price_data.get("low_price_eur"),
        "notes":      price_data.get("notes", ""),
        "trend":      price_data.get("trend", ""),
        "confidence": price_data.get("confidence", 0),
    })
    # Garder 90 jours max de daily
    if len(history[model_key]["daily"]) > 90:
        oldest = sorted(history[model_key]["daily"].keys())[0]
        del history[model_key]["daily"][oldest]
    return history


# ── Telegram ──────────────────────────────────────────────────────────────────

def tg_send(token, chat_id, text, parse_mode="HTML"):
    """Envoie un message Telegram (HTML)."""
    body = json.dumps({
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"   [Telegram] Erreur : {e}", flush=True)
        return None

def format_tg_report(missions_results, week_str):
    """Formate le rapport Telegram hebdomadaire."""
    lines = [
        f"🛒 <b>Purchase Intelligence — Rapport hebdo</b>",
        f"📅 Semaine du {week_str}",
        "",
    ]
    total_alerts = 0
    for slug, res in missions_results.items():
        if not res["models"]:
            continue
        icon = res.get("icon", "📦")
        name = res.get("name", slug)
        lines.append(f"{icon} <b>{name}</b>")

        for m in res["models"]:
            mkey    = f"{m['model_brand']} {m['model_name']}"
            current = m.get("current_price_eur")
            low     = m.get("low_price_eur")
            target  = m.get("target")
            notes   = m.get("notes", "")
            trend   = m.get("trend", "")

            # Emoji statut
            if current and target and current <= target:
                status = "✅"
                total_alerts += 1
            elif low and target and low <= target:
                status = "🟡"
                total_alerts += 1
            elif trend == "baisse":
                status = "📉"
            else:
                status = "⏳"

            price_str = f"{current} €" if current else "N/A"
            low_str   = f"  (bas: {low} €)" if low and low != current else ""
            tgt_str   = f"  cible: {target} €" if target else ""
            trend_str = f"  📈" if trend == "hausse" else ("  📉" if trend == "baisse" else "")
            notes_str = f"\n   <i>{notes}</i>" if notes else ""

            lines.append(f"  {status} <b>{mkey}</b> — {price_str}{low_str}{tgt_str}{trend_str}{notes_str}")

        lines.append("")

    if total_alerts > 0:
        lines.insert(2, f"🔔 <b>{total_alerts} alerte(s) prix !</b>")
        lines.insert(3, "")

    lines.append("<i>Purchase Intelligence Agent · IA uniquement, vérifiez avant d'acheter</i>")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def process_mission(slug, api_key, dry_run=False):
    """Traite une mission : check prix + mise à jour historique."""
    paths    = mission_paths(slug)
    shortlist = load_json(paths["shortlist"])
    mission_meta = load_mission_meta(slug)

    if not shortlist or not shortlist.get("locked"):
        print(f"   [{slug}] Shortlist non verrouillée — ignorée", flush=True)
        return None

    models = [m for m in shortlist.get("models", []) if m.get("enabled", True)]
    if not models:
        print(f"   [{slug}] Aucun modèle actif", flush=True)
        return None

    print(f"   [{slug}] {len(models)} modèles → interrogation Claude...", flush=True)
    try:
        price_data = check_prices(api_key, slug, models, mission_meta)
    except Exception as e:
        print(f"   [{slug}] Erreur Claude : {e}", flush=True)
        price_data = []

    # Index par clé normalisée
    price_map = {}
    for pd in price_data:
        key = pd.get("key", "")
        price_map[key] = pd
        # Essais de matching approximatif
        for m in models:
            mkey = f"{m['brand']} {m['model']}"
            if m["brand"].lower() in key.lower() and m["model"].lower() in key.lower():
                price_map[mkey] = pd

    # Mise à jour historique
    history = load_json(paths["history"], {})
    results_models = []
    for m in models:
        mkey = f"{m['brand']} {m['model']}"
        pd   = price_map.get(mkey, price_map.get(f"{m['brand']} {m['model']}", {}))

        if pd and not dry_run:
            history = update_history(history, mkey, pd)

        results_models.append({
            "model_brand":       m["brand"],
            "model_name":        m["model"],
            "target":            m.get("target_purchase_price"),
            "quality_score":     m.get("quality_score"),
            "current_price_eur": pd.get("current_price_eur"),
            "low_price_eur":     pd.get("low_price_eur"),
            "notes":             pd.get("notes",""),
            "trend":             pd.get("trend",""),
            "confidence":        pd.get("confidence", 0),
        })
        cp   = pd.get("current_price_eur","?")
        low  = pd.get("low_price_eur","?")
        tgt  = m.get("target_purchase_price","?")
        note = pd.get("notes","")
        print(f"      {mkey}: {cp}€ (bas: {low}€) cible: {tgt}€  {note}", flush=True)

    if not dry_run:
        save_json(paths["history"], history)
        print(f"   [{slug}] Historique mis à jour → {paths['history']}", flush=True)

    return {
        "slug":   slug,
        "icon":   mission_meta.get("icon","📦"),
        "name":   mission_meta.get("name", slug),
        "models": results_models,
    }


def main():
    parser = argparse.ArgumentParser(description="Purchase Intelligence Tracker")
    parser.add_argument("--mission", help="Slug de la mission")
    parser.add_argument("--all",     action="store_true", help="Toutes les missions")
    parser.add_argument("--dry-run", action="store_true", help="Sans écriture ni Telegram")
    args = parser.parse_args()

    if not args.mission and not args.all:
        parser.error("Spécifie --mission slug ou --all")

    cfg = load_config()
    if not cfg["anthropic"]:
        raise SystemExit("ANTHROPIC_API_KEY manquant (env ou config.local.json)")

    # Sélection des slugs
    if args.all:
        slugs = load_all_slugs()
        print(f"Missions verrouillées : {slugs}", flush=True)
    else:
        slugs = [args.mission]

    if not slugs:
        print("Aucune shortlist verrouillée trouvée.")
        return

    today    = date.today()
    week_str = today.strftime("%d/%m/%Y")
    print(f"\n🛒 Purchase Intelligence Tracker — {week_str}", flush=True)
    print(f"   Mode : {'DRY-RUN' if args.dry_run else 'LIVE'}", flush=True)
    print()

    missions_results = {}
    for slug in slugs:
        print(f"[{slug}]", flush=True)
        result = process_mission(slug, cfg["anthropic"], dry_run=args.dry_run)
        if result:
            missions_results[slug] = result
        time.sleep(2)  # politesse API

    if not missions_results:
        print("Aucun résultat à envoyer.")
        return

    # Rapport Telegram
    report = format_tg_report(missions_results, week_str)
    print("\n── Rapport Telegram ──────────────────────────")
    print(report)
    print("──────────────────────────────────────────────")

    if args.dry_run:
        print("\n[DRY-RUN] Telegram non envoyé.")
        return

    if not cfg["tg_token"] or not cfg["tg_chat_id"]:
        print("\nTelegram non configuré — rapport affiché seulement.")
        print("Configure TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID.")
        return

    print("\nEnvoi Telegram...", flush=True)
    r = tg_send(cfg["tg_token"], cfg["tg_chat_id"], report)
    if r and r.get("ok"):
        print("✅ Message envoyé !")
    else:
        print(f"❌ Échec : {r}")


if __name__ == "__main__":
    main()
