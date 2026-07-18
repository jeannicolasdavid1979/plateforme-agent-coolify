# Journal des évolutions

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/).
Les dates suivent l'ordre de développement.

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
