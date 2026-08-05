# Automatisation — GitHub Actions

Le tracker tourne tous les **dimanches à 9h** dans le cloud GitHub,
même PC éteint. Tu reçois le rapport sur Telegram.

## Étapes (15 min)

### 1. Créer un repo GitHub

Sur [github.com/new](https://github.com/new) :
- Nom : `purchase-intelligence` (ou ce que tu veux)
- **Privé** (tes données d'achat = privées)
- Ne cocher aucune option (pas de README auto)

### 2. Pusher le projet

Dans un terminal, depuis `C:\Users\benja\Documents\Work\Perso\` :

```bash
git init
git add procurement/ .github/
git commit -m "Initial commit"
git remote add origin https://github.com/TON_USERNAME/purchase-intelligence.git
git push -u origin main
```

⚠️ Vérifie que `config.local.json` est dans `.gitignore` (clés API = jamais dans le repo).

### 3. Trouver ton Telegram chat_id

Dans Telegram :
1. Envoie un message à ton bot (ou à `@userinfobot` pour ton ID perso)
2. Ou ouvre : `https://api.telegram.org/bot<TON_TOKEN>/getUpdates`
3. Cherche `"chat": {"id": 123456789}` → c'est ton chat_id

### 4. Ajouter les secrets GitHub

Sur ton repo → **Settings → Secrets and variables → Actions → New repository secret** :

| Nom                   | Valeur                                |
|-----------------------|---------------------------------------|
| `ANTHROPIC_API_KEY`   | `sk-ant-...`                          |
| `TELEGRAM_BOT_TOKEN`  | `1234567890:AAF...`                   |
| `TELEGRAM_CHAT_ID`    | `123456789` (ton chat_id)             |

### 5. Tester manuellement

Sur ton repo → **Actions → 🛒 Purchase Intelligence — Rapport hebdo → Run workflow**

Tu devrais recevoir le rapport Telegram dans la minute.

---

## Rapport Telegram reçu chaque dimanche

```
🛒 Purchase Intelligence — Rapport hebdo
📅 Semaine du 10/08/2026

🔔 1 alerte prix !

🧊 Réfrigérateur
  ✅ Bosch KGN39AIAT — 699 € (bas: 649 €)  cible: 720 €
     Promo Boulanger -12% jusqu'au 20/08
  ⏳ Liebherr CNd 5753 — 879 €  cible: 780 €
     Stable, pas de promo en vue

🛋️ Canapé
  📉 [modèles analysés une fois lancé]

Purchase Intelligence Agent · IA uniquement, vérifiez avant d'acheter
```

## Ajouter une nouvelle mission

1. Lance l'analyse depuis Streamlit
2. Valide la shortlist → "Envoyer au suivi des prix"
3. `git add procurement/data/ && git commit -m "Shortlist canapé" && git push`
4. La prochaine exécution Actions inclut automatiquement cette mission

## Modifier le jour/heure

Dans `.github/workflows/weekly_report.yml`, ligne `cron` :
```yaml
- cron: "0 7 * * 0"   # dimanche 9h Paris
- cron: "0 7 * * 1"   # lundi 9h Paris
- cron: "0 7 * * 3"   # mercredi 9h Paris
- cron: "0 7 1 * *"   # 1er du mois à 9h
```
