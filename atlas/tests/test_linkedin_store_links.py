from atlas.modules.linkedin.store import has_banned_link


def test_only_the_exact_public_host_is_allowed():
    assert not has_banned_link("Read more: https://agency.storefront.test/academy/")
    assert not has_banned_link("https://AGENCY.STOREFRONT.TEST/x")
    for bad in ("https://evil.agency.storefront.test/", "http://lab.storefront.test", "storefront.test",
                "https://agency.storefront.test.evil.io/", "https://x-agency.storefront.test/",
                "https://PulseDemo.example", "https://my.guarder.example.com/a"):
        assert has_banned_link(bad), bad
