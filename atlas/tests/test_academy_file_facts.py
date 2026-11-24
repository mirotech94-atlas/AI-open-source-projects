"""Each file on the paid access page shows its real size and date, read
from disk now (plan F19); a file not on disk shows neither."""
from __future__ import annotations

import pytest

from atlas.api import academy as api
from atlas.modules.academy import delivery, stripe_read
from tests import academy_shop_fakes as fakes
from tests.academy_shop_fakes import SID
from tests.test_academy_access import PRICE_KIT, _session

fake, served = fakes.fake, fakes.served              # the shared fixtures


def test_each_file_shows_its_real_size_and_date(served, tmp_path):
    root = tmp_path / "files" / "vault-kit"
    root.mkdir(parents=True)
    f = root / "claude-obsidian-vault-kit.zip"
    f.write_bytes(b"x" * 1536)
    import os
    os.utime(f, (1_760_000_000, 1_760_000_000))       # 9 Oct 2025
    r = served.client.get(delivery.ACCESS_PATH, params={"session_id": SID})
    assert r.status_code == 200
    assert "(1.5 KB, updated 09 Oct 2025)" in r.text


def test_a_missing_file_gets_no_size_or_date(served):
    served.verdicts[SID] = stripe_read.judge(_session(price=PRICE_KIT), served.cat)
    r = served.client.get(delivery.ACCESS_PATH, params={"session_id": SID})
    assert "Not on the server yet" in r.text and "updated " not in r.text


@pytest.mark.parametrize("n,text", [(0, "0 bytes"), (900, "900 bytes"),
                                    (1536, "1.5 KB"), (5 * 1024 * 1024, "5.0 MB"),
                                    (3 * 1024 ** 3, "3.0 GB")])
def test_human_size(n, text):
    assert api.human_size(n) == text

