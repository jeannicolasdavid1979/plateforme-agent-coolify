"""Pages légales servies par la plateforme.

Obligatoires pour un service en ligne français/UE :
  - Mentions légales (LCEN art. 6 III) — identité de l'éditeur et de l'hébergeur
  - Politique de confidentialité (RGPD art. 13/14) — traitements, droits, contact
  - CGV/CGU — conditions de vente et d'utilisation du service
  - Politique cookies — traceurs déposés (ici : strictement nécessaires)

Le contenu se personnalise depuis les coordonnées de config.py (à renseigner
avant mise en production). Les pages partagent la charte sombre de la
plateforme et renvoient les unes aux autres via un pied de page commun.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db

router = APIRouter()

_STYLE = """
  :root { color-scheme: dark }
  body { font-family:Inter,"Helvetica Neue",system-ui,sans-serif; background:#050507;
         color:#e7e9f0; margin:0; line-height:1.7 }
  .wrap { max-width:760px; margin:0 auto; padding:64px 24px 96px }
  a { color:#8ea2ff; text-decoration:none }
  a:hover { text-decoration:underline }
  .brand { font-weight:700; letter-spacing:.02em; font-size:20px; margin-bottom:40px; display:inline-block }
  .brand span { color:#8ea2ff }
  h1 { font-size:30px; letter-spacing:-0.02em; margin:0 0 8px }
  h2 { font-size:19px; margin:36px 0 10px; color:#fff }
  h3 { font-size:16px; margin:24px 0 6px; color:#c7ccdb }
  p, li { color:#b7bccd }
  .updated { color:#6b7186; font-size:13px; margin-bottom:8px }
  table { border-collapse:collapse; width:100%; margin:12px 0; font-size:14px }
  th, td { text-align:left; padding:9px 12px; border:1px solid rgba(255,255,255,.10); vertical-align:top }
  th { background:rgba(255,255,255,.04); color:#dfe3ee; font-weight:600 }
  .todo { color:#e0a458 }
  code { background:rgba(255,255,255,.06); padding:2px 6px; border-radius:5px; font-size:13px }
  footer { margin-top:56px; padding-top:24px; border-top:1px solid rgba(255,255,255,.10);
           font-size:14px; display:flex; flex-wrap:wrap; gap:18px }
  footer a { color:#8a90a3 }
"""

_LINKS = [
    ("/legal/mentions", "Mentions légales"),
    ("/legal/confidentialite", "Confidentialité"),
    ("/legal/cgv", "CGV / CGU"),
    ("/legal/cookies", "Cookies"),
    ("/", "← Retour au site"),
]


def _shell(title: str, body: str) -> HTMLResponse:
    footer = " ".join(f'<a href="{href}">{label}</a>' for href, label in _LINKS)
    html = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — {get_settings().site_name}</title>
<style>{_STYLE}</style></head><body><div class="wrap">
<a class="brand" href="/">HER<span>MES</span></a>
{body}
<footer>{footer}</footer>
</div></body></html>"""
    return HTMLResponse(html)


def _highlight_todo(value: str) -> str:
    """Marque en couleur les champs de config encore à renseigner."""
    if value and value.strip().startswith("[À RENSEIGNER"):
        return f'<span class="todo">{value}</span>'
    return value or "—"


@router.get("/legal/mentions", response_class=HTMLResponse)
def mentions_legales():
    s = get_settings()
    pub = _highlight_todo(s.legal_publisher)
    rows = [
        ("Éditeur du site", pub),
        ("Forme juridique", _highlight_todo(s.legal_status)),
        ("SIRET / RCS", _highlight_todo(s.legal_siret)),
        ("Adresse", _highlight_todo(s.legal_address)),
        ("Directeur de la publication", _highlight_todo(s.legal_director)),
        ("Contact", f'<a href="mailto:{s.legal_contact_email}">{s.legal_contact_email}</a>'),
    ]
    if s.legal_capital:
        rows.insert(2, ("Capital social", s.legal_capital))
    if s.legal_vat:
        rows.append(("TVA intracommunautaire", s.legal_vat))
    editor_rows = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
    body = f"""
<h1>Mentions légales</h1>
<p class="updated">Conformément à l'article 6 III de la loi n° 2004-575 du 21 juin 2004
pour la confiance dans l'économie numérique (LCEN).</p>

<h2>Éditeur</h2>
<table>{editor_rows}</table>

<h2>Hébergeur</h2>
<table>
  <tr><th>Raison sociale</th><td>{s.host_name}</td></tr>
  <tr><th>Adresse</th><td>{s.host_address}</td></tr>
  <tr><th>Contact</th><td>{s.host_contact}</td></tr>
</table>
<p>Les agents déployés via la plateforme sont hébergés sur cette même
infrastructure (serveur dédié, orchestration Coolify).</p>

<h2>Propriété intellectuelle</h2>
<p>L'ensemble des éléments du site ({s.site_name}) — marque, textes, interface —
est protégé. Toute reproduction sans autorisation est interdite. Les agents
déployés reposent sur des logiciels tiers (Hermes Agent, OpenRouter) soumis à
leurs propres licences.</p>

<h2>Signalement</h2>
<p>Tout contenu illicite constaté sur un agent hébergé peut être signalé à
<a href="mailto:{s.legal_contact_email}">{s.legal_contact_email}</a>.</p>
"""
    return _shell("Mentions légales", body)


@router.get("/legal/confidentialite", response_class=HTMLResponse)
def politique_confidentialite():
    s = get_settings()
    body = f"""
<h1>Politique de confidentialité</h1>
<p class="updated">Version du {s.terms_version} · Règlement (UE) 2016/679 (RGPD)
et loi Informatique et Libertés modifiée.</p>

<p>Cette politique décrit comment {s.site_name} traite vos données
personnelles lorsque vous créez un compte et déployez des agents.</p>

<h2>1. Responsable du traitement</h2>
<p>{_highlight_todo(s.legal_publisher)}, éditeur du site, est responsable du
traitement. Pour toute question relative à vos données :
<a href="mailto:{s.dpo_email}">{s.dpo_email}</a>.</p>

<h2>2. Données collectées et finalités</h2>
<table>
  <tr><th>Donnée</th><th>Finalité</th><th>Base légale</th><th>Conservation</th></tr>
  <tr><td>Adresse e-mail</td><td>Création et gestion du compte, connexion,
      communication de service</td><td>Exécution du contrat</td>
      <td>Durée du compte + 12 mois</td></tr>
  <tr><td>Mot de passe (haché, scrypt)</td><td>Sécurisation de l'accès</td>
      <td>Exécution du contrat</td><td>Durée du compte</td></tr>
  <tr><td>Agents déployés (nom, sous-domaine, modèle, instructions)</td>
      <td>Fourniture du service</td><td>Exécution du contrat</td>
      <td>Durée du compte, suppression immédiate à la demande</td></tr>
  <tr><td>Historique de paiement (montants, crédits)</td>
      <td>Facturation et obligations comptables</td>
      <td>Obligation légale</td><td>10 ans (comptabilité)</td></tr>
  <tr><td>Preuve de consentement (date, version)</td>
      <td>Preuve de l'acceptation des conditions</td>
      <td>Obligation légale (RGPD)</td><td>Durée du compte + 3 ans</td></tr>
  <tr><td>Journaux techniques (déploiements, statuts)</td>
      <td>Fonctionnement et diagnostic</td><td>Intérêt légitime</td>
      <td>Suppression avec l'agent</td></tr>
</table>
<p>Aucune donnée de navigation à des fins publicitaires n'est collectée. Aucun
profilage n'est réalisé.</p>

<h2>3. Contenu échangé avec les agents</h2>
<p>Les conversations et fichiers que vous confiez à un agent sont traités par
le fournisseur de modèle (OpenRouter et le fournisseur du modèle choisi) pour
générer les réponses. Ne confiez à un agent que des données pour lesquelles
vous disposez d'une base légale. Consultez la politique d'OpenRouter :
<a href="https://openrouter.ai/privacy" target="_blank" rel="noopener">openrouter.ai/privacy</a>.</p>

<h2>4. Destinataires et sous-traitants</h2>
<ul>
  <li><strong>{s.host_name}</strong> — hébergement de l'infrastructure (UE).</li>
  <li><strong>OpenRouter</strong> — routage des requêtes vers les modèles d'IA.</li>
  <li><strong>Prestataire de paiement</strong> (Stripe, à l'activation) —
      traitement des paiements ; nous ne stockons aucune donnée de carte.</li>
</ul>
<p>Vos données ne sont ni vendues, ni cédées à des tiers à des fins commerciales.</p>

<h2>5. Vos droits</h2>
<p>Vous disposez des droits d'accès, de rectification, d'effacement, de
limitation, d'opposition et de portabilité (RGPD art. 15 à 22).</p>
<ul>
  <li><strong>Accès et portabilité</strong> : depuis votre tableau de bord,
      « Exporter mes données » télécharge l'ensemble de vos données au format JSON.</li>
  <li><strong>Effacement</strong> : « Supprimer mon compte » détruit
      définitivement votre compte, vos agents et leurs données.</li>
  <li>Pour tout autre droit, écrivez à
      <a href="mailto:{s.dpo_email}">{s.dpo_email}</a>.</li>
</ul>
<p>Vous pouvez introduire une réclamation auprès de la CNIL
(<a href="https://www.cnil.fr" target="_blank" rel="noopener">www.cnil.fr</a>).</p>

<h2>6. Sécurité</h2>
<p>Mots de passe hachés (scrypt), accès par jeton signé, chiffrement TLS,
isolation de chaque agent dans ses propres conteneurs, clé d'IA dédiée et
plafonnée par client.</p>

<h2>7. Modifications</h2>
<p>Cette politique peut évoluer ; la version en vigueur est datée en tête de page.</p>
"""
    return _shell("Politique de confidentialité", body)


@router.get("/legal/cgv", response_class=HTMLResponse)
def cgv(db: Session = Depends(get_db)):
    s = get_settings()
    # Montants réellement appliqués (défauts de config surchargés par l'admin).
    from .api import get_pricing
    p = get_pricing(db)
    monthly = f"{p['hosting_price_eur']:.2f}".replace(".", ",")
    annual = f"{p['hosting_annual_price_eur']:.2f}".replace(".", ",")
    retention = int(p["hosting_retention_days"])
    grace = int(p["hosting_grace_days"])
    grace_txt = (f" Un délai de grâce de {grace} jour(s) suit l'échéance avant suspension."
                 if grace > 0 else "")
    body = f"""
<h1>Conditions générales de vente et d'utilisation</h1>
<p class="updated">Version du {s.terms_version}.</p>

<h2>1. Objet</h2>
<p>Les présentes conditions régissent l'utilisation de {s.site_name}, service
de déploiement d'agents conversationnels d'intelligence artificielle, et la
vente des prestations associées (déploiement, crédits d'IA).</p>

<h2>2. Compte</h2>
<p>La création d'un compte requiert une adresse e-mail valide et l'acceptation
des présentes conditions et de la politique de confidentialité. Vous êtes
responsable de la confidentialité de vos identifiants et des usages faits de
vos agents.</p>

<h2>3. Prestations et prix</h2>
<p>Le déploiement d'un agent et les recharges de crédit d'IA sont facturés aux
tarifs affichés au moment de la commande, en euros toutes taxes comprises le
cas échéant. L'éditeur peut modifier ses tarifs à tout moment ; le prix
applicable est celui affiché lors de la commande. Un crédit d'IA offert peut
accompagner le premier déploiement.</p>

<h2>4. Hébergement et abonnement récurrent</h2>
<p>Chaque agent déployé est hébergé contre un <strong>abonnement d'hébergement</strong>.
Le premier mois est inclus dans le déploiement. À l'échéance, l'abonnement doit
être renouvelé pour maintenir l'agent en ligne, au choix :</p>
<ul>
  <li><strong>Mensuel</strong> : {monthly} € par mois, renouvellement avant la date
  anniversaire mensuelle. Un compte à rebours vous indique le temps restant.</li>
  <li><strong>Annuel</strong> : {annual} € pour douze mois.</li>
</ul>
<p>À défaut de renouvellement à la date anniversaire, l'agent est
<strong>suspendu</strong> (conteneurs arrêtés, accès interrompu).{grace_txt} Les
données sont alors <strong>conservées {retention} jours</strong>, pendant lesquels
le compte reste <strong>restaurable</strong> après régularisation (par vous via un
paiement, ou par l'éditeur). Passé ce délai de {retention} jours de retard, l'agent
et ses données sont <strong>supprimés définitivement</strong>, sans possibilité de
restauration. La suspension ne donne lieu à aucun remboursement du crédit d'IA
restant. Vous pouvez résilier à tout moment en cessant de renouveler ; aucun
prélèvement n'intervient sans une action de paiement de votre part, sauf si vous
avez souscrit un abonnement à débit automatique, résiliable depuis votre espace.</p>

<h2>5. Paiement</h2>
<p>Le paiement s'effectue en ligne au moment de la commande. Le déploiement de
l'agent est déclenché après confirmation du paiement. Les crédits d'IA
alimentent une clé dédiée, plafonnée, permettant à l'agent d'appeler les
modèles ; ils sont consommés à l'usage. Un supplément de frais de service peut
s'appliquer aux recharges ; il est affiché avant paiement.</p>

<h2>6. Droit de rétractation</h2>
<p>Le service étant fourni immédiatement et de manière personnalisée après
paiement, vous demandez expressément son exécution immédiate et reconnaissez,
conformément à l'article L221-28 du Code de la consommation, renoncer à votre
droit de rétractation une fois le déploiement lancé. Le crédit d'IA non
consommé n'est pas remboursable et est perdu en cas de suppression de l'agent.</p>

<h2>7. Disponibilité</h2>
<p>Le service est fourni « en l'état », selon une obligation de moyens. Des
interruptions peuvent survenir (maintenance, incident d'hébergement, panne des
fournisseurs de modèles). La mise en ligne d'un agent inclut une phase de
convergence (routage, certificat) de quelques minutes.</p>

<h2>8. Usages interdits</h2>
<p>Il est interdit d'utiliser un agent à des fins illicites, pour produire des
contenus contraires à la loi, porter atteinte aux droits de tiers, ou
contourner les plafonds et l'isolation du service. Tout manquement peut
entraîner la suspension ou la suppression du compte sans remboursement.</p>

<h2>9. Responsabilité</h2>
<p>Les réponses des agents sont générées par des modèles d'IA et peuvent
comporter des erreurs ; elles ne constituent pas un conseil professionnel.
Vous restez responsable de l'usage que vous faites des contenus produits.
La responsabilité de l'éditeur est limitée au montant des sommes versées au
titre de la prestation concernée.</p>

<h2>10. Résiliation</h2>
<p>Vous pouvez supprimer un agent ou votre compte à tout moment depuis le
tableau de bord, ou résilier en cessant de renouveler l'abonnement d'hébergement
(voir article 4). La suppression est définitive et entraîne la destruction des
données et des crédits associés.</p>

<h2>11. Droit applicable</h2>
<p>Les présentes conditions sont soumises au droit français. À défaut de
résolution amiable, les tribunaux français sont compétents. Contact :
<a href="mailto:{s.legal_contact_email}">{s.legal_contact_email}</a>.</p>
"""
    return _shell("CGV / CGU", body)


@router.get("/legal/cookies", response_class=HTMLResponse)
def cookies():
    s = get_settings()
    body = f"""
<h1>Politique de gestion des cookies et traceurs</h1>
<p class="updated">Version du {s.terms_version}.</p>

<p>{s.site_name} utilise le strict minimum de traceurs, uniquement ceux
nécessaires à son fonctionnement. Aucun cookie publicitaire, aucun traceur de
mesure d'audience tiers, aucun partage à des fins marketing.</p>

<h2>Traceurs utilisés</h2>
<table>
  <tr><th>Nom</th><th>Type</th><th>Finalité</th><th>Durée</th></tr>
  <tr><td><code>hermes_jwt</code></td><td>Stockage local (localStorage)</td>
      <td>Maintenir votre session connectée</td><td>Jusqu'à déconnexion</td></tr>
  <tr><td><code>hermes_email</code></td><td>Stockage local</td>
      <td>Afficher votre e-mail dans l'interface</td><td>Jusqu'à déconnexion</td></tr>
  <tr><td><code>hermes_finalized_*</code></td><td>Stockage local</td>
      <td>Ne pas rejouer l'animation de finalisation d'un agent</td>
      <td>Persistant (effaçable)</td></tr>
</table>

<p>Ces traceurs relèvent des cookies « strictement nécessaires » au sens de
l'article 82 de la loi Informatique et Libertés : ils sont exemptés de
consentement préalable car indispensables à la fourniture du service que vous
demandez. Ils ne sont pas déposés à des fins de suivi.</p>

<h2>Gérer ou supprimer ces traceurs</h2>
<p>La déconnexion efface les traceurs de session. Vous pouvez aussi vider le
stockage local du site depuis les réglages de votre navigateur à tout moment,
sans perte de vos données de compte (conservées côté serveur).</p>

<p>Questions : <a href="mailto:{s.dpo_email}">{s.dpo_email}</a>.</p>
"""
    return _shell("Cookies", body)


@router.get("/api/legal/config")
def public_legal_config() -> dict:
    """Infos publiques minimales pour le pied de page du dashboard."""
    s = get_settings()
    return {
        "site_name": s.site_name,
        "terms_version": s.terms_version,
        "year": date.today().year,
    }
