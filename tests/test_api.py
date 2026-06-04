import pytest
from httpx import AsyncClient, ASGITransport
from src.main import app
from src.api.dependencies import get_session_service, get_presence_registry
from src.domain.models import Estimate


@pytest.fixture(autouse=True)
def reset_service():
    get_session_service.cache_clear()
    get_presence_registry.cache_clear()
    yield
    get_session_service.cache_clear()
    get_presence_registry.cache_clear()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def authed_client(client):
    """Client with a username cookie set."""
    client.cookies.set("username", "TestPM")
    return client


# ── Login ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_page_renders(client):
    r = await client.get("/login")
    assert r.status_code == 200
    assert "Przedstaw się" in r.text


@pytest.mark.asyncio
async def test_login_sets_cookie_and_redirects(client):
    r = await client.post("/login", data={"username": "Alice", "next_url": "/"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert "username" in r.cookies


@pytest.mark.asyncio
async def test_login_with_next_redirects_correctly(client):
    r = await client.post("/login", data={"username": "Alice", "next_url": "/sessions/abc"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/sessions/abc"


@pytest.mark.asyncio
async def test_login_rejects_unsafe_next(client):
    r = await client.post("/login", data={"username": "Alice", "next_url": "https://evil.com"}, follow_redirects=False)
    assert r.headers["location"] == "/"


@pytest.mark.asyncio
async def test_login_with_cookie_redirects_to_home(client):
    client.cookies.set("username", "Alice")
    r = await client.get("/login", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"


# ── Name uniqueness ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_blocked_when_name_live_for_other_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as a, \
               AsyncClient(transport=transport, base_url="http://test") as b:
        r1 = await a.post("/login", data={"username": "Alice"}, follow_redirects=False)
        assert r1.headers["location"] == "/"

        r2 = await b.post("/login", data={"username": "Alice"}, follow_redirects=False)
        assert r2.status_code == 303
        assert "error=name_taken" in r2.headers["location"]
        assert "username" not in r2.cookies


@pytest.mark.asyncio
async def test_login_allowed_same_client_relogin(client):
    r1 = await client.post("/login", data={"username": "Alice"}, follow_redirects=False)
    assert r1.headers["location"] == "/"
    r2 = await client.post("/login", data={"username": "Alice"}, follow_redirects=False)
    assert r2.headers["location"] == "/"


@pytest.mark.asyncio
async def test_login_allowed_after_logout():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as a, \
               AsyncClient(transport=transport, base_url="http://test") as b:
        await a.post("/login", data={"username": "Alice"}, follow_redirects=False)
        await a.post("/logout", follow_redirects=False)
        r = await b.post("/login", data={"username": "Alice"}, follow_redirects=False)
        assert r.headers["location"] == "/"


@pytest.mark.asyncio
async def test_name_freed_after_grace(monkeypatch):
    monkeypatch.setattr("src.config.settings.PRESENCE_GRACE_SECONDS", 0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as a, \
               AsyncClient(transport=transport, base_url="http://test") as b:
        await a.post("/login", data={"username": "Alice"}, follow_redirects=False)
        r = await b.post("/login", data={"username": "Alice"}, follow_redirects=False)
        assert r.headers["location"] == "/"


@pytest.mark.asyncio
async def test_heartbeat_keeps_name_taken(client):
    r = await client.post("/heartbeat", follow_redirects=False)
    assert r.status_code == 204


# ── Home ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_home_without_cookie_redirects_to_login(client):
    r = await client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


@pytest.mark.asyncio
async def test_home_with_cookie_renders(authed_client):
    r = await authed_client.get("/")
    assert r.status_code == 200
    assert "Plannie" in r.text


# ── Session creation ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_session_redirects_to_session_page(authed_client):
    r = await authed_client.post("/sessions", data={"title": "Sprint 1"}, follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "/sessions/" in loc
    assert "/pm" not in loc
    assert "/join" not in loc


@pytest.mark.asyncio
async def test_create_session_sets_creator(authed_client):
    r = await authed_client.post("/sessions", data={"title": "Sprint 1"}, follow_redirects=True)
    assert r.status_code == 200
    service = get_session_service()
    session_id = str(r.url).split("/sessions/")[1].rstrip("/")
    session = service.get_session(session_id)
    assert session.creator == "TestPM"


@pytest.mark.asyncio
async def test_create_session_without_cookie_redirects_to_login(client):
    r = await client.post("/sessions", data={"title": "Sprint 1"}, follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


@pytest.mark.asyncio
async def test_create_session_over_limit_redirects_with_error(authed_client):
    service = get_session_service()
    for _ in range(service.SESSION_LIMIT):
        service.create_session()
    r = await authed_client.post("/sessions", data={"title": "One too many"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/?error=limit"


# ── Session view ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_view_renders(authed_client):
    r1 = await authed_client.post("/sessions", data={"title": "Sprint 1"}, follow_redirects=True)
    assert r1.status_code == 200
    assert "Sprint 1" in r1.text
    assert "data:image/png" in r1.text


@pytest.mark.asyncio
async def test_session_view_shows_pm_controls_for_creator(authed_client):
    r1 = await authed_client.post("/sessions", data={"title": "Sprint 1"}, follow_redirects=True)
    assert "Zamknij i pokaż wyniki" in r1.text


@pytest.mark.asyncio
async def test_session_view_hides_pm_controls_for_participant(client):
    # Create session as PM
    client.cookies.set("username", "PM")
    r1 = await client.post("/sessions", data={"title": "Sprint"}, follow_redirects=False)
    session_id = r1.headers["location"].split("/sessions/")[1].rstrip("/")

    # Access as different user
    client.cookies.set("username", "Participant")
    r2 = await client.get(f"/sessions/{session_id}")
    assert r2.status_code == 200
    assert "Zamknij i pokaż wyniki" not in r2.text


@pytest.mark.asyncio
async def test_session_view_without_cookie_redirects_to_login(client):
    client.cookies.set("username", "PM")
    r1 = await client.post("/sessions", data={"title": "Sprint"}, follow_redirects=False)
    session_id = r1.headers["location"].split("/sessions/")[1].rstrip("/")

    client.cookies.clear()
    r2 = await client.get(f"/sessions/{session_id}", follow_redirects=False)
    assert r2.status_code == 302
    assert "/login" in r2.headers["location"]


@pytest.mark.asyncio
async def test_session_view_uses_public_host_from_env(authed_client, monkeypatch):
    monkeypatch.setenv("PUBLIC_HOST", "planning.example.com")
    r = await authed_client.post("/sessions", data={"title": "Sprint 1"}, follow_redirects=True)
    assert r.status_code == 200
    assert "planning.example.com/sessions/" in r.text


@pytest.mark.asyncio
async def test_session_view_uses_public_scheme_and_port(authed_client, monkeypatch):
    monkeypatch.setenv("PUBLIC_HOST", "planning.example.com")
    monkeypatch.setenv("PUBLIC_SCHEME", "https")
    monkeypatch.setenv("PUBLIC_PORT", "8443")
    r = await authed_client.post("/sessions", data={"title": "Sprint 1"}, follow_redirects=True)
    assert r.status_code == 200
    assert "https://planning.example.com:8443/sessions/" in r.text


@pytest.mark.asyncio
async def test_session_view_full_public_host_url_has_precedence(authed_client, monkeypatch):
    monkeypatch.setenv("PUBLIC_HOST", "https://planning.example.com")
    monkeypatch.setenv("PUBLIC_SCHEME", "http")
    monkeypatch.setenv("PUBLIC_PORT", "9999")
    r = await authed_client.post("/sessions", data={"title": "Sprint 1"}, follow_redirects=True)
    assert r.status_code == 200
    assert "https://planning.example.com/sessions/" in r.text
    assert "9999/sessions/" not in r.text


@pytest.mark.asyncio
async def test_session_view_auto_adds_participant(client):
    client.cookies.set("username", "PM")
    r1 = await client.post("/sessions", data={"title": "Sprint"}, follow_redirects=False)
    session_id = r1.headers["location"].split("/sessions/")[1].rstrip("/")

    client.cookies.set("username", "Alice")
    await client.get(f"/sessions/{session_id}")

    service = get_session_service()
    session = service.get_session(session_id)
    assert "Alice" in session.participants


@pytest.mark.asyncio
async def test_session_view_marks_user_as_voted_when_estimate_exists(authed_client):
    r1 = await authed_client.post("/sessions", data={"title": "Sprint"}, follow_redirects=False)
    session_id = r1.headers["location"].split("/sessions/")[1].rstrip("/")

    service = get_session_service()
    session = service.get_session(session_id)
    p = session.add_or_update_participant("TestPM")
    p.estimate = Estimate.FIVE
    service._repo.save(session)

    r2 = await authed_client.get(f"/sessions/{session_id}")
    assert r2.status_code == 200
    assert 'class="participant voted"' in r2.text


@pytest.mark.asyncio
async def test_nonexistent_session_redirects_to_home(authed_client):
    r = await authed_client.get("/sessions/00000000-0000-0000-0000-000000000000", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"


# ── Voting ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vote_uses_cookie_identity(authed_client):
    r1 = await authed_client.post("/sessions", data={"title": "T"}, follow_redirects=False)
    session_id = r1.headers["location"].split("/sessions/")[1].rstrip("/")

    r2 = await authed_client.post(
        f"/sessions/{session_id}/vote",
        data={"estimate": "5"},
        follow_redirects=False,
    )
    assert r2.status_code == 303
    service = get_session_service()
    assert service.get_session(session_id).participants["TestPM"].estimate == Estimate.FIVE


@pytest.mark.asyncio
async def test_diacritic_username_round_trips(client):
    # login encodes the cookie; voting decodes it back to the original identity
    r = await client.post("/login", data={"username": "Łukasz", "next_url": "/"}, follow_redirects=False)
    assert "%C5%81ukasz" in r.headers.get("set-cookie", "")

    r1 = await client.post("/sessions", data={"title": "T"}, follow_redirects=False)
    session_id = r1.headers["location"].split("/sessions/")[1].rstrip("/")
    await client.post(f"/sessions/{session_id}/vote", data={"estimate": "5"}, follow_redirects=False)

    service = get_session_service()
    assert "Łukasz" in service.get_session(session_id).participants


# ── Logout ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_logout_clears_cookie_and_redirects_to_login(authed_client):
    r1 = await authed_client.post("/sessions", data={"title": "T"}, follow_redirects=False)
    session_id = r1.headers["location"].split("/sessions/")[1].rstrip("/")

    r2 = await authed_client.post(f"/sessions/{session_id}/logout", follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"] == "/login"
    set_cookie = r2.headers.get("set-cookie", "")
    assert "username=" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


@pytest.mark.asyncio
async def test_logout_removes_participant(client):
    client.cookies.set("username", "PM")
    r1 = await client.post("/sessions", data={"title": "T"}, follow_redirects=False)
    session_id = r1.headers["location"].split("/sessions/")[1].rstrip("/")

    client.cookies.set("username", "Alice")
    await client.get(f"/sessions/{session_id}")

    service = get_session_service()
    assert "Alice" in service.get_session(session_id).participants

    await client.post(f"/sessions/{session_id}/logout", follow_redirects=False)

    assert "Alice" not in service.get_session(session_id).participants


# ── Closed session auto-join guard ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_closed_session_does_not_auto_add_new_viewer(client):
    client.cookies.set("username", "PM")
    r1 = await client.post("/sessions", data={"title": "T"}, follow_redirects=False)
    session_id = r1.headers["location"].split("/sessions/")[1].rstrip("/")
    await client.post(f"/sessions/{session_id}/close", follow_redirects=False)

    client.cookies.set("username", "Latecomer")
    await client.get(f"/sessions/{session_id}")

    service = get_session_service()
    assert "Latecomer" not in service.get_session(session_id).participants


# ── Close / reset ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_non_pm_cannot_close_session(client):
    client.cookies.set("username", "PM")
    r1 = await client.post("/sessions", data={"title": "T"}, follow_redirects=False)
    session_id = r1.headers["location"].split("/sessions/")[1].rstrip("/")

    client.cookies.set("username", "Intruder")
    await client.post(f"/sessions/{session_id}/close", follow_redirects=False)

    service = get_session_service()
    assert not service.get_session(session_id).is_closed


@pytest.mark.asyncio
async def test_non_pm_cannot_reset_session(client):
    client.cookies.set("username", "PM")
    r1 = await client.post("/sessions", data={"title": "Original"}, follow_redirects=False)
    session_id = r1.headers["location"].split("/sessions/")[1].rstrip("/")

    client.cookies.set("username", "Intruder")
    await client.post(f"/sessions/{session_id}/reset", data={"title": "Hacked"}, follow_redirects=False)

    service = get_session_service()
    assert service.get_session(session_id).title == "Original"


@pytest.mark.asyncio
async def test_close_session(authed_client):
    r1 = await authed_client.post("/sessions", data={"title": "T"}, follow_redirects=False)
    session_id = r1.headers["location"].split("/sessions/")[1].rstrip("/")

    r2 = await authed_client.post(f"/sessions/{session_id}/close", follow_redirects=True)
    assert r2.status_code == 200
    assert "zamknięta" in r2.text.lower()


@pytest.mark.asyncio
async def test_vote_summary_sorted_after_close(authed_client):
    r1 = await authed_client.post("/sessions", data={"title": "T"}, follow_redirects=False)
    session_id = r1.headers["location"].split("/sessions/")[1].rstrip("/")

    service = get_session_service()
    for user, val in [("A", "5"), ("B", "5"), ("C", "5"), ("D", "8"), ("E", "8"), ("F", "3")]:
        service.submit_vote(session_id, user, val)

    await authed_client.post(f"/sessions/{session_id}/close", follow_redirects=False)
    r = await authed_client.get(f"/sessions/{session_id}")
    assert r.status_code == 200
    assert "Podsumowanie głosowania" in r.text

    i5 = r.text.find('class="summary-estimate">5<')
    i8 = r.text.find('class="summary-estimate">8<')
    i3 = r.text.find('class="summary-estimate">3<')
    assert i5 != -1 and i8 != -1 and i3 != -1
    assert i5 < i8 < i3
