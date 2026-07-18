"""Référentiel des scènes de l'Atelier — la source de vérité de l'admin.

Pour chaque nœud d'animation : QUEL élément d'interface le déclenche, à QUEL
événement exact (survol, focus, saisie, validation, transition d'état), entre
quels plans de référence il joue (A = atelier inerte, B = Hermès vivant), et le
TEMPLATE DE PROMPT complet utilisé pour le générer — avec les contraintes de
raccord (caméra verrouillée, cycles clos, première et dernière frame = plans de
référence). En changeant de décor : régénérer d'abord les plans A/B, puis
chaque scène avec son template + start_image/end_image — tout reste raccord.
"""

# Contraintes communes à TOUTE génération (à coller en fin de prompt).
PROMPT_RULES = (
    "Fixed locked camera, zero camera movement, photoreal. "
    "Every motion cycle must fully CLOSE before the end (steam dissipated, "
    "arms retracted, lights settled). The FIRST frame must be the provided "
    "start_image and the FINAL frame must be IDENTICAL to the provided "
    "end_image. Generate with medias roles start_image and end_image."
)

SCENES = {
    "intro": {
        "label": "Arrivée sur le site — l'atelier s'allume",
        "element": "Chargement de la page (visiteur non connecté)",
        "events": "Automatique, une fois par session (et rejouée après déconnexion)",
        "refs": "A → A",
        "kind": "one-shot",
        "prompt": "THE LAB POWERS ON: from the exact start frame, the hall's lights first die to near-darkness — only the red beacon keeps rotating faintly. Then power returns with difficulty: relays clack, neon strips flicker and sputter in the depth of the hall, station label panels blink on one by one, the CRT panels warm up, the blue accent lights stabilize. Everything settles back EXACTLY to the initial state. The robot statue never moves, its visor stays dark. No steam.",
    },
    "dormant": {
        "label": "Boucle ambiante — atelier en veille",
        "element": "Fond permanent tant qu'aucun agent n'est en vie",
        "events": "Boucle continue (base), repart de la frame 0 après chaque scène",
        "refs": "A → A (boucle)",
        "kind": "boucle",
        "prompt": "IDLE AMBIENT LOOP of the vast laboratory hall: the red beacon light sweeps slowly, faint dust drifts in the volumetric depth, the blue accent lights breathe almost imperceptibly, a distant panel flickers once briefly. The robot statue on its golden caduceus dais never moves, visor dark. The giant tilted screen stays off. No steam, no bubbles.",
    },
    "email": {
        "label": "Champ Email — scanner biométrique ROUGE",
        "element": "Champ « email » (connexion/inscription)",
        "events": "Survol souris (mouseenter) et prise de focus (clic ou Tab) — max 1×/45 s",
        "refs": "A → A",
        "kind": "one-shot",
        "prompt": "EMAIL — BIOMETRIC SCAN: a compact mechanical scanner arm descends smoothly from the ceiling shadows, projects a thin RED laser grid that sweeps the air twice as if scanning the visitor, then the laser cuts off and the arm fully RETRACTS back into the ceiling out of view. The red beacon pulses once in sync. The robot statue never moves, visor dark.",
    },
    "password": {
        "label": "Champ Mot de passe — scan approfondi ORANGE",
        "element": "Champ « mot de passe »",
        "events": "Survol souris et prise de focus — max 1×/45 s",
        "refs": "A → A",
        "kind": "one-shot",
        "prompt": "PASSWORD — DEEP SCAN: the same ceiling scanner arm descends again, but this time projects an ORANGE laser grid that sweeps more thoroughly, analyzing; small orange indicator LEDs light up along the station panels; then the laser cuts, the orange LEDs fade out, and the arm fully RETRACTS into the ceiling. The robot statue never moves, visor dark, red beacon unchanged.",
    },
    "account": {
        "label": "Accès accordé — scanner VERT + onde verte",
        "element": "Boutons « Créer mon compte » et « Se connecter »",
        "events": "Après SUCCÈS de la création de compte ou de la connexion (et touche Entrée dans les champs)",
        "refs": "A → A",
        "kind": "one-shot",
        "prompt": "ACCESS GRANTED — THE LAB ACCEPTS THE OPERATOR: the ceiling scanner arm descends, projects a GREEN laser grid that sweeps once, satisfied; then a soft green wave of light travels down the hall from front to back, station panels brightening in sequence as machines wake from standby; the red beacon flashes green twice; then the wave fades, panels settle back, the scanner arm fully retracts, the beacon returns to red. The robot statue never moves, visor dark. Very satisfying.",
    },
    "name": {
        "label": "Nom de l'agent — boot du firmware",
        "element": "Champ « Nom de l'agent » (page Commander)",
        "events": "Survol souris (mouseenter) — max 1×/45 s",
        "refs": "A → A",
        "kind": "one-shot",
        "prompt": "AGENT NAME — FIRMWARE BOOT: the first interaction with Hermes himself. Faint scrolling boot pixels flicker briefly across the robot's dark eye visor, like firmware starting; his small golden helmet wings pivot slightly and settle back; his ear-side sensors adjust once. His body never moves otherwise, he remains a statue. Then the visor goes dark again exactly as before. Subtle, precise, intimate.",
    },
    "sub": {
        "label": "Sous-domaine — le routeur trouve le chemin",
        "element": "Champ « Sous-domaine »",
        "events": "Survol souris (mouseenter) — max 1×/45 s",
        "refs": "A → A",
        "kind": "one-shot",
        "prompt": "SUBDOMAIN — THE ROUTER FINDS THE PATH: the 'LLM ROUTER CONNECTION' station panel lights up brighter; luminous data flows appear along the wall conduits, change direction at junction points like a railway switching yard finding the right route, and converge into a single steady path toward the far door; the path holds one second, satisfied; then the flows fade out and the panel settles back. The robot statue never moves, visor dark.",
    },
    "deploy": {
        "label": "Survol Payer/Déployer — suspense (grésillement)",
        "element": "Tout bouton contenant Payer / Déployer / Prolonger / S'abonner",
        "events": "Survol souris (mouseover) — max 1×/20 s + grésillement CSS pendant le survol",
        "refs": "A → A",
        "kind": "one-shot",
        "prompt": "DEPLOY HOVER — HELD SUSPENSE: the hall's lights SIZZLE anxiously — neon strips dim and surge, buzzing; the red beacon quickens its sweep; tiny electric arcs crackle once along a wall conduit; the station panels flicker in nervous anticipation; the robot statue's dark visor emits ONE single faint expectant pulse, as if holding its breath. Tension builds but nothing releases. Then every light settles back to normal. The robot never moves.",
    },
    "deploying": {
        "label": "Déploiement en cours — la forge travaille",
        "element": "État des agents (serveur)",
        "events": "Boucle de fond tant qu'un agent est en statut pending/deploying (Docker travaille)",
        "refs": "A → A (boucle)",
        "kind": "boucle",
        "prompt": "DEPLOYMENT IN PROGRESS — THE FORGE WORKS, ambient loop: deep in the hall, industrial machinery activates — rails vibrate subtly, a robotic gantry arm slides across the back and returns to its dock, station panels blink 'working' patterns, streams of light pulse along the floor conduits toward the dais in steady waves, the 'DEPLOYMENT ENGINE' station panel glows brighter then settles. The robot statue's chest swells VERY slightly once, like a first breath, then stills.",
    },
    "birth": {
        "label": "LA NAISSANCE — livraison de l'agent (10 s)",
        "element": "État des agents (serveur) + retour de paiement",
        "events": "Passage au statut running (agent livré) ; aussi au retour Stripe ?paiement=ok. Voile et interface effacés pendant la scène",
        "refs": "A → B (10 s)",
        "kind": "one-shot",
        "prompt": "THE BIRTH OF HERMES — long cinematic from the inert state to the awakened state: the giant circular quantum core at the far end of the hall ignites first, its rings glowing cyan-white; a wave of energy rolls forward through the hall, station panels flaring in sequence; streams of light race along the floor conduits to the golden caduceus dais; the dais rim lights up; energy climbs into the robot statue — thin golden lines ignite across his armor, his caduceus emblem glows, and finally his eye visor lights up soft warm white; his chest rises once, a first breath, his fingers flex subtly; the red beacon turns GREEN; the giant tilted console screen switches on with a faint dashboard glow; the 'CORE STATUS' panel turns green ACTIVE. Ending in perfect stillness in the awakened state. Majestic, emotional. Duration 10 seconds.",
    },
    "alive": {
        "label": "Boucle vie — Hermès éveillé",
        "element": "Fond permanent quand au moins un agent est en ligne (et au retour du créateur)",
        "events": "Boucle continue (base) après la naissance ; immédiate à la connexion si un agent tourne déjà",
        "refs": "B → B (boucle)",
        "kind": "boucle",
        "prompt": "HERMES ALIVE — ambient loop from the awakened state: the robot's warm white visor glows steadily; he turns his head very slowly a few degrees toward the giant console screen on the left, holds a moment as if aware of his environment, then returns to face forward exactly as before; his fingers flex once subtly; the green beacon sweeps calmly; the quantum core rings at the far end pulse gently; a soft light travels once along the golden lines of his armor and fades. Serene, majestic.",
    },
    "delete": {
        "label": "Suppression — l'agent foudroyé (survoltage)",
        "element": "Bouton « Supprimer » d'un agent (après double confirmation)",
        "events": "Après SUCCÈS de la suppression côté serveur",
        "refs": "B → A",
        "kind": "one-shot",
        "prompt": "DELETION — OVERVOLTAGE: from the awakened state, alarms flash — the green beacon snaps to red strobing; a violent electrical overvoltage strikes the robot: bright arcs crackle over his armor, his visor flares then DIES to black, the golden lines on his armor extinguish, a thin wisp of smoke rises from his shoulders and fully dissipates; the giant console screen shuts off; the quantum core powers down; the 'CORE STATUS' panel returns to red INACTIVE; the beacon settles back to its normal red sweep. The robot stands inert again, a statue. Smoke fully gone at the end. Dramatic but clean.",
    },
}
