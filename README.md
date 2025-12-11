
# MaintControl

MaintControl est un exemple complet de logiciel SaaS de gestion des interventions.

## Technologies

- Backend : Python 3 + Flask
- Frontend : HTML + CSS + JavaScript
- IA : moteur de priorisation simple dans `ai/scheduler.py`
- Génération de PDF : reportlab
- Export CSV : standard library
- Générateur de clés d'activation :
  - Côté serveur : routes Flask dans `app.py`
  - Module Java optionnel : `java/LicenseKeyGenerator.java`

## Lancement

1. Créez un environnement virtuel et installez les dépendances :

```bash
pip install -r requirements.txt
```

2. Démarrez le serveur Flask :

```bash
python app.py
```

3. Ouvrez votre navigateur sur http://localhost:5000

## Comptes de démonstration

- Administrateur : `admin` / `admin`
- Utilisateurs techniques : `user1`, `user2`, `user3` / `password`
- Clients : `user4`, `user5` / `password`

Chaque utilisateur non administrateur est en période d'essai de 30 jours.
Les exports CSV/PDF et certaines fonctions avancées sont bloqués si la période d'essai est expirée et qu'aucune clé n'a été activée.

## Langues

Interface disponible en français, anglais, espagnol et allemand.
La langue est sélectionnable dans la barre supérieure.
