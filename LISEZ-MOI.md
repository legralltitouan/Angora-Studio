# Angora Studio — Montage automatique AgentOMG / Marvouch → CapCut

Version 2.4.3. Le projet modèle CapCut conserve son nom `AutoShibi-V4` pour rester compatible avec les installations existantes.

Version **entièrement repensée** du logiciel : même résultat final dans CapCut
(dérush, zooms animés, shibis placés par qui-parle exact), **même qualité de
montage**, mais une interface claire façon « Claude » (mode blanc), un moteur
beaucoup plus rapide, et **un retour en direct permanent** : tu sais toujours
exactement ce qui se passe, tranche par tranche.

À partir d'un enregistrement, le logiciel :

1. **dérushe** : coupe faux départs, hésitations, temps morts, raccourcit les silences ;
2. **anime** chaque plan avec un zoom/dézoom (vrais keyframes CapCut, retouchables) ;
3. **pose les shibis** : la pose du personnage qui parle (AgentOMG vert à droite,
   Marvouch bleu à gauche), choisie selon le texte et le ton. **Qui parle est
   exact** grâce aux pistes audio séparées du rec ;
4. génère un **nouveau projet CapCut** prêt à ouvrir — ou **complète ton projet
   déjà monté à la main** (zooms + shibis sur tes plans).

---

## 🚀 Démarrage rapide

1. **Copie ton dossier `Assets/`** actuel dans ce dossier (ou son contenu dans
   `Assets/` fourni) : `AgentOMG Pose/` et `Marvouch Pose/`, images `1.png…22.png`.
2. **Double-clique `Lancer.bat`** (les dépendances Python s'installent toutes
   seules au premier lancement). L'interface s'ouvre dans ton navigateur.
3. Colle ta **clé Gemini** dans « Réglages avancés » (elle est gardée dans `.env`
   sur ton PC), choisis ta vidéo ou ton projet, vérifie les coches vertes,
   puis **▶ Lancer le montage**.

Prérequis : **Python 3.10+** (coche « Add to PATH ») et **ffmpeg**
(`winget install ffmpeg` dans un terminal, puis rouvre le logiciel).
Ton ancien fichier `.env` et tes anciens réglages `gui_settings.json` sont
**repris automatiquement** si tu décompresses cette v2 dans le même répertoire que l'ancienne version.

> **Juste envie de voir l'interface ?** `python app.py --demo` lance un aperçu
> complet avec des données factices (aucun fichier touché).

---

## 🖥️ Comment ça se passe dans l'interface

Trois étapes, de haut en bas — pas de liste de réglages interminable :

### 1 · La source
- **Nouveau montage automatique** : choisis la vidéo du rec (`Parcourir…` ouvre
  le sélecteur Windows) et le **projet modèle** à cloner (création en 3 minutes
  expliquée dans le guide intégré, bouton « Guide » en haut).
- **Terminer mon montage** : choisis ton projet CapCut dans la liste (plans,
  durée et vidéo source s'affichent), puis décide de travailler **sur une copie**
  ou directement (une **sauvegarde est faite juste avant** dans tous les cas).

### 2 · La recette
- **Recettes en un clic** : ⚖️ Équilibré (ton réglage habituel), ⚡ Ultra-rythmé,
  🌿 Doux, 🔍 Zooms seuls, 🐾 Shibis seuls — puis chaque curseur reste ajustable
  (sensibilité du dérush, fréquence/intensité des zooms, taille des shibis…).
- **« 🔍 Détecter les pistes »** lit la vidéo et montre, pour chaque piste audio,
  la part de voix et les niveaux ; l'attribution AgentOMG/Marvouch est proposée
  quand le nom des pistes le permet.
- **Aperçu en direct** de la scène shibi (taille + côtés) et **galerie des 22 poses**.
- Tes réglages sont **enregistrés automatiquement** à chaque changement.

### 3 · Lancer
- Résumé de ce qui va se passer, **estimation** (nombre de tranches Gemini, dont
  celles déjà en cache) et **vérifications en direct** (✓/⚠/✗ avec l'action à faire).
- Le bouton ne se lance que si aucun point bloquant ne subsiste.
- **Vérification complète** = l'ancien `--check`, avec test du réseau Gemini.

### Pendant le montage — fini l'écran figé
- Barre de progression **réelle** (unités terminées / total, pondérées par étape),
  **chronomètre + temps restant estimé**, indicateur « en direct ».
- Étapes détaillées : Préparation → Dérush → Qui parle ? → Choix des poses → Projet CapCut,
  chacune avec son compteur (ex. *tranche 7/12*).
- Panneau **« En ce moment »** : chaque appel Gemini/ffmpeg en cours, avec son
  état (*envoi à Google…*, *le modèle réfléchit…*, *quota : pause puis modèle
  suivant…*) et son **chronomètre qui tourne**. Un signe de vie arrive toutes
  les 2 secondes quoi qu'il arrive.
- Journal horodaté, copiable. **Arrêter** est propre : tout ce qui est déjà
  analysé est en cache, relancer reprend là où ça s'est arrêté.
- Tu peux **fermer l'onglet** : le montage continue ; en rouvrant l'adresse,
  tu retrouves l'état exact (et le journal complet).
- À la fin : récapitulatif (durée avant/après, plans, répliques, zooms, shibis
  par personnage) et boutons *Ouvrir CapCut* / *dossier du projet*.

---

## ⚡ Ce qui a été optimisé (et ce qui n'a pas changé)

**Inchangé — pour un montage de même qualité :** les prompts Gemini, les règles de
coupe des 3 niveaux de dérush, les tables et l'alternance des zooms, le catalogue des
22 poses + les règles de secours émotion/intention, l'anti-répétition des poses,
les seuils VAD et le recalage sur la parole réelle, le placement des shibis
(taille/coins/miroir), la copie du format de keyframes depuis ton CapCut.

**Plus rapide et plus fiable :**
- **Une seule extraction audio** par piste, conservée dans le cache et partagée
  entre dérush et transcription (auparavant ré-extraite à chaque fois) ; offsets
  des tranches lus d'un coup (fini le ffprobe par fichier).
- **Travaux en parallèle** : pendant que Gemini analyse (réseau), ffmpeg fait déjà
  la détection de parole et l'extraction des pistes (CPU).
- **VAD en streaming** : plus de chargement de centaines de Mo d'audio en RAM.
- **Gemini mieux piloté** : sortie JSON structurée (moins de réponses illisibles),
  un modèle en 404 est écarté pour tout le montage, les 429 attendent avec un vrai
  backoff puis changent de modèle, timeouts par requête, **poses demandées en
  parallèle** (avant : lot par lot), et en mode « une seule piste » on envoie
  **l'audio seul** au lieu de téléverser la vidéo (~100× plus léger).
- **Écriture CapCut optimisée** (index des matériaux : plus de balayages répétés),
  silences mis en cache, recalage des coupes par dichotomie.
- **Cache par vidéo renforcé** (empreinte avec échantillons du fichier) : chaque
  tranche réussie est sauvegardée immédiatement — interrompre ne coûte rien.

---

## 📁 Les fichiers

```text
Lancer.bat                    double-clic = interface (installe les dépendances si besoin)
Lancer (avec console).bat     idem, avec la console visible (dépannage)
app.py                        démarre le serveur local + ouvre le navigateur
autoshibi/                    le logiciel (moteur + interface web dans autoshibi/web/)
autocut_shibi.py              ligne de commande compatible v1 (--check, --video, …)
gui.py                        compatibilité : python gui.py ouvre la nouvelle interface
requirements.txt  .env(.example)
Assets/                       tes 2 dossiers de poses (22 png chacun)
cache/                        créé tout seul : analyses par vidéo (réutilisées entre lancements)
style_profile.json            créé tout seul : rythme de coupe des vidéos de référence
autoshibi_settings.json       tes derniers réglages (repris depuis gui_settings.json v1)
tests/e2e_fake.py             test complet sans clé (fausse IA + projets CapCut synthétiques)
```

Le projet CapCut **modèle** reste nécessaire (création expliquée dans le guide
intégré) : c'est lui qui donne le format d'image et le format exact des zooms de
TA version de CapCut. Aucun ID à copier : tout est détecté et renouvelé par le logiciel.

---

## 🎚️ Qui parle ? Les pistes séparées (inchangé, toujours exact)

Piste 1 = mix global (dérush) · piste 2 = AgentOMG seul · piste 3 = Marvouch seul
(numéros réglables dans l'interface). La parole est détectée sur chaque piste :
de la voix sur la piste 2 = AgentOMG parle. Les deux shibis s'affichent ensemble
quand ils parlent en même temps. Un texte inventé sur un silence est supprimé ;
une parole non transcrite reçoit quand même son shibi.

## ⌨️ Ligne de commande (comme avant)

```bat
python autocut_shibi.py --check
python autocut_shibi.py --video "D:\rec\autre_video.mkv"
python autocut_shibi.py --level 2 --repick
```

`--no-derush`, `--no-zoom`, `--no-tracks`, `--fresh`, `--refresh-style`,
`--debug`, `--workers N`, `--template NOM` sont aussi disponibles. `--reuse`
n'est plus nécessaire : le cache est toujours réutilisé.

## 🧰 Dépannage

| Symptôme | Piste |
|---|---|
| ❌ dans les vérifications | Le texte sous le point rouge dit quoi faire |
| `429 / quota / 503` Gemini | Le logiciel patiente puis change de modèle ; baisse « requêtes en parallèle » (Avancé) si ça persiste |
| `404` pour un modèle | Il est écarté automatiquement pour ce montage ; retire-le de `GEMINI_MODELS` dans `.env` |
| Zooms figés | Le modèle n'a pas son keyframe d'exemple (voir le guide intégré) |
| Shibis inversés | Échange les numéros de pistes AgentOMG/Marvouch |
| Shibi sans voix / voix douce manquée | Ajuste « Sensibilité de la voix » (plus bas = plus strict, ex. 20 ; plus doux, ex. 30) |
| Certificat / connexion Gemini | Antivirus HTTPS, VPN, filtre DNS… : « Vérification complète » affiche le certificat reçu ; teste en partage de connexion |
| Annuler une modification | Recopie les fichiers de `<projet>\_autoshibi_backups\<date>` à leur place (CapCut fermé) |
| L'interface ne s'ouvre pas | `Lancer (avec console).bat` montre l'erreur ; détail aussi dans `autoshibi.log` |
| Refaire une analyse de zéro | « Vider le cache de la vidéo » (Avancé) ou `--fresh` |

**CapCut doit être fermé pendant le traitement** (l'interface propose de le
fermer automatiquement, réglage Avancé).

## Vérifier le dérush avant les shibis et zooms

Dans **La recette du montage → Dérush**, active **Vérifier les coupes avant de générer le montage**.
L'option est mémorisée, désactivée par défaut, et disponible pour les nouveaux montages avec dérush.

Ordre du traitement : préparation → dérush IA → **Vérifier** (si activé) → transcription utile → choix des poses → zooms et projet CapCut.
La détection de voix et l'extraction audio peuvent être préparées en arrière-plan avant la validation ; aucun choix de pose ni zoom n'est généré avant.

- **Vert** : gardé ; **gris hachuré** : coupé ; **orange** : sélection.
- Clique pour sélectionner et placer le curseur, puis **Garder / Couper** (ou Suppr).
- Glisse les petites poignées entre deux segments pour ajuster la frontière. L'aimant cherche le centre d'un silence à ±0,35 seconde ; il peut être désactivé.
- **+ / −** zoome la timeline ; le curseur horizontal déplace la fenêtre visible.
- **Scinder au curseur** permet de couper une partie d'un segment, puis de garder/couper la nouvelle portion.
- **Annuler / Rétablir**, Ctrl+Z / Ctrl+Maj+Z et les boutons précédent/suivant sont disponibles.
- **Revenir au dérush IA** rétablit la proposition initiale après confirmation.
- **Valider et générer le montage** reprend le pipeline avec les passages conservés. Les zooms et poses sont calculés sur ce nouveau montage.

La timeline n'attend aucun encodage vidéo ni appel IA supplémentaire. La forme d'onde est calculée sur les extraits audio déjà disponibles, en arrière-plan, et mise en cache. Elle peut prendre quelques secondes au premier passage suivant la durée de la source et le PC, mais ne bloque pas l'édition.

Le lecteur facultatif lit le fichier source directement, avec navigation HTTP Range et saut des passages coupés pendant la lecture. Certains MKV/codecs ne sont pas compatibles avec le navigateur : un message apparaît, sans conversion automatique. La timeline reste utilisable.

Les ajustements sont conservés lors d'un rechargement de la page **dans le même onglet** tant que le serveur est ouvert. Ils ne sont pas conservés après un redémarrage du serveur ou pour un autre traitement. Tu peux annuler le traitement normalement pendant cette étape.

La transcription Gemini travaille par tranches : une tranche partiellement gardée est encore transcrite entière pour le contexte. Les tranches entièrement supprimées sont sautées ; seuls les beats remappés sur les passages gardés sont envoyés au choix des poses. Les plans contigus peuvent être redécoupés au silence pour respecter le réglage de durée des plans et le rythme des zooms.

Tests : `python tests/verification_test.py`, `python tests/e2e_fake.py` et, pour tester une restauration/suppression avant génération, `ANGORA_TEST_VERIFY=1 python tests/e2e_fake.py` (Linux/macOS ; sous Windows, définir la variable d'environnement avant la commande).

### Correctif 2.4.1
La validation accepte les intervalles positifs courts et tolère les petits arrondis aux extrémités de la vidéo. Le seuil de 40 ms reste une aide de manipulation, pas un motif de rejet des coupes existantes.

### Interface 2.4.2
Contraste dark mode corrigé pour les résultats et le bandeau projet ; cases arrondies adaptées aux thèmes ; pourcentage entier dans le titre de l’onglet mis à jour par les événements existants, sans requête supplémentaire. L’option de nouvelle sélection des poses a été clarifiée.

### Lecture et sélection 2.4.3
La sélection du dérush suit la tête de lecture, y compris les déplacements dans le lecteur. Garder/Couper agit pendant la lecture sans la mettre en pause. La fenêtre de timeline suit le curseur lorsqu’il sort de la zone visible. Pour revoir les passages coupés et les restaurer, décocher « Sauter les passages coupés pendant la lecture ». Le slider de navigation est rempli en orange uniquement à gauche de sa poignée ; il déplace la fenêtre de timeline, pas la lecture.
