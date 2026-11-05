"""Countries, regions and the destination-location rule from inputs/me.yaml."""

from __future__ import annotations

import re

from .util import norm, strip_accents

# Common spellings → canonical (lower-case) country name
ALIASES = {
    "uk": "united kingdom", "u.k.": "united kingdom", "great britain": "united kingdom", "britain": "united kingdom",
    "england": "united kingdom", "scotland": "united kingdom", "wales": "united kingdom",
    "northern ireland": "united kingdom",
    "usa": "united states", "us": "united states", "u.s.": "united states", "u.s.a.": "united states",
    "united states of america": "united states", "america": "united states",
    "czech republic": "czechia", "the netherlands": "netherlands", "holland": "netherlands",
    "turkey": "turkiye", "republic of korea": "south korea", "korea": "south korea",
    "espana": "spain", "deutschland": "germany", "danmark": "denmark", "schweiz": "switzerland",
    "suisse": "switzerland", "italia": "italy", "osterreich": "austria", "uae": "united arab emirates",
    "prc": "china", "p.r. china": "china",
}

EUROPE = {
    "albania", "andorra", "armenia", "austria", "belarus", "belgium", "bosnia and herzegovina", "bulgaria",
    "croatia", "cyprus", "czechia", "denmark", "estonia", "faroe islands", "finland", "france", "georgia",
    "germany", "greece", "hungary", "iceland", "ireland", "italy", "kosovo", "latvia", "liechtenstein",
    "lithuania", "luxembourg", "malta", "moldova", "monaco", "montenegro", "netherlands", "north macedonia",
    "norway", "poland", "portugal", "romania", "russia", "san marino", "serbia", "slovakia", "slovenia",
    "spain", "sweden", "switzerland", "turkiye", "ukraine", "united kingdom", "vatican",
}
NORTH_AMERICA = {"united states", "canada"}
REGIONS = {"europe": EUROPE, "north america": NORTH_AMERICA}

# Non-European countries/places that show up in academic job ads, so that
# items without an explicit country can still be placed.
_OTHER = {
    "china", "japan", "south korea", "taiwan", "hong kong", "singapore", "india", "australia", "new zealand",
    "israel", "united arab emirates", "saudi arabia", "qatar", "brazil", "chile", "argentina", "mexico",
    "south africa", "egypt", "morocco", "tunisia", "iran", "pakistan", "vietnam", "thailand", "malaysia",
    "indonesia", "uzbekistan", "kazakhstan",
}
_CITY_HINTS = {
    "beijing": "china", "shanghai": "china", "shenzhen": "china", "hangzhou": "china", "nanjing": "china",
    "hefei": "china", "wuhan": "china", "guangzhou": "china", "xi'an": "china", "suzhou": "china",
    "tokyo": "japan", "kyoto": "japan", "osaka": "japan", "sendai": "japan", "seoul": "south korea",
    "gwangju": "south korea", "daejeon": "south korea", "taipei": "taiwan", "sydney": "australia",
    "melbourne": "australia", "brisbane": "australia", "bangalore": "india", "bengaluru": "india",
    "mumbai": "india", "abu dhabi": "united arab emirates", "dubai": "united arab emirates",
    "tel aviv": "israel", "rehovot": "israel", "haifa": "israel", "jerusalem": "israel",
    "toronto": "canada", "montreal": "canada", "vancouver": "canada", "ottawa": "canada", "waterloo, on": "canada",
    "sherbrooke": "canada", "new york": "united states", "boston": "united states", "chicago": "united states",
    "berkeley": "united states", "stanford": "united states", "princeton": "united states",
    "evanston": "united states", "pasadena": "united states", "argonne": "united states",
    "los alamos": "united states", "santa barbara": "united states", "cambridge, ma": "united states",
    "copenhagen": "denmark", "kobenhavn": "denmark", "aarhus": "denmark", "delft": "netherlands",
    "amsterdam": "netherlands", "leiden": "netherlands", "utrecht": "netherlands", "eindhoven": "netherlands",
    "berlin": "germany", "munich": "germany", "munchen": "germany", "dresden": "germany", "aachen": "germany",
    "karlsruhe": "germany", "stuttgart": "germany", "hamburg": "germany", "regensburg": "germany",
    "paris": "france", "grenoble": "france", "orsay": "france", "lyon": "france", "madrid": "spain",
    "barcelona": "spain", "san sebastian": "spain", "donostia": "spain", "zurich": "switzerland",
    "geneva": "switzerland", "basel": "switzerland", "lausanne": "switzerland", "vienna": "austria",
    "wien": "austria", "innsbruck": "austria", "stockholm": "sweden", "lund": "sweden", "uppsala": "sweden",
    "gothenburg": "sweden", "goteborg": "sweden", "helsinki": "finland", "espoo": "finland",
    "aalto": "finland", "oslo": "norway", "trondheim": "norway", "rome": "italy", "roma": "italy",
    "milan": "italy", "pisa": "italy", "trieste": "italy", "lisbon": "portugal", "porto": "portugal",
    "warsaw": "poland", "krakow": "poland", "prague": "czechia", "dublin": "ireland", "oxford": "united kingdom",
    "cambridge, uk": "united kingdom", "london": "united kingdom", "manchester": "united kingdom",
    "edinburgh": "united kingdom", "bristol": "united kingdom", "leuven": "belgium", "brussels": "belgium",
    "ghent": "belgium", "luxembourg": "luxembourg",
}
_US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts",
    "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "wisconsin", "wyoming", "district of columbia",
}
_CA_PROVINCES = {"ontario", "quebec", "british columbia", "alberta", "manitoba", "nova scotia", "saskatchewan"}
_REGION_WORDS = {  # INSPIRE uses region names
    "asia": "china", "australasia": "australia", "middle east": "israel", "africa": "south africa",
    "south america": "brazil", "latin america": "brazil",
}

_ALL_COUNTRIES = EUROPE | NORTH_AMERICA | _OTHER
_SPECIAL = {
    "new mexico": "united states", "georgia tech": "united states", "georgia institute of technology": "united states",
    "atlanta": "united states", "university of georgia": "united states", "new south wales": "australia",
    "south america": "brazil", "latin america": "brazil",
}


def country_key(name: str | None) -> str:
    n = norm(strip_accents(name or "")).strip(" .")
    return ALIASES.get(n, n)


def _word_in(word: str, text: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text) is not None


def infer_country(*texts: str | None) -> str | None:
    """Best-effort country from free text (location, institution, title)."""
    t = norm(" | ".join(x for x in texts if x))
    if not t:
        return None
    for special, c in _SPECIAL.items():  # names that would otherwise match the wrong country
        if _word_in(special, t):
            return c
    for alias, canon in ALIASES.items():
        if len(alias) > 3 and alias != "america" and _word_in(alias, t):
            return canon
    for c in sorted(_ALL_COUNTRIES, key=len, reverse=True):
        if _word_in(c, t):
            return c
    if re.search(r"(?<![a-z])(usa|u\.s\.a?\.?)(?![a-z])", t):
        return "united states"
    for city, c in _CITY_HINTS.items():
        if _word_in(city, t):
            return c
    for s in _US_STATES:
        if _word_in(s, t):
            return "united states"
    for p in _CA_PROVINCES:
        if _word_in(p, t):
            return "canada"
    if _word_in("north america", t):
        return "united states"  # US or Canada: both are treated alike by the rule
    if _word_in("europe", t):
        return "europe"
    for w, c in _REGION_WORDS.items():
        if _word_in(w, t):
            return c
    return None


class LocationRule:
    """Where you are willing to go.

    allowed     regions ("Europe", "North America") and/or countries always accepted
    exceptional countries accepted only for outstanding matches (relevance ≥ exceptional_min_relevance)
    excluded    countries never accepted
    Offers whose country cannot be determined are kept.
    """

    def __init__(self, allowed: list[str], exceptional: list[str], excluded: list[str], exceptional_min: int):
        self.allowed: set[str] = set()
        for a in allowed:
            k = country_key(a)
            self.allowed |= REGIONS.get(k, {k})
        self.exceptional: set[str] = set()
        for e in exceptional:
            k = country_key(e)
            self.exceptional |= REGIONS.get(k, {k})
        self.exceptional -= self.allowed
        self.excluded = {country_key(c) for c in excluded}
        self.exceptional_min = exceptional_min
        self.active = bool(self.allowed)

    def status(self, country: str | None) -> str:
        """'allowed' | 'exceptional' | 'excluded' | 'unknown'."""
        if not self.active:
            return "allowed"
        c = country_key(country)
        if not c:
            return "unknown"
        if c in self.excluded:
            return "excluded"
        if c == "europe":
            return "allowed" if EUROPE & self.allowed else "excluded"
        if c in self.allowed:
            return "allowed"
        if c in self.exceptional:
            return "exceptional"
        return "excluded"

    def accepts(self, country: str | None, relevance: int) -> bool:
        s = self.status(country)
        return s in ("allowed", "unknown") or (s == "exceptional" and relevance >= self.exceptional_min)

    def describe(self) -> str:
        if not self.active:
            return "anywhere"
        parts = ["Europe" if EUROPE <= self.allowed else ""]
        extra = sorted(self.allowed - EUROPE)
        parts += [c.title() for c in extra]
        s = "only " + ", ".join(p for p in parts if p)
        if self.exceptional:
            s += (f"; exceptionally {', '.join(sorted(c.title() for c in self.exceptional))} "
                  f"(only outstanding matches, relevance ≥ {self.exceptional_min})")
        if self.excluded:
            s += f"; never {', '.join(sorted(c.title() for c in self.excluded))}"
        return s


def rule_from(me) -> LocationRule:
    p = me.preferences
    return LocationRule(p.regions_allowed, p.exceptional_countries, p.countries_excluded,
                        p.exceptional_min_relevance)
