# mypy: disable-error-code=no-untyped-def
from bizguard.graph.ids import api_id, db_id, mq_id, proto_id, repo_id


def test_repo_id():
    assert repo_id("core", "a/B.java", "B.m()") == "repo://core/a/B.java#B.m()"


def test_api_id():
    assert api_id("core", "post", "/v1/x") == "api://core/POST/v1/x"


def test_proto_id():
    assert proto_id("x.v1", "S", "m") == "proto://x.v1/S/m"


def test_db_id():
    assert db_id("core", "orders", "status") == "db://core/orders#status"


def test_mq_id():
    assert mq_id("core", "orders", "status") == "mq://core/orders#status"
