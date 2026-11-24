from atlas.modules.mail.accounts import bulk_headers


def _h(**kw):
    return [{"name": k.replace("_", "-"), "value": v} for k, v in kw.items()]


def test_list_unsubscribe_is_bulk():
    assert bulk_headers(_h(List_Unsubscribe="<mailto:x@y.z>"))


def test_precedence_bulk_and_auto_submitted():
    assert bulk_headers(_h(Precedence="bulk"))
    assert bulk_headers(_h(Auto_Submitted="auto-generated"))


def test_person_is_not_bulk():
    assert not bulk_headers(_h(From="a@b.c", Subject="hi"))
    assert not bulk_headers(_h(Auto_Submitted="no"))
    assert not bulk_headers([])
