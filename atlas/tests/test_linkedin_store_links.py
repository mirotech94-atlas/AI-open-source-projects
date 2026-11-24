from atlas.modules.linkedin.store import has_banned_link


def test_only_the_exact_public_host_is_allowed():
    assert not has_banned_link("Read more: https://agency.example.com/academy/")
    assert not has_banned_link("https://AGENCY.18DN.COM/x")
    for bad in ("https://evil.agency.example.com/", "http://lab.example.com", "example.com",
                "https://agency.example.com.evil.io/", "https://x-agency.example.com/",
                "https://PulseDemo.example", "https://my.vault.example/a"):
        assert has_banned_link(bad), bad
