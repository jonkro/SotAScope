"""Tests for project-layer API: projects, topic lists, and work membership."""


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def test_create_and_list_projects(client):
    r = client.post("/api/projects", json={
        "name": "My Research",
        "description": "Exploring networks",
        "owner": "alice",
    })
    assert r.status_code == 201
    project = r.json()
    assert project["name"] == "My Research"
    assert project["owner"] == "alice"
    assert project["topic_lists"] == []

    r = client.post("/api/projects", json={"name": "Side Project"})
    assert r.status_code == 201

    r = client.get("/api/projects")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_get_project_detail(client):
    pid = client.post("/api/projects", json={"name": "P1"}).json()["id"]

    # Create a topic list so detail has content
    client.post(f"/api/projects/{pid}/topic-lists", json={
        "name": "Core", "color": "#ff0000",
    })

    r = client.get(f"/api/projects/{pid}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["name"] == "P1"
    assert len(detail["topic_lists"]) == 1
    assert detail["topic_lists"][0]["name"] == "Core"


def test_update_project(client):
    pid = client.post("/api/projects", json={"name": "Old"}).json()["id"]

    r = client.patch(f"/api/projects/{pid}", json={"name": "New", "owner": "bob"})
    assert r.status_code == 200
    assert r.json()["name"] == "New"
    assert r.json()["owner"] == "bob"


def test_delete_project(client):
    pid = client.post("/api/projects", json={"name": "Temp"}).json()["id"]

    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 204

    r = client.get(f"/api/projects/{pid}")
    assert r.status_code == 404


def test_delete_project_cascades_topic_lists(client):
    pid = client.post("/api/projects", json={"name": "P"}).json()["id"]
    client.post(f"/api/projects/{pid}/topic-lists", json={
        "name": "TL", "color": "#000000",
    })

    client.delete(f"/api/projects/{pid}")

    # Topic list should be gone too
    r = client.get(f"/api/projects/{pid}/topic-lists")
    assert r.status_code == 404  # project not found


def test_search_projects(client):
    client.post("/api/projects", json={"name": "Network Analysis"})
    client.post("/api/projects", json={"name": "ML Survey"})

    r = client.get("/api/projects", params={"q": "network"})
    assert len(r.json()) == 1
    assert r.json()[0]["name"] == "Network Analysis"


def test_project_not_found(client):
    assert client.get("/api/projects/999").status_code == 404
    assert client.patch("/api/projects/999", json={"name": "x"}).status_code == 404
    assert client.delete("/api/projects/999").status_code == 404


# ---------------------------------------------------------------------------
# Topic lists
# ---------------------------------------------------------------------------

def test_topic_list_crud(client):
    pid = client.post("/api/projects", json={"name": "P"}).json()["id"]

    # Create
    r = client.post(f"/api/projects/{pid}/topic-lists", json={
        "name": "Routing Protocols", "color": "#3b82f6",
    })
    assert r.status_code == 201
    tl = r.json()
    tlid = tl["id"]
    assert tl["name"] == "Routing Protocols"
    assert tl["color"] == "#3b82f6"
    assert tl["works"] == []

    # List
    r = client.get(f"/api/projects/{pid}/topic-lists")
    assert r.status_code == 200
    assert len(r.json()) == 1

    # Get detail
    r = client.get(f"/api/projects/{pid}/topic-lists/{tlid}")
    assert r.status_code == 200
    assert r.json()["name"] == "Routing Protocols"

    # Update
    r = client.patch(f"/api/projects/{pid}/topic-lists/{tlid}", json={
        "name": "Routing", "color": "#ef4444",
    })
    assert r.status_code == 200
    assert r.json()["name"] == "Routing"
    assert r.json()["color"] == "#ef4444"

    # Delete
    r = client.delete(f"/api/projects/{pid}/topic-lists/{tlid}")
    assert r.status_code == 204

    r = client.get(f"/api/projects/{pid}/topic-lists")
    assert len(r.json()) == 0


def test_topic_list_not_found(client):
    pid = client.post("/api/projects", json={"name": "P"}).json()["id"]

    assert client.get(f"/api/projects/{pid}/topic-lists/999").status_code == 404
    assert client.patch(
        f"/api/projects/{pid}/topic-lists/999", json={"name": "x"}
    ).status_code == 404
    assert client.delete(f"/api/projects/{pid}/topic-lists/999").status_code == 404


def test_topic_list_scoped_to_project(client):
    """A topic list from project A should not be accessible under project B."""
    p1 = client.post("/api/projects", json={"name": "P1"}).json()["id"]
    p2 = client.post("/api/projects", json={"name": "P2"}).json()["id"]

    tl = client.post(f"/api/projects/{p1}/topic-lists", json={
        "name": "TL", "color": "#000000",
    }).json()

    # Accessing topic list via wrong project should 404
    assert client.get(f"/api/projects/{p2}/topic-lists/{tl['id']}").status_code == 404


# ---------------------------------------------------------------------------
# Topic list works (add / remove papers)
# ---------------------------------------------------------------------------

def _setup_project_with_works(client):
    """Helper: create a project, topic list, and two works."""
    pid = client.post("/api/projects", json={"name": "P"}).json()["id"]
    tlid = client.post(f"/api/projects/{pid}/topic-lists", json={
        "name": "Seeds", "color": "#22c55e",
    }).json()["id"]

    w1 = client.post("/api/works", json={
        "title": "Paper One", "doi": "10.1/one", "publication_year": 2022,
    }).json()
    w2 = client.post("/api/works", json={
        "title": "Paper Two", "doi": "10.1/two", "publication_year": 2023,
    }).json()

    return pid, tlid, w1, w2


def test_add_and_remove_work_from_topic_list(client):
    pid, tlid, w1, w2 = _setup_project_with_works(client)

    # Add first work
    r = client.post(
        f"/api/projects/{pid}/topic-lists/{tlid}/works",
        json={"work_id": w1["id"]},
    )
    assert r.status_code == 201
    assoc = r.json()
    assert assoc["work_id"] == w1["id"]
    assert assoc["work"]["title"] == "Paper One"

    # Add second work
    r = client.post(
        f"/api/projects/{pid}/topic-lists/{tlid}/works",
        json={"work_id": w2["id"]},
    )
    assert r.status_code == 201

    # Topic list detail should show both works
    r = client.get(f"/api/projects/{pid}/topic-lists/{tlid}")
    assert len(r.json()["works"]) == 2

    # Remove first work
    r = client.delete(
        f"/api/projects/{pid}/topic-lists/{tlid}/works/{w1['id']}"
    )
    assert r.status_code == 204

    # Only one work left
    r = client.get(f"/api/projects/{pid}/topic-lists/{tlid}")
    assert len(r.json()["works"]) == 1
    assert r.json()["works"][0]["work"]["title"] == "Paper Two"


def test_duplicate_work_in_topic_list_rejected(client):
    pid, tlid, w1, _ = _setup_project_with_works(client)

    client.post(
        f"/api/projects/{pid}/topic-lists/{tlid}/works",
        json={"work_id": w1["id"]},
    )
    r = client.post(
        f"/api/projects/{pid}/topic-lists/{tlid}/works",
        json={"work_id": w1["id"]},
    )
    assert r.status_code == 409


def test_add_nonexistent_work_rejected(client):
    pid, tlid, _, _ = _setup_project_with_works(client)

    r = client.post(
        f"/api/projects/{pid}/topic-lists/{tlid}/works",
        json={"work_id": 9999},
    )
    assert r.status_code == 422


def test_remove_work_not_in_list(client):
    pid, tlid, w1, _ = _setup_project_with_works(client)

    r = client.delete(
        f"/api/projects/{pid}/topic-lists/{tlid}/works/{w1['id']}"
    )
    assert r.status_code == 404


def test_work_in_multiple_topic_lists(client):
    """A single work can appear in multiple topic lists within the same project."""
    pid = client.post("/api/projects", json={"name": "P"}).json()["id"]
    tl1 = client.post(f"/api/projects/{pid}/topic-lists", json={
        "name": "List A", "color": "#ff0000",
    }).json()["id"]
    tl2 = client.post(f"/api/projects/{pid}/topic-lists", json={
        "name": "List B", "color": "#0000ff",
    }).json()["id"]

    work = client.post("/api/works", json={
        "title": "Shared Paper", "doi": "10.1/shared",
    }).json()

    r1 = client.post(
        f"/api/projects/{pid}/topic-lists/{tl1}/works",
        json={"work_id": work["id"]},
    )
    r2 = client.post(
        f"/api/projects/{pid}/topic-lists/{tl2}/works",
        json={"work_id": work["id"]},
    )
    assert r1.status_code == 201
    assert r2.status_code == 201

    # Both lists should contain the work
    d1 = client.get(f"/api/projects/{pid}/topic-lists/{tl1}").json()
    d2 = client.get(f"/api/projects/{pid}/topic-lists/{tl2}").json()
    assert len(d1["works"]) == 1
    assert len(d2["works"]) == 1
