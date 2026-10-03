import io

import pytest

import app as web


@pytest.fixture
def client():
    web.app.config["TESTING"] = True
    with web.app.test_client() as client:
        yield client


def test_pages_and_data_endpoints(client):
    assert client.get("/").status_code == 200
    overview = client.get("/api/overview").get_json()
    assert overview["datasets"]["drinks"]["stats"]["items"] == 74
    assert client.get("/api/charts?metric=protein").status_code == 200
    assert client.get("/api/charts?metric=nope").status_code == 400


def test_filtering_and_csv_download(client):
    data = client.get("/api/items?dataset=food&calories_max=500&sort=calories&desc=1").get_json()
    assert data["count"] < data["total"]
    assert data["rows"][0]["calories"] == 500
    assert client.get("/api/items.csv?dataset=drinks&caffeine=yes").data.startswith(b"name,")


def test_upload_replaces_only_this_session_and_reset_restores(client):
    csv = b"Item,Calories,Protein\nTest Latte,100,5\n"
    res = client.post("/api/upload", data={"drinks": (io.BytesIO(csv), "mine.csv")}, content_type="multipart/form-data")
    assert res.status_code == 200
    assert client.get("/api/overview").get_json()["datasets"]["drinks"]["stats"]["items"] == 1
    with web.app.test_client() as other:  # a different browser still sees the sample data
        assert other.get("/api/overview").get_json()["datasets"]["drinks"]["stats"]["items"] == 74
    client.post("/api/reset")
    assert client.get("/api/overview").get_json()["using_defaults"]


def test_bad_upload_is_rejected(client):
    res = client.post("/api/upload", data={"food": (io.BytesIO(b"a,b\n1,2\n"), "bad.txt")}, content_type="multipart/form-data")
    assert res.status_code == 400 and "food" in res.get_json()["problems"]


def test_upload_is_all_or_nothing(client):
    """A good drinks file uploaded with a broken food file changes nothing."""
    good = (io.BytesIO(b"Item,Calories\nTest Latte,100\n"), "drinks.csv")
    bad = (io.BytesIO(b"no,numbers\nhere,either\n"), "food.csv")
    res = client.post("/api/upload", data={"drinks": good, "food": bad}, content_type="multipart/form-data")
    assert res.status_code == 400 and set(res.get_json()["problems"]) == {"food"}
    assert client.get("/api/overview").get_json()["using_defaults"]


def test_oversized_upload_gets_a_readable_message(client):
    big = io.BytesIO(b"Item,Calories\n" + b"Latte,100\n" * 600_000)  # about 6 MB
    res = client.post("/api/upload", data={"drinks": (big, "big.csv")}, content_type="multipart/form-data")
    assert res.status_code == 413 and "MB" in res.get_json()["error"]


def test_questions_are_validated(client):
    assert client.post("/api/ask", json={"question": "  "}).status_code == 400
    assert client.post("/api/ask", json={"question": "x" * 501}).status_code == 400


def _upload(client, **files):
    return client.post("/api/upload", data={k: (io.BytesIO(v), f"{k}.csv") for k, v in files.items()}, content_type="multipart/form-data")


def test_uploading_one_file_leaves_the_other_dataset_out(client):
    """Drinks-only upload must not be paired with the sample food file."""
    assert _upload(client, drinks=b"Item,Calories,Protein\nTest Latte,100,5\n").status_code == 200
    overview = client.get("/api/overview").get_json()
    assert list(overview["datasets"]) == ["drinks"]
    assert overview["comparison"] == [] and not overview["using_defaults"]
    figures = client.get("/api/charts?metric=calories").get_json()
    assert figures["averages"] is None and figures["top"]  # drinks-vs-food needs both; the rest still draw
    res = client.get("/api/items?dataset=food")
    assert res.status_code == 400 and "food" in res.get_json()["error"]
    assert client.get("/api/items?dataset=drinks").get_json()["total"] == 1


def test_second_upload_adds_the_other_dataset_and_reset_restores_both(client):
    _upload(client, drinks=b"Item,Calories\nTest Latte,100\n")
    _upload(client, food=b"Item,Calories\nTest Bagel,300\n")
    overview = client.get("/api/overview").get_json()
    assert set(overview["datasets"]) == {"drinks", "food"} and overview["comparison"]
    assert overview["datasets"]["food"]["stats"]["items"] == 1
    client.post("/api/reset")
    overview = client.get("/api/overview").get_json()
    assert overview["using_defaults"] and overview["datasets"]["food"]["stats"]["items"] == 113


def test_swapped_files_are_rejected_with_a_message(client):
    food_csv = b"Item,Calories\nBig Mac,580\nMedium French Fries,320\nChicken McNuggets,410\nEgg McMuffin,310\n"
    res = _upload(client, drinks=food_csv)
    assert res.status_code == 400 and "looks like a food file" in res.get_json()["problems"]["drinks"]
    assert client.get("/api/overview").get_json()["using_defaults"]  # nothing was replaced


def test_removing_one_file_keeps_the_other_and_removing_both_restores_the_sample(client):
    assert client.post("/api/remove", json={"dataset": "drinks"}).status_code == 400  # nothing uploaded yet
    _upload(client, drinks=b"Item,Calories\nTest Latte,100\n", food=b"Item,Calories\nTest Bagel,300\n")
    assert client.post("/api/remove", json={"dataset": "drinks"}).get_json() == {"ok": True, "using_defaults": False}
    assert list(client.get("/api/overview").get_json()["datasets"]) == ["food"]
    assert client.post("/api/remove", json={"dataset": "food"}).get_json()["using_defaults"]
    assert client.get("/api/overview").get_json()["datasets"]["food"]["stats"]["items"] == 113


def test_every_api_answer_carries_the_data_version(client):
    first = client.get("/api/version").get_json()["version"]
    assert first == "sample"
    res = _upload(client, drinks=b"Item,Calories\nTest Latte,100\n")
    uploaded = res.headers["X-Data-Version"]
    assert uploaded != "sample" and res.headers["Cache-Control"] == "no-store"
    assert client.get("/api/charts?metric=calories").headers["X-Data-Version"] == uploaded
    client.post("/api/reset")
    assert client.get("/api/version").get_json()["version"] == "sample"
