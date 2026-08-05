"""
Purchase Intelligence Agent — moteur générique
Lit une mission (missions/{slug}.json), analyse le marché FR,
génère un rapport HTML + scored.json + shortlist.json.

Usage:
  python agent.py --mission refrigerateur
  python agent.py --mission television --output-dir reports/
"""

import json, os, re, time, argparse
import urllib.request, urllib.error, urllib.parse
from datetime import datetime

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
MISSIONS_DIR = os.path.join(SCRIPT_DIR, "missions")
DATA_DIR     = os.path.join(SCRIPT_DIR, "data")
REPORTS_DIR  = os.path.join(SCRIPT_DIR, "reports")


# ── Config & API key ──────────────────────────────────────────────────────────

def load_api_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    for cfg_path in [
        os.path.join(SCRIPT_DIR, "config.json"),
        os.path.join(SCRIPT_DIR, "..", "feeds", "config.local.json"),
        os.path.join(SCRIPT_DIR, "..", "maison", "config.json"),
    ]:
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
            k = cfg.get("anthropic_api_key", "")
            if k and not k.startswith("REMPLACE"):
                return k
    raise SystemExit("Cle API Anthropic introuvable.")


def load_mission(slug, overrides=None):
    path = os.path.join(MISSIONS_DIR, f"{slug}.json")
    if not os.path.exists(path):
        raise SystemExit(f"Mission introuvable : {path}")
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    if overrides:
        for k, v in (overrides or {}).items():
            m.setdefault("_overrides", {})[k] = v
    return m

def mission_paths(slug):
    """Retourne les chemins de fichiers pour un slug de mission."""
    data_dir = os.path.join(DATA_DIR, slug)
    os.makedirs(data_dir,   exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    return {
        "scored":    os.path.join(data_dir, "scored.json"),
        "shortlist": os.path.join(data_dir, "shortlist.json"),
        "report":    os.path.join(REPORTS_DIR, f"{slug}_report.html"),
    }


# ── Web helpers ───────────────────────────────────────────────────────────────

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,*/*;q=0.9",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"[ERREUR FETCH: {e}]"

def strip_html(html, max_chars=5000):
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL|re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>",  " ", text, flags=re.DOTALL|re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:max_chars]

def fetch_expert_sources(mission):
    data = {}
    for url in mission.get("expert_sources", []):
        key = re.sub(r"https?://(?:www\.)?([^/]+).*", r"\1", url)
        print(f"   -> {key}...", flush=True)
        data[key] = strip_html(fetch(url), 5000)
        time.sleep(1.2)
    return data


# ── Claude helper ─────────────────────────────────────────────────────────────

def make_expert_system(mission):
    domain = mission.get("domain", "grande consommation")
    return (f"Tu es un expert {domain} specialise marche francais. "
            f"Tu connais precisement les modeles disponibles, leur fiabilite reelle "
            f"(pannes, SAV, historique), les prix pratiques et les tactiques de fausses promotions. "
            f"REGLE ABSOLUE : tu reponds UNIQUEMENT avec du JSON brut valide. "
            f"INTERDIT : blocs markdown, texte introductif, commentaires. "
            f"OBLIGATOIRE : commencer directement par {{ ou [, finir par }} ou ]")

def claude(api_key, prompt, system=None, max_tokens=4096):
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
        with urllib.request.urlopen(req, timeout=180) as r:
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
    raw = m.group(0) if m else cleaned
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    fixed = raw
    ob = fixed.count('{') - fixed.count('}')
    br = fixed.count('[') - fixed.count(']')
    for ch in [',', '"', ':']:
        if fixed.rstrip().endswith(ch):
            fixed = fixed.rstrip()[:-1]
    fixed += ']' * max(0, br) + '}' * max(0, ob)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        raise ValueError(f"JSON non parseable:\n{text[:400]}")


# ════════════════════════════════════════════════════════════════════════════════
# PHASE 1 — Scan univers marques + pre-filtrage
# ════════════════════════════════════════════════════════════════════════════════

def _discover_prompt_generic(mission, brand_universe, dims, perf, sens, sources_text):
    """Prompt générique pour la découverte d'univers produit."""
    product_type = mission.get("product_type", "produit")
    dim_strs = [f"{k} ≤ {v.get('max')}" for k, v in dims.items() if v.get("max")]
    perf_str = f"Performance principale ≥ {perf.get('minimum',0)} {perf.get('unit','')}" if perf else ""
    sens_str = f"Contrainte sensorielle ≤ {sens.get('maximum',0)} {sens.get('unit','')}" if sens else ""
    n_fin    = mission.get("n_finalists", 6)
    return f"""MISSION : identifier les meilleurs {product_type} disponibles en France.

UNIVERS DE MARQUES A EVALUER — TOUTES sans exception :
{', '.join(brand_universe)}

FILTRES ELIMINATOIRES :
{chr(10).join(filter(None, dim_strs + [perf_str, sens_str]))}
- Produit NEUF disponible en France

METHODE :
1. Pour chaque marque, identifie 1-3 references pertinentes
2. Elimine les non-conformes — explique pourquoi
3. Classe les {n_fin*2} meilleurs candidats survivants par interet potentiel
4. Indique pour chaque marque si elle a des candidats ou non

DONNEES EXPERTES :
{sources_text}

JSON attendu :
{{"brand_coverage":{{"Marque1":"Candidat KGN49XLEA (700mm, 440L)","Marque2":"Hors budget (>1500 EUR)"}},
"candidates":[
  {{"brand":"X","model":"Y","type":"combine","width_mm":700,"height_mm":200,"depth_mm":67,
    "volume_total_L":440,"noise_db":37,"energy_class":"D","annual_kwh":166,
    "price_fr_eur":849,"warranty_years":2,
    "features":["no_frost_total","fast_cooling"],
    "reliability_notes":"Compresseur eprouve, SAV FR dense","source":"Les Numeriques + marche FR"}}
]}}"""

def _discover_prompt_furniture(mission, brand_universe, sources_text):
    """Prompt spécialisé pour les canapés/meubles — détection OEM + fabricant vs distributeur."""
    product_type   = mission.get("product_type", "produit")
    n_fin          = mission.get("n_finalists", 12)
    tiers          = mission.get("manufacturer_tiers", {})
    oem            = mission.get("oem_detection", {})
    construction   = mission.get("construction_spec", {})
    red_flags      = mission.get("red_flags", [])
    longevity_obj  = mission.get("longevity_objective", "")

    tiers_text = ""
    for tier, info in tiers.items():
        if tier == "distributors": continue
        brands_str = ", ".join(info.get("brands", []))
        tiers_text += f"  {tier.replace('_',' ').upper()} : {brands_str} — {info.get('note','')}\n"

    distributors = tiers.get("distributors", {}).get("brands", [])

    foam_seat = construction.get("foams",{}).get("seat",{})
    susps = construction.get("suspensions",{}).get("ranking",[])
    susps_text = " > ".join(s.get("type","") for s in susps)
    struct_ok  = ", ".join(construction.get("structure",{}).get("preferred",[]))
    struct_ko  = ", ".join(construction.get("structure",{}).get("avoid",[]))
    fabric_min = construction.get("fabric",{}).get("martindale_minimum",80000)

    oem_instruction = oem.get("instruction","")
    red_flags_text  = "\n".join(f"  - {rf}" for rf in red_flags)

    return f"""MISSION : identifier les meilleurs {product_type} disponibles en France.

OBJECTIF : {longevity_obj}

MARQUES A EVALUER — TOUTES :
{', '.join(brand_universe)}

HIERARCHIE QUALITE FABRICANTS :
{tiers_text}
MARQUES DISTRIBUTEURS (identifier leur fabricant OEM) :
{', '.join(distributors)}

DETECTION OEM OBLIGATOIRE :
{oem_instruction}

SIGNAUX D'ALARME — jamais noter haut si :
{red_flags_text}

SPECIFICATIONS TECHNIQUES REQUISES :
Structure : preferee = {struct_ok} | a eviter = {struct_ko}
Suspensions (ordre preference) : {susps_text}
Mousse assise : HR {foam_seat.get('density_kg_m3',{}).get('min',35)}-{foam_seat.get('density_kg_m3',{}).get('max',45)} kg/m3
Tissu : Martindale > {fabric_min}

DONNEES EXPERTES :
{sources_text}

METHODE :
1. Pour chaque marque/distributeur, identifie 1-2 modeles 3 places ou angle representatifs
2. Pour les distributeurs : identifie le fabricant OEM reel (usine, pays, gamme)
3. Detecte les doublons OEM entre distributeurs differents
4. Elimine les marques sans donnees techniques fiables (red flags)
5. Selectionne les {n_fin*2} meilleurs candidats

JSON :
{{"brand_coverage":{{"SITS":"Modele Neva 3P (fabricant direct, Suede/Pologne)","Story":"Modele Oslo (fabricant OEM inconnu — insuffisant)"}},
"candidates":[
  {{"brand":"SITS","model":"Neva","actual_manufacturer":"SITS","manufacturer_country":"Pologne",
    "distributor_brand":null,"is_distributor_brand":false,
    "oem_siblings":[],
    "type":"3_places","seats":3,"price_fr_eur":2200,"warranty_years":5,
    "structure":"hetre massif + multiplis bouleau","suspensions":"ressorts Nosag",
    "foam_seat_density_kg_m3":40,"foam_type":"HR haute resilience",
    "martindale":100000,"fabric_type":"tissu premium",
    "features":["modulable","tetes_appui_reglables"],
    "reliability_notes":"Fabricant scandinave reference, SAV FR via revendeurs agrees",
    "source":"quechoisir + avis revendeurs"}}
]}}"""

def discover_universe(api_key, mission, sources):
    product_type   = mission.get("product_type", "produit")
    brand_universe = mission.get("brand_universe", [])
    dims = mission.get("constraints", {}).get("mandatory", {}).get("dimensions", {})
    perf = mission.get("criteria", {}).get("main_performance", {})
    sens = mission.get("criteria", {}).get("sensory", {})
    sources_text = "\n\n".join(f"=== {k.upper()} ===\n{v}" for k, v in sources.items()) if sources else "(pas de sources)"

    # Choix du prompt selon le domaine
    is_furniture = bool(mission.get("manufacturer_tiers") or mission.get("oem_detection"))
    if is_furniture:
        prompt = _discover_prompt_furniture(mission, brand_universe, sources_text)
        max_tok = 4500
    else:
        prompt = _discover_prompt_generic(mission, brand_universe, dims, perf, sens, sources_text)
        max_tok = 3500

    text = claude(api_key, prompt, system=make_expert_system(mission), max_tokens=max_tok)
    data = parse_json(text)

    # Filtrage hard en Python
    crit = mission.get("criteria", {})
    perf_min = crit.get("main_performance", {}).get("minimum", 0)
    sens_max = crit.get("sensory", {}).get("maximum", 9999)
    w_max    = dims.get("width", {}).get("max", 9999) if dims else 9999

    candidates = []
    for c in data.get("candidates", []):
        vol = c.get("volume_total_L") or 0
        w   = c.get("width_mm") or 0
        db  = c.get("noise_db") or 0
        if perf_min and vol and vol < perf_min:
            print(f"   [filtre] {c['brand']} {c['model']} : volume {vol} < {perf_min}", flush=True)
            continue
        if w_max and w and w > w_max:
            print(f"   [filtre] {c['brand']} {c['model']} : largeur {w} > {w_max}", flush=True)
            continue
        if sens_max and db and db > sens_max:
            print(f"   [filtre] {c['brand']} {c['model']} : bruit {db} > {sens_max}", flush=True)
            continue
        candidates.append(c)

    data["candidates"] = candidates
    return data


# ════════════════════════════════════════════════════════════════════════════════
# PHASE 2 — Classement rapide → top 6 finalistes
# ════════════════════════════════════════════════════════════════════════════════

def quick_rank(api_key, mission, universe_data):
    dims_eval = mission.get("evaluation", {}).get("dimensions", {})
    r_w  = dims_eval.get("reliability", {}).get("weight", 35)
    p_w  = dims_eval.get("main_performance", {}).get("weight", 20)
    c_w  = dims_eval.get("total_cost", {}).get("weight", 20)
    r_l  = dims_eval.get("reliability", {}).get("label", "Fiabilite")
    p_l  = dims_eval.get("main_performance", {}).get("label", "Performance")
    c_l  = dims_eval.get("total_cost", {}).get("label", "Cout")
    product_type = mission.get("product_type", "produit")

    candidates = universe_data.get("candidates", [])

    prompt = f"""Classement rapide de {len(candidates)} {product_type}.

CRITERES (3 cles, total {r_w+p_w+c_w}) :
- {r_l} : {r_w} pts (fiabilite historique, SAV France)
- {p_l} : {p_w} pts (performance principale, adequation usage)
- {c_w} : {c_w} pts (rapport Q/P marche FR)

Identifie les 6 meilleurs finalistes. Pour chaque modele : sous-total + verdict.

CANDIDATS :
{json.dumps(candidates, ensure_ascii=False, indent=2)}

JSON :
{{"ranked":[
  {{"brand":"X","model":"Y","quick_scores":{{"reliability":28,"performance":16,"cost":16,"subtotal":60}},
    "finalist":true,"selection_reason":"Meilleure fiabilite + volume ideal"}}
],"finalist_summary":"Explication des 6 choix en 2 phrases."}}"""

    text = claude(api_key, prompt, system=make_expert_system(mission), max_tokens=2500)
    data = parse_json(text)

    ranked_map = {f"{r['brand']}_{r['model']}": r for r in data.get("ranked", [])}
    all_ranked, finalists_info = [], []

    for c in candidates:
        key  = f"{c['brand']}_{c['model']}"
        rank = ranked_map.get(key, {})
        c["quick_scores"]     = rank.get("quick_scores", {})
        c["finalist"]         = rank.get("finalist", False)
        c["selection_reason"] = rank.get("selection_reason", "")
        all_ranked.append(c)
        if c["finalist"]:
            finalists_info.append(c)

    n_fin_target = mission.get("n_finalists", 6)
    if len(finalists_info) < 4:
        all_ranked.sort(key=lambda x: -x.get("quick_scores", {}).get("subtotal", 0))
        finalists_info = all_ranked[:n_fin_target]
        for c in finalists_info:
            c["finalist"] = True

    data["candidates_ranked"] = all_ranked
    data["finalists"]         = finalists_info
    return data


# ════════════════════════════════════════════════════════════════════════════════
# PHASE 3 — Analyse experte complète (lots de 3)
# ════════════════════════════════════════════════════════════════════════════════

def _score_batch(api_key, mission, batch):
    dims_eval    = mission.get("evaluation", {}).get("dimensions", {})
    product_type = mission.get("product_type", "produit")
    years        = mission.get("context", {}).get("tco_years", 15)
    perf         = mission.get("criteria", {}).get("main_performance", {})
    sens         = mission.get("criteria", {}).get("sensory", {})
    is_furniture = bool(mission.get("manufacturer_tiers") or mission.get("oem_detection"))

    dims_text = "\n".join(
        f"- {v.get('label','?')} : {v.get('weight',0)} pts — {v.get('description','')}"
        for v in dims_eval.values()
    )

    perf_desc = f"0 si < {perf.get('minimum',0)}{perf.get('unit','')}, max a {perf.get('ideal', perf.get('minimum',0))}{perf.get('unit','')}" if perf else ""
    sens_desc = f"<= {sens.get('target',0)}{sens.get('unit','')} = max, > {sens.get('maximum',0)}{sens.get('unit','')} = 0" if sens else ""

    # Bloc OEM/fabricant pour mobilier
    oem_block = ""
    if is_furniture:
        oem = mission.get("oem_detection", {})
        construction = mission.get("construction_spec", {})
        longevity = mission.get("longevity_objective", "")
        tiers = mission.get("manufacturer_tiers", {})
        tiers_summary = " | ".join(
            f"{k.replace('_',' ')}: {', '.join(v.get('brands',[])[:4])}"
            for k, v in tiers.items() if k != "distributors" and v.get("brands")
        )
        foam_spec = construction.get("foams",{}).get("seat",{})
        susps = " > ".join(s.get("type","") for s in construction.get("suspensions",{}).get("ranking",[]))
        struct_ok = ", ".join(construction.get("structure",{}).get("preferred",[]))
        oem_block = f"""
OBJECTIF : {longevity}

HIERARCHIE FABRICANTS : {tiers_summary}

DETECTION OEM : {oem.get('instruction','')}

SPECS CONSTRUCTION A VERIFIER ET NOTER :
- Structure : {struct_ok}
- Suspensions (ordre pref) : {susps}
- Mousse assise : HR {foam_spec.get('density_kg_m3',{}).get('min',35)}-{foam_spec.get('density_kg_m3',{}).get('max',45)} kg/m3
- Martindale tissu : > {construction.get('fabric',{}).get('martindale_minimum',80000)}

CHAMPS SUPPLEMENTAIRES OBLIGATOIRES :
actual_manufacturer, manufacturer_country, distributor_brand, is_distributor_brand,
oem_siblings (liste des modeles equivalents chez d'autres distributeurs avec prix et delta),
structure_type, suspension_type, foam_seat_density_kg_m3, martindale, warranty_years,
parts_availability_years, covers_availability, manufacturer_age_years
"""

    prompt = f"""Analyse experte COMPLETE de ces {len(batch)} {product_type}.
{oem_block}
PONDERATIONS (total 100) :
{dims_text}
  Performance : {perf_desc}
  Sensoriel   : {sens_desc}

FOURNIR POUR CHAQUE MODELE :
1. Sous-scores fiabilite : compressor/electronics/sav_france/field_reports + sources
2. Historique generations (3 max) : gen_name, years_range, key_changes
3. Pays fabrication reel
4. Reparabilite 1-5 : duree pieces, cout, facile vs difficile
5. Risque : faible/modere/eleve + facteurs
6. Confiance decomposee (total 100) en 5 composantes :
   user_reviews(max 25), independent_tests(max 20), price_history(max 15),
   model_longevity(max 15), source_consistency(max 25)
   — pour chaque : value, max, note courte
7. Historique prix structure : periode, typical_eur, promo_low_eur
8. Verdict : action(buy_now/wait_promo/wait_successor), summary(1 phrase),
   buy_if_below_eur, best_moment, risk_of_waiting
9. Proba panne {years} ans (%) + cout reparation moyen (EUR)
10. Classement final + prix analyse + forces/faiblesses (max 3) + justification (1 phrase)

MODELES :
{json.dumps(batch, ensure_ascii=False, indent=2)}

JSON (tableau scored) :
{{"scored":[
  {{"brand":"X","model":"Y","type":"combine",
    "width_mm":700,"height_mm":200,"depth_mm":67,"volume_total_L":440,
    "noise_db":37,"energy_class":"D","annual_kwh":166,"price_fr_eur":849,
    "warranty_years":2,"features":["no_frost_total"],
    "country_of_origin":"Espagne",
    "generation_history":[
      {{"gen":"Serie 4","years":"2012-2018","key_changes":"Compresseur standard"}},
      {{"gen":"Serie 6+","years":"2022+","key_changes":"VarioInverter ameliore — generation actuelle"}}
    ],
    "current_generation":"Serie 6+ (2022+)",
    "scores":{{"reliability":30,"main_performance":17,"total_cost":15,
      "resource_efficiency":7,"sensory":7,"features":3,"total":79,
      "reliability_breakdown":{{"compressor":11,"electronics":7,"sav_france":8,"field_reports":4}},
      "reliability_sources":["Que Choisir 8.5/10","1400 avis 4.2/5"]}},
    "repairability":{{"score":4,"parts_years":10,"parts_cost_level":"modere",
      "easy_repairs":["joints","clayettes"],"hard_repairs":["carte elec 250-400 EUR"]}},
    "risk_level":"faible","risk_factors":["Compresseur eprouve","SAV dense"],
    "confidence":{{"total":83,"components":[
      {{"label":"Avis utilisateurs","value":20,"max":25,"note":"1400 avis 4.2/5"}},
      {{"label":"Tests independants","value":18,"max":20,"note":"QC 8.5/10"}},
      {{"label":"Historique prix","value":12,"max":15,"note":"Donnees partielles"}},
      {{"label":"Recul modele","value":15,"max":15,"note":"Gamme depuis 2019"}},
      {{"label":"Coherence sources","value":18,"max":25,"note":"Quelques divergences"}}
    ]}},
    "price_history":[
      {{"period":"2022","typical_eur":999,"promo_low_eur":849}},
      {{"period":"BF 2023","typical_eur":849,"promo_low_eur":699}}
    ],
    "verdict":{{"action":"wait_promo","summary":"Attendre Black Friday pour -15%.",
      "buy_if_below_eur":720,"best_moment":"Black Friday (novembre)","risk_of_waiting":"faible"}},
    "estimated_repair_cost_eur":170,"repair_probability_15yr_pct":16,
    "ranking":"good_but_wait","price_analysis":"Attendre soldes -15%",
    "strengths":["Fiabilite excellente","SAV top","Pieces 10 ans"],
    "weaknesses":["Classe D","Profondeur 67cm"],
    "justification":"Reference fiabilite/SAV — attendre promo."
  }}
]}}"""

    text = claude(api_key, prompt, system=make_expert_system(mission), max_tokens=8000)
    data = parse_json(text)
    if isinstance(data, list):
        return data
    return data.get("scored", [])


def deep_score(api_key, mission, rank_data):
    finalists  = rank_data.get("finalists", [])
    all_scored = []
    batch_size = 3
    for i in range(0, len(finalists), batch_size):
        batch = finalists[i:i + batch_size]
        n_fin = len(finalists)
        print(f"   Lot {i//batch_size+1}/{(n_fin+batch_size-1)//batch_size} ({len(batch)} modeles)...", flush=True)
        all_scored.extend(_score_batch(api_key, mission, batch))

    top  = sorted(all_scored, key=lambda x: -x.get("scores", {}).get("total", 0))
    top3 = ", ".join(f"{p['brand']} {p['model']} ({p['scores']['total']}/100)" for p in top[:3])
    names = ", ".join(f"{p['brand']} {p['model']}" for p in all_scored)
    summary_prompt = (f"Resume en 2 phrases la recommandation parmi ces produits : {names}. "
                      f"Top 3 : {top3}. Reponds UNIQUEMENT avec le texte, sans JSON.")
    summary = claude(api_key, summary_prompt, max_tokens=200)
    return {"scored": all_scored, "summary": summary.strip()}


# ════════════════════════════════════════════════════════════════════════════════
# CALCULS PYTHON — TCO + Pareto
# ════════════════════════════════════════════════════════════════════════════════

def compute_tco(p, kwh_price=0.22, years=15):
    price    = p.get("price_fr_eur") or 0
    kwh_yr   = p.get("annual_kwh") or 0
    elec     = round(kwh_yr * years * kwh_price) if kwh_yr else 0
    prob     = (p.get("repair_probability_15yr_pct") or 20) / 100
    repair_c = p.get("estimated_repair_cost_eur") or 150
    repair   = round(prob * repair_c)
    total    = price + elec + repair
    return {
        "purchase_eur":        price,
        "electricity_eur":     elec,
        "repair_expected_eur": repair,
        "total_eur":           total,
        "annual_eur":          round(total / years) if years else 0,
        "kwh_price":           kwh_price,
        "years":               years,
    }

def compute_pareto(products):
    keys = ["reliability", "main_performance", "total_cost",
            "resource_efficiency", "sensory", "features"]
    def vec(p):
        s = p.get("scores", {})
        return [s.get(k, 0) for k in keys]
    vecs = [vec(p) for p in products]
    n = len(products)
    dominated = [False] * n
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            if (all(vecs[j][k] >= vecs[i][k] for k in range(len(keys))) and
                    any(vecs[j][k] >  vecs[i][k] for k in range(len(keys)))):
                dominated[i] = True
                break
    for i, p in enumerate(products):
        p["pareto_non_dominated"] = not dominated[i]
    return products

def build_shop_urls(brand, model, retailers):
    """Construit des URLs de recherche pour chaque revendeur."""
    query = urllib.parse.quote_plus(f"{brand} {model}")
    return [
        {"name": r["name"], "url": r["search_url"].replace("{query}", query)}
        for r in retailers
    ]

def enrich_scored(scored_data, mission):
    kwh_price = mission.get("context", {}).get("kwh_price_eur", 0.22)
    years     = mission.get("context", {}).get("tco_years", 15)
    retailers = mission.get("retailers", [])
    for p in scored_data.get("scored", []):
        p["tco"]       = compute_tco(p, kwh_price, years)
        p["shop_urls"] = build_shop_urls(p["brand"], p["model"], retailers)
    compute_pareto(scored_data["scored"])
    return scored_data


# ════════════════════════════════════════════════════════════════════════════════
# RAPPORT HTML (avec CSS print + bouton PDF navigateur)
# ════════════════════════════════════════════════════════════════════════════════

RANK_STYLE = {
    "buy_now":        ("ACHETER MAINTENANT", "#22543d", "#c6f6d5", "#276749"),
    "excellent_deal": ("EXCELLENT DEAL",     "#2a4365", "#bee3f8", "#2b6cb0"),
    "good_but_wait":  ("BON - ATTENDRE",     "#7b341e", "#feebc8", "#c05621"),
    "avoid":          ("EVITER",             "#742a2a", "#fed7d7", "#c53030"),
}

def bar(val, max_val, color):
    pct = min(100, round(val/max_val*100)) if max_val else 0
    return (f'<div style="background:#e2e8f0;border-radius:3px;height:7px;margin-top:2px">'
            f'<div style="background:{color};width:{pct}%;height:7px;border-radius:3px"></div></div>')

def build_report(scored_data, universe_data, rank_data, mission):
    products     = sorted(scored_data["scored"], key=lambda x: -x["scores"]["total"])
    dims_eval    = mission.get("evaluation", {}).get("dimensions", {})
    product_type = mission.get("product_type", "produit")
    icon         = mission.get("icon", "📦")
    name         = mission.get("name", product_type)
    kwh_price    = mission.get("context", {}).get("kwh_price_eur", 0.22)
    years        = mission.get("context", {}).get("tco_years", 15)
    now          = datetime.now().strftime("%d/%m/%Y a %Hh%M")

    # Funnel banner
    brand_cov  = universe_data.get("brand_coverage", {})
    n_cand     = len(universe_data.get("candidates", []))
    n_brands   = len(brand_cov)
    fin_sum    = rank_data.get("finalist_summary", "")
    brand_rows = "".join(
        f'<tr><td style="padding:2px 12px 2px 0;font-weight:600;color:#ccc">{b}</td>'
        f'<td style="color:{"#68d391" if "candidat" in s.lower() or "trouve" in s.lower() else "#a0aec0"};font-size:.78rem">{s}</td></tr>'
        for b, s in brand_cov.items()
    )
    funnel_html = f"""
<div style="background:#1a1a2e;color:#e2e8f0;border-radius:12px;padding:20px 24px;margin-bottom:24px">
  <div style="font-size:.72rem;text-transform:uppercase;color:#4299e1;letter-spacing:.07em;margin-bottom:10px">Funnel de selection</div>
  <div style="display:flex;gap:24px;font-size:.9rem;flex-wrap:wrap;margin-bottom:14px">
    <div><strong style="font-size:1.4rem;color:#63b3ed">{n_brands}</strong><br>marques evaluees</div>
    <div style="color:#4299e1;font-size:1.5rem;align-self:center">&#8594;</div>
    <div><strong style="font-size:1.4rem;color:#63b3ed">{n_cand}</strong><br>candidats conformes</div>
    <div style="color:#4299e1;font-size:1.5rem;align-self:center">&#8594;</div>
    <div><strong style="font-size:1.4rem;color:#63b3ed">{len(products)}</strong><br>finalistes analyses</div>
  </div>
  <details><summary style="cursor:pointer;color:#a0aec0;font-size:.82rem">Detail marques &#9660;</summary>
    <table style="margin-top:10px;border-collapse:collapse">{brand_rows}</table>
    {f'<p style="font-size:.8rem;color:#a0aec0;margin-top:10px">{fin_sum}</p>' if fin_sum else ""}
  </details>
</div>"""

    # Cards
    DIM_COLORS = {
        "reliability":        "#e53e3e",
        "main_performance":   "#3182ce",
        "total_cost":         "#38a169",
        "resource_efficiency":"#d69e2e",
        "sensory":            "#805ad5",
        "features":           "#dd6b20",
    }
    cards = ""
    for p in products:
        rank   = p.get("ranking", "good_but_wait")
        label, txt_dark, bg_light, border = RANK_STYLE.get(rank, RANK_STYLE["good_but_wait"])
        s      = p["scores"]
        total  = s["total"]
        tco    = p.get("tco", {})
        conf   = (p.get("confidence") or {}).get("total") or p.get("confidence_score", 0)
        verdict= p.get("verdict", {})

        score_rows = "".join(f"""<tr>
          <td style="font-size:.76rem;color:#666;padding:2px 10px 2px 0;white-space:nowrap">{v.get('label','')}</td>
          <td style="min-width:80px">{bar(s.get(k,0),v.get('weight',0),DIM_COLORS.get(k,'#aaa'))}</td>
          <td style="font-size:.76rem;text-align:right;padding-left:6px;font-weight:600">{s.get(k,0)}/{v.get('weight',0)}</td>
        </tr>""" for k, v in dims_eval.items())

        rb   = s.get("reliability_breakdown", {})
        rsrc = s.get("reliability_sources", [])
        rb_html = ""
        if rb:
            rb_html = f"""<div style="margin-top:8px;padding:8px 12px;background:#fff5f5;border-radius:7px;font-size:.74rem;color:#555">
              <strong style="color:#c53030">Detail fiabilite : </strong>
              Compresseur {rb.get("compressor","?")} /12 &nbsp;·&nbsp;
              Electronique {rb.get("electronics","?")} /10 &nbsp;·&nbsp;
              SAV {rb.get("sav_france","?")} /8 &nbsp;·&nbsp;
              Terrain {rb.get("field_reports","?")} /5<br>
              <span style="color:#888">{" · ".join(rsrc)}</span></div>"""

        tco_html = ""
        if tco and tco.get("total_eur"):
            tco_html = f"""<div style="margin-top:12px;background:#f0fff4;border-radius:8px;padding:10px 14px;font-size:.82rem">
              <strong style="color:#276749">TCO {years} ans</strong> &nbsp;
              Achat {tco.get("purchase_eur","?")} EUR
              + Electricite {tco.get("electricity_eur","?")} EUR
              + Reparations ~{tco.get("repair_expected_eur","?")} EUR
              = <strong style="color:#276749">{tco.get("total_eur","?")} EUR</strong>
              <span style="color:#888"> ({tco.get("annual_eur","?")} EUR/an)</span>
            </div>"""

        verdict_html = ""
        if verdict:
            v_bg = {"buy_now":"#c6f6d5","wait_promo":"#fefcbf","wait_successor":"#bee3f8"}.get(verdict.get("action",""),"#f7fafc")
            verdict_html = f"""<div style="background:{v_bg};border-radius:8px;padding:10px 14px;margin-top:12px;font-size:.83rem">
              <strong>Verdict :</strong> {verdict.get("summary","")}
              &nbsp;·&nbsp; Cible : <strong>{verdict.get("buy_if_below_eur","?")} EUR</strong>
              &nbsp;·&nbsp; Meilleur moment : {verdict.get("best_moment","?")}
            </div>"""

        ph = p.get("price_history", [])
        ph_html = ""
        if ph:
            ph_items = "".join(
                f'<div style="text-align:center;padding:4px 8px;background:#f7fafc;border-radius:4px">'
                f'<div style="font-size:.68rem;color:#888">{x.get("period","")}</div>'
                f'<div style="font-weight:700;font-size:.82rem">{x.get("typical_eur","?")} EUR</div>'
                f'{"<div style=color:#e53e3e;font-size:.72rem>" + str(x["promo_low_eur"]) + " EUR promo</div>" if x.get("promo_low_eur") else ""}'
                f'</div>'
                for x in ph
            )
            ph_html = f'<div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">{ph_items}</div>'

        gen_html = ""
        gen_history = p.get("generation_history", [])
        if gen_history:
            gen_items = "".join(
                f'<div style="padding:6px 10px;border-radius:6px;margin-bottom:4px;'
                f'background:{"#f0fff4" if "actuel" in g.get("key_changes","").lower() else "#f7fafc"}">'
                f'<strong>{g.get("gen","")}</strong> <span style="color:#888;font-size:.78rem">({g.get("years","")})</span><br>'
                f'<span style="font-size:.78rem;color:#555">{g.get("key_changes","")}</span></div>'
                for g in gen_history
            )
            gen_html = f'<div style="margin-top:10px">{gen_items}</div>'

        st_html = "".join(f"<li>{x}</li>" for x in p.get("strengths",[]))
        wk_html = "".join(f"<li style='color:#c53030'>{x}</li>" for x in p.get("weaknesses",[]))
        origin  = p.get("country_of_origin","")
        cur_gen = p.get("current_generation", p.get("manufacturing_generation",""))

        cards += f"""
<div style="background:white;border-radius:14px;box-shadow:0 2px 12px rgba(0,0,0,.09);
  padding:24px;margin-bottom:28px;border-top:5px solid {border}" class="product-card">

  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;margin-bottom:14px">
    <div>
      <span style="background:{bg_light};color:{txt_dark};padding:3px 14px;border-radius:20px;font-size:.75rem;font-weight:700">{label}</span>
      {"<span style='background:#e9d8fd;color:#553c9a;padding:3px 10px;border-radius:20px;font-size:.72rem;font-weight:700;margin-left:6px'>Pareto</span>" if p.get("pareto_non_dominated") else ""}
      <h2 style="font-size:1.25rem;font-weight:800;margin:8px 0 4px;color:#1a1a2e">{p["brand"]} {p["model"]}</h2>
      <div style="font-size:.82rem;color:#718096">
        {f"Fab. {origin} · " if origin else ""}{cur_gen}
      </div>
    </div>
    <div style="text-align:right">
      <div style="font-size:2.4rem;font-weight:900;color:{border};line-height:1">{total}</div>
      <div style="font-size:.72rem;color:#aaa">/100</div>
      <div style="font-size:1.2rem;font-weight:700">~{p.get("price_fr_eur","?")} EUR</div>
      <div style="background:{"#c6f6d5" if conf>=80 else "#feebc8" if conf>=55 else "#fed7d7"};
        color:{"#276749" if conf>=80 else "#c05621" if conf>=55 else "#c53030"};
        padding:2px 10px;border-radius:10px;font-size:.74rem;font-weight:700;margin-top:4px">
        Confiance {conf}%
      </div>
    </div>
  </div>

  <div style="display:grid;grid-template-columns:auto 1fr;gap:18px;align-items:start">
    <table style="border-collapse:collapse">{score_rows}</table>
    <ul style="font-size:.81rem;margin:0;padding-left:16px;line-height:1.85;color:#2d3748">{st_html}{wk_html}</ul>
  </div>
  {rb_html}
  {tco_html}
  {verdict_html}
  {ph_html}
  {gen_html}
  <div style="margin-top:12px;padding:10px 14px;background:#f7fafc;border-radius:8px;font-size:.83rem;color:#4a5568">
    {p.get("justification","")}
  </div>
  <div style="margin-top:4px;font-size:.74rem;color:#a0aec0;font-style:italic">{p.get("price_analysis","")}</div>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{icon} {name} — Rapport d'achat</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f0f4f8;color:#222}}
header{{background:#1a1a2e;color:white;padding:24px 32px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}}
header h1{{font-size:1.4rem;font-weight:800}}
header p{{font-size:.82rem;color:#a0aec0;margin-top:4px}}
.print-btn{{background:#4299e1;color:white;border:none;padding:8px 18px;border-radius:8px;cursor:pointer;font-size:.85rem;font-weight:600}}
.print-btn:hover{{background:#2b6cb0}}
main{{max-width:960px;margin:0 auto;padding:24px 20px}}
.summary{{background:#1a1a2e;color:#e2e8f0;border-radius:12px;padding:16px 20px;margin-bottom:22px;font-size:.88rem;line-height:1.65;border-left:4px solid #4299e1}}
.note{{text-align:center;font-size:.72rem;color:#a0aec0;margin-top:24px;padding-bottom:20px}}
details summary::-webkit-details-marker{{color:#aaa}}
@media print{{
  .print-btn,.no-print{{display:none!important}}
  body{{background:white}}
  header{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
  .product-card{{break-inside:avoid;box-shadow:none!important;border:1px solid #e2e8f0!important}}
  main{{padding:0}}
}}
</style>
</head>
<body>
<header>
  <div>
    <h1>{icon} Rapport d'achat — {name}</h1>
    <p>Genere le {now} · {n_brands} marques evaluees · TCO sur {years} ans · {kwh_price} EUR/kWh · Marche France</p>
  </div>
  <button class="print-btn no-print" onclick="window.print()">🖨️ Imprimer / Sauvegarder PDF</button>
</header>
<main>
  {funnel_html}
  <div class="summary"><strong>Synthese :</strong> {scored_data.get("summary","")}</div>
  {cards}
  <p class="note">Rapport genere par IA (Claude Sonnet). Verifiez prix et disponibilites avant achat.</p>
</main>
</body>
</html>"""


# ── Shortlist ─────────────────────────────────────────────────────────────────

def build_shortlist(scored_data, mission):
    products = sorted(scored_data["scored"], key=lambda x: -x["scores"]["total"])
    models   = []
    for p in products:
        if p.get("ranking") == "avoid":
            continue
        fair = p.get("price_fr_eur", 0) or 0
        tco  = p.get("tco", {})
        rep  = p.get("repairability", {})
        models.append({
            "brand":                 p["brand"],
            "model":                 p["model"],
            "exact_reference":       p["model"],
            "search_terms":          [f"{p['brand']} {p['model']}"],
            "quality_score":         p["scores"]["total"],
            "ranking":               p.get("ranking"),
            "country_of_origin":     p.get("country_of_origin"),
            "risk_level":            p.get("risk_level"),
            "confidence_score":      (p.get("confidence") or {}).get("total") or p.get("confidence_score"),
            "repairability_score":   rep.get("score"),
            "pareto_non_dominated":  p.get("pareto_non_dominated", False),
            "tco_total_eur":         tco.get("total_eur"),
            "tco_annual_eur":        tco.get("annual_eur"),
            "estimated_fair_price":  fair,
            "target_purchase_price": int((p.get("verdict") or {}).get("buy_if_below_eur") or fair * 0.85) if fair else None,
            "price_history_note":    " · ".join(f"{x['period']} {x.get('promo_low_eur') or x.get('typical_eur','?')} EUR" for x in (p.get("price_history") or [])[:3]),
            "strengths":             p.get("strengths", []),
            "weaknesses":            p.get("weaknesses", []),
            "shop_urls":             p.get("shop_urls", []),
            "enabled":               True,
            "notes":                 "",
        })
    return {
        "locked":          False,
        "generated_at":    datetime.now().strftime("%Y-%m-%d"),
        "mission_slug":    mission.get("slug", ""),
        "mission_summary": scored_data.get("summary", ""),
        "models":          models,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Purchase Intelligence Agent")
    parser.add_argument("--mission", required=True, help="Slug de la mission (ex: refrigerateur)")
    args = parser.parse_args()

    api_key = load_api_key()
    mission = load_mission(args.mission)
    paths   = mission_paths(args.mission)
    product_type = mission.get("product_type", "produit")
    icon    = mission.get("icon", "📦")

    n_brands = len(mission.get("brand_universe", []))
    print(f"{icon} Purchase Intelligence — {product_type} — {n_brands} marques", flush=True)
    print()

    print("[1/5] Sources expertes...", flush=True)
    sources = fetch_expert_sources(mission)

    print(f"\n[2/5] Scan {n_brands} marques...", flush=True)
    universe_data = discover_universe(api_key, mission, sources)
    n_cand = len(universe_data.get("candidates", []))
    print(f"   {n_cand} candidats conformes", flush=True)

    print(f"\n[3/5] Classement rapide...", flush=True)
    rank_data = quick_rank(api_key, mission, universe_data)
    n_fin = len(rank_data.get("finalists", []))
    print(f"   {n_fin} finalistes", flush=True)
    for c in rank_data.get("candidates_ranked", []):
        st = "FIN" if c.get("finalist") else "   "
        sub = c.get("quick_scores", {}).get("subtotal", "?")
        print(f"   [{st}] {c['brand']} {c['model']} — {sub}/75", flush=True)

    print(f"\n[4/5] Analyse experte {n_fin} finalistes...", flush=True)
    scored = deep_score(api_key, mission, rank_data)

    print("\n[5/5] TCO + Pareto + rapport...", flush=True)
    scored = enrich_scored(scored, mission)

    html = build_report(scored, universe_data, rank_data, mission)
    with open(paths["report"], "w", encoding="utf-8") as f:
        f.write(html)

    combined = dict(scored)
    combined["_funnel"] = {
        "brand_coverage":   universe_data.get("brand_coverage", {}),
        "all_candidates":   rank_data.get("candidates_ranked", []),
        "finalist_summary": rank_data.get("finalist_summary", ""),
    }
    with open(paths["scored"], "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    shortlist = build_shortlist(scored, mission)
    sl_path = paths["shortlist"]
    existing = {}
    if os.path.exists(sl_path):
        try:
            existing = json.loads(open(sl_path, encoding="utf-8").read())
        except Exception:
            pass
    if not existing.get("locked"):
        with open(sl_path, "w", encoding="utf-8") as f:
            json.dump(shortlist, f, ensure_ascii=False, indent=2)

    print(f"\nRapport   -> {paths['report']}")
    print(f"Shortlist -> {sl_path}")
    print()
    rank_lbl = {"buy_now":"[ACHETER]","excellent_deal":"[EXCELLENT]","good_but_wait":"[ATTENDRE]","avoid":"[EVITER]"}
    for i, p in enumerate(sorted(scored["scored"], key=lambda x: -x["scores"]["total"]), 1):
        r   = rank_lbl.get(p.get("ranking",""), "[?]")
        tco = p.get("tco", {})
        pnd = " [Pareto]" if p.get("pareto_non_dominated") else ""
        conf = (p.get("confidence") or {}).get("total") or p.get("confidence_score","?")
        print(f"  {i}. {r}{pnd} {p['brand']} {p['model']} "
              f"— {p['scores']['total']}/100 — confiance {conf}% "
              f"— TCO {tco.get('total_eur','?')} EUR")

if __name__ == "__main__":
    main()
