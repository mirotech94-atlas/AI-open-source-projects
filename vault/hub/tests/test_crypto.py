from guardian_hub import crypto

def test_roundtrip_and_soft_failure():
    tok = crypto.encrypt("https://hooks.slack.com/services/AAA/BBB/CCC")
    assert tok != "https://hooks.slack.com/services/AAA/BBB/CCC"
    assert crypto.decrypt(tok) == "https://hooks.slack.com/services/AAA/BBB/CCC"
    assert crypto.decrypt("not-a-fernet-token") == ""
