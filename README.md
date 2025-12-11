
# MaintControl

MaintControl est un logiciel SaaS de gestion des interventions (clients / techniciens / admin) avec :

- gestion des licences et clés d'activation
- période d'essai de 30 jours
- IA de priorisation
- multi-langues (FR / EN / ES / DE)
- gestion des utilisateurs (création + suppression par l'admin uniquement)
- champs d'organisation supplémentaires : type d'intervention, catégorie
- filtres dans la liste des interventions
- export CSV / PDF

## Lancement en local (Windows / Flask 3.x)

```bash
pip install -r requirements.txt
python app.py
```

Accès par défaut :
- admin / admin
- user1..user5 / password

## Déploiement sur Render

1. Pousse ce dossier sur un dépôt GitHub.
2. Sur Render, crée un nouveau service **Web** à partir de ce dépôt.
3. Render utilisera :

- `requirements.txt` pour installer les dépendances
- `Procfile` pour lancer l'application :

```bash
gunicorn app:app
```
