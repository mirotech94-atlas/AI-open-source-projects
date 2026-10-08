import pathlib, re, sys
from guardian_hub import schemas_dash as sd

TYPES_TS = pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "types.ts"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from check_types import PY_ONLY_ALIASES, TS_ONLY_ALIASES  # allow-lists, see check_types.py

def test_every_payload_model_is_mirrored_in_types_ts():
    py = {n for n in dir(sd) if n[:1].isupper() and hasattr(getattr(sd, n), "model_fields")} - PY_ONLY_ALIASES
    ts = set(re.findall(r"^export type (\w+)", TYPES_TS.read_text(), re.M)) - TS_ONLY_ALIASES
    assert py - ts == set(), f"missing from web/src/types.ts: {sorted(py - ts)}"
    assert ts - py == set(), f"missing from schemas_dash.py: {sorted(ts - py)}"

def test_field_names_match_for_every_shared_model():
    src = TYPES_TS.read_text()
    for name in ("OverviewPayload", "DeviceCard", "InboxRow", "SecurityPayload", "DnsPayload",
                 "UpdatesPayload", "TopologyPayload", "ReportCard", "IntegrationCard", "ChatResponse",
                 "TrafficPeer", "TrafficTalker", "TrafficOverview", "DeviceTraffic"):
        block = re.search(rf"export type {name} = \{{(.*?)\n\}};", src, re.S).group(1)
        ts_fields = set(re.findall(r"^\s{2}(\w+)\??:", block, re.M))
        assert ts_fields == set(getattr(sd, name).model_fields), name

def test_presence_and_acttoken_tables_exist(client):
    from sqlalchemy import inspect
    from guardian_hub.db import engine
    names = set(inspect(engine).get_table_names())
    assert {"presence_sample", "ip_geo", "act_token"} <= names
