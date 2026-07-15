# DESIGN.md — Système de direction artistique Hermes

> Ce fichier est un moteur, pas une inspiration. Toute génération d'interface
> (dashboard, chat d'instance, landing) DOIT s'y référer : « Use DESIGN.md »
> puis générer **section par section** (jamais le site entier), en respectant
> les presets ci-dessous. Les assets sont fournis, jamais inventés.

## 01 Identity
- Marque : HERMES — infrastructure invisible, luxe technique, calme.
- Ton : affirmatif, peu de mots, zéro jargon. Français. Prix en €.
- Sensation cible : concession premium la nuit — sombre, précis, lumineux par touches.

## 02 Color Engine (palette restreinte — ne rien ajouter)
- `--bg: #050507` fond absolu · `--bg-2: #0c0d12` panneaux profonds
- `--surface: rgba(255,255,255,0.04)` cartes · `--line: rgba(255,255,255,0.10)`
- `--text: #f2f3f7` · `--muted: #8a90a3`
- `--accent: #e0a458` (ambre Hermes, UNIQUE couleur d'accent, usage < 10 % de la surface)
- États : ok `#4caf7d` · run `#5c9de0` · bad `#e05c5c` — réservés aux badges/journal.

## 03 Typography Engine
- Famille : `Inter, "Helvetica Neue", system-ui` · titres weight 600, corps 400.
- Échelle stricte : 13 / 15 / 17 / 22 / 28 / 40 / 64 px. Aucune taille hors échelle.
- Titres display : tracking `-0.02em`, line-height 1.05 ; corps line-height 1.55.
- Lettres capitales espacées (`letter-spacing:.18em`) uniquement pour la marque et les eyebrows.

## 04 Spacing & Grid
- Unité : 8 px. Respirations de section : 96 px (desktop) / 56 px (mobile).
- Conteneur max 1100 px, gouttières 28 px. Grille cartes : `minmax(240px,1fr)`, gap 16 px.
- Radius : 14 px (cartes), 9 px (contrôles), 20 px (pills). Jamais d'autre valeur.

## 05 Glass Engine
- Preset `glass-medium` : fond `rgba(255,255,255,0.06)`, blur 18 px,
  bordure `rgba(255,255,255,0.14)`, ombre `0 20px 60px rgba(0,0,0,0.45)`.
- Preset `glass-low` (par défaut cartes) : opacité 4 %, blur 0, bordure `--line`.
- Le glass met en scène ; il ne porte jamais de texte < 15 px.

## 06 Motion Engine (presets — aucune animation improvisée)
- `fade-up` : opacity 0→1 + translateY 24px→0 · 900ms · cubic-bezier(.22,1,.36,1) · délais en cascade 100ms.
- `reveal-word` : titres hero, mot par mot, 60ms d'écart.
- `hover-lift` : translateY -2px + ombre douce · 200ms.
- `glow-accent` : boutons primaires au survol, halo ambre 12 % · 250ms.
- Journal de provisioning : chaque étape apparaît en `fade-up` court (400ms).
- Respect `prefers-reduced-motion` : tout devient opacity seule.

## 07 Component Engine
- **Bouton primaire** : fond `--accent`, texte `#1a1205`, radius 9, padding 12/22, weight 600.
- **Bouton ghost** : fond `--surface`, texte `--text`, même géométrie.
- **Carte offre** : glass-low, prix en 40px, eyebrow muted, un seul CTA.
- **Badge d'état** : pill 12px weight 600, couleurs d'état, jamais d'accent.
- **Kit d'emport** : bordure pointillée `--accent`, code en `--accent`, fond `--bg`.
- **Champ** : fond `--bg`, bordure `#2a2e3a`, focus bordure `--accent` (sans glow).

## 08 Composition Engine (ordre des sections, générées indépendamment)
Hero (cinematic, reveal-word) → Preuve (3 stats) → Offres (3 cartes) →
Journal live (le provisioning comme spectacle) → Kit d'emport → FAQ → Footer.
Une idée par section. Espace négatif généreux. Jamais deux accents visibles en même temps.

## 09 Interaction Engine
- Curseur : défaut ; `magnetic` réservé au CTA hero (translation max 6px).
- Feedback < 100ms sur tout clic (état pressed : scale .98).
- Polling visuel : le journal se met à jour sans layout shift (hauteurs réservées).

## 10 AI Prompt Engine (comment générer avec ce fichier)
```
Use DESIGN.md.
Generate <section> only.
Follow typography scale + spacing 8px.
Glass = low (medium pour hero).
Animation preset = fade-up (reveal-word pour h1).
Accent budget = 1 élément par viewport.
Assets fournis : <urls>. Never invent assets.
Output : HTML/CSS autonome, aucune dépendance externe.
```

## Assets (fournis — jamais inventés par le générateur de code)
- `orchestrator/hermes_orchestrator/static/agent.webp` — portrait officiel de
  l'agent Hermes (généré via Higgsfield). Identité : silhouette élancée et
  élégante (référence gamme NOUR), design Apple-like — unibody graphite mat,
  joints précis, liserés dorés fins — mécanique visible et honnête
  (pistons/roulements/durites chromés au cou et aux articulations). Tête sans
  aucun trait humain : visière lisse + bandeau capteur ambré. **Clin d'œil
  mythologique obligatoire** : ailes du pétase usinées en or sur les côtés de
  la tête + caducée gravé lumineux ambre sur le torse. Ni militaire, ni casque
  type super-héros (droits d'auteur), ni visage humain (effrayant). Posture
  calme, mains jointes : prêt à servir.
  Copy hero associée : « Hermès portait les messages des dieux. Le vôtre porte
  vos missions. »
  **Règle du regard (marketing, non négociable)** : le sujet regarde TOUJOURS
  vers le texte/le dialogue, jamais vers l'extérieur du cadre — un regard qui
  fuit induit le désintérêt. Sujet à droite ⇒ tête tournée vers la gauche.
  Usage : hero uniquement, fondu au noir par `mask-image` radial + `floatSlow` 9s.
  Déclinaisons par offre/destination (gamme façon NOUR : un attribut + un nom
  par métier — commerce, support, juridique…) : même base, même palette, seules
  la pose et l'accessoire changent.

## 11 QA / Consistency Engine (checklist avant merge)
- [ ] Aucune couleur hors palette · [ ] aucune taille hors échelle
- [ ] Accent < 10 % de la surface · [ ] sections générées séparément mais rythme vertical constant (96px)
- [ ] Contraste AA sur `--muted` · [ ] reduced-motion OK · [ ] aucun asset inventé
- [ ] Français, €, virgule décimale.
