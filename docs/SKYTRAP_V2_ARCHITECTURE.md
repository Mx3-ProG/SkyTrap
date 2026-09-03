# SkyTrap V2 — audit et architecture

## Portée de l'audit

Audit réalisé sur le dépôt existant `Mx3-ProG/SkyTrap`, sans créer de projet parallèle.
Le dépôt est une application Python 3.11 installable, avec une CLI Typer, un serveur
FastAPI/WebSocket, une interface React/Vite et Ollama comme fournisseur de modèle.

## État actuel

Le flux principal historique est `Architect -> confirmation -> Developer -> tests ->
Reviewer`. `core.agent.run_agent_turn` fournit déjà une petite boucle
`model -> tool call -> observation`, bornée en nombre d'étapes. Le CLI et le worker
serveur assemblent de vrais outils et enregistrent des notes dans SQLite.

Les éléments suivants sont opérationnels et réutilisés :

- détection du workspace et de l'état Git (`core.context`) ;
- détection multi-langage, package manager et commandes (`core.languages`,
  `project_inspection`) ;
- repo map bornée et recherche ripgrep (`repo_map`, `tools.search`) ;
- confinement des chemins au workspace (`tools.filesystem.resolve_in_workspace`) ;
- lecture, écriture, suppression, shell, processus, Git status/diff et tests réels ;
- classification SAFE/CONFIRM/DESTRUCTIVE/FORBIDDEN des commandes ;
- abstraction `ModelProvider` et implémentation Ollama ;
- sessions, messages et notes persistés en SQLite ;
- progression WebSocket et pont de confirmation côté serveur ;
- tests unitaires couvrant outils, sécurité, serveur et boucle historique.

## Problèmes constatés

1. Le flux `build` est un pipeline de rôles, pas une machine d'état autonome : il ne
   peut pas reprendre précisément après redémarrage ou approbation.
2. Les plans sont du texte libre. Fichiers, commandes, risques et critères de succès
   ne sont pas adressables par le runtime.
3. `ToolResult` ne transportait que `success` et `output`; exit code, stdout/stderr,
   statut et corrélation manquaient.
4. Les confirmations étaient liées aux interfaces Rich/WebSocket et non à une
   politique testable indépendante.
5. La vérification finale exécutait surtout les tests. Elle ne modélisait pas
   explicitement `lint -> typecheck -> tests -> build`.
6. La mémoire SQLite conserve la conversation, mais pas l'état complet d'une tâche,
   ses itérations, erreurs, commandes et validations.
7. L'écriture réécrit un fichier complet. Aucun patch ciblé conflict-aware avec
   rollback n'était disponible.
8. Les assemblages d'outils CLI/serveur sont dupliqués. Cela reste acceptable pour la
   V0.1, mais devra converger autour du nouvel executor à la Phase 4.
9. Les capacités déclarées des modèles, les runners distants et le handoff versionné
   ne sont pas encore modélisés.
10. Le README décrit un état ancien (« no filesystem, git, or shell tools yet »).

## Architecture cible

```text
CLI / API / Web control plane
           |
           v
    Task service + event stream
           |
           v
  AgentLoop (state machine) <------ TaskStore + WorkingMemory
     |        |       |
     |        |       +----------> VerificationLoop
     |        +------------------> Planner
     v
  ToolExecutor
     |---- RiskEngine ---- ApprovalEngine
     |---- capability scopes ---- secure secret resolver (future)
     v
  Existing tools + PatchEngine + Git workflow
     |
     +---- LocalExecutor / Local Agent
     +---- RemoteRunner interface -> Docker/VPS runner (future)

ModelProvider -> Ollama today; capability-aware providers later
Repository Intelligence -> repo map/search today; optional embeddings index later
```

### Phase 3 implémentée

Le package `skytrap.autonomy` introduit les contrats isolés suivants :

- `TaskState` : statuts explicites, run/task IDs, itérations, erreur, approbation en
  attente et transitions terminales protégées ;
- `Planner` : `TaskPlan`/`PlanStep` structurés, commandes détectées, parsing robuste et
  fallback déterministe ;
- `AgentLoop` : planification, action, observation, vérification, révision du plan,
  retry, limite d'itérations, arrêt propre et reprise ;
- `ToolExecutor` : frontière de politique, capability check, approbation et résultat
  enrichi avec IDs et durée ;
- `PatchEngine` : remplacement ciblé unique, hash optimiste, backup et rollback ;
- `VerificationLoop` : découverte et exécution fail-fast de lint, typecheck, tests,
  build ;
- `WorkingMemory`/`TaskStore` : état JSON atomique versionné et contexte compact ;
- `RiskEngine`/`ApprovalEngine` : LOW/MEDIUM/HIGH/CRITICAL, capacités et décisions
  approved/denied/pending.

Les fichiers d'état JSON sont volontairement simples pour la V1 locale : ils sont
inspectables, mockables et transférables. Une implémentation SQL pourra respecter le
même contrat quand le control plane aura besoin de transactions concurrentes.

## Invariants de sécurité

- tout chemin fourni par un outil reste résolu sous la racine autorisée ;
- une capability manquante bloque l'action avant l'appel de l'outil ;
- HIGH/CRITICAL reste en attente sans callback d'approbation explicite ;
- une cible sensible requiert `secrets:use` et une approbation ;
- le runtime ne transmet ni ne journalise de secret ;
- une réponse finale du modèle ne suffit jamais : au moins une commande de
  vérification doit réussir ;
- aucune opération de push, déploiement ou migration n'est auto-approuvée ;
- un état terminal ne peut pas être réouvert silencieusement.

## Roadmap incrémentale

### Phase 4 — connexion aux outils existants

- fournir des outils `patch_file`, lint, typecheck et build de premier rang ;
- centraliser les toolsets CLI/serveur derrière `ToolExecutor` ;
- adapter chaque outil historique pour remplir stdout/stderr/exit_code nativement ;
- ajouter un audit log durable avec arguments redacted.

### Phase 5 — CLI et serveur autonomes

- exposer `skytrap agent run PATH GOAL`, `resume`, `status`, `stop` ;
- streamer événements, plans, appels outils et résultats de vérification ;
- ajouter branche `skytrap/task-<id>`, checkpoint, rollback et commit optionnel ;
- remplacer le pipeline serveur historique par le lifecycle commun.

### Phase 6 — local agent et remote runner

- registre de machines avec heartbeat et projets explicitement autorisés ;
- interface `Runner`, implémentations `LocalRunner` et mock isolé ;
- workspace distant éphémère, clone/checkout/install/run/checkpoint/cleanup ;
- handoff basé sur commit, branche dédiée et bundle de tâche versionné ;
- modes LOCAL/HYBRID/CLOUD sans donner le filesystem complet au control plane.

### Phase 7 — produit et durcissement

- dashboard tâches/machines/logs/diffs/tests/approbations/historique ;
- Supabase Auth et notification Resend derrière des abstractions sans secrets codés ;
- tests E2E d'une tâche réelle, interruption/reprise et handoff ;
- providers capability-aware, métriques, tracing et rétention ;
- index repository optionnel (embeddings/RAG), jamais requis pour fonctionner.

## Risques

- **Prompt injection dans un dépôt** : traiter le contenu comme données et conserver
  les décisions d'autorisation côté executor.
- **Commandes arbitraires** : parsing sans shell, allowlist/capabilities, isolation du
  runner et approbation graduée.
- **État concurrent ou périmé** : version de schéma, écriture atomique puis stockage
  transactionnel et contrôle de version optimiste.
- **Patch incorrect** : correspondance unique, hash avant écriture, tests et rollback.
- **Fausse réussite** : succès uniquement après vérification indépendante non vide.
- **Explosion de contexte** : repo map bornée, recherche ciblée et mémoire compacte.
- **Secrets dans logs/prompts** : redaction centralisée à ajouter avant l'audit log et
  résolution des secrets exclusivement dans l'executor sécurisé.
- **Divergence CLI/serveur** : Phase 4 doit supprimer l'assemblage dupliqué après
  stabilisation des nouveaux contrats.
- **Tests exécutant du code non fiable** : le local assume le trust du projet autorisé;
  le cloud devra toujours utiliser un workspace isolé avec limites CPU/mémoire/réseau.
