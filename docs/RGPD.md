# Registre des activités de traitement (RGPD art. 30)

> Modèle pré-rempli d'après le fonctionnement de la plateforme. **À compléter et
> à valider** par le responsable de traitement avant exploitation (identité,
> coordonnées du DPO le cas échéant, accords de sous-traitance signés).

## Responsable de traitement

- **Éditeur** : voir `LEGAL_PUBLISHER` (mentions légales) — *à renseigner*.
- **Contact RGPD** : voir `DPO_EMAIL` — *à renseigner*.

## Finalités et bases légales

| # | Traitement | Finalité | Base légale (RGPD art. 6) |
|---|------------|----------|---------------------------|
| 1 | Gestion des comptes | Création, authentification, communication de service | Exécution du contrat (b) |
| 2 | Déploiement et gestion des agents | Fourniture du service commandé | Exécution du contrat (b) |
| 3 | Paiements et crédits | Facturation, obligations comptables | Obligation légale (c) |
| 4 | Preuve de consentement | Démontrer l'acceptation des conditions | Obligation légale (c) |
| 5 | Journaux techniques | Fonctionnement, sécurité, diagnostic | Intérêt légitime (f) |

## Catégories de données

- **Identité / contact** : adresse e-mail.
- **Authentification** : mot de passe haché (scrypt) — jamais en clair, jamais exporté.
- **Données de service** : nom, sous-domaine, modèle et instructions des agents.
- **Données financières** : montants, crédits, statut des paiements. *Aucune donnée
  de carte n'est stockée* (déléguée au prestataire de paiement).
- **Preuve** : date et version du consentement.

> Aucune donnée sensible (art. 9) n'est collectée par la plateforme. Les contenus
> que l'utilisateur confie à un agent transitent par le fournisseur de modèle et
> relèvent de la responsabilité de l'utilisateur.

## Catégories de personnes concernées

- Clients / utilisateurs inscrits sur la plateforme.

## Durées de conservation

| Donnée | Durée |
|--------|-------|
| Compte et e-mail | Durée du compte, puis 12 mois |
| Agents et leurs données | Durée du compte ; suppression immédiate à la demande |
| Historique de paiement | 10 ans (obligations comptables) |
| Preuve de consentement | Durée du compte + 3 ans |
| Journaux techniques | Supprimés avec l'agent |

## Destinataires / sous-traitants

| Sous-traitant | Rôle | Localisation | Encadrement |
|---------------|------|--------------|-------------|
| Hébergeur (Hetzner par défaut) | Hébergement de l'infrastructure | UE | DPA à signer |
| OpenRouter | Routage des requêtes vers les modèles d'IA | Hors UE possible | CGU + politique de confidentialité |
| Prestataire de paiement (Stripe, à l'activation) | Traitement des paiements | UE/US (clauses types) | DPA à signer |

## Transferts hors UE

- Susceptibles via OpenRouter / le fournisseur de modèle choisi et le prestataire
  de paiement. À encadrer par des clauses contractuelles types (CCT) et à
  documenter dans la politique de confidentialité.

## Mesures de sécurité

- Mots de passe hachés (scrypt, sel aléatoire).
- Authentification par jeton signé (JWT, secret à configurer).
- Chiffrement en transit (TLS/HTTPS via Traefik).
- Isolation de chaque agent dans ses propres conteneurs.
- Clé d'IA dédiée et **plafonnée** par client (limite la casse en cas de fuite).
- Suppression en cascade vérifiable (`_purge_tenant`).

## Exercice des droits

| Droit | Mise en œuvre |
|-------|---------------|
| Accès / portabilité (art. 15/20) | `GET /api/account/export` — bouton « Exporter mes données » |
| Effacement (art. 17) | `DELETE /api/account` — bouton « Supprimer mon compte » |
| Rectification, limitation, opposition | Sur demande à l'adresse `DPO_EMAIL` |

Réclamation possible auprès de la CNIL (www.cnil.fr).
