# turnstile

**Un allocateur GPU déterministe pour modèles d'IA locaux : un seul modèle à la fois sur la carte, une file d'attente propre, et un service qui ne ment jamais sur ce qui tourne vraiment.**

[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/bengua/turnstile/actions/workflows/ci.yml/badge.svg)](https://github.com/bengua/turnstile/actions/workflows/ci.yml)

**Tutoriel** : [installation pas à pas sur freedzhost.cloud](https://freedzhost.cloud/fr/turnstile.html) · [step-by-step tutorial on freedzhost.cloud (EN)](https://freedzhost.cloud/en/turnstile.html)

> English version below: [English](#english).

---

## Présentation

Quand on fait tourner plusieurs modèles d'IA sur une ou deux cartes graphiques, tout le monde veut la mémoire de la carte : le modèle de discussion, l'assistant de code, le logiciel de génération d'images, un jeu… On finit par décrire « qui a droit à quoi » dans des fichiers. Et ces fichiers dérivent : ils disent qu'un gros modèle est chargé alors que la carte est vide, ou qu'un modèle de 27 milliards de paramètres répond sur un port… alors que c'est un petit 3B qui répond.

**turnstile** (le « tourniquet ») arrête de croire les fichiers et **observe la machine**. Il tient une grille de *slots* (un modèle, une carte, un contexte), charge celui qu'on lui demande, décharge les autres, met les demandes en file d'attente pendant un chargement, et vérifie toutes les 10 secondes que la réalité correspond à ce qu'il annonce.

Ce n'est pas un serveur de modèles : `llama.cpp` (ou un autre moteur) sert les modèles dans des conteneurs Docker. turnstile décide simplement **qui a la carte**, et le dit honnêtement.

## Fonctionnalités

- **Un slot à la fois par carte** : demander `coder-c1` arrête ce qui occupait la carte 1, démarre le bon conteneur et attend qu'il réponde vraiment.
- **File d'attente propre** : les demandes qui arrivent pendant un chargement attendent leur tour au lieu de se marcher dessus.
- **Cinq faits vérifiés en continu** : conteneur vivant, port qui répond, mémoire de la carte cohérente, débit crédible, et surtout **l'identité du modèle qui répond** (lue dans le journal du serveur).
- **Slots de libération** (`free-c0`, `free-c1`, `free-all`) : un jeu ou un logiciel d'images récupère une carte en la *demandant*, et `turnstile restore` remet ensuite exactement ce qui tournait avant.
- **Il se mesure lui-même** : nombre de bascules, échecs, taux de réutilisation, temps de chargement appris par profil de carte.
- **Garde-fous** : plafond de 30 bascules par heure, 20 s d'activité continue avant toute bascule automatique, refus explicites (jamais une connexion coupée en silence), conteneurs lancés à la main respectés.
- **Aucune dépendance** : uniquement Python 3 (bibliothèque standard), Docker et systemd en mode utilisateur. Pas besoin des droits root.
- **NVIDIA testé au quotidien** ; AMD et Intel écrits d'après la documentation, non testés.

## Installation depuis zéro

### Prérequis

- Linux avec **systemd** (sessions utilisateur) et **Docker** ;
- **Python 3.10 ou plus récent** ;
- une carte graphique NVIDIA avec `nvidia-smi` (sans carte, turnstile démarre quand même et ne gère que des slots CPU) ;
- vos modèles servis par des conteneurs Docker existants, **un conteneur par slot, nommé comme le slot** (par exemple un conteneur `llama.cpp` nommé `coder-c1`).

### Installer

```bash
git clone https://github.com/bengua/turnstile
cd turnstile
./install.sh
```

Le script installe tout **pour l'utilisateur courant** : les commandes dans `~/.local/bin`, les services dans `~/.config/systemd/user`, et une configuration d'exemple dans `~/.config/turnstile/`. Rien ne démarre tout seul.

### Premier lancement

1. Décrivez vos slots, une ligne par slot :

   ```
   nom|port|carte|poids|arguments
   ```

   `carte` vaut un index (`0`, `1`…), `all` pour un modèle réparti sur toutes les cartes, ou `cpu`. Partez de [`examples/containers.map`](examples/containers.map) :

   ```bash
   $EDITOR ~/.config/turnstile/containers.map
   ```

2. Démarrez l'allocateur et regardez ce qui tourne vraiment :

   ```bash
   systemctl --user enable --now turnstiled
   turnstile status
   ```

3. (Facultatif) Gardez une trace quotidienne, puis, une fois en confiance, laissez-le suivre votre activité (il commence en mode « proposition seulement ») :

   ```bash
   systemctl --user enable --now turnstile-record.timer
   systemctl --user enable --now turnstile-watch
   ```

## Utilisation

```bash
turnstile status        # ce qui tourne VRAIMENT, faits vérifiés par slot
turnstile slots         # ce qui pourrait tourner
turnstile coder-c1      # charger un slot : already_serving / served / queued
turnstile free-c0       # rendre la carte 0 (avant un jeu, par exemple)
turnstile restore       # revenir à ce qui tournait avant
turnstile counters      # ce que turnstile a mesuré sur lui-même
```

La même chose existe en API HTTP sur le port `8130`, pour l'automatisation :

| Route | Rôle |
|---|---|
| `GET /state` | les faits vérifiés, la file d'attente, les slots de libération |
| `GET /slots` | les slots définis |
| `GET /counters` | bascules, échecs, réutilisation, durées apprises |
| `GET /health` | état de vie, fabricant, nombre de cartes |
| `POST /slot` | `{"slot": "coder-c1"}`, ou `{"slot": "restore"}` |
| `POST /pair` | archiver une paire de jugements (pour `turnstile-cascade`) |

## Configuration

Tout se règle par variables d'environnement (par exemple via `systemctl --user edit turnstiled`). Toutes sont facultatives.

| Variable | Défaut | Rôle |
|---|---|---|
| `TURNSTILE_MAP` | `~/.config/turnstile/containers.map` | la carte des slots |
| `TURNSTILE_PORT` | `8130` | port de l'API |
| `TURNSTILE_BIND` | `0.0.0.0` | adresse d'écoute (`127.0.0.1` pour rester en local) |
| `TURNSTILE_TOKEN` | *(vide)* | jeton `Authorization: Bearer` exigé sur toute l'API ; un avertissement s'affiche si l'API écoute hors de la boucle locale sans jeton |
| `TURNSTILE_READ_TOKEN` | *(vide)* | second jeton qui n'ouvre que les routes `GET` |
| `TURNSTILE_API` | `http://127.0.0.1:8130` | adresse de l'API utilisée par la commande `turnstile` |
| `TURNSTILE_STATE` | `~/.local/state/turnstile/state.json` | fichier d'état |
| `TURNSTILE_IGNORE` | *(vide)* | conteneurs à ignorer, séparés par des virgules (une passerelle, un proxy) |
| `TURNSTILE_GUESTS` | `~/.config/turnstile/guests.map` | « invités » qui n'utilisent une carte que lorsqu'aucun slot ne l'occupe |
| `TURNSTILE_STANDINS` | `~/.config/turnstile/standins.map` | remplaçants pendant une bascule |
| `TURNSTILE_HARNESS_MAP` | `~/.config/turnstile/harness.map` | ce que `turnstile-watch` observe |
| `TURNSTILE_NTFY` | *(vide)* | URL d'un sujet [ntfy](https://ntfy.sh) pour les alertes ; sans elle, aucune alerte n'est envoyée |

### Intégrations facultatives

Le cœur (`turnstiled`, `turnstile`) fonctionne seul. Les outils suivants sont **désactivés par défaut** et ne servent que si vous utilisez les logiciels correspondants :

- **`turnstile-litellm`** : génère la liste des modèles d'une passerelle [LiteLLM](https://github.com/BerriAI/litellm) à partir de la carte des slots (`turnstile-litellm > config.yaml`). Variables : `TURNSTILE_LITELLM_HOST` (défaut `127.0.0.1`), `TURNSTILE_LITELLM_TIMEOUT` (défaut `1800`).
- **`integrations/turnstile-openfox`** : pointe le client de code OpenFox vers la passerelle avec les modèles de la carte. Non installé par `install.sh` ; à lancer à la main (`--dry-run` pour voir sans écrire). Variables : `OPENFOX_CONFIG` (défaut `~/.config/openfox/config.json`), `TURNSTILE_GATEWAY_URL` (défaut `http://127.0.0.1:4000/v1`).
- **`turnstile-alert`** : notification [ntfy](https://ntfy.sh) quand un service tombe, seulement si `TURNSTILE_NTFY` est défini.
- **`examples/guard-before-run.n8n.json`** : un workflow [n8n](https://n8n.io) qui lit l'état réel avant de lancer une tâche et demande l'avis d'un « juge » ; `turnstile-cascade` s'appuie sur les paires de jugements archivées.

Pour aller plus loin : [la carte](docs/the-map.md), [la chaîne](docs/the-chain.md), [le durcissement](docs/hardening.md) (en anglais).

### Tests

```bash
pip install "ruff==0.16.6" "pytest>=8"   # ou : uv sync --all-extras
./verify.sh
```

Les tests unitaires tournent partout, sans carte graphique ni Docker. Les scripts de `fault-injection/` sont différents : ils cassent volontairement une vraie machine (arrêt brutal en plein chargement, disque plein…) et ne sont jamais lancés par la CI.

## Pourquoi ce projet

Parce qu'une IA locale, c'est génial… jusqu'au jour où l'on découvre que le « gros modèle » qu'on croyait utiliser n'était même pas chargé.
turnstile apprend une règle simple et utile bien au-delà de l'IA : **on observe la machine au lieu de croire ses propres fichiers**.
Et c'est une brique de plus pour faire tourner ses modèles chez soi, sur son matériel, sans dépendre d'un nuage : la souveraineté numérique, concrètement.

## Licence

[MIT](LICENSE).

---

## English

**turnstile** is a deterministic GPU allocator for local AI models: one model at a time on each card, a clean queue, and a service that never lies about what is really running. It does not serve models (`llama.cpp` in Docker does); it decides who gets the card, then checks every 10 seconds that the model answering is the one it claims — container alive, port answering, memory consistent, credible throughput, and the model's identity read from the server's own log.

### Install

Requirements: Linux with systemd user sessions, Docker, Python 3.10+, and one existing container per slot, named like the slot. An NVIDIA card is tested daily; AMD and Intel are untested; with no card, only CPU slots are managed.

```bash
git clone https://github.com/bengua/turnstile
cd turnstile
./install.sh                                  # user-level, no root
$EDITOR ~/.config/turnstile/containers.map    # name|port|card|weights|arguments
systemctl --user enable --now turnstiled
turnstile status
```

### Usage

```bash
turnstile status | slots | counters
turnstile coder-c1        # load a slot (already_serving / served / queued)
turnstile free-c0         # hand card 0 back, e.g. before a game
turnstile restore         # go back to what was serving before
```

The same surface is an HTTP API on `:8130` (`GET /state /slots /counters /health`, `POST /slot`). Set `TURNSTILE_TOKEN` to require a bearer token, and `TURNSTILE_READ_TOKEN` for a read-only one. The LiteLLM, OpenFox, ntfy and n8n integrations are optional and off by default; see "Intégrations facultatives" above. Tests: `./verify.sh` (no GPU or Docker needed).

### Why

Local AI is great until you find out the "big model" you were using was never loaded. turnstile observes the machine instead of trusting its own config files — and it is one more building block for running your models at home, on your own hardware.

### License

[MIT](LICENSE).

---

Un projet [FreedzHost](https://freedzhost.cloud) — souveraineté numérique, IA locale, homelab.
A [FreedzHost](https://freedzhost.cloud) project.
