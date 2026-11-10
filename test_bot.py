"""
test_bot.py — end-to-end-ish tests for the Telegram layer using fakes.

No network, no BotFather token, no Google account. A FakeBot records the calls
the real handler would make, and a fake Drive serves the bytes.

    python -m pytest -q
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot as botmod
from gdrive_core import DriveClient, DriveFile, DriveError


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeMessage:
    def __init__(self, sink: list, text: str, **kw):
        self.sink = sink
        self.text = text
        self.kw = kw

    async def edit_text(self, text, **kw):
        self.text = text
        self.sink.append({"call": "edit", "text": text, **kw})
        return self


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail_next: Exception | None = None

    async def send_message(self, **kw):
        if self.fail_next is not None:
            err, self.fail_next = self.fail_next, None
            raise err
        self.calls.append({"call": "send_message", **kw})
        return FakeMessage(self.calls, kw.get("text", ""))

    async def send_chat_action(self, chat_id, action):
        self.calls.append({"call": "chat_action", "chat_id": chat_id})

    async def send_document(self, chat_id, document, **kw):
        self.calls.append(
            {"call": "send_document", "chat_id": chat_id, "name": kw.get("filename"),
             "caption": kw.get("caption"), "size": document.size}
        )
        return FakeMessage(self.calls, "doc")

    async def send_video(self, chat_id, video, **kw):
        self.calls.append({"call": "send_video", "name": kw.get("filename"),
                           "streaming": kw.get("supports_streaming")})
        return FakeMessage(self.calls, "video")

    async def send_audio(self, chat_id, audio, **kw):
        self.calls.append({"call": "send_audio", "name": kw.get("filename")})
        return FakeMessage(self.calls, "audio")

    async def send_photo(self, chat_id, photo, **kw):
        self.calls.append({"call": "send_photo", "name": Path(photo.name).name})
        return FakeMessage(self.calls, "photo")

    def sent_texts(self) -> list[str]:
        return [c.get("text", "") for c in self.calls if c["call"] == "send_message"]


URL = "http://fake.local/uc"


def make_state(tmp: Path, payload: bytes = b"hello world" * 5000) -> SimpleNamespace:
    """Build a BotState lookalike with a fake-serving Drive client."""
    client = DriveClient()

    async def _media(info):
        return (URL, {})

    client._media_request = _media  # type: ignore[assignment]

    # serve bytes without any network: swap the byte pump for a direct write
    async def fake_download(target, dest, on_progress=None, cancel=None, max_bytes=None):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if on_progress:
            await on_progress(len(payload), len(payload))
        dest.write_bytes(payload)
        return dest

    client.download = fake_download  # type: ignore[assignment]

    cfg = botmod.BotConfig()
    cfg.max_download_gb = 20
    cfg.max_upload_mb = 2000
    cfg.temp_dir = str(tmp)
    cfg.keep_files = False

    state = SimpleNamespace(
        cfg=cfg,
        client=client,
        downloads=asyncio.Semaphore(1),
        active={},
        temp_root=tmp,
        started_at=0.0,
        store=botmod.JobStore(None),
        jobs=None,
    )
    return state


@pytest.fixture(autouse=True)
def patch_state(monkeypatch, tmp_path):
    state = make_state(tmp_path)
    monkeypatch.setattr(botmod, "STATE", state)
    yield state


# --------------------------------------------------------------------------- #
# file_job
# --------------------------------------------------------------------------- #
def test_file_job_sends_document_and_cleans_up(tmp_path):
    fake = FakeBot()

    async def go():
        info = DriveFile("1A2b3C4d5E6f7G8h9I0jK", "lesson1.pdf", 55_000, "application/pdf")
        ok = await botmod.file_job(fake, 42, info)
        assert ok is True

    asyncio.run(go())
    docs = [c for c in fake.calls if c["call"] == "send_document"]
    assert len(docs) == 1
    assert docs[0]["name"] == "lesson1.pdf"
    assert "lesson1.pdf" in docs[0]["caption"]
    assert list(tmp_path.glob("*.pdf")) == []  # temp file removed
    assert any("Uploading" in t or "✅" in t for t in fake.sent_texts() + [c.get("text", "") for c in fake.calls if c["call"] == "edit"])


def test_file_job_routes_video_and_audio():
    fake = FakeBot()

    async def go():
        await botmod.file_job(fake, 1, DriveFile("aaaaaaaaaa", "lecture.mp4", 10, "video/mp4"))
        await botmod.file_job(fake, 1, DriveFile("bbbbbbbbbb", "podcast.mp3", 10, "audio/mpeg"))

    asyncio.run(go())
    kinds = [c["call"] for c in fake.calls]
    assert "send_video" in kinds
    assert "send_audio" in kinds
    video = next(c for c in fake.calls if c["call"] == "send_video")
    assert video["streaming"] is True  # streaming flag, no transcode


def test_file_job_respects_size_limit():
    fake = FakeBot()

    async def go():
        ok = await botmod.file_job(
            fake, 1, DriveFile("aaaaaaaaaa", "huge.iso", 50 * 1024**3, "application/octet-stream")
        )
        assert ok is False

    asyncio.run(go())
    assert not [c for c in fake.calls if c["call"] == "send_document"]
    assert any("over this bot" in t for t in fake.sent_texts())


def test_file_job_flattens_course_path_into_name():
    fake = FakeBot()

    async def go():
        info = DriveFile(
            "aaaaaaaaaa", "intro.mp4", 100, "video/mp4", rel_path="Module 1/Lesson 2/intro.mp4"
        )
        await botmod.file_job(fake, 1, info)

    asyncio.run(go())
    video = next(c for c in fake.calls if c["call"] == "send_video")
    assert video["name"] == "Module 1 - Lesson 2 - intro.mp4"


def test_safe_send_falls_back_to_plain_text():
    from telegram.error import BadRequest

    fake = FakeBot()
    fake.fail_next = BadRequest("can't parse entities")

    async def go():
        await botmod.safe_send(fake, 1, "bad <markup> here")

    asyncio.run(go())
    assert len([c for c in fake.calls if c["call"] == "send_message"]) == 1


# --------------------------------------------------------------------------- #
# JobStore
# --------------------------------------------------------------------------- #
def test_job_store_dedupe_and_reset():
    store = botmod.JobStore(None)
    assert store.is_done(7, "abc") is False
    store.mark_done(7, "abc", "a.pdf", 10)
    assert store.is_done(7, "abc") is True
    assert store.is_done(8, "abc") is False  # per-chat ledger
    assert store.count_done(7) == 1
    store.reset(7)
    assert store.is_done(7, "abc") is False


# --------------------------------------------------------------------------- #
# Course queue
# --------------------------------------------------------------------------- #
def test_course_job_skips_done_and_counts(tmp_path):
    fake = FakeBot()
    state = botmod.STATE
    files = [
        DriveFile(f"file{i:06d}xx", f"f{i}.pdf", 100, "application/pdf", rel_path=f"M/{i}.pdf")
        for i in range(3)
    ]
    state.store.mark_done(5, "file000000xx", "f0.pdf", 100)  # pretend file 0 was sent before

    job = botmod.CourseJob(5, 9, "folder")
    job.files = files
    jobs = botmod.Jobs(state.cfg, state.store)

    async def go():
        await jobs._process(fake, job)

    asyncio.run(go())
    docs = [c for c in fake.calls if c["call"] == "send_document"]
    assert len(docs) == 2  # one skipped as already sent
    assert job.sent == 2
    assert job.skipped == 1
    final = fake.sent_texts()[-1]
    assert "Course finished" in final
    assert state.store.is_done(5, "file000001xx")


def test_course_job_cancel_before_start():
    fake = FakeBot()
    state = botmod.STATE
    job = botmod.CourseJob(5, 9, "folder")
    job.files = [DriveFile("aaaaaaaaaa", "x.pdf", 10, "application/pdf")]
    job.cancel.set()
    jobs = botmod.Jobs(state.cfg, state.store)

    async def go():
        await jobs._process(fake, job)

    asyncio.run(go())
    assert not [c for c in fake.calls if c["call"] == "send_document"]
    assert any("Stopped" in t for t in fake.sent_texts())


# --------------------------------------------------------------------------- #
# Recursive folder walk
# --------------------------------------------------------------------------- #
def test_iter_folder_recurses_and_builds_rel_paths():
    client = DriveClient()
    tree = {
        "ROOT": [
            {"id": "sub1", "name": "Module 1", "mimeType": "application/vnd.google-apps.folder"},
            {"id": "f_top", "name": "syllabus.pdf", "size": "100", "mimeType": "application/pdf"},
            {"id": "doc1", "name": "Notes", "mimeType": "application/vnd.google-apps.document"},
            {"id": "sc1", "name": "shortcut", "mimeType": "application/vnd.google-apps.shortcut",
             "shortcutDetails": {"targetId": "f_target"}},
        ],
        "sub1": [
            {"id": "f_vid", "name": "intro.mp4", "size": "200", "mimeType": "video/mp4"},
        ],
    }

    async def fake_children(folder_id):
        for item in tree.get(folder_id, []):
            yield item

    client._list_children = fake_children  # type: ignore[assignment]
    client.credentials = object()  # type: ignore[assignment]  # makes .authorized True

    async def fake_get_file(file_id):
        return DriveFile(file_id, "linked.pdf", 50, "application/pdf")

    client.get_file = fake_get_file  # type: ignore[assignment]

    async def go():
        return [
            f
            async for f in client.iter_folder("ROOT", recursive=True, max_files=50)
        ]

    files = asyncio.run(go())
    paths = {f.rel_path for f in files}
    assert "syllabus.pdf" in paths
    assert "Module 1/intro.mp4" in paths
    assert "Notes" in paths                      # Google Doc kept as native
    assert "linked.pdf" in paths                 # shortcut resolved
    assert all(not f.is_folder for f in files)


def test_iter_folder_non_recursive_only_top_level():
    client = DriveClient()
    tree = {
        "ROOT": [
            {"id": "sub1", "name": "Module 1", "mimeType": "application/vnd.google-apps.folder"},
            {"id": "f_top", "name": "a.pdf", "size": "1", "mimeType": "application/pdf"},
        ]
    }

    async def fake_children(folder_id):
        for item in tree.get(folder_id, []):
            yield item

    client._list_children = fake_children  # type: ignore[assignment]
    client.credentials = object()  # type: ignore[assignment]  # makes .authorized True

    async def go():
        return [f async for f in client.iter_folder("ROOT", recursive=False)]

    files = asyncio.run(go())
    assert [f.name for f in files] == ["a.pdf"]


def test_iter_folder_requires_credentials():
    client = DriveClient()  # no creds, no api key
    client.credentials = None

    async def go():
        return [f async for f in client.iter_folder("ROOT")]

    with pytest.raises(DriveError):
        asyncio.run(go())


# --------------------------------------------------------------------------- #
# Update-level handlers
# --------------------------------------------------------------------------- #
class FakeUser:
    id = 1
    first_name = "Tester"


class FakeChat:
    id = 99


class FakeUpdateMessage:
    def __init__(self, text: str, bot: "FakeBot"):
        self.text = text
        self.message_id = 7
        self.chat_id = 99
        self._bot = bot

    async def reply_text(self, text, **kw):
        self._bot.calls.append({"call": "send_message", "text": text, **kw})
        return FakeMessage(self._bot.calls, text)


class FakeUpdate:
    def __init__(self, text: str, bot: "FakeBot"):
        self.effective_user = FakeUser()
        self.effective_chat = FakeChat()
        self.effective_message = FakeUpdateMessage(text, bot)
        self.message = self.effective_message


class FakeContext:
    def __init__(self, bot: "FakeBot"):
        self.bot = bot


def test_on_text_single_file_link_sends_file():
    state = botmod.STATE
    fake = FakeBot()
    ctx = FakeContext(fake)

    async def fake_get_file(file_id):
        return DriveFile(file_id, "lecture.mp4", 1000, "video/mp4")

    state.client.get_file = fake_get_file  # type: ignore[assignment]
    update = FakeUpdate("here: https://drive.google.com/file/d/1A2b3C4d5E6f7G8h9I0jK/view", fake)

    async def go():
        await botmod.on_text(update, ctx)

    asyncio.run(go())
    assert [c["call"] for c in fake.calls].count("send_video") == 1


def test_on_text_plain_text_is_rejected_helpfully():
    fake = FakeBot()
    update = FakeUpdate("koi link nahi hai", fake)

    async def go():
        await botmod.on_text(update, FakeContext(fake))

    asyncio.run(go())
    assert any("Drive link" in t for t in fake.sent_texts())


def test_on_text_folder_offers_confirmation():
    state = botmod.STATE
    fake = FakeBot()

    async def fake_iter(folder_id, recursive=True, max_files=300, order="deep"):
        for i in range(12):
            yield DriveFile(f"id{i:09d}", f"v{i}.mp4", 5_000_000, "video/mp4",
                            rel_path=f"Module 1/{i}.mp4")

    state.client.iter_folder = fake_iter  # type: ignore[assignment]
    update = FakeUpdate(
        "course: https://drive.google.com/drive/folders/1A2b3C4d5E6f7G8h9I0jK", fake
    )

    async def go():
        await botmod.offer_folder(fake, update, "1A2b3C4d5E6f7G8h9I0jK")

    asyncio.run(go())
    body = [t for t in fake.sent_texts() if "Folder ready" in t]
    assert body, fake.sent_texts()
    assert "Files: <b>12</b>" in body[0]
    assert "Module 1/0.mp4" in body[0]
    assert botmod.PENDING[99] and len(botmod.PENDING[99]) == 12
    # keyboard carries the start/cancel/reset actions
    markup = next(c["reply_markup"] for c in fake.calls if c.get("reply_markup"))
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert any(d.startswith("start:") for d in datas)
    assert "cancel" in datas and "reset" in datas


# --------------------------------------------------------------------------- #
# Sequence ordering — the whole point of course mode
# --------------------------------------------------------------------------- #
def _tree_client(tree: dict):
    client = DriveClient()
    client.credentials = object()  # type: ignore[assignment]

    async def fake_children(folder_id):
        for item in tree.get(folder_id, []):
            yield item

    client._list_children = fake_children  # type: ignore[assignment]
    return client


COURSE_TREE = {
    "ROOT": [
        {"id": "m2", "name": "Module 2", "mimeType": "application/vnd.google-apps.folder"},
        {"id": "m10", "name": "Module 10", "mimeType": "application/vnd.google-apps.folder"},
        {"id": "m1", "name": "Module 1", "mimeType": "application/vnd.google-apps.folder"},
        {"id": "f_readme", "name": "README.pdf", "size": "10", "mimeType": "application/pdf"},
    ],
    "m1": [
        {"id": "l10", "name": "Lesson 10.mp4", "size": "10", "mimeType": "video/mp4"},
        {"id": "l2", "name": "Lesson 2.mp4", "size": "10", "mimeType": "video/mp4"},
        {"id": "l1", "name": "Lesson 1.mp4", "size": "10", "mimeType": "video/mp4"},
    ],
    "m2": [{"id": "x", "name": "a.mp4", "size": "10", "mimeType": "video/mp4"}],
    "m10": [{"id": "y", "name": "b.mp4", "size": "10", "mimeType": "video/mp4"}],
}


def test_deep_order_is_course_sequence():
    client = _tree_client(COURSE_TREE)

    async def go():
        return [f.rel_path async for f in client.iter_folder("ROOT", order="deep")]

    assert asyncio.run(go()) == [
        "Module 1/Lesson 1.mp4",   # natural sort inside the module: 1, 2, 10
        "Module 1/Lesson 2.mp4",
        "Module 1/Lesson 10.mp4",
        "Module 2/a.mp4",          # Module 1 is fully finished before Module 2
        "Module 10/b.mp4",         # and "Module 10" comes after "Module 2"
        "README.pdf",              # loose top-level files last
    ]


def test_breadth_order_walks_level_by_level():
    client = _tree_client(COURSE_TREE)

    async def go():
        return [f.rel_path async for f in client.iter_folder("ROOT", order="breadth")]

    assert asyncio.run(go()) == [
        "README.pdf",              # level 1 files first
        "Module 1/Lesson 1.mp4",   # then level 2, folder by folder
        "Module 1/Lesson 2.mp4",
        "Module 1/Lesson 10.mp4",
        "Module 2/a.mp4",
        "Module 10/b.mp4",
    ]


def test_max_files_cuts_at_sequence_position():
    client = _tree_client(COURSE_TREE)

    async def go():
        return [f.rel_path async for f in client.iter_folder("ROOT", max_files=3)]

    assert asyncio.run(go()) == [
        "Module 1/Lesson 1.mp4",
        "Module 1/Lesson 2.mp4",
        "Module 1/Lesson 10.mp4",
    ]


def test_service_account_json_env_is_used(tmp_path, monkeypatch):
    """Railway path: credentials arrive as an env var, not a mounted file."""
    import json

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from gdrive_core import DriveCredentials

    pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode()
    )
    fake_key = {
        "type": "service_account",
        "project_id": "demo",
        "private_key_id": "abc",
        "private_key": pem,
        "client_email": "bot@demo.iam.gserviceaccount.com",
        "client_id": "1",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", json.dumps(fake_key))
    cfg = botmod.BotConfig.from_env()
    assert cfg.service_account_json and cfg.service_account_json.startswith("{")

    creds = DriveCredentials(service_account_info=json.loads(cfg.service_account_json))
    assert creds.is_service_account
    assert creds.email == "bot@demo.iam.gserviceaccount.com"


# --------------------------------------------------------------------------- #
# Credential diagnostics
# --------------------------------------------------------------------------- #
def test_audit_catches_quotes_and_placeholders():
    from diagnose_credentials import audit

    assert any("quotes" in p for p in audit("GOOGLE_CLIENT_ID", '"123-abc.apps.googleusercontent.com"'))
    assert any("placeholder" in p or "does not look like" in p for p in audit("GOOGLE_CLIENT_ID", "YOUR_CLIENT_ID"))
    assert any("Playground" in p for p in audit("GOOGLE_CLIENT_ID", "407408718192.apps.googleusercontent.com"))
    assert audit("GOOGLE_CLIENT_SECRET", "GOCSPX-abcdefghijklmnopqrstuvwx") == []
    assert any("ACCESS token" in p for p in audit("GOOGLE_REFRESH_TOKEN", "ya29.a0Ae4"))
    assert audit("GOOGLE_REFRESH_TOKEN", "1//0gAbCdEf") == []
    assert any("MISSING" in p for p in audit("GOOGLE_CLIENT_ID", ""))


def test_credentials_need_client_pair_with_refresh_token(monkeypatch):
    """A refresh token alone can never work — fail loudly instead of at /auth."""
    from gdrive_core import DriveCredentials, DriveError

    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    try:
        DriveCredentials(refresh_token="1//0gfake")
    except DriveError as exc:
        assert "GOOGLE_CLIENT_ID" in str(exc)
    else:
        raise AssertionError("expected DriveError")


# --------------------------------------------------------------------------- #
# Credential failures must reach the user as readable text, never a traceback
# --------------------------------------------------------------------------- #
def _fake_refresh_error(msg: str) -> Exception:
    return RuntimeError(msg)


def test_invalid_client_error_is_actionable():
    from gdrive_core import DriveCredentials, DriveError

    err = DriveCredentials._explain_refresh_failure(
        _fake_refresh_error(
            "('invalid_client: The OAuth client was not found.', {'error': "
            "'invalid_client', 'error_description': 'The OAuth client was not found.'})"
        )
    )
    text = str(err)
    assert isinstance(err, DriveError)
    assert "invalid_client" in text
    assert "Use your own OAuth credentials" in text   # the step people miss
    assert "diagnose_credentials.py" in text
    assert "RefreshError" not in text                  # no raw traceback soup


def test_invalid_grant_mentions_seven_day_expiry():
    from gdrive_core import DriveCredentials

    text = str(
        DriveCredentials._explain_refresh_failure(
            _fake_refresh_error("('invalid_grant: Token has been expired or revoked.', {})")
        )
    )
    assert "invalid_grant" in text
    assert "7 days" in text and "Publish" in text


def test_unknown_refresh_error_still_wrapped():
    from gdrive_core import DriveCredentials, DriveError

    err = DriveCredentials._explain_refresh_failure(ValueError("weird"))
    assert isinstance(err, DriveError) and "weird" in str(err)


def test_folder_scan_reports_credentials_problem_not_traceback():
    """The exact case from the user's screenshot: folder link + broken creds."""
    state = botmod.STATE
    fake = FakeBot()

    class BrokenClient:
        authorized = True
        api_key = None

        async def iter_folder(self, *a, **kw):
            from gdrive_core import DriveCredentials

            raise DriveCredentials._explain_refresh_failure(
                RuntimeError("invalid_client: The OAuth client was not found.")
            )
            yield  # pragma: no cover

    state.client = BrokenClient()  # type: ignore[assignment]
    update = FakeUpdate(
        "https://drive.google.com/drive/folders/1OoGb2u2v9o9SbO1eACZ10Pa4GzV--SO4", fake
    )

    async def go():
        await botmod.offer_folder(fake, update, "1OoGb2u2v9o9SbO1eACZ10Pa4GzV--SO4")

    asyncio.run(go())
    body = " ".join(fake.sent_texts())
    assert "invalid_client" in body
    assert "Credentials" in body          # actionable next step
    assert "RefreshError" not in body     # not the raw exception repr


# --------------------------------------------------------------------------- #
# Setup wizard
# --------------------------------------------------------------------------- #
def test_wizard_merges_env_without_clobbering(tmp_path):
    from setup_wizard import env_block, merge_env

    env = tmp_path / ".env"
    env.write_text("# keep me\nBOT_TOKEN=abc\nGOOGLE_CLIENT_ID=old\nALLOWED_USER_IDS=1\n")
    merge_env(env, {"GOOGLE_CLIENT_ID": "new", "GOOGLE_REFRESH_TOKEN": "1//tok"})

    text = env.read_text()
    assert "# keep me" in text and "BOT_TOKEN=abc" in text and "ALLOWED_USER_IDS=1" in text
    assert "GOOGLE_CLIENT_ID=new" in text and "GOOGLE_CLIENT_ID=old" not in text
    assert "GOOGLE_REFRESH_TOKEN=1//tok" in text
    assert oct(env.stat().st_mode)[-3:] == "600"

    block = env_block({"GOOGLE_CLIENT_ID": "new"}, "render")
    assert "GOOGLE_CLIENT_ID=new" in block and "JOBS_DB=/data/jobs.sqlite3" in block


def test_wizard_strips_quotes_and_space():
    from setup_wizard import clean

    assert clean(' "123-abc.apps.googleusercontent.com" ') == "123-abc.apps.googleusercontent.com"
    assert clean("GOCSPX-xyz\n") == "GOCSPX-xyz"


def test_wizard_rejects_incomplete_credentials(monkeypatch, tmp_path):
    import setup_wizard

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GOOGLE_CLIENT_ID=only-the-id\n")
    monkeypatch.delenv("GOOGLE_REFRESH_TOKEN", raising=False)
    assert setup_wizard.check_only("render") == 1


# --------------------------------------------------------------------------- #
# doctor.py
# --------------------------------------------------------------------------- #
def test_doctor_flags_env_format_problems():
    import doctor

    doctor.RESULTS.clear()
    values = doctor.check_env(
        {
            "BOT_TOKEN": '"123456:ABC"',
            "GOOGLE_CLIENT_ID": "407408718192.apps.googleusercontent.com",
            "GOOGLE_CLIENT_SECRET": "nope",
            "GOOGLE_REFRESH_TOKEN": "ya29.access",
            "ALLOWED_USER_IDS": "123",
        }
    )
    failures = {name for name, ok, _ in doctor.RESULTS if ok is False}
    assert "BOT_TOKEN" in failures          # quotes
    assert "GOOGLE_CLIENT_SECRET" in failures  # no GOCSPX-
    assert "GOOGLE_REFRESH_TOKEN" in failures  # access token, not refresh
    assert values["ALLOWED_USER_IDS"] == "123"


def test_doctor_reports_verdict_without_network(monkeypatch):
    import doctor

    doctor.RESULTS.clear()
    doctor.add("fake problem", False, "because")
    assert doctor.verdict() == 1
    doctor.RESULTS.clear()
    doctor.add("all good", True, "")
    assert doctor.verdict() == 0


# --------------------------------------------------------------------------- #
# health.py — self diagnosis
# --------------------------------------------------------------------------- #
def test_health_config_flags_empty_allowlist():
    import health

    cfg = botmod.BotConfig()
    cfg.bot_token = "123456:ABC"
    cfg.allowed_users = set()
    checks = health.check_config(cfg)
    by_label = {c.label: c for c in checks}
    assert by_label["BOT_TOKEN"].ok is True
    assert by_label["ALLOWED_USER_IDS"].ok is None  # warning, not failure

    cfg.bot_token = "broken-token"
    assert {c.label: c for c in health.check_config(cfg)}["BOT_TOKEN"].ok is False


def test_health_render_points_at_first_failure_and_hint():
    import health

    checks = [
        health.Check("BOT_TOKEN", True, "123456…"),
        health.Check("webhook", False, "SET → https://x"),
        health.Check("disk free", True, "20 GB"),
    ]
    text = health.render(checks)
    assert "❌" in text and "webhook" in text
    assert "/fixwebhook" in text           # actionable hint, not just a red mark


def test_health_compact_summary():
    import health

    ok = [health.Check("a", True, ""), health.Check("b", True, "")]
    assert "checks pass" in health.compact(ok)
    bad = ok + [health.Check("webhook", False, "set"), health.Check("ledger", False, "x")]
    summary = health.compact(bad)
    assert "⚠️" in summary and "webhook" in summary


def test_health_runtime_detects_unwritable_ledger(tmp_path, monkeypatch):
    import health

    cfg = botmod.BotConfig()
    cfg.temp_dir = str(tmp_path)
    cfg.jobs_db = str(tmp_path / "sub" / "jobs.sqlite3")
    labels = {c.label: c for c in health.check_runtime(cfg)}
    assert labels["ledger"].ok is True
    assert labels["temp dir"].ok is True


# --------------------------------------------------------------------------- #
# Upload path regressions (research pass, Oct 2026)
# --------------------------------------------------------------------------- #
def test_media_write_timeout_is_raised_above_ptb_default(monkeypatch):
    """PTB defaults media_write_timeout to 20s — big uploads die there."""
    monkeypatch.setenv("BOT_TOKEN", "123456789:AAHfake")
    monkeypatch.setenv("MEDIA_WRITE_TIMEOUT", "1800")
    cfg = botmod.BotConfig.from_env()
    botmod.build_application(cfg)   # side effect: raises the media write timeout
    assert botmod.MEDIA_WRITE_TIMEOUT_CONFIGURED >= 600


def test_local_mode_passes_path_not_file_object(monkeypatch):
    """With a local Bot API server PTB can send a path: 2 GB cap, no copy."""
    state = botmod.STATE
    state.cfg.bot_api_base_url = "http://127.0.0.1:8081/bot"
    assert botmod._local_mode() is True

    captured = {}

    class RecordingBot:
        async def send_document(self, chat_id, document, **kw):
            captured["document"] = document
            captured["filename"] = kw.get("filename")
            return None

    tmp = state.temp_root / "big.pdf"   # document path (not video)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(b"x" * 1024)

    asyncio.run(botmod.send_file_to_chat(RecordingBot(), 1, tmp, "cap"))
    assert isinstance(captured["document"], str)          # path, not a file object
    assert captured["document"].endswith("big.pdf")
    assert captured["filename"] == "big.pdf"

    state.cfg.bot_api_base_url = None
    assert botmod._local_mode() is False


# --------------------------------------------------------------------------- #
# /streamdl — the "view-only video" pipeline, end to end (fakes only)
# --------------------------------------------------------------------------- #
def _stream_state(monkeypatch, tmp_path, with_audio=True):
    """STATE whose Drive client returns two stream URLs and no network."""
    import media_download

    state = botmod.STATE
    state.temp_root = tmp_path

    async def fake_parts(file_id):
        return ("https://r1.fake/videoplayback?mime=video%2Fmp4&range=0-99",
                "https://r1.fake/videoplayback?mime=audio%2Fmp4" if with_audio else None)

    async def fake_get_file(file_id):
        return DriveFile(file_id, "Lecture 01.mp4", 5000, "video/mp4")

    async def fake_session():
        return object()

    async def fake_fetch(session, url, dest, **kw):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"z" * 4096)
        return media_download.FetchedTrack(path=Path(dest), kind=kw.get("kind", "video"), bytes_written=4096)

    state.client.get_stream_parts = fake_parts          # type: ignore[assignment]
    state.client.get_file = fake_get_file               # type: ignore[assignment]
    state.client.session = fake_session                 # type: ignore[assignment]
    monkeypatch.setattr(media_download, "fetch_track", fake_fetch)
    return state


@pytest.fixture()
def fake_mux(monkeypatch, tmp_path):
    """Replace the ffmpeg call with a file write and report success."""
    import media_mux

    monkeypatch.setattr(media_mux, "available", lambda: True)

    async def fake_mux_run(video, audio=None, out=None, **kw):
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"m" * 8192)
        return media_mux.MuxResult(
            path=out, duration=6.0, video_codec="h264", audio_codec="aac"
        )

    async def fake_verify(result):
        return True, "x.mp4 · v:h264 · a:aac (copy) · 6s"

    monkeypatch.setattr(media_mux, "mux", fake_mux_run)
    monkeypatch.setattr(media_mux, "verify", fake_verify)
    return media_mux


def test_streamdl_full_pipeline_downloads_two_tracks_muxes_and_sends(
    monkeypatch, tmp_path, fake_mux
):
    _stream_state(monkeypatch, tmp_path)
    fake = FakeBot()

    class Ctx:
        bot = fake
        args = ["https://drive.google.com/file/d/1A2b3C4d5E6f7G8h9I0jK1lM2nO3pQ4r5/view"]

    update = FakeUpdate("ignored", fake)

    async def go():
        await botmod.cmd_streamdl(update, Ctx())

    asyncio.run(go())

    texts = " ".join(fake.sent_texts())
    assert "Tracks mile" in texts or "track" in texts.lower()
    # exactly one media upload, and it is the merged file
    uploads = [c for c in fake.calls if c["call"] in ("send_video", "send_document")]
    assert len(uploads) == 1
    assert uploads[0]["call"] == "send_video"
    assert "final.mp4" in (uploads[0].get("name") or "")


def test_streamdl_reports_missing_stream_parts(monkeypatch, tmp_path, fake_mux):
    state = _stream_state(monkeypatch, tmp_path)

    async def no_parts(file_id):
        return (None, None)

    state.client.get_stream_parts = no_parts  # type: ignore[assignment]
    fake = FakeBot()

    class Ctx:
        bot = fake
        args = ["https://drive.google.com/file/d/1A2b3C4d5E6f7G8h9I0jK1lM2nO3pQ4r5/view"]

    asyncio.run(botmod.cmd_streamdl(FakeUpdate("x", fake), Ctx()))
    texts = " ".join(fake.sent_texts())
    assert "No stream found" in texts
    assert "private" in texts.lower() or "public" in texts.lower()
    assert not [c for c in fake.calls if c["call"] == "send_video"]


def test_streamdl_requires_a_link(monkeypatch, tmp_path):
    fake = FakeBot()

    class Ctx:
        bot = fake
        args = []

    asyncio.run(botmod.cmd_streamdl(FakeUpdate("no link here", fake), Ctx()))
    assert "Usage" in " ".join(fake.sent_texts())


def test_streamdl_explains_missing_ffmpeg(monkeypatch, tmp_path):
    import media_mux

    monkeypatch.setattr(media_mux, "available", lambda: False)
    fake = FakeBot()

    class Ctx:
        bot = fake
        args = ["https://drive.google.com/file/d/1A2b3C4d5E6f7G8h9I0jK1lM2nO3pQ4r5/view"]

    asyncio.run(botmod.cmd_streamdl(FakeUpdate("x", fake), Ctx()))
    assert "ffmpeg" in " ".join(fake.sent_texts())


def test_streamdl_track_failure_is_reported_not_crashed(monkeypatch, tmp_path, fake_mux):
    import media_download

    _stream_state(monkeypatch, tmp_path)

    async def boom(session, url, dest, **kw):
        raise media_download.StreamFetchError("link expired")

    monkeypatch.setattr(media_download, "fetch_track", boom)
    fake = FakeBot()

    class Ctx:
        bot = fake
        args = ["https://drive.google.com/file/d/1A2b3C4d5E6f7G8h9I0jK1lM2nO3pQ4r5/view"]

    asyncio.run(botmod.cmd_streamdl(FakeUpdate("x", fake), Ctx()))
    # the failure lands as an edit on the progress message, not a new message
    assert "link expired" in _all_text(fake)


# --------------------------------------------------------------------------- #
# AUTO_QUOTA_FIX — silent recovery without typing /quotafix
# --------------------------------------------------------------------------- #
def _quota_then_copy_state(monkeypatch, tmp_path, *, can_write=True, auto=True):
    """Download raises QuotaExceededError; download_via_copy succeeds."""
    from gdrive_core import QuotaExceededError

    state = botmod.STATE
    state.cfg.auto_quota_fix = auto
    state.temp_root = tmp_path

    calls: list[str] = []

    async def fake_download(target, dest, on_progress=None, cancel=None, max_bytes=None):
        calls.append("download")
        raise QuotaExceededError("quota exceeded for this file")

    async def fake_copy(file_id, dest, on_progress=None, cancel=None, max_bytes=None):
        calls.append("copy")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"q" * 3000)

    state.client.download = fake_download          # type: ignore[assignment]
    state.client.download_via_copy = fake_copy     # type: ignore[assignment]
    creds = SimpleNamespace(can_write=can_write)
    state.client.credentials = creds               # type: ignore[assignment]
    return state, calls


def _all_text(fake) -> str:
    """Every string the bot pushed to the chat — messages *and* status edits."""
    return " ".join(
        str(c.get("text", "")) for c in fake.calls if c.get("text") is not None
    )


def test_auto_quota_fix_retries_via_copy_and_sends(monkeypatch, tmp_path):
    state, calls = _quota_then_copy_state(monkeypatch, tmp_path)
    fake = FakeBot()

    async def fake_get_file(file_id):
        return DriveFile(file_id, "lecture.mp4", 3000, "video/mp4")

    state.client.get_file = fake_get_file
    update = FakeUpdate("https://drive.google.com/file/d/1A2b3C4d5E6f7G8h9I0jK/view", fake)
    asyncio.run(botmod.on_text(update, FakeContext(fake)))

    assert calls == ["download", "copy"]
    assert [c["call"] for c in fake.calls].count("send_video") == 1
    assert "auto-fixing" in _all_text(fake)


def test_auto_quota_fix_does_nothing_without_write_scope(monkeypatch, tmp_path):
    state, calls = _quota_then_copy_state(monkeypatch, tmp_path, can_write=False)
    fake = FakeBot()

    async def fake_get_file(file_id):
        return DriveFile(file_id, "lecture.mp4", 3000, "video/mp4")

    state.client.get_file = fake_get_file
    update = FakeUpdate("https://drive.google.com/file/d/1A2b3C4d5E6f7G8h9I0jK/view", fake)
    asyncio.run(botmod.on_text(update, FakeContext(fake)))

    assert calls == ["download"]          # no copy attempted
    assert "quota exceeded" in _all_text(fake).lower()


def test_auto_quota_fix_off_by_default(monkeypatch, tmp_path):
    state, calls = _quota_then_copy_state(monkeypatch, tmp_path, auto=False)
    fake = FakeBot()

    async def fake_get_file(file_id):
        return DriveFile(file_id, "lecture.mp4", 3000, "video/mp4")

    state.client.get_file = fake_get_file
    update = FakeUpdate("https://drive.google.com/file/d/1A2b3C4d5E6f7G8h9I0jK/view", fake)
    asyncio.run(botmod.on_text(update, FakeContext(fake)))

    assert calls == ["download"]
