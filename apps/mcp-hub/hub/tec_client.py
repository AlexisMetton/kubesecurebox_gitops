# Proxy HTTP vers l'API The European Citizen (votes PE, MEPs, palmarès)
from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlencode

import requests

TEC_API_URL = os.environ.get(
    "TEC_API_URL", "https://api.theeuropeancitizen.eu"
).rstrip("/")
TEC_SITE_URL = os.environ.get(
    "TEC_SITE_URL", "https://theeuropeancitizen.eu"
).rstrip("/")
TEC_API_TOKEN = os.environ.get("TEC_API_TOKEN", "").strip()

_TIMEOUT = 60

# Heuristique sujets sensibles (pas d'humour osé côté rédaction)
_SENSITIVE_RE = re.compile(
    r"(abus\s+sexuel|p[ée]dophil|enfant[s]?\s+en\s+ligne|"
    r"crime[s]?\s+de\s+guerre|g[ée]nocide|pers[ée]cution|"
    r"viol\b|mariage\s+forc[ée]|enl[èe]vement|"
    r"holocaust|attentat|massacre)",
    re.IGNORECASE,
)


def _headers() -> dict[str, str]:
    h = {"Accept": "application/json", "Accept-Language": "fr"}
    if TEC_API_TOKEN:
        h["Authorization"] = f"Bearer {TEC_API_TOKEN}"
    return h


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    qs = ""
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        if clean:
            qs = "?" + urlencode(clean)
    r = requests.get(
        f"{TEC_API_URL}{path}{qs}",
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def _utm(url: str, campaign: str = "mcp_tec") -> str:
    sep = "&" if "?" in url else "?"
    return (
        f"{url}{sep}utm_source=x&utm_medium=social&utm_campaign={campaign}"
    )


def site_vote_url(adopted_text_id: int | str) -> str:
    return _utm(f"{TEC_SITE_URL}/fr/textes-adoptes/{adopted_text_id}/")


def site_mep_url(identifier: str) -> str:
    return _utm(f"{TEC_SITE_URL}/fr/deputes/{identifier}/")


def site_leaderboard_url() -> str:
    return _utm(f"{TEC_SITE_URL}/fr/palmares/")


def site_home_url() -> str:
    return _utm(f"{TEC_SITE_URL}/fr/")


def _is_sensitive(*texts: str | None) -> bool:
    blob = " ".join(t for t in texts if t)
    return bool(_SENSITIVE_RE.search(blob))


def _normalize_vote_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalise un item liste (last-adopted / adopted-texts)."""
    vote_id = row.get("id")
    adopted_text_id = row.get("adopted_text_id") or vote_id
    title = (
        row.get("explanationTitle")
        or row.get("title")
        or row.get("label_adopted_text")
        or ""
    )
    favor = row.get("numberVotesFavor")
    if favor is None:
        favor = row.get("number_votes_favor")
    against = row.get("numberVotesAgainst")
    if against is None:
        against = row.get("number_votes_against")
    abst = row.get("numberVotesAbstention")
    if abst is None:
        abst = row.get("number_votes_abstention")
    return {
        "vote_id": vote_id,
        "adopted_text_id": adopted_text_id,
        "label": row.get("label_adopted_text") or "",
        "title_citoyen": title,
        "activity_date": row.get("activity_date"),
        "vote_result": row.get("voteResult"),
        "votes_pour": favor,
        "votes_contre": against,
        "votes_abstention": abst,
        "decision_method": row.get("decision_method") or row.get("activity_type"),
        "site_url": site_vote_url(adopted_text_id) if adopted_text_id else None,
        "sensitive_topic": _is_sensitive(title, row.get("label_adopted_text")),
    }


def recent_votes(limit: int = 10) -> dict[str, Any]:
    limit = max(1, min(int(limit), 30))
    raw = _get("/api/last-adopted-texts")
    items = raw.get("data") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        items = []
    out = [_normalize_vote_row(x) for x in items[:limit] if isinstance(x, dict)]
    return {
        "items": out,
        "count": len(out),
        "note": (
            "Titres citoyens + totaux. "
            "URL site = adopted_text_id. "
            "Détail nominatif FR parfois incomplet côté API."
        ),
        "home_url": site_home_url(),
    }


def vote_detail(
    adopted_text_id: int,
    *,
    country: str | None = "FRA",
    votes_limit: int = 80,
) -> dict[str, Any]:
    adopted_text_id = int(adopted_text_id)
    votes_limit = max(1, min(int(votes_limit), 200))
    detail = _get(f"/api/adopted-text/{adopted_text_id}")
    data = detail.get("data") if isinstance(detail, dict) else detail
    if not isinstance(data, dict):
        return {"error": "réponse détail invalide", "adopted_text_id": adopted_text_id}

    meeting = data.get("meeting_result") or {}
    explanations = data.get("explanations") or data.get("text_adopted_explanations") or {}
    fr_expl = explanations.get("fr") if isinstance(explanations, dict) else None
    title = ""
    summary_excerpt = ""
    if isinstance(fr_expl, dict):
        title = (fr_expl.get("title") or "").strip()
        content = fr_expl.get("content") or ""
        # strip tags lightly
        summary_excerpt = re.sub(r"<[^>]+>", " ", str(content))
        summary_excerpt = re.sub(r"\s+", " ", summary_excerpt).strip()[:800]
    if not title:
        title = (
            data.get("explanationTitle")
            or data.get("title")
            or data.get("label_adopted_text")
            or ""
        )

    pour = meeting.get("voteFavor") or data.get("numberVotesFavor")
    contre = meeting.get("voteAgainst") or data.get("numberVotesAgainst")
    abst = meeting.get("voteAbstention") or data.get("numberVotesAbstention")

    votes_payload = _get(
        f"/api/adopted-text/{adopted_text_id}/votes",
        {
            "country": country or "",
            "perPage": votes_limit,
        },
    )
    vote_rows = votes_payload.get("data") if isinstance(votes_payload, dict) else []
    if not isinstance(vote_rows, list):
        vote_rows = []

    nominative = []
    non_vote_count = 0
    for v in vote_rows:
        if not isinstance(v, dict):
            continue
        label = v.get("vote_result_fr") or v.get("vote_result") or ""
        if label in ("Non voté", "Non vote", None, ""):
            non_vote_count += 1
        nominative.append(
            {
                "full_name": v.get("full_name"),
                "country": v.get("country"),
                "political_group": v.get("political_group"),
                "vote": label,
                "presence_status": v.get("presence_status"),
            }
        )

    # Si tout est « Non voté », signaler clairement (bug / gap enrichissement)
    usable = [n for n in nominative if n.get("vote") not in ("Non voté", "Non vote", "")]
    nominative_note = None
    if nominative and not usable:
        nominative_note = (
            "Détail nominatif indisponible (tous « Non voté»). "
            "Utiliser les totaux + CTA site ; ne pas inventer de votes individuels."
        )
        nominative = []

    sensitive = _is_sensitive(title, summary_excerpt)

    return {
        "adopted_text_id": adopted_text_id,
        "title_citoyen": title,
        "summary_excerpt": summary_excerpt or None,
        "activity_date": meeting.get("activity_date") or data.get("activity_date"),
        "vote_result": meeting.get("resultVote") or data.get("voteResult"),
        "votes_pour": pour,
        "votes_contre": contre,
        "votes_abstention": abst,
        "country_filter": country,
        "nominative_votes": nominative[:votes_limit],
        "nominative_count": len(nominative),
        "nominative_note": nominative_note,
        "non_vote_rows_seen": non_vote_count if nominative_note else None,
        "sensitive_topic": sensitive,
        "site_url": site_vote_url(adopted_text_id),
        "post_hints": {
            "anchor": "Parlement européen / vote / eurodéputés",
            "tone": "sober_sensitive" if sensitive else "sarcastic_ok",
            "show_group_when_critiquing_non_right": True,
        },
    }


def mep_profile(identifier: str, *, recent_votes_limit: int = 5) -> dict[str, Any]:
    identifier = str(identifier).strip()
    if not identifier:
        return {"error": "identifier requis"}
    recent_votes_limit = max(1, min(int(recent_votes_limit), 20))
    raw = _get(f"/api/meps/{identifier}")
    data = raw.get("data") if isinstance(raw, dict) else raw
    if not isinstance(data, dict):
        return {"error": "MEP introuvable", "identifier": identifier}

    votes_raw = _get(
        f"/api/meps/{identifier}/adopted-texts",
        {"perPage": recent_votes_limit},
    )
    votes = votes_raw.get("data") if isinstance(votes_raw, dict) else votes_raw
    if not isinstance(votes, list):
        votes = []

    return {
        "identifier": identifier,
        "full_name": (
            data.get("full_name")
            or f"{data.get('given_name', '')} {data.get('family_name', '')}".strip()
        ),
        "country": data.get("country"),
        "political_group": data.get("political_group") or data.get("group"),
        "national_party": data.get("national_party"),
        "recent_votes": votes[:recent_votes_limit],
        "site_url": site_mep_url(identifier),
        "leaderboard_url": site_leaderboard_url(),
    }


def leaderboard(country: str = "FRA") -> dict[str, Any]:
    country = (country or "FRA").strip().upper()
    raw = _get("/api/meps/leaderboard", {"country": country})
    data = raw.get("data") if isinstance(raw, dict) else raw
    if not isinstance(data, dict):
        return {"error": "leaderboard invalide", "country": country}

    def _top(key: str, n: int = 5) -> list[dict[str, Any]]:
        rows = data.get(key) or []
        out = []
        for m in rows[:n]:
            if not isinstance(m, dict):
                continue
            out.append(
                {
                    "identifier": m.get("identifier"),
                    "full_name": (
                        f"{m.get('given_name', '')} {m.get('family_name', '')}".strip()
                    ),
                    "political_group": m.get("political_group"),
                    "country": m.get("country"),
                    "value": m.get("value"),
                    "vote_for_count": m.get("vote_for_count"),
                    "vote_against_count": m.get("vote_against_count"),
                    "vote_abstention_count": m.get("vote_abstention_count"),
                    "non_voting_count": m.get("non_voting_count"),
                    "site_url": (
                        site_mep_url(str(m["identifier"]))
                        if m.get("identifier")
                        else None
                    ),
                }
            )
        return out

    return {
        "country": country,
        "votes_for": _top("votes_for"),
        "votes_against": _top("votes_against"),
        "votes_abstention": _top("votes_abstention"),
        "votes_non_voting": _top("votes_non_voting"),
        "attendance_signed": _top("attendance_signed"),
        "attendance_not_signed": _top("attendance_not_signed"),
        "attendance_excused": _top("attendance_excused"),
        "site_url": site_leaderboard_url(),
        "note": "Stats cumulées index TEC — pas un jugement moral.",
    }


def post_context(
    *,
    adopted_text_id: int | None = None,
    mode: str = "vote",
    country: str = "FRA",
) -> dict[str, Any]:
    """
    Paquet factuel pour rédiger un post X (le LLM applique la persona du skill).
    mode: vote | leaderboard | evergreen_mep
    """
    mode = (mode or "vote").strip().lower()
    country = (country or "FRA").strip().upper()

    base = {
        "mode": mode,
        "persona_skill": "european-citizen",
        "do_not_auto_post": True,
        "variants_requested": ["safe", "edgy", "sober_if_sensitive"],
        "rules_short": [
            "Ancrer Parlement européen / vote / eurodéputés en 1–2 lignes",
            "Vouvoiement ; sarcasme OK hors sujets sensibles",
            "Pas de vibe complot médias ; pas d’annoncer « sarcasme »",
            "Groupe politique surtout si critique hors droite",
            "Ne jamais inventer un vote nominatif absent des données",
            "Lien site UTM en fin de post",
        ],
    }

    if mode == "leaderboard":
        lb = leaderboard(country=country)
        base["data"] = lb
        base["sensitive_topic"] = False
        base["suggested_angles"] = [
            "non_voting",
            "votes_against",
            "attendance_not_signed",
        ]
        return base

    if adopted_text_id is None:
        recent = recent_votes(limit=1)
        items = recent.get("items") or []
        if not items:
            return {**base, "error": "aucun vote récent"}
        adopted_text_id = items[0]["adopted_text_id"]

    detail = vote_detail(int(adopted_text_id), country=country)
    base["data"] = detail
    base["sensitive_topic"] = bool(detail.get("sensitive_topic"))
    base["suggested_angles"] = (
        ["totals_only", "cta_check_names"]
        if detail.get("nominative_note")
        else ["totals", "fr_nominative_contrast", "cta"]
    )
    return base
