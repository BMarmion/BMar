# 🛒 Purchase Intelligence Agent

Moteur d'analyse d'achat multi-critères, piloté par missions JSON.  
Un seul runtime, des dizaines de catégories de produits.

## Lancer

```bash
cd procurement
pip install -r requirements.txt
streamlit run app.py
```

## Structure

```
procurement/
├── agent.py              # Moteur d'analyse (Anthropic API)
├── tracker.py            # Veille de prix (scraping + Telegram)
├── app.py                # Interface Streamlit multi-missions
├── requirements.txt
├── missions/             # 1 fichier JSON = 1 catégorie
│   ├── refrigerateur.json
│   ├── television.json
│   └── canape.json
├── data/                 # Résultats générés (gitignore)
│   └── {slug}/
│       ├── scored.json
│       ├── shortlist.json
│       └── price_history.json
└── reports/              # Rapports HTML exportables
    └── {slug}_report.html
```

## Utiliser l'agent en ligne de commande

```bash
# Lancer une analyse de réfrigérateur
python agent.py --mission refrigerateur

# Vérifier les prix (dry-run)
python tracker.py --mission refrigerateur --dry-run
```

## Créer une nouvelle mission

Crée un fichier `missions/ma_categorie.json` :

```json
{
  "name": "Ma catégorie",
  "slug": "ma-categorie",
  "icon": "📦",
  "product_type": "description du produit recherché",
  "domain": "domaine expert pour le prompt Claude",
  "brand_universe": ["Marque1", "Marque2", "..."],
  "expert_sources": ["https://..."],
  "context": { "kwh_price_eur": 0.22, "tco_years": 10 },
  "constraints": { "mandatory": { "condition": "new" } },
  "evaluation": {
    "score": 100,
    "dimensions": {
      "reliability":         { "weight": 30, "label": "...", "description": "..." },
      "main_performance":    { "weight": 25, "label": "...", "description": "..." },
      "total_cost":          { "weight": 25, "label": "...", "description": "..." },
      "resource_efficiency": { "weight":  5, "label": "...", "description": "..." },
      "sensory":             { "weight": 10, "label": "...", "description": "..." },
      "features":            { "weight":  5, "label": "...", "description": "..." }
    }
  },
  "criteria": {}
}
```

L'onglet apparaît automatiquement dans Streamlit.

## Exporter en PDF

Après une analyse, clique **Télécharger le rapport** → ouvre le `.html` dans Chrome → `Ctrl+P` → **Enregistrer en PDF**.

## Clé API

La clé Anthropic est lue depuis `../feeds/config.local.json` :
```json
{ "anthropic_api_key": "sk-ant-..." }
```
