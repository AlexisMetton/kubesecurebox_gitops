---
name: procurement-fr
description: Interroger les marchés publics FR indexés (BOAMP) via les tools procurement_*.
---

Pour toute question sur les marchés publics / attributaires / acheteurs :

1. Charger ce skill, puis utiliser uniquement les tools `procurement_*` (scope `public:procurement`).
2. Toujours citer `source_url` des avis renvoyés. Ne jamais inventer un montant, un SIREN ou un attributaire absent des résultats.
3. Rappeler les limites : couverture BOAMP incomplète (sous-seuils souvent absents), qualité geo/SIREN variable. **Ne pas** conclure à de la fraude ou du favoritisme.
4. Enchaînements utiles :
   - Vue zone : `procurement_top_winners(dept="58")` puis `procurement_search(dept="58", q=…)`.
   - Une entreprise : `procurement_by_winner(siren=…)` ou `name=…`.
   - Une collectivité : `procurement_by_buyer(name=…)` ou `dept=…`.
   - Détail : `procurement_get(notice_id=…)`.
5. Préférer des réponses courtes : tableau / liste + liens, puis 2–3 phrases de synthèse factuelle.
