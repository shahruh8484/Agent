from fbadsagent.cloner.models import ClonerProject
from fbadsagent.cloner.store import ClonerProjectStore, slugify


def test_slugify():
    assert slugify("Glycofort!") == "glycofort"
    assert slugify("   ") == "landing"


def make_project(id="p1", slug="") -> ClonerProject:
    return ClonerProject(
        id=id,
        name="Glycofort",
        description="A dietary supplement",
        created_at="2026-01-01 00:00 UTC",
        slug=slug,
    )


def test_save_and_list(tmp_path):
    store = ClonerProjectStore(tmp_path / "projects.json")
    store.save(make_project())

    projects = store.list_projects()
    assert len(projects) == 1
    assert projects[0].id == "p1"


def test_get_project_returns_none_when_missing(tmp_path):
    store = ClonerProjectStore(tmp_path / "projects.json")
    assert store.get_project("nope") is None


def test_save_upserts_by_id(tmp_path):
    store = ClonerProjectStore(tmp_path / "projects.json")
    store.save(make_project())
    updated = make_project()
    updated.status = "done"
    store.save(updated)

    projects = store.list_projects()
    assert len(projects) == 1
    assert projects[0].status == "done"


def test_get_by_slug(tmp_path):
    store = ClonerProjectStore(tmp_path / "projects.json")
    store.save(make_project(slug="glycofort-ab12cd"))

    found = store.get_by_slug("glycofort-ab12cd")
    assert found is not None
    assert found.id == "p1"
    assert store.get_by_slug("nope") is None


def test_remove(tmp_path):
    store = ClonerProjectStore(tmp_path / "projects.json")
    store.save(make_project("a"))
    store.save(make_project("b"))
    store.remove("a")

    assert [p.id for p in store.list_projects()] == ["b"]


def test_list_projects_newest_first(tmp_path):
    store = ClonerProjectStore(tmp_path / "projects.json")
    store.save(make_project("a"))
    store.save(make_project("b"))

    assert [p.id for p in store.list_projects()] == ["b", "a"]
