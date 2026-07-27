# Journal des évolutions

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/).
Les dates suivent l'ordre de développement.

## [Non publié — branche de test] — Le gateway démarre enfin, par le bon chemin

### Corrigé
- **« Agent: not detected » depuis le tout premier déploiement — et la
  boucle de redémarrage d'hier étaient la MÊME cause, dans les deux sens.**
  Vérifié au fait (`docker inspect nousresearch/hermes-agent`) : l'image a
  pour ENTRYPOINT `[/init, /opt/hermes/docker/main-wrapper.sh]`. `/init` est
  s6-overlay ; `main-wrapper.sh` est le SEUL maillon qui sait résoudre
  « gateway run » en un exec du binaire réel. Notre entrypoint personnalisé
  (nécessaire pour écrire la config du modèle avant démarrage) remplaçait
  toute cette chaîne par un `/init` nu :
  - lui passer « gateway run » directement (correctif d'hier) faisait
    chercher à s6 un binaire littéral « gateway », inexistant — boucle de
    redémarrage ;
  - ne rien lui passer du tout (repli d'urgence d'hier) laissait l'API de
    l'agent muette — d'où « Agent: not detected » en continu, symptôme
    présent depuis le premier agent jamais déployé.
  L'entrypoint préserve désormais la chaîne complète de l'image, config du
  modèle comprise : `exec /init /opt/hermes/docker/main-wrapper.sh gateway run`.

## [Non publié — branche de test] — Bouton de secours : forcer une mise à jour

### Ajouté
- **« ⚡ Forcer » en admin** (`POST /api/agents/{id}/request-update?force=true`,
  réservé à l'admin) : repousse la configuration et redémarre l'agent MÊME
  sans écart de version détecté. Deux situations où la mise à jour normale
  ne sert à rien : un correctif qui ne change AUCUN numéro de version (le
  retrait de `gateway run` de l'entrypoint, par exemple — aucune version
  n'en témoigne, donc rien ne l'aurait jamais proposé aux agents déjà
  déployés) ; ou un agent arrêté après un incident, que le contrôle
  « doit être en ligne » bloquerait sinon. Reste gratuit pour l'admin,
  comme toute mise à jour qu'il déclenche. Un client ne peut pas s'en servir
  pour contourner ses propres gardes.

## [Non publié — branche de test] — Mises à jour visibles et pilotées par le client

### Ajouté
- **Bloc « Version de votre agent » dans l'espace client** : la vérification
  se fait toute seule à l'affichage, le client voit la version installée et la
  dernière publiée, et déclenche la mise à jour lui-même. Le pilotage
  n'existait que côté admin.
- **Lecture de la version RÉELLEMENT installée** (`app/agent_probe.py`),
  par deux sources sans authentification : les images épinglées dans le
  compose Coolify (ce que l'hôte lance) et la page de connexion de l'agent,
  qui porte sa version (`/static/login.js?v=v0.51.92`).
  La première version de cette sonde tentait d'ouvrir une session sur l'agent :
  inutile — la version est publique — et néfaste, la rafale d'essais
  emplissant le journal du client de connexions refusées.

### Ajouté (suite)
- **Sas d'attente pendant la mise à jour** : l'agent redémarre et son adresse
  met un moment à répondre. Le client voit désormais un chronomètre et les
  étapes en cours, et le bouton « Ouvrir mon agent » n'apparaît qu'une fois le
  redémarrage terminé — auparavant il tombait sur une erreur du serveur, ce
  qui donnait à croire que la mise à jour avait échoué.
- **Gateway démarré et configuré dès la création.** Sans lui, les tâches
  planifiées ne se déclenchent jamais — l'interface ne bat pas la seconde
  elle-même, c'est le démon du moteur qui le fait toutes les 60 s — et le
  client n'a qu'une pastille pour l'en avertir. Deux manques :
  le conteneur moteur ne lançait **pas** le démon (l'image démarre ses
  services par défaut ; il faut lui passer `gateway run`, sans quoi rien
  n'écoute sur 8642), et l'interface n'avait pas son adresse. Les deux sont
  désormais posés, l'adresse utilisant le nom de conteneur Coolify
  (`{service}-{uuid}`), seul nom que le DNS de Docker résout à coup sûr.
  **La mise à jour remet cette configuration d'aplomb** : les agents déployés
  avant ce correctif la récupèrent sans redéploiement.

### Corrigé
- **Version du moteur pistée au mauvais endroit** : elle était relevée par
  empreinte de commit GitHub (« 07e97d2f ») — illisible pour un client, et
  incomparable au numéro dont parlent les utilisateurs. Le moteur publie ses
  versions sur **PyPI** (`hermes-agent`, 0.19.0 au moment d'écrire) ; c'est
  désormais cette source qui fait référence. Ses images Docker, elles, portent
  des étiquettes datées (`v2026.7.20`) qui ne renseignent pas sur la version
  du logiciel.
- **« Agent introuvable » sur l'agent d'un client, depuis l'admin** : la
  vérification de version n'acceptait que les agents du compte appelant.
  L'exploitant voyait donc une croix rouge sur un agent parfaitement
  fonctionnel, comme si la plateforme l'avait perdu. L'admin accède désormais
  à toute la flotte. **Aucune mise à jour ne lui est facturée** — ni sur ses
  propres agents, ni sur ceux de ses clients, et jamais sur le quota offert du
  client, qui n'a rien demandé : il exploite la plateforme, il ne s'achète pas
  son propre service. Le quota et le paiement ne concernent que les clients ;
  le cloisonnement entre clients, lui, reste entier.
- **Une mise à jour rejouait un déploiement complet** : `run_job` parcourait
  toujours les étapes de création au lieu de celles enregistrées dans le job —
  un agent mis à jour se serait vu attribuer un **second service Coolify**.
- **Les versions sont ÉPINGLÉES dans le compose** — c'est la cause racine.
  Le template Coolify fige l'interface sur un tag (`hermes-webui:0.51.92`) et
  le moteur sur un digest (`@sha256:…`). Un `docker compose pull` sur une
  référence épinglée retire exactement la MÊME image : aucun appel d'API,
  quel qu'il soit, ne pouvait mettre à jour quoi que ce soit. La mise à jour
  réécrit désormais ces références avant de tirer les images — et c'est aussi
  ce qui explique qu'un agent neuf naisse en 0.51.92.
- **La mise à jour ne mettait rien à jour.** Elle passait par
  `/deploy?force=true` — or, dans les sources de Coolify, le contrôleur de
  l'API appelle `StartService::run($resource)` pour un *Service* **sans le
  drapeau de tirage d'images** : `force` n'y est lu que pour les
  *Applications*. L'hôte réutilisait donc ses images en cache. Le seul appel
  qui met réellement à jour est `POST /services/{uuid}/restart?latest=true`,
  qui déclenche un `docker compose pull` — couvrant **les deux conteneurs**
  du compose, le moteur de l'agent comme son interface. Un refus de l'hôte
  fait maintenant échouer l'étape au lieu d'annoncer un succès en trompe-l'œil.
- **Un agent neuf naissait déjà en retard** (constaté : 0.51.92 livré alors
  que 0.52.149 était publié). Le premier déploiement repointe maintenant les
  images du modèle sur la dernière version publiée AVANT de les tirer — sans
  cela, le tirage rapportait les mêmes images épinglées. Étape non bloquante :
  un agent livré sur une version un peu ancienne reste un agent qui
  fonctionne, et son client pourra le mettre à jour d'un clic.
- **« À jour » affirmé sans rien avoir constaté** : la version amont était
  recopiée sur l'agent comme s'il l'avait installée. L'état « à jour » ne
  s'affiche plus que sur une version LUE sur l'agent ; sinon l'interface dit
  franchement « version non détectée » et laisse la mise à jour possible.
- Une mise à jour est refusée sur un agent hors ligne, et le règlement ne
  présume plus les versions : elles sont relues après le redéploiement.

## [Non publié — branche de test] — Confort d'exploitation

### Ajouté
- **Mise en pause d'un encaissement** : chaque produit (déploiement,
  hébergements, chaque montant de recharge) se suspend d'un clic. Le lien
  Stripe **reste enregistré** — plus besoin de l'effacer puis de le recoller
  pour tester ; le parcours bascule sur la page de paiement simulée. La pause
  court-circuite aussi le mode API, sans quoi elle n'aurait aucun effet dès
  qu'une clé Stripe est configurée. Un **bandeau d'avertissement** rappelle en
  permanence ce qui n'encaisse plus, et la carte concernée est teintée.
- **Mot de passe affichable** (accueil et page de réinitialisation) : une
  faute de frappe invisible se soldait par un « identifiants invalides »
  incompréhensible.
- **Suppression d'agent** : le nom à recopier est affiché avec un bouton
  « Copier », et le bouton de suppression ne s'arme que si la frappe
  correspond exactement — garde renforcée et corvée de saisie supprimée.
  Même fenêtre côté admin, où l'on détruit l'agent d'un client.

## [Non publié — branche de test] — Reprise en main : accès admin, e-mails, modèle par défaut

### Corrigé
- **Création d'agent en erreur 500** : `update_cost_eur` et
  `free_updates_per_month` avaient rejoint les clés de tarification sans valeur
  par défaut, faisant échouer la lecture des prix. L'agent était créé puis le
  paiement échouait — d'où le « sous-domaine déjà existant » au second essai.
  Un test refuse désormais toute clé de tarification sans valeur par défaut.
- **Réglages de mise à jour sans effet** : ils étaient écrits sous une clé et
  lus sous une autre ; l'admin les pilote réellement.
- **Mise à jour facturée à vide** : un agent dont la version n'a jamais été
  relevée n'est plus déclaré « en retard ». Le premier relevé pose sa
  référence, les écarts suivants sont réels.
- **Port SMTP 465** : le TLS implicite n'était pas géré — une connexion
  STARTTLS y reste muette jusqu'au délai d'attente, d'où des e-mails qui « ne
  partent pas » sans erreur visible. Le mode est déduit du port, `SMTP_SSL`
  permet de le forcer.

### Ajouté
- **Levier de secours pour l'accès admin** (`ADMIN_BOOTSTRAP_PASSWORD`) : le
  « mot de passe oublié » suppose un SMTP opérationnel ; sans lui, l'exploitant
  n'avait aucun recours. La variable (ré)applique un mot de passe aux comptes
  d'`ADMIN_EMAILS` à chaque démarrage — à retirer une fois la main reprise.
- **Panneau « E-mails » en admin** : état de la configuration, envoi d'un
  e-mail de test renvoyant la cause exacte d'un échec, et génération d'un
  **lien de réinitialisation** à transmettre de la main à la main — de quoi
  dépanner un client sans attendre la réparation du SMTP.
- **Diagnostic de persistance** : au démarrage et dans la supervision admin,
  l'application dit où sa base est écrite et si un volume la protège. Un
  redéploiement sans volume monté fait « disparaître » comptes et agents ;
  c'est désormais visible avant d'y perdre des données.

### Modifié
- **Modèle par défaut : `openai/gpt-4o-mini`** (au lieu de `gpt-4o`) — pour la
  configuration, les nouveaux agents et le sélecteur du parcours d'achat.

## [Non publié — branche de test] — Import manuel des médias de l'Atelier

### Ajouté
- **Import manuel des scènes** (production hors Higgsfield) : chaque fiche
  scène a un bouton « 📥 Importer une vidéo » ; les plans de référence A/B ont
  leur propre carte avec vignette, import et retour au défaut. Le média importé
  devient ACTIF aussitôt, la fiche est marquée « import manuel », « ↺ Défaut »
  restaure le fichier d'origine.
- **Contraintes affichées et vérifiées** : notice dépliable dans l'admin
  (formats, tailles, durées, raccord) ; côté serveur le type réel est contrôlé
  par signature binaire (pas l'extension), la taille est bornée (vidéo 30 Mo,
  image 5 Mo) ; résolution ≠ 1280×720 ou durée > 15 s → avertissement non
  bloquant affiché après l'import (sonde ffmpeg si disponible).
- **Stockage persistant** : les imports vivent dans le volume de données
  (`data/media`, servi sous `/media/...`) — ils survivent aux redéploiements,
  contrairement à `app/static/` reconstruit avec l'image Docker.
- Le moteur du front sait charger chaque scène depuis `/static` (embarquée) ou
  `/media` (importée) avec ses extensions réelles ; les plans fixes A/B suivent
  aussi la configuration (`/api/lab-config` renvoie `custom` + `refs`).

### Technique
- Nouvelle dépendance `python-multipart` (upload FastAPI).

## [Non publié — branche de test] — Vigie OpenRouter et variables assouplies

### Ajouté
- **Crédit OpenRouter en direct** : tuile « crédit restant » du compte
  (`GET /api/v1/credits` avec la clé maître, repli sur la clé partagée),
  seuils colorés (< 10 $ = orange, < 3 $ = rouge), consommation cumulée,
  caché 5 min côté serveur + bouton Actualiser.
- **Fenêtre d'alerte OpenRouter** : sonde de joignabilité de l'API + lecture
  du flux officiel d'incidents (status.openrouter.ai/incidents.rss — il n'y a
  pas de summary.json, vérifié). Incident de moins de 48 h non résolu ou API
  muette → bandeau rouge en tête d'admin avec lien « suivre l'incident ».
- **Top 10 modèles agentiques** : classement d'usage OpenRouter le plus proche
  de l'agentique (catégorie `programming` — « agentic » n'existe pas dans leur
  API, vérifié), restreint aux modèles qui savent appeler des outils, avec
  contexte et prix $/M tokens (entrée/sortie).
- **Réseaux sociaux : afficher/masquer** — un œil 👁 par réseau ; un lien
  masqué disparaît du pied de page public mais reste conservé en admin
  (schéma v2 `{url, visible}`, rétro-compatible avec l'ancien format).
- **Outils externes éditables** : la liste (Higgsfield, Stripe, Coolify,
  Hetzner, OpenRouter par défaut) devient entièrement pilotable — ajouter,
  renommer, changer le lien, masquer, supprimer — dans l'esprit « chaque
  variable du site doit pouvoir être éditée/masquée/supprimée ».

## [Non publié — branche de test] — L'admin devient un poste de pilotage

### Ajouté
- **Fiches scènes complètes** (« Scènes & interactions ») : pour chacun des 12
  nœuds — vignette avec LECTURE de la vidéo active, élément déclencheur exact,
  événement précis (survol / focus / Entrée / succès serveur / transition
  d'état), plans de référence (A→A, A→B, B→B, B→A), type (boucle/one-shot),
  fichier actif modifiable, et TEMPLATE DE PROMPT copiable + contraintes de
  raccord communes — pour régénérer une scène ou changer tout le décor en
  restant raccord. Lien direct Higgsfield.
- **Supervision** : tuiles (comptes, en ligne <5 min, agents actifs, paniers
  abandonnés, revenu total, conversion), santé serveur CPU/RAM/disque avec
  seuils colorés et conseil d'upgrade Hetzner, table clients triée par CA
  (🟢 en ligne, payé/crédit, paniers abandonnés, dernière visite — nouveau
  champ users.last_seen rafraîchi au plus 1×/min).
- **Outils externes** : Higgsfield, Stripe, Coolify, Hetzner, OpenRouter.
- **Réseaux sociaux** : liens gérés en admin (YouTube, X, Instagram, TikTok,
  LinkedIn, Facebook), affichés aussitôt dans le pied de page public.
- **Réactions supplémentaires** : déconnexion → tout s'éteint puis l'atelier se
  rallume (intro rejouée) ; connexion réussie → scanner vert ; touche Entrée
  dans email/mot de passe → validation.
- **Header transparent** en pastilles — le visage d'Hermès n'est plus masqué.
- 🦉 **Easter egg** : Bibli, la chouette de cuivre du premier plan-maître,
  vit désormais dans l'interface — triple-clic sur HERMES pour l'appeler
  (et un salut dans la console).

## [Non publié — branche de test] — L'Atelier complet : 10 scènes, une par interaction

### Ajouté
- **Les 10 scènes de l'atelier** (toutes générées depuis le plan-maître unique,
  1280×720, WebM VP9 + MP4 H.264, chargées à la demande) :
  arrivée (néons qui s'allument difficilement), boucle ambiante, survol email
  (CRT + code vert), survol mot de passe (tubes turquoise en surtension),
  compte créé (pompe qui bat, grosse scène), survol nom d'agent (orbe
  quantique), survol sous-domaine (manomètres + vapeur), survol Déployer
  (grésillement, suspense), NAISSANCE 10 s (voile et interface effacés,
  lignes Tron dorées sur l'armure), boucle vie (câbles relâchés, tête qui
  scrute, particule dorée).
- Séquence vérifiée en navigateur de bout en bout : intro → sommeil →
  micro-scènes au survol → compte créé → naissance → vie.

## [Non publié — branche de test] — Le Laboratoire : expérience narrative

### Ajouté
- **Le laboratoire d'alchimie numérique** : la page n'est plus une landing —
  c'est une scène fixe plein écran (image maître générée depuis le portrait
  d'Hermes, identité préservée : caducée, casque ailé, visière cyan) où Hermes
  est suspendu à ses câbles au milieu des alambics. Le texte vient à la scène
  (panneaux + scroll-snap), l'identification se fait sur un « terminal du
  laboratoire » (console scanlines).
- **Machine à états `Lab`** pilotée par les actions réelles de l'utilisateur :
  focus email/mot de passe → impulsions d'énergie cyan ; création du compte →
  impulsion or ; connexion → laboratoire « chargé » (plus lumineux) ; survol
  de Payer/Déployer → Hermès **tressaille** ; paiement/déploiement → **injection
  de data** (double flash or) ; agent en ligne → bascule sur la boucle « vie »
  (visière éclatante, tête qui scrute, particule lumineuse parcourant le torse,
  câbles relâchés). Au retour d'un créateur dont l'agent tourne, Hermès est
  **déjà éveillé**.
- **Deux boucles vidéo** générées depuis le même plan maître (sommeil / vie),
  WebM VP9 + MP4 H.264, ~1,2 Mo au total, fondus de 2 s ; repli image fixe
  (`lab.webp`, 73 Ko) si réseau lent ou `prefers-reduced-motion`.

## [Non publié — branche de test] — Hermes animé, diagnostic Stripe, parcours d'accueil

### Ajouté
- **Hermes prend vie** : vidéo d'éveil générée depuis le portrait d'origine
  (même cadrage exact — les yeux s'embrasent en cyan, le caducée pectoral pulse
  en or, poussières dorées, caméra qui respire). Fondu doux par-dessus l'image,
  boucle de 5 s, deux formats légers (WebM VP9 252 Ko + MP4 H.264 418 Ko),
  repli automatique sur l'image fixe (vidéo absente, réseau lent ou
  `prefers-reduced-motion`).
- **Diagnostic de clé Stripe en direct** dans l'admin : détecte une clé
  PUBLIQUE `pk_` collée à la place de la SECRÈTE `sk_` (erreur classique — la
  pastille l'explique), une clé refusée par Stripe (401) ou valide (LIVE/TEST).
  Aucun appel API tenté avec une clé du mauvais type (repli immédiat).
- **Parcours d'accueil** : « Créer un compte » devient l'action principale
  (« Se connecter » discret en dessous) ; après connexion, on atterrit sur
  « Commander » (plus de dérive vers « Mon compte »).

## [Non publié] — Stripe en mode API automatique

### Ajouté
- **Intégration API Stripe complète** (`STRIPE_SECRET_KEY`) : les paiements sont
  des **Checkout Sessions créées à la volée** avec `price_data` inline — le
  montant envoyé à Stripe est exactement celui calculé par la plateforme (prix
  admin + frais de service − code promo). **Tout changement dans « Réglages
  business » s'applique immédiatement**, sans produit/prix/lien à maintenir
  côté Stripe. L'abonnement mensuel devient un **abonnement récurrent Stripe**
  (`mode=subscription`), renouvelé par `invoice.paid`.
- Chaîne de replis : API → Payment Links de l'admin → page de paiement simulée
  (montants < 0,50 € — minimum Stripe — servis par la page simulée).
- Admin : pastille d'état « API Stripe active / mode liens » ; `GET
  /api/admin/stripe` expose `api_enabled`.

## [Non publié] — Flotte admin, codes promo, e-mails (vérif. & reset)

### Ajouté
- **Vue admin « Flotte — tous les agents »** : liste tous les agents de tous les
  clients (email propriétaire, état d'hébergement + chrono, crédit) avec
  **Suspendre / Restaurer / +1 mois offert / Supprimer** et déclenchement manuel
  du balayage. Endpoints `POST /api/admin/agents/{id}/suspend|extend`,
  `DELETE /api/admin/agents/{id}`, email propriétaire dans la liste admin.
- **Codes promo** (`PromoCode`) : remise en % ou en montant, par périmètre
  (all/deploy/topup/hosting), avec usages max et expiration. Appliqués au montant
  payé (crédit inchangé), compteur incrémenté au paiement. Champ *Code promo*
  côté client, admin CRUD (`/api/admin/promos`), prévisualisation
  (`/api/promo/validate`). Remise affichée sur la page de paiement.
- **Vérification d'e-mail** : token à l'inscription, `GET /api/auth/verify`,
  bandeau + renvoi (`/api/auth/resend-verification`), `email_verified` exposé.
- **Réinitialisation de mot de passe** : `POST /api/auth/forgot` (anti-énumération)
  → e-mail avec lien vers la page `/reset-password` → `POST /api/auth/reset`
  (token 1 h). Lien « Mot de passe oublié ? » sur la connexion.
- **Mailer** (`mailer.py`) : envoi SMTP si configuré (`SMTP_*`), sinon lien
  journalisé (repli dev). `PUBLIC_BASE_URL` pour les liens des e-mails.

## [Non publié] — Admin en cartes produit & offres d'hébergement clarifiées

### Ajouté / modifié
- **Deux formules d'hébergement distinctes** (au lieu d'un mensuel/annuel flou) :
  *sans engagement* **29 €/mois** (prolongation manuelle, chrono FOMO explicite
  « vous perdez l'agent en fin de mois »), et *abonnement* **19 €/mois** engagé
  12 mois (prélèvement Stripe auto, message serein) ou **209 €/an** payé en une
  fois (1 mois offert). Le déploiement démarre en « sans engagement » pour
  pousser vers l'abonnement. Plans portés par le `Checkout` (`plan`) et le
  `Tenant` (`manual` / `sub_monthly` / `sub_annual`).
- **Interface admin refondue** en **cartes « produit »** : chaque offre réunit
  son **montant et son lien Stripe** au même endroit (déploiement, sans
  engagement, abonnement mensuel, annuel) ; les **recharges** sont des lignes
  *montant + lien* ajoutables ; un **seul bouton** enregistre prix et liens.
- Liens Stripe hébergement séparés : `hosting_manual`, `hosting_sub` (abonnement
  récurrent), `hosting_annual`. Webhook `invoice.paid` conserve le plan
  `sub_monthly`.

## [Non publié] — Abonnement d'hébergement (revenu récurrent) & Stripe (initial)

### Ajouté
- **Abonnement d'hébergement par agent**, chrono **FOMO** sur la carte de
  l'agent (jours → heures → minutes, rouge sous 7 j).
- **Cycle de vie automatique** : à l'échéance (+ grâce configurable) l'agent est
  **suspendu** (stop Coolify) ; données **restaurables 30 j** (paiement client
  ou bouton admin *Restaurer*) ; au-delà, **suppression définitive**. Balayage
  horaire en tâche de fond + `POST /api/admin/enforce-hosting`. Nouveau
  `stop_service` côté client Coolify.
- **Paiements Stripe par Payment Links** : liens configurables en admin
  (déploiement, hébergement mensuel/annuel, un par montant de recharge). Le
  client est redirigé avec `client_reference_id` ; le webhook
  `POST /api/stripe/webhook` crédite automatiquement (`checkout.session.completed`)
  et prolonge les abonnements auto (`invoice.paid`), signature vérifiée via
  `STRIPE_WEBHOOK_SECRET`. **Repli** sur la page de paiement simulée sans lien.
- **Réglages admin étendus** : prix d'hébergement mensuel/annuel, jours de grâce
  et de rétention ; section « Liens de paiement Stripe ».
- **CGV** : nouvelle clause d'hébergement/abonnement (art. 4) — montants et délais
  réels, suspension, rétention, restauration, résiliation.
- Cycle exposé par l'API (`hosting` dans le détail d'un agent) et inclus dans
  l'export RGPD.

## [Non publié] — Frais de service, admin robuste & persistance

### Corrigé (démarrage)
- **Boucle de crash au démarrage** (`FileExistsError: './data'`) quand `/app/data`
  était monté comme un **fichier** (mauvaise config Coolify : `File Mount` au
  lieu d'un `Volume`) : `os.makedirs(exist_ok=True)` levait car la cible n'est
  pas un dossier. `db.py` détecte désormais ce cas, **démarre quand même** en
  repli éphémère (`/tmp`, persistance désactivée + erreur loggée) et gère aussi
  `FileExistsError`/`OSError`. `Dockerfile` : `DATABASE_URL` absolu par défaut
  (`sqlite:////app/data/orchestrator.db`) et `mkdir -p /app/data`, valable même
  en déploiement Dockerfile (où le compose n'est pas lu).

### Ajouté
- **Frais de service sur les recharges** (`SERVICE_FEE_RATE`, 10 % par défaut,
  éditable par l'admin de 0 à 100 %) pour financer l'exploitation. Transparent
  côté client : il choisit un crédit (ex. 10 €), voit le total à payer frais
  inclus (11 €), et reçoit exactement le crédit choisi (plafond OpenRouter relevé
  d'autant). `amount_eur` = payé, `credit_eur` = crédité ; la page de paiement et
  les pastilles de recharge détaillent les deux. Nouveau champ admin « Frais de
  service ».
- **Health check** dans `docker-compose.yml` (`GET /health`) : Traefik/Coolify
  n'aiguillent le trafic qu'une fois l'app prête (supprime l'avertissement
  Coolify « No health check configured »).

### Corrigé
- **Promotion admin robuste** : elle s'applique désormais dès l'inscription **et**
  à chaque requête authentifiée (auto-cicatrisante). Un email d'`ADMIN_EMAILS`
  devient admin **immédiatement**, sans étape déconnexion/reconnexion, et même
  après une base repartie de zéro. L'ancienne promotion « à la connexion »
  (walrus trompeur) est nettoyée.
- **Persistance** : `DATABASE_URL` pointe explicitement vers un chemin **absolu**
  du volume (`sqlite:////app/data/orchestrator.db`) — un chemin relatif dépendait
  du répertoire courant. Documentation du piège Coolify (déploiement
  Dockerfile/Nixpacks qui ignore le volume) et des deux corrections possibles
  (type « Docker Compose » ou Persistent Storage sur `/app/data`).

## [Non publié] — Recharges à montants multiples & correctifs d'exploitation

### Ajouté
- **Recharges à montants multiples** : le client choisit parmi 5 / 10 / 20 / 50 /
  100 € (liste configurable, éditable par l'admin). Chaque euro rechargé relève
  d'autant le plafond de la clé OpenRouter dédiée. Montant validé côté serveur
  contre la liste proposée. Nouveau champ admin « Montants de recharge proposés ».

### Corrigé
- **Bug admin critique** : le `docker-compose.yml` passait `ORCH_JWT_SECRET` /
  `ORCH_ADMIN_EMAILS`, alors que le code lit `JWT_SECRET` / `ADMIN_EMAILS`
  (préfixe vide). Conséquence : l'admin n'était **jamais** promu et le JWT
  restait sur sa valeur par défaut. Noms corrigés et alignés.
- `docker-compose.yml` complété : `OPENROUTER_PROVISIONING_KEY`, `EUR_USD_RATE`,
  coordonnées légales.

### Documentation
- README : sections « Accès administrateur » et « Persistance des données »
  (le volume nommé `orchestrator-data` conserve profils, agents et crédits au
  fil des redéploiements).

## [Non publié] — Conformité RGPD & légale

### Ajouté
- **Pages légales** servies par l'application, liées depuis le pied de page :
  mentions légales (`/legal/mentions`), politique de confidentialité RGPD
  (`/legal/confidentialite`), CGV/CGU (`/legal/cgv`), politique cookies
  (`/legal/cookies`). Contenu personnalisé depuis les coordonnées de `config.py`.
- **Consentement à l'inscription** : case obligatoire, acceptation horodatée et
  versionnée (`users.consent_at` / `consent_version`) comme preuve.
- **Droit d'accès et de portabilité** (RGPD art. 15/20) : `GET /api/account/export`
  exporte toutes les données de l'utilisateur en JSON (bouton « Exporter mes données »).
- **Droit à l'effacement** (art. 17) : `DELETE /api/account` détruit le compte,
  ses agents (services Coolify + clés OpenRouter) et toutes les données, avec
  double confirmation côté interface.
- **Bandeau cookies** informatif (traceurs strictement nécessaires, art. 82 LIL).
- Coordonnées légales configurables (éditeur, hébergeur, DPO) dans `config.py`.
- Registre des traitements dans `docs/RGPD.md`.
- Validation du mot de passe (≥ 8 caractères) à l'inscription.

### Sécurité / conformité
- Le hash du mot de passe est exclu de l'export de données.
- Destruction en cascade réutilisable (`_purge_tenant`) partagée entre la
  suppression d'un agent et l'effacement du compte.

## Correctif majeur — Domaine personnalisé

### Corrigé
- **Le sous-domaine du client n'était pas appliqué** (l'agent restait sur une
  adresse `*.sslip.io`). Cause identifiée dans la spec OpenAPI de Coolify : le
  parseur de compose **ignore** la valeur d'une variable `SERVICE_FQDN_*` et
  régénère son propre domaine. Le domaine se pose désormais via le **champ
  officiel `urls`** (`+ force_domain_override`), transmis dès la création du
  service (`POST /services`) puis re-confirmé par `PATCH /services/{uuid}`.
  - `coolify.py` : `set_service_urls()`, `urls` sur `create_service` /
    `create_service_from_compose` ; suppression de l'ancien `patch_service_fqdn`
    (champs inexistants).
  - `provisioning.py` : `find_web_services()` détecte le conteneur HTTP à cibler ;
    `_step_set_fqdn` PATCHe systématiquement et journalise les domaines retenus.
- **Sous-domaine refusé sur une majuscule** sans explication : il est désormais
  normalisé en minuscules (DNS insensible à la casse), côté serveur et via
  `slugify()` côté client ; message d'erreur clarifié pour les caractères invalides.

## Facturation & clés d'IA dédiées

### Ajouté
- **Clé OpenRouter dédiée par agent**, nommée `hermes-<sous-domaine>` et
  **plafonnée au crédit payé** (provisioning API). Chaque recharge relève le
  plafond ; la suppression de l'agent supprime la clé.
- **Paiement simulé** (cycle de vie type Stripe Checkout) : page `/pay/{id}`,
  crédit initial offert au déploiement, recharges (`topup`).
- **Réglages business admin** : prix de déploiement, montant de recharge, crédit
  offert — modifiables en base (`settings`) et surchargeant les défauts de config.
- **Suppression d'agent** avec double confirmation (Coolify + clé + DB).

## Interface & expérience

### Ajouté
- Refonte visuelle **design system Hermes** (`design/DESIGN.md`) : palette
  restreinte, glass-morphism, animations reveal-word, respect de
  `prefers-reduced-motion`.
- Accès à l'agent repensé : URL + mot de passe avec **spoiler** (flou au clic) et
  boutons copier, **chrono de finalisation** (fenêtre de convergence SSL ~60 s).

### Corrigé
- **Health check** robuste : attente jusqu'à 300 s, acceptation des réponses
  200-403, réussite si Coolify confirme les conteneurs `running` (URL publique en
  convergence).
- **Adresse effective adoptée** quand Coolify n'a pas retenu le domaine
  personnalisé, pour que l'agent soit joignable immédiatement.
- **Apostrophes / retours à la ligne** dans les prompts qui cassaient le parsing
  `.env` de Coolify (conteneurs `exited`) : valeurs assainies.
- **Modèle injecté au démarrage** du conteneur via un entrypoint qui écrit
  `~/.hermes/config.yaml` (le `docker exec` étant impossible depuis la plateforme).

## Fondations

### Ajouté
- Déploiement des agents comme **Services Coolify natifs** (template
  `hermes-agent-with-webui`, deux conteneurs agent + webui, volume partagé) via
  l'API Coolify plutôt que `docker run`.
- Dashboard web de création et gestion des agents.
- Authentification (inscription, connexion, JWT, hash scrypt), rôle admin.
- Moteur de provisioning à étapes avec journal de déploiement en direct.
