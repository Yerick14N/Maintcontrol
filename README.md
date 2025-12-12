
# MaintControl Multi-Entreprises

MaintControl est un logiciel SaaS de gestion des interventions (clients / techniciens / admin) avec :

- Multi-entreprises (table `companies`)
- Base clients (table `customers`) distincte des utilisateurs
- Période d'essai de 30 jours + clés d'activation
- Gestion des licences par entreprise
- Facturation simple des licences (factures + paiements simulés)
- Multi-langues (FR / EN / ES / DE)
- IA de priorisation des interventions
- Export CSV / PDF (simple et avancé)
- Planning des interventions
- Paramètres d'entreprise incluant un domaine personnalisé

## Lancement en local

```bash
pip install -r requirements.txt
python app.py
```

Accès par défaut :

Les identifiants **admin/admin** ont été supprimés (trop faibles).

- Les nouveaux identifiants admin (générés) sont dans : `NEW_ADMIN_CREDENTIALS.txt`
- En production, il est recommandé de **surcharger** ces identifiants via les variables d’environnement :
  - `ADMIN_USERNAME`
  - `ADMIN_PASSWORD` (minimum 12 caractères)

## Déploiement sur Render

1. Pousser ce dossier sur un dépôt GitHub.
2. Sur Render, créer un nouveau service **Web** relié à ce dépôt.
3. Build command : `pip install -r requirements.txt`
4. Start command : `gunicorn app:app`
