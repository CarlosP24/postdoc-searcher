from datetime import date

from postdoc_searcher.config import PI, Fellowship, HostRule, Me, MobilityRule, Residence, Topic, load_config
from postdoc_searcher.fellowships import build, check_candidate, estimate_window, host_allows
from postdoc_searcher.llm import tool_schema
from postdoc_searcher.models import Opportunity, RawItem, SearchReport
from postdoc_searcher.notify import pending_events
from postdoc_searcher.prefilter import Prefilter, mentioned_pis, same_institution, same_person
from postdoc_searcher.sources.feeds import _madgex_fields
from postdoc_searcher.sources.jobs_ac_uk import parse_results
from postdoc_searcher.store import Store, compute_status
from postdoc_searcher.util import canonical_url, parse_date

GROUPS = {"EU": ["Spain", "Denmark", "Germany"], "HE_ASSOCIATED": ["United Kingdom", "Switzerland"]}


def opp(**kw):
    base = dict(id="x", kind="position", title="Postdoc", url="https://a.org/x",
                first_seen="2026-11-01", last_seen="2026-11-01")
    base.update(kw)
    return Opportunity(**base)


# ── util ──

def test_canonical_url_strips_tracking():
    a = canonical_url("https://www.nature.com/job/1/x/?TrackID=12&utm_source=rss&b=2")
    assert a == "https://nature.com/job/1/x?b=2"


def test_parse_date_month_precision():
    assert parse_date("2027-09", end_of_month=True) == date(2027, 9, 30)
    assert parse_date("2027-09") == date(2027, 9, 1)
    assert parse_date("15 Sep 2027") is None


# ── prefilter ──

def make_pf(broad=False):
    topics = [Topic(name="Majorana", keywords=["majorana", "topological superconduct"])]
    pis = [PI(name="Ramón Aguado", institution="ICMM-CSIC", country="Spain")]
    return Prefilter(topics, pis, [r"professor|tenure", r"\bph\.?d\.?\s*(student|position)"],
                     field_keywords=["physics"], broad=broad)


def test_prefilter_topic_and_exclusion():
    pf = make_pf()
    assert pf.score(RawItem("s", "Postdoc on Majorana nanowires", "u"))
    assert pf.score(RawItem("s", "PhD position on Majorana", "u")) is None
    assert pf.score(RawItem("s", "Assistant Professor in topological superconductivity", "u")) is None
    # a title that also says postdoc is never excluded
    assert pf.score(RawItem("s", "Professors and Postdoctoral Researchers in Majorana physics", "u"))


def test_prefilter_broad_mode_only_with_ai():
    item = RawItem("s", "Postdoctoral Research Associate, Department of Physics", "u")
    assert make_pf(broad=False).score(item) is None
    assert make_pf(broad=True).score(item) is not None


def test_pi_mentions_are_accent_insensitive():
    pis = [PI(name="Ramón Aguado")]
    assert mentioned_pis(pis, "Join the group of Prof. Ramon Aguado in Madrid") == ["Ramón Aguado"]
    assert mentioned_pis(pis, "Aguado street 5") == []
    assert same_person("K. Flensberg", "Karsten Flensberg")
    assert same_institution("Niels Bohr Institute, University of Copenhagen", "University of Copenhagen")


# ── fellowships ──

MSCA = Fellowship(id="msca", name="MSCA", url="https://x", host=HostRule(groups=["EU", "HE_ASSOCIATED"]),
                  mobility=MobilityRule(countries="host", max_months=12, window_months=36),
                  typical_open_month=4, typical_deadline_month=9)


def me(**kw):
    base = dict(name="C", phd_defense_date="2027-03", nationality=["Spanish"],
                residence_history=[Residence(country="Spain", start="2019-09", end="present")])
    base.update(kw)
    return Me(**base)


def test_msca_mobility_rule():
    ref = date(2027, 9, 1)
    ok, _ = check_candidate(MSCA, me(), PI(name="A", country="Denmark"), ref)
    assert ok is True
    ok, why = check_candidate(MSCA, me(), PI(name="B", country="Spain"), ref)
    assert ok is False and "mobility" in why[0]


def test_host_rules_and_global_fellowship():
    gf = Fellowship(id="gf", name="GF", url="https://x", host=HostRule(any=True, exclude_groups=["EU", "HE_ASSOCIATED"]))
    assert host_allows(gf, PI(name="X", country="USA"), GROUPS)
    assert not host_allows(gf, PI(name="Y", country="Germany"), GROUPS)
    assert host_allows(MSCA, PI(name="Z", country="UK"), GROUPS)


def test_nationality_and_phd_institution_rules():
    hum = Fellowship(id="h", name="H", url="https://x", host=HostRule(countries=["Germany"]), nationality_excluded=["German"])
    assert check_candidate(hum, me(nationality=["German"]), PI(name="P", country="Germany"), date(2027, 5, 1))[0] is False
    jdc = Fellowship(id="j", name="J", url="https://x", host=HostRule(countries=["Spain"]), exclude_phd_institution=True)
    m = me(phd_institution="ICMM-CSIC, Madrid")
    assert check_candidate(jdc, m, PI(name="P", country="Spain", institution="ICMM-CSIC"), date(2027, 5, 1))[0] is False
    assert check_candidate(jdc, m, PI(name="Q", country="Spain", institution="ICFO Barcelona"), date(2027, 5, 1))[0] is True


def test_estimate_window_crosses_year():
    f = Fellowship(id="l", name="L", url="https://x", typical_open_month=11, typical_deadline_month=2)
    assert estimate_window(f, date(2027, 1, 10)) == (date(2026, 11, 1), date(2027, 2, 28))
    assert estimate_window(MSCA, date(2026, 11, 4)) == (date(2027, 4, 1), date(2027, 9, 30))


def test_repo_config_loads_and_builds():
    cfg = load_config()
    assert cfg.pis and cfg.topics and cfg.catalog.fellowships
    out = build(cfg, {}, date(2026, 11, 4), {})
    assert len(out) == len(cfg.catalog.fellowships)
    assert all(o.kind == "fellowship" and o.url.startswith("http") for o in out)


# ── store / status / notify ──

def test_status():
    ref = date(2026, 11, 4)
    assert compute_status(opp(deadline="2026-11-01"), ref) == "closed"
    assert compute_status(opp(deadline="2026-11"), ref) == "open"
    assert compute_status(opp(call_opens="2027-04-01", deadline="2027-09-10"), ref) == "upcoming"
    assert compute_status(opp(rolling=True), ref) == "rolling"
    assert compute_status(opp(), ref) == "unknown"


def test_store_merges_duplicates(tmp_path):
    s = Store(tmp_path / "o.json")
    s.upsert(opp(id="a", title="Postdoc in Majorana theory", institution="NBI", url="https://a.org/1",
                 relevance=60, sources=["A"]))
    merged, new = s.upsert(opp(id="b", title="Postdoc in Majorana Theory", institution="NBI", url="https://b.org/2",
                               relevance=80, deadline="2027-01-15", sources=["B"]))
    assert not new and merged.relevance == 80 and merged.deadline == "2027-01-15"
    assert merged.sources == ["A", "B"] and len(s.all()) == 1
    s.save()
    assert len(Store(tmp_path / "o.json").all()) == 1


def test_pending_events_transitions():
    a = opp(id="a", relevance=80, status="open")
    b = opp(id="b", relevance=80, status="open", notified_status="upcoming")
    c = opp(id="c", relevance=30, status="open")
    d = opp(id="d", kind="fellowship", relevance=80, status="open", notified_status="unknown", dates_estimated=True)
    kinds = {o.id: k for k, o in pending_events([a, b, c, d], 65)}
    assert kinds == {"a": "new", "b": "opened"}


# ── schemas / parsers ──

def test_tool_schema_inlines_refs_and_keeps_title_field():
    schema = tool_schema(SearchReport)
    assert "$ref" not in str(schema) and "$defs" not in schema
    item = schema["properties"]["opportunities"]["items"]
    assert "title" in item["properties"] and item["additionalProperties"] is False


def test_madgex_title_split():
    title, inst, salary, loc = _madgex_fields("MPI-PKS: Distinguished Postdoctoral Fellow",
                                              "Competitive:\n\nMPI-PKS:\nThe institute seeks...\nDresden, Sachsen")
    assert (title, inst, salary, loc) == ("Distinguished Postdoctoral Fellow", "MPI-PKS", "Competitive", "Dresden, Sachsen")


def test_jobs_ac_uk_parser():
    html = """<div class="j-search-result__result"><div class="j-search-result__text">
      <a href="/job/ABC/postdoc">Postdoctoral Researcher in Quantum Transport</a>
      <div class="j-search-result__employer"><b>University of Oxford</b></div>
      <div>Location: Oxford</div><div class="j-search-result__info"><strong>Salary:</strong> £40,000</div>
      </div><div class="j-search-result__date-logos"><span class="j-search-result__date--blue">14 Dec</span></div></div>"""
    [it] = parse_results(html)
    assert it.url == "https://www.jobs.ac.uk/job/ABC/postdoc"
    assert it.institution == "University of Oxford" and it.location == "Oxford"
    assert it.deadline and it.deadline.endswith("-12-14")
