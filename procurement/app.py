"""
Purchase Intelligence Agent — Interface Streamlit
Lancer : streamlit run app.py
"""

import json, os, subprocess, sys
from datetime import date

import plotly.graph_objects as go
import streamlit as st

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
MISSIONS_DIR = os.path.join(SCRIPT_DIR, "missions")
DATA_DIR     = os.path.join(SCRIPT_DIR, "data")
REPORTS_DIR  = os.path.join(SCRIPT_DIR, "reports")
AGENT_PY     = os.path.join(SCRIPT_DIR, "agent.py")
TRACKER_PY   = os.path.join(SCRIPT_DIR, "tracker.py")


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json(path, default=None):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def run_script(script, extra_args=None):
    cmd = [sys.executable, script] + (extra_args or [])
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(cmd, cwd=SCRIPT_DIR, capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=360, env=env)
    return r.returncode == 0, r.stdout, r.stderr

def mission_paths(slug):
    data_dir = os.path.join(DATA_DIR, slug)
    os.makedirs(data_dir,    exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    return {
        "scored":    os.path.join(data_dir, "scored.json"),
        "shortlist": os.path.join(data_dir, "shortlist.json"),
        "history":   os.path.join(data_dir, "price_history.json"),
        "report":    os.path.join(REPORTS_DIR, f"{slug}_report.html"),
    }

def load_missions():
    os.makedirs(MISSIONS_DIR, exist_ok=True)
    missions = []
    for f in sorted(os.listdir(MISSIONS_DIR)):
        if f.endswith(".json"):
            try:
                with open(os.path.join(MISSIONS_DIR, f), encoding="utf-8") as fp:
                    m = json.load(fp)
                    m["_file"] = os.path.join(MISSIONS_DIR, f)
                    missions.append(m)
            except Exception:
                pass
    return missions


# ── UI helpers ────────────────────────────────────────────────────────────────

RANK_STYLE = {
    "buy_now":        ("✅ ACHETER",   "#276749", "#c6f6d5"),
    "excellent_deal": ("⭐ EXCELLENT", "#2b6cb0", "#bee3f8"),
    "good_but_wait":  ("🟡 ATTENDRE", "#c05621", "#feebc8"),
    "avoid":          ("🔴 ÉVITER",   "#c53030", "#fed7d7"),
}
RISK_STYLE = {
    "faible": ("🟢 Faible", "#276749", "#c6f6d5"),
    "modere": ("🟡 Modéré", "#c05621", "#feebc8"),
    "eleve":  ("🔴 Élevé",  "#c53030", "#fed7d7"),
}
DIM_COLORS = {
    "reliability":         "#e53e3e",
    "main_performance":    "#3182ce",
    "total_cost":          "#38a169",
    "resource_efficiency": "#d69e2e",
    "sensory":             "#805ad5",
    "features":            "#dd6b20",
}
CONF_COLORS = {
    "Avis utilisateurs":  "#3182ce",
    "Tests independants": "#38a169",
    "Historique prix":    "#d69e2e",
    "Recul modele":       "#805ad5",
    "Coherence sources":  "#e53e3e",
}

def badge(label, fg, bg):
    return (f'<span style="background:{bg};color:{fg};padding:2px 10px;'
            f'border-radius:12px;font-size:.75rem;font-weight:700;white-space:nowrap">'
            f'{label}</span>')

def score_bar_html(val, max_val, color, label=None):
    pct = min(100, round(val/max_val*100)) if max_val else 0
    bar = (f'<div style="flex:1;background:#e2e8f0;border-radius:3px;height:6px">'
           f'<div style="background:{color};width:{pct}%;height:6px;border-radius:3px"></div></div>')
    lbl_span  = f'<span style="font-size:.72rem;color:#555;min-width:34px">{val}/{max_val}</span>'
    name_span = f'<span style="font-size:.72rem;color:#888;min-width:110px">{label}</span>' if label else ""
    return f'<div style="display:flex;align-items:center;gap:6px;margin:2px 0">{name_span}{bar}{lbl_span}</div>'

def stars_html(n, max_n=5):
    return "".join("⭐" if i < round(n or 0) else "☆" for i in range(max_n))

def confidence_color(score):
    if score >= 80: return "#276749", "#c6f6d5"
    if score >= 55: return "#c05621", "#feebc8"
    return "#c53030", "#fed7d7"

def shop_buttons_html(shop_urls):
    """Génère des liens cliquables vers les revendeurs."""
    if not shop_urls:
        return ""
    links = " ".join(
        f'<a href="{r["url"]}" target="_blank" style="'
        f'background:#edf2f7;color:#2d3748;padding:3px 10px;border-radius:8px;'
        f'font-size:.73rem;text-decoration:none;white-space:nowrap;display:inline-block">'
        f'🔗 {r["name"]}</a>'
        for r in shop_urls
    )
    return f'<div style="display:flex;flex-wrap:wrap;gap:5px;margin-top:8px">{links}</div>'


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="🛒 Purchase Intelligence", layout="wide", page_icon="🛒")
st.markdown('<style>[data-testid="stMetricValue"]{font-size:1rem!important}'
            '[data-testid="stMetricLabel"]{font-size:.7rem!important}</style>',
            unsafe_allow_html=True)

st.title("🛒 Purchase Intelligence Agent")
st.caption("Analyse d'achat multi-critères · Veille de prix · Marché France")

missions = load_missions()
if not missions:
    st.error("Aucune mission trouvée dans `missions/`.")
    st.code("Crée un fichier missions/refrigerateur.json pour démarrer.")
    st.stop()


# ── Onglets principaux : 1 par mission + 1 suivi global ──────────────────────
tab_labels = [f"{m.get('icon','📦')} {m['name']}" for m in missions] + ["📈 Suivi des prix"]
all_tabs   = st.tabs(tab_labels)

mission_tabs = all_tabs[:-1]
suivi_tab    = all_tabs[-1]


# ════════════════════════════════════════════════════════════════════════════════
# ONGLETS MISSION — Sélection
# ════════════════════════════════════════════════════════════════════════════════

for mission, tab in zip(missions, mission_tabs):
    slug      = mission.get("slug", mission["name"].lower())
    icon      = mission.get("icon", "📦")
    paths     = mission_paths(slug)
    dims_eval = mission.get("evaluation", {}).get("dimensions", {})

    with tab:
        scored    = load_json(paths["scored"])
        shortlist = load_json(paths["shortlist"])
        is_locked = shortlist and shortlist.get("locked")

        if is_locked:
            n_tr = len([m for m in shortlist["models"] if m.get("enabled", True)])
            st.success(f"✅ Shortlist verrouillée · {n_tr} modèles dans le suivi global")
            if st.button("🔓 Déverrouiller", key=f"unlock_{slug}"):
                shortlist["locked"] = False
                save_json(paths["shortlist"], shortlist)
                st.rerun()
            st.divider()

        # ── Export rapport ────────────────────────────────────────────────────
        if os.path.exists(paths["report"]):
            with open(paths["report"], encoding="utf-8") as f:
                html_content = f.read()
            exp_c1, exp_c2 = st.columns([3, 1])
            exp_c1.info("📄 Rapport dispo · télécharge le HTML puis **Ctrl+P → Enregistrer en PDF** dans Chrome")
            exp_c2.download_button(
                label="💾 Télécharger le rapport",
                data=html_content.encode("utf-8"),
                file_name=f"{slug}_rapport.html",
                mime="text/html",
                key=f"dl_{slug}",
                use_container_width=True,
            )

        # ── Lancement analyse ─────────────────────────────────────────────────
        with st.expander("⚙️ Lancer une nouvelle analyse", expanded=not bool(scored)):
            if st.button(f"🔍 Analyser le marché {icon}", type="primary",
                         use_container_width=True, key=f"run_{slug}"):
                with st.spinner(f"Analyse {mission['name']} en cours… 3-4 minutes"):
                    ok, out, err = run_script(AGENT_PY, ["--mission", slug])
                if ok:
                    st.success("Analyse terminée !")
                    st.code(out[-800:])
                    st.rerun()
                else:
                    st.error("Erreur lors de l'analyse")
                    st.code(err[-2000:])

        if not scored:
            st.info(f"Lance l'analyse pour obtenir une sélection de {mission['name'].lower()}.")
            continue

        products = sorted(scored.get("scored", []), key=lambda x: -x["scores"]["total"])

        # ── Funnel ───────────────────────────────────────────────────────────
        funnel    = scored.get("_funnel", {})
        brand_cov = funnel.get("brand_coverage", {})
        all_cand  = funnel.get("all_candidates", [])
        fin_sum   = funnel.get("finalist_summary", "")
        if funnel:
            st.markdown(
                f'<div style="background:#1a1a2e;color:#e2e8f0;border-radius:10px;'
                f'padding:14px 18px;margin-bottom:14px">'
                f'<span style="font-size:.7rem;text-transform:uppercase;color:#4299e1">Funnel</span><br>'
                f'<strong style="font-size:1.4rem;color:#63b3ed">{len(brand_cov)}</strong>'
                f'<span style="color:#888"> marques → </span>'
                f'<strong style="font-size:1.4rem;color:#63b3ed">{len(all_cand)}</strong>'
                f'<span style="color:#888"> candidats → </span>'
                f'<strong style="font-size:1.4rem;color:#63b3ed">{len(products)}</strong>'
                f'<span style="color:#888"> finalistes</span></div>',
                unsafe_allow_html=True)
            with st.expander(f"🔍 Marques évaluées ({len(brand_cov)})"):
                for brand, status in brand_cov.items():
                    ok_b = "candidat" in status.lower() or "trouve" in status.lower()
                    st.markdown(f"{'✅' if ok_b else '—'} **{brand}** — {status}")
            if all_cand:
                with st.expander(f"📊 Classement rapide — {len(all_cand)} candidats"):
                    for c in sorted(all_cand, key=lambda x: -x.get("quick_scores",{}).get("subtotal",0)):
                        fin = c.get("finalist", False)
                        sub = c.get("quick_scores", {}).get("subtotal","?")
                        st.markdown(f"{'🏆' if fin else '❌'} **{c['brand']} {c['model']}** · {sub}/75 — {c.get('selection_reason','')}")
            if fin_sum:
                st.caption(f"💬 {fin_sum}")

        st.subheader(f"{len(products)} finalistes analysés")
        if scored.get("summary"):
            st.info(f"💡 {scored['summary']}")

        selections = {}

        for i, p in enumerate(products):
            rank     = p.get("ranking", "good_but_wait")
            total    = p["scores"]["total"]
            fair     = p.get("price_fr_eur") or 0
            tco      = p.get("tco", {})
            rep      = p.get("repairability", {})
            conf_obj = p.get("confidence") or {}
            conf     = conf_obj.get("total") or p.get("confidence_score") or 0
            risk     = p.get("risk_level", "modere")
            verdict  = p.get("verdict", {})
            shops    = p.get("shop_urls", [])
            key      = f"{p['brand']} {p['model']}"

            rl, rf, rb_c           = RANK_STYLE.get(rank, RANK_STYLE["good_but_wait"])
            riskl, riskf, riskbg   = RISK_STYLE.get(risk, RISK_STYLE["modere"])
            conf_fg, conf_bg       = confidence_color(conf)

            with st.container(border=True):
                col_main, col_meta, col_action = st.columns([5, 2, 2])

                with col_main:
                    bdg = badge(rl, rf, rb_c) + " " + badge(riskl, riskf, riskbg) + " "
                    if p.get("pareto_non_dominated"):
                        bdg += badge("◆ Pareto", "#553c9a", "#e9d8fd") + " "
                    bdg += badge(f"Confiance {conf}%", conf_fg, conf_bg)
                    st.markdown(bdg, unsafe_allow_html=True)

                    st.markdown(f"### {p['brand']} {p['model']}")
                    origin       = p.get("country_of_origin") or p.get("manufacturer_country","")
                    cur_gen      = p.get("current_generation", p.get("manufacturing_generation",""))
                    actual_mfr   = p.get("actual_manufacturer","")
                    distrib      = p.get("distributor_brand","")
                    is_distrib   = p.get("is_distributor_brand", False)
                    oem_siblings = p.get("oem_siblings", [])

                    specs   = " · ".join(filter(None, [
                        f"{p.get('volume_total_L','')}L"             if p.get("volume_total_L") else "",
                        (f"{p.get('width_mm','')}×{p.get('height_mm','')}×{p.get('depth_mm','')} mm"
                         if p.get("width_mm") else ""),
                        f"{p.get('noise_db','')} dB"                 if p.get("noise_db") else "",
                        f"Classe {p.get('energy_class','')}"         if p.get("energy_class") else "",
                        f"{p.get('annual_kwh','')} kWh/an"           if p.get("annual_kwh") else "",
                        f"Fab. {origin}"                              if origin else "",
                        p.get("structure_type","")                   if p.get("structure_type") else "",
                        p.get("suspension_type","")                  if p.get("suspension_type") else "",
                        f"Mousse {p.get('foam_seat_density_kg_m3','')} kg/m³" if p.get("foam_seat_density_kg_m3") else "",
                        f"Martindale {p.get('martindale','')}+"      if p.get("martindale") else "",
                    ]))
                    st.caption(specs)
                    if cur_gen:
                        st.caption(f"🏭 {cur_gen}")

                    # Bloc fabricant / OEM
                    if actual_mfr or is_distrib:
                        if is_distrib and actual_mfr:
                            st.markdown(
                                f'<div style="background:#fefcbf;border-radius:6px;padding:5px 10px;font-size:.78rem;margin-top:4px">'
                                f'🏭 <strong>Fab. réel :</strong> {actual_mfr}'
                                f'{" (" + origin + ")" if origin else ""}'
                                f'{"  · Distribué par <strong>" + distrib + "</strong>" if distrib else ""}'
                                f'</div>', unsafe_allow_html=True)
                        elif actual_mfr:
                            st.caption(f"🏭 Fabricant : {actual_mfr}{' (' + origin + ')' if origin else ''}")

                    # Doublons OEM
                    if oem_siblings:
                        sib_lines = []
                        for sib in oem_siblings:
                            sib_name  = sib.get("brand","") + " " + sib.get("model","")
                            sib_price = sib.get("price_fr_eur","?")
                            sib_delta = sib.get("price_delta_eur")
                            delta_str = f" → <strong style='color:#276749'>−{sib_delta} €</strong>" if sib_delta else ""
                            sib_lines.append(f"≈ {sib_name} ({sib_price} €){delta_str}")
                        st.markdown(
                            f'<div style="background:#c6f6d5;border-radius:6px;padding:6px 10px;font-size:.78rem;margin-top:4px">'
                            f'💡 <strong>OEM identique disponible moins cher :</strong><br>'
                            + "<br>".join(sib_lines) +
                            f'</div>', unsafe_allow_html=True)

                    bars_html = "".join(
                        score_bar_html(p["scores"].get(k,0), v.get("weight",0),
                                       DIM_COLORS.get(k,"#aaa"), v.get("label",""))
                        for k, v in dims_eval.items()
                    )
                    st.markdown(bars_html, unsafe_allow_html=True)

                    cols_ff = st.columns(2)
                    with cols_ff[0]:
                        for txt in p.get("strengths",[]):
                            st.markdown(f"<small>✅ {txt}</small>", unsafe_allow_html=True)
                    with cols_ff[1]:
                        for txt in p.get("weaknesses",[]):
                            st.markdown(f"<small>⚠️ {txt}</small>", unsafe_allow_html=True)
                    if p.get("justification"):
                        st.caption(p["justification"])

                    # Liens revendeurs
                    if shops:
                        st.markdown(shop_buttons_html(shops), unsafe_allow_html=True)

                with col_meta:
                    st.metric("Score",       f"{total}/100")
                    st.metric("Prix estimé", f"~{fair} €")
                    if tco and tco.get("total_eur"):
                        st.metric(f"TCO {tco.get('years',15)} ans", f"{tco['total_eur']} €",
                                  help=(f"Achat {tco.get('purchase_eur','?')} € + "
                                        f"Électricité {tco.get('electricity_eur','?')} € + "
                                        f"Réparations ~{tco.get('repair_expected_eur','?')} €"))
                        st.caption(f"≈ {tco.get('annual_eur','?')} €/an")
                    if rep:
                        st.markdown(f"**Répar.** {stars_html(rep.get('score',0))}", unsafe_allow_html=True)
                        st.caption(f"Pièces {rep.get('parts_years','?')} ans · {rep.get('parts_cost_level','?')}")

                with col_action:
                    include = st.checkbox("Shortlist", value=rank != "avoid", key=f"inc_{slug}_{i}")
                    if include:
                        default_t = int(verdict.get("buy_if_below_eur") or fair*0.85) if fair else 400
                        target = st.number_input("Prix cible (€)", value=default_t,
                                                 step=10, min_value=100, key=f"tgt_{slug}_{i}")
                    else:
                        target = None
                    selections[key] = {"include": include, "target": target, "product": p}

                st.divider()

                # ── Verdict ───────────────────────────────────────────────────
                if verdict:
                    v_bg = {"buy_now":"#c6f6d5","wait_promo":"#fefcbf","wait_successor":"#bee3f8"}.get(
                        verdict.get("action",""), "#f7fafc")
                    st.markdown(
                        f'<div style="background:{v_bg};border-radius:7px;padding:9px 13px">'
                        f'💡 <strong>Verdict :</strong> {verdict.get("summary","")} '
                        f'<span style="color:#555;font-size:.82rem">— Cible : '
                        f'<strong>{verdict.get("buy_if_below_eur","?")} €</strong>'
                        f' · Moment : {verdict.get("best_moment","?")}'
                        f' · Attente : {verdict.get("risk_of_waiting","?")}</span></div>',
                        unsafe_allow_html=True)

                # ── Sous-onglets détail ───────────────────────────────────────
                t_prix, t_conf, t_gen, t_rep = st.tabs(["📊 Prix", "🔍 Confiance", "🏭 Générations", "🔧 Répar."])

                with t_prix:
                    ph = p.get("price_history", [])
                    if ph and len(ph) >= 2:
                        periods = [x.get("period","") for x in ph]
                        typs    = [x.get("typical_eur") for x in ph]
                        promos  = [x.get("promo_low_eur") for x in ph]
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(x=periods, y=typs, mode="lines+markers",
                            name="Standard", line=dict(color="#3182ce",width=2), marker=dict(size=7),
                            hovertemplate="%{x}: %{y} €<extra></extra>"))
                        px_c = [v for v in promos if v is not None]
                        px_x = [periods[j] for j,v in enumerate(promos) if v is not None]
                        if px_c:
                            fig.add_trace(go.Scatter(x=px_x, y=px_c, mode="markers",
                                name="Promo", marker=dict(color="#e53e3e",size=10,symbol="star"),
                                hovertemplate="%{x}: %{y} €<extra></extra>"))
                        if verdict.get("buy_if_below_eur"):
                            fig.add_hline(y=verdict["buy_if_below_eur"], line_dash="dash",
                                          line_color="#38a169",
                                          annotation_text=f"Cible {verdict['buy_if_below_eur']} €",
                                          annotation_position="bottom right")
                        fig.update_layout(
                            height=190, margin=dict(l=0,r=80,t=10,b=0),
                            legend=dict(orientation="h",y=1.15), plot_bgcolor="white",
                            xaxis=dict(showgrid=False), yaxis=dict(ticksuffix=" €",gridcolor="#f0f0f0"))
                        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})
                    else:
                        st.caption(p.get("price_history_note","Pas de données structurées."))
                    if p.get("price_target_rationale"):
                        st.caption(f"🎯 {p['price_target_rationale']}")

                with t_conf:
                    components = conf_obj.get("components", [])
                    if components:
                        for comp in components:
                            c_lbl = comp.get("label","")
                            c_val = comp.get("value",0)
                            c_max = comp.get("max",25)
                            c_note= comp.get("note","")
                            color = CONF_COLORS.get(c_lbl, "#718096")
                            st.markdown(score_bar_html(c_val, c_max, color, f"{c_lbl} ({c_val}/{c_max})"),
                                        unsafe_allow_html=True)
                            if c_note:
                                st.caption(f"  → {c_note}")

                with t_gen:
                    gen_history  = p.get("generation_history", [])
                    cur_gen_name = (p.get("current_generation") or "").split("(")[0].strip()
                    if gen_history:
                        for gen in gen_history:
                            is_cur = (gen.get("gen","") in cur_gen_name
                                      or cur_gen_name in gen.get("gen",""))
                            st.markdown(
                                f'<div style="background:{"#f0fff4" if is_cur else "#f7fafc"};'
                                f'border-radius:6px;padding:7px 11px;margin-bottom:5px">'
                                f'{"👉 <strong>ACTUEL</strong> — " if is_cur else ""}'
                                f'<strong>{gen.get("gen","")}</strong> '
                                f'<span style="color:#888">({gen.get("years","")})</span><br>'
                                f'<span style="font-size:.8rem;color:#555">{gen.get("key_changes","")}</span>'
                                f'</div>', unsafe_allow_html=True)

                with t_rep:
                    if rep:
                        r1, r2 = st.columns(2)
                        with r1:
                            st.markdown("**Facile**")
                            for x in rep.get("easy_repairs",[]):
                                st.markdown(f"<small>✅ {x}</small>", unsafe_allow_html=True)
                        with r2:
                            st.markdown("**Difficile / Coûteux**")
                            for x in rep.get("hard_repairs",[]):
                                st.markdown(f"<small>⚠️ {x}</small>", unsafe_allow_html=True)
                        st.caption(f"{stars_html(rep.get('score',0))} · Pièces {rep.get('parts_years','?')} ans · {rep.get('parts_cost_level','?')}")
                    rb   = p["scores"].get("reliability_breakdown", {})
                    rsrc = p["scores"].get("reliability_sources", [])
                    if rb:
                        st.divider()
                        fc1, fc2 = st.columns(2)
                        fc1.metric("Compresseur",  f"{rb.get('compressor','?')}/12")
                        fc1.metric("Électronique", f"{rb.get('electronics','?')}/10")
                        fc2.metric("SAV France",   f"{rb.get('sav_france','?')}/8")
                        fc2.metric("Terrain",      f"{rb.get('field_reports','?')}/5")
                        if rsrc:
                            st.caption("Sources : " + " · ".join(rsrc))

        # ── Verrouiller shortlist ─────────────────────────────────────────────
        st.divider()
        n_sel = sum(1 for v in selections.values() if v["include"])
        st.caption(f"{n_sel} modèle(s) sélectionné(s)")

        if st.button("🔒 Valider et envoyer au suivi des prix", type="primary",
                     disabled=n_sel == 0, use_container_width=True, key=f"lock_{slug}"):
            models_out = []
            for mkey, sel in selections.items():
                if not sel["include"]:
                    continue
                p    = sel["product"]
                fair = p.get("price_fr_eur") or 0
                tco  = p.get("tco", {})
                rep  = p.get("repairability", {})
                models_out.append({
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
                    "target_purchase_price": sel["target"],
                    "price_history_note":    " · ".join(
                        f"{x['period']} {x.get('promo_low_eur') or x.get('typical_eur','?')} EUR"
                        for x in (p.get("price_history") or [])[:3]),
                    "strengths":             p.get("strengths", []),
                    "weaknesses":            p.get("weaknesses", []),
                    "shop_urls":             p.get("shop_urls", []),
                    "enabled":               True,
                    "notes":                 "",
                })
            save_json(paths["shortlist"], {
                "locked":          True,
                "generated_at":    date.today().isoformat(),
                "mission_slug":    slug,
                "mission_name":    mission["name"],
                "mission_icon":    icon,
                "mission_summary": scored.get("summary",""),
                "models":          models_out,
            })
            st.success(f"✅ {len(models_out)} modèles ajoutés au suivi → onglet 📈 Suivi des prix")
            st.rerun()


# ════════════════════════════════════════════════════════════════════════════════
# ONGLET GLOBAL — SUIVI DES PRIX (toutes missions)
# ════════════════════════════════════════════════════════════════════════════════

with suivi_tab:

    # Charger toutes les shortlists verrouillées
    all_shortlists = []
    for mission in missions:
        slug  = mission.get("slug", mission["name"].lower())
        paths = mission_paths(slug)
        sl    = load_json(paths["shortlist"])
        if sl and sl.get("locked"):
            sl["_slug"]  = slug
            sl["_paths"] = paths
            sl["_miss"]  = mission
            all_shortlists.append(sl)

    if not all_shortlists:
        st.info("Aucune shortlist verrouillée. Lance une analyse depuis un onglet mission, puis clique **Valider et envoyer au suivi des prix**.")
        st.stop()

    total_models = sum(
        len([m for m in sl["models"] if m.get("enabled", True)])
        for sl in all_shortlists
    )
    st.markdown(
        f'<div style="background:#1a1a2e;color:#e2e8f0;border-radius:10px;'
        f'padding:14px 20px;margin-bottom:16px">'
        f'<strong style="font-size:1.1rem">📈 Suivi global des prix</strong> · '
        f'<strong style="color:#63b3ed">{total_models} modèles</strong> sur '
        f'<strong style="color:#63b3ed">{len(all_shortlists)} catégorie(s)</strong></div>',
        unsafe_allow_html=True)

    # ── Contrôles globaux ─────────────────────────────────────────────────────
    ctrl1, ctrl2, ctrl3, _ = st.columns([2, 2, 1, 2])
    with ctrl1:
        if st.button("🔍 Vérifier tous les prix", type="primary", use_container_width=True):
            if not os.path.exists(TRACKER_PY):
                st.error("tracker.py introuvable")
            else:
                with st.spinner("Scraping toutes les missions…"):
                    ok, out, err = run_script(TRACKER_PY, ["--all", "--dry-run"])
                st.code(out[-1500:] if ok else err[-1500:])
                st.rerun()
    with ctrl2:
        if st.button("📱 Vérifier + alertes Telegram", use_container_width=True):
            if not os.path.exists(TRACKER_PY):
                st.error("tracker.py introuvable")
            else:
                with st.spinner("Vérification + alertes Telegram…"):
                    ok, out, err = run_script(TRACKER_PY, ["--all"])
                st.success("Alertes envoyées") if ok else st.error(err[-800:])
                st.rerun()
    with ctrl3:
        if st.button("🔄", use_container_width=True, help="Rafraîchir"):
            st.rerun()

    st.divider()

    # ── Section par mission ───────────────────────────────────────────────────
    for sl in all_shortlists:
        slug      = sl["_slug"]
        mission   = sl["_miss"]
        m_icon    = sl.get("mission_icon", mission.get("icon","📦"))
        m_name    = sl.get("mission_name", mission.get("name", slug))
        m_summary = sl.get("mission_summary","")
        paths     = sl["_paths"]
        history   = load_json(paths["history"], {})
        models_sl = [m for m in sl.get("models", []) if m.get("enabled", True)]

        # En-tête section
        sec1, sec2 = st.columns([4, 3])
        with sec1:
            st.markdown(f"### {m_icon} {m_name}")
            if m_summary:
                st.caption(m_summary)
        with sec2:
            n_with_data = sum(1 for m in models_sl
                              if history.get(f"{m['brand']} {m['model']}", {}).get("daily"))
            ck1, ck2 = st.columns(2)
            with ck1:
                if st.button(f"🔍 Prix {m_name}", use_container_width=True, key=f"ck_{slug}"):
                    if not os.path.exists(TRACKER_PY):
                        st.error("tracker.py introuvable")
                    else:
                        with st.spinner(f"Vérification {m_name}…"):
                            ok, out, err = run_script(TRACKER_PY, ["--mission", slug, "--dry-run"])
                        st.code(out[-800:] if ok else err[-800:])
                        st.rerun()
            with ck2:
                if st.button(f"📱 Alertes", use_container_width=True, key=f"ntf_{slug}"):
                    if not os.path.exists(TRACKER_PY):
                        st.error("tracker.py introuvable")
                    else:
                        with st.spinner(f"Alertes {m_name}…"):
                            ok, out, err = run_script(TRACKER_PY, ["--mission", slug])
                        st.success("OK") if ok else st.error(err[-400:])
                        st.rerun()
            st.caption(f"{len(models_sl)} modèles · {n_with_data} avec données scraping")

        # ── Cartes modèles ────────────────────────────────────────────────────
        for model in models_sl:
            mkey     = f"{model['brand']} {model['model']}"
            h        = history.get(mkey, {})
            daily    = h.get("daily", {})
            all_mins = [v["min"] for v in daily.values() if "min" in v]
            current  = sorted(daily.items())[-1][1]["min"] if daily else None
            hist_low = min(all_mins) if all_mins else None
            median   = sorted(all_mins)[len(all_mins)//2] if all_mins else None
            target   = model.get("target_purchase_price")
            shops    = model.get("shop_urls", [])

            if current and target and current <= target:
                status, s_color = "🟢 Prix cible atteint !", "#276749"
            elif current and hist_low and current <= hist_low * 1.05:
                status, s_color = "⭐ Proche du plus bas historique", "#2b6cb0"
            elif current and median and current <= median * 0.80:
                status, s_color = "⭐ Remise ≥ 20 %", "#2b6cb0"
            elif current:
                status, s_color = "⏳ En attente", "#718096"
            else:
                status, s_color = "❓ Pas encore de données", "#a0aec0"

            with st.container(border=True):
                left, right = st.columns([3, 5])

                with left:
                    st.markdown(f"**{mkey}** · Score {model.get('quality_score','?')}/100")

                    b_html = ""
                    r = model.get("risk_level","")
                    if r:
                        rl2, rf2, rb2 = RISK_STYLE.get(r, ("?","#555","#eee"))
                        b_html += badge(rl2, rf2, rb2) + " "
                    rank_k = model.get("ranking","")
                    if rank_k:
                        rl3, rf3, rb3 = RANK_STYLE.get(rank_k, ("?","#555","#eee"))
                        b_html += badge(rl3, rf3, rb3) + " "
                    c_s = model.get("confidence_score")
                    if c_s:
                        cf, cb = confidence_color(c_s)
                        b_html += badge(f"Confiance {c_s}%", cf, cb)
                    if model.get("pareto_non_dominated"):
                        b_html += " " + badge("◆ Pareto", "#553c9a", "#e9d8fd")
                    if b_html:
                        st.markdown(b_html, unsafe_allow_html=True)

                    st.markdown(f'<span style="color:{s_color};font-weight:600;font-size:.88rem">{status}</span>',
                                unsafe_allow_html=True)

                    if model.get("price_history_note"):
                        st.caption(f"📊 Historique estimé : {model['price_history_note']}")
                    if model.get("tco_total_eur"):
                        st.caption(f"💰 TCO : {model['tco_total_eur']} € · {model.get('tco_annual_eur','?')} €/an")

                    # Liens revendeurs
                    if shops:
                        st.markdown(shop_buttons_html(shops), unsafe_allow_html=True)

                with right:
                    m1, m2, m3, m4, m5 = st.columns(5)
                    m1.metric("Actuel",  f"{current} €"  if current  else "—")
                    m2.metric("Cible",   f"{target} €"   if target   else "—")
                    m3.metric("Médiane", f"{median} €"   if median   else "—")
                    m4.metric("Min",     f"{hist_low} €" if hist_low else "—")
                    m5.metric("Obs.",    len(all_mins))

                    if len(all_mins) >= 2:
                        dates_s = sorted(daily.keys())
                        pp      = [daily[d]["min"] for d in dates_s]
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(
                            x=dates_s, y=pp, mode="lines+markers",
                            line=dict(color="#3182ce",width=2), marker=dict(size=5),
                            hovertemplate="%{x}: %{y} €<extra></extra>"))
                        if target:
                            fig.add_hline(y=target, line_dash="dash", line_color="#38a169",
                                          annotation_text=f"Cible {target} €",
                                          annotation_position="bottom right")
                        if median:
                            fig.add_hline(y=median, line_dash="dot", line_color="#e53e3e",
                                          annotation_text=f"Médiane {median} €",
                                          annotation_position="top right")
                        fig.update_layout(
                            height=160, margin=dict(l=0,r=70,t=8,b=0),
                            showlegend=False, plot_bgcolor="white",
                            xaxis=dict(showgrid=False),
                            yaxis=dict(ticksuffix=" €", gridcolor="#f0f0f0"))
                        st.plotly_chart(fig, use_container_width=True,
                                        config={"displayModeBar": False})
                    elif not daily:
                        st.caption("Aucune donnée — lance une vérification des prix.")

        st.divider()
