from pathlib import Path

from fastapi.testclient import TestClient

from fbadsagent.cloner.app import create_app
from fbadsagent.cloner.models import ClonerProject
from fbadsagent.cloner.store import ClonerProjectStore


def build_client(web_settings, project_store=None):
    app = create_app(settings=web_settings, project_store=project_store)
    return TestClient(app)


def login(client):
    return client.post("/login", data={"username": "admin", "password": "correct-horse"})


def make_project(**overrides) -> ClonerProject:
    defaults = dict(
        id="c1",
        name="Glycofort",
        description="A dietary supplement",
        created_at="2026-01-01 00:00 UTC",
    )
    defaults.update(overrides)
    return ClonerProject(**defaults)


def test_root_redirects_to_login_when_unauthenticated(web_settings):
    client = build_client(web_settings)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_login_with_wrong_password_rejected(web_settings):
    client = build_client(web_settings)
    response = client.post("/login", data={"username": "admin", "password": "nope"})
    assert response.status_code == 401
    assert "Неверное имя пользователя или пароль" in response.text


def test_login_success_shows_dashboard(web_settings):
    client = build_client(web_settings)
    response = login(client)
    assert response.status_code == 200
    assert "Cloner" in response.text


def test_add_project_persists_and_redirects(web_settings, tmp_path):
    store = ClonerProjectStore(tmp_path / "projects.json")
    client = build_client(web_settings, project_store=store)
    login(client)

    response = client.post(
        "/projects/add",
        data={
            "name": "Glycofort",
            "description": "A dietary supplement",
            "language": "Uzbek",
            "landing_style": "static",
            "reference_landing_urls": "https://example.com/offer",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    projects = store.list_projects()
    assert len(projects) == 1
    assert projects[0].name == "Glycofort"
    assert projects[0].reference_landing_urls == ["https://example.com/offer"]
    assert projects[0].status == "pending"


def test_add_project_saves_uploaded_reference_files(web_settings, tmp_path):
    store = ClonerProjectStore(tmp_path / "projects.json")
    client = build_client(web_settings, project_store=store)
    login(client)

    client.post(
        "/projects/add",
        data={"name": "Glycofort", "description": "x"},
        files={
            "reference_landing_screenshots": ("shot.png", b"fake-png-bytes", "image/png"),
            "reference_creatives": ("ad.png", b"fake-png-bytes", "image/png"),
        },
        follow_redirects=False,
    )

    project = store.list_projects()[0]
    assert len(project.reference_landing_screenshot_paths) == 1
    assert Path(project.reference_landing_screenshot_paths[0]).exists()
    assert len(project.reference_creative_paths) == 1
    assert Path(project.reference_creative_paths[0]).exists()


def test_generate_project_runs_pipeline_and_saves_result(web_settings, tmp_path, mocker):
    store = ClonerProjectStore(tmp_path / "projects.json")
    store.save(make_project())
    client = build_client(web_settings, project_store=store)
    login(client)

    done_project = make_project(status="done", slug="glycofort-ab12cd", headline="x")
    mocker.patch("fbadsagent.cloner.app.run_cloner_project", return_value=done_project)

    response = client.post("/projects/generate", data={"project_id": "c1"}, follow_redirects=False)

    assert response.status_code == 302
    assert store.get_project("c1").status == "done"
    assert store.get_project("c1").slug == "glycofort-ab12cd"


def test_delete_project(web_settings, tmp_path):
    store = ClonerProjectStore(tmp_path / "projects.json")
    store.save(make_project())
    client = build_client(web_settings, project_store=store)
    login(client)

    client.post("/projects/delete", data={"project_id": "c1"}, follow_redirects=False)

    assert store.list_projects() == []


def test_public_landing_page_renders_static(web_settings, tmp_path):
    store = ClonerProjectStore(tmp_path / "projects.json")
    store.save(
        make_project(
            status="done",
            slug="glycofort-ab12cd",
            headline="Lower Your Sugar Naturally",
            subheadline="Support your routine",
            benefits=["All natural", "No side effects"],
            cta_text="Order Now",
        )
    )
    client = build_client(web_settings, project_store=store)

    response = client.get("/lp/glycofort-ab12cd")

    assert response.status_code == 200
    assert "Lower Your Sugar Naturally" in response.text
    assert "Order Now" in response.text


def test_public_landing_page_renders_quiz(web_settings, tmp_path):
    from fbadsagent.models import QuizQuestion

    store = ClonerProjectStore(tmp_path / "projects.json")
    store.save(
        make_project(
            status="done",
            slug="glycofort-ab12cd",
            landing_style="quiz",
            headline="A Few Quick Questions",
            quiz_questions=[QuizQuestion(text="Do you exercise often?", options=["Yes", "No"])],
            quiz_result_message="This matches your routine.",
            cta_text="See My Recommendation",
        )
    )
    client = build_client(web_settings, project_store=store)

    response = client.get("/lp/glycofort-ab12cd")

    assert response.status_code == 200
    assert "A Few Quick Questions" in response.text
    assert "Do you exercise often?" in response.text


def test_public_landing_page_404_when_not_done(web_settings, tmp_path):
    store = ClonerProjectStore(tmp_path / "projects.json")
    store.save(make_project(status="pending"))
    client = build_client(web_settings, project_store=store)

    response = client.get("/lp/nope")
    assert response.status_code == 404
