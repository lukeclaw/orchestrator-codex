"""Tests for /tmp recovery: SOT deploy functions, manifest I/O, and health checks."""

import json
import os

# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------


class TestManifestIO:
    def test_write_and_read_manifest(self, tmp_path):
        from orchestrator.agents.deploy import _read_manifest, _write_manifest

        paths = ["bin/lib.sh", "configs/settings.json", "prompt.md"]
        _write_manifest(str(tmp_path), paths)

        result = _read_manifest(str(tmp_path))
        assert result is not None
        assert set(result) == set(paths)

    def test_read_manifest_missing(self, tmp_path):
        from orchestrator.agents.deploy import _read_manifest

        result = _read_manifest(str(tmp_path))
        assert result is None

    def test_read_manifest_corrupt(self, tmp_path):
        from orchestrator.agents.deploy import MANIFEST_FILENAME, _read_manifest

        (tmp_path / MANIFEST_FILENAME).write_text("not json {{{")
        result = _read_manifest(str(tmp_path))
        assert result is None

    def test_read_manifest_wrong_schema(self, tmp_path):
        from orchestrator.agents.deploy import MANIFEST_FILENAME, _read_manifest

        (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(["just", "a", "list"]))
        result = _read_manifest(str(tmp_path))
        assert result is None

    def test_manifest_deduplicates(self, tmp_path):
        from orchestrator.agents.deploy import _read_manifest, _write_manifest

        _write_manifest(str(tmp_path), ["a.txt", "a.txt", "b.txt"])
        result = _read_manifest(str(tmp_path))
        assert result == ["a.txt", "b.txt"]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


class TestDBHelpers:
    def test_get_custom_skills_from_db(self, db):
        from orchestrator.agents.deploy import _get_custom_skills_from_db
        from orchestrator.state.repositories.skills import create_skill

        create_skill(db, name="my-skill", target="worker", description="test", content="body")
        result = _get_custom_skills_from_db(db, "worker")
        assert len(result) == 1
        assert result[0]["name"] == "my-skill"
        assert result[0]["content"] == "body"

    def test_get_custom_skills_empty(self, db):
        from orchestrator.agents.deploy import _get_custom_skills_from_db

        result = _get_custom_skills_from_db(db, "worker")
        assert result == []

    def test_get_custom_skills_filters_by_target(self, db):
        from orchestrator.agents.deploy import _get_custom_skills_from_db
        from orchestrator.state.repositories.skills import create_skill

        create_skill(db, name="brain-skill", target="brain", description="b", content="c")
        result = _get_custom_skills_from_db(db, "worker")
        assert result == []

    def test_get_disabled_builtins_from_db(self, db):
        from orchestrator.agents.deploy import _get_disabled_builtins_from_db
        from orchestrator.state.repositories.skills import set_builtin_skill_enabled

        set_builtin_skill_enabled(db, "deploy", "worker", False)
        set_builtin_skill_enabled(db, "check-worker", "worker", False)
        result = _get_disabled_builtins_from_db(db, "worker")
        assert "deploy" in result
        assert "check-worker" in result

    def test_get_disabled_builtins_empty(self, db):
        from orchestrator.agents.deploy import _get_disabled_builtins_from_db

        result = _get_disabled_builtins_from_db(db, "worker")
        assert result == set()

    def test_get_brain_memory_section_filters_by_provider(self, db):
        from orchestrator.agents.deploy import get_brain_memory_section
        from orchestrator.state.repositories.context import create_context_item

        create_context_item(
            db,
            title="Shared Wisdom",
            content="Shared lesson",
            scope="brain",
            category="wisdom",
        )
        create_context_item(
            db,
            title="Claude Wisdom",
            content="Claude lesson",
            scope="brain",
            category="wisdom",
            provider="claude",
        )
        create_context_item(
            db,
            title="Codex Wisdom",
            content="Codex lesson",
            scope="brain",
            category="wisdom",
            provider="codex",
        )

        codex_section = get_brain_memory_section(db, provider="codex")
        assert "Codex lesson" in codex_section
        assert "Claude lesson" not in codex_section

        claude_section = get_brain_memory_section(db, provider="claude")
        assert "Claude lesson" in claude_section
        assert "Codex lesson" not in claude_section


# ---------------------------------------------------------------------------
# _deploy_builtin_skills
# ---------------------------------------------------------------------------


class TestDeployBuiltinSkills:
    def test_copies_md_files(self, tmp_path):
        from orchestrator.agents.deploy import _deploy_builtin_skills

        src = tmp_path / "src"
        src.mkdir()
        (src / "deploy.md").write_text("# Deploy")
        (src / "check.md").write_text("# Check")
        (src / "readme.txt").write_text("not a skill")

        dest = tmp_path / "dest"
        copied = _deploy_builtin_skills(str(src), str(dest))
        assert sorted(copied) == ["check.md", "deploy.md"]
        assert (dest / "deploy.md").read_text() == "# Deploy"
        assert not (dest / "readme.txt").exists()

    def test_skips_disabled(self, tmp_path):
        from orchestrator.agents.deploy import _deploy_builtin_skills

        src = tmp_path / "src"
        src.mkdir()
        (src / "deploy.md").write_text("# Deploy")
        (src / "check.md").write_text("# Check")

        dest = tmp_path / "dest"
        copied = _deploy_builtin_skills(str(src), str(dest), disabled_names={"deploy"})
        assert copied == ["check.md"]
        assert not (dest / "deploy.md").exists()

    def test_clears_stale_files(self, tmp_path):
        from orchestrator.agents.deploy import _deploy_builtin_skills

        src = tmp_path / "src"
        src.mkdir()
        (src / "new.md").write_text("# New")

        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "old.md").write_text("# Old stale skill")

        _deploy_builtin_skills(str(src), str(dest))
        assert not (dest / "old.md").exists()
        assert (dest / "new.md").exists()

    def test_handles_none_src(self, tmp_path):
        from orchestrator.agents.deploy import _deploy_builtin_skills

        dest = tmp_path / "dest"
        result = _deploy_builtin_skills(None, str(dest))
        assert result == []


# ---------------------------------------------------------------------------
# SOT deploy functions (deploy_worker_tmp_contents, deploy_brain_tmp_contents)
# ---------------------------------------------------------------------------


class TestDeployWorkerTmpContents:
    def test_creates_all_expected_files(self, tmp_path):
        from orchestrator.agents.deploy import (
            MANIFEST_FILENAME,
            _read_manifest,
            deploy_worker_tmp_contents,
        )

        tmp_dir = str(tmp_path / "worker1")
        created = deploy_worker_tmp_contents(
            tmp_dir,
            session_id="test-session-id",
            api_base="http://127.0.0.1:8093",
        )
        assert len(created) > 0

        # Manifest should be written
        manifest = _read_manifest(tmp_dir)
        assert manifest is not None
        assert len(manifest) == len(set(created))

        # Key files should exist
        assert os.path.exists(os.path.join(tmp_dir, "configs", "settings.json"))
        assert os.path.exists(os.path.join(tmp_dir, "bin", "lib.sh"))
        assert os.path.isfile(os.path.join(tmp_dir, MANIFEST_FILENAME))

    def test_prompt_md_created(self, tmp_path):
        from orchestrator.agents.deploy import deploy_worker_tmp_contents

        tmp_dir = str(tmp_path / "worker2")
        created = deploy_worker_tmp_contents(tmp_dir, session_id="sid-123")

        prompt_path = os.path.join(tmp_dir, "prompt.md")
        if os.path.exists(prompt_path):
            assert "prompt.md" in created
            content = open(prompt_path).read()
            assert "sid-123" in content

    def test_with_custom_skills(self, tmp_path):
        from orchestrator.agents.deploy import deploy_worker_tmp_contents

        tmp_dir = str(tmp_path / "worker3")
        skills = [{"name": "my-tool", "description": "test", "content": "body"}]
        created = deploy_worker_tmp_contents(tmp_dir, session_id="sid", custom_skills=skills)
        assert ".claude/commands/my-tool.md" in created
        assert os.path.exists(os.path.join(tmp_dir, ".claude", "commands", "my-tool.md"))

    def test_with_disabled_builtins(self, tmp_path):
        from orchestrator.agents.deploy import deploy_worker_tmp_contents

        tmp_dir = str(tmp_path / "worker4")
        deploy_worker_tmp_contents(
            tmp_dir,
            session_id="sid",
            disabled_builtin_names={"deploy"},
        )
        commands_dir = os.path.join(tmp_dir, ".claude", "commands")
        if os.path.isdir(commands_dir):
            files = os.listdir(commands_dir)
            assert "deploy.md" not in files

    def test_with_db_conn(self, db, tmp_path):
        from orchestrator.agents.deploy import deploy_worker_tmp_contents
        from orchestrator.state.repositories.skills import create_skill

        create_skill(db, name="db-skill", target="worker", description="d", content="c")
        tmp_dir = str(tmp_path / "worker5")
        created = deploy_worker_tmp_contents(tmp_dir, session_id="sid", conn=db)
        assert ".claude/commands/db-skill.md" in created

    def test_idempotent(self, tmp_path):
        """Calling deploy twice should produce same result."""
        from orchestrator.agents.deploy import _read_manifest, deploy_worker_tmp_contents

        tmp_dir = str(tmp_path / "worker6")
        created1 = deploy_worker_tmp_contents(tmp_dir, session_id="sid")
        created2 = deploy_worker_tmp_contents(tmp_dir, session_id="sid")
        assert sorted(created1) == sorted(created2)
        manifest = _read_manifest(tmp_dir)
        assert manifest is not None


class TestDeployCodexWorkerTmpContents:
    def test_creates_codex_worker_files(self, tmp_path):
        from orchestrator.agents.deploy import _read_manifest, deploy_codex_worker_tmp_contents

        tmp_dir = str(tmp_path / "codex-worker")
        created = deploy_codex_worker_tmp_contents(tmp_dir, session_id="sid-123")

        assert "prompt.md" in created
        assert "bin/lib.sh" in created
        assert not os.path.exists(os.path.join(tmp_dir, "configs", "settings.json"))
        manifest = _read_manifest(tmp_dir)
        assert manifest is not None


class TestDeployBrainTmpContents:
    def test_creates_all_expected_files(self, tmp_path):
        from orchestrator.agents.deploy import _read_manifest, deploy_brain_tmp_contents

        brain_dir = str(tmp_path / "brain")
        created = deploy_brain_tmp_contents(brain_dir)
        assert len(created) > 0

        manifest = _read_manifest(brain_dir)
        assert manifest is not None

        assert os.path.exists(os.path.join(brain_dir, "bin", "lib.sh"))
        assert os.path.exists(os.path.join(brain_dir, ".claude", "settings.json"))

    def test_claude_md_created(self, tmp_path):
        from orchestrator.agents.deploy import deploy_brain_tmp_contents

        brain_dir = str(tmp_path / "brain2")
        created = deploy_brain_tmp_contents(brain_dir)
        claude_md = os.path.join(brain_dir, "CLAUDE.md")
        if os.path.exists(claude_md):
            assert "CLAUDE.md" in created

    def test_with_db_conn(self, db, tmp_path):
        from orchestrator.agents.deploy import deploy_brain_tmp_contents
        from orchestrator.state.repositories.context import create_context_item
        from orchestrator.state.repositories.skills import create_skill

        create_skill(db, name="brain-skill", target="brain", description="d", content="c")
        create_context_item(
            db,
            title="Codex Wisdom",
            content="Use Codex-specific guidance",
            scope="brain",
            category="wisdom",
            provider="codex",
        )
        brain_dir = str(tmp_path / "brain3")
        created = deploy_brain_tmp_contents(brain_dir, conn=db, provider="codex")
        assert ".claude/commands/brain-skill.md" in created
        assert "Use Codex-specific guidance" in open(os.path.join(brain_dir, "CLAUDE.md")).read()


class TestDeployCodexBrainTmpContents:
    def test_creates_codex_brain_files(self, db, tmp_path):
        from orchestrator.agents.deploy import _read_manifest, deploy_codex_brain_tmp_contents
        from orchestrator.state.repositories.context import create_context_item

        create_context_item(
            db,
            title="Codex Wisdom",
            content="Codex memory",
            scope="brain",
            category="wisdom",
            provider="codex",
        )

        brain_dir = str(tmp_path / "codex-brain")
        created = deploy_codex_brain_tmp_contents(brain_dir, conn=db, provider="codex")

        assert "prompt.md" in created
        assert "bin/lib.sh" in created
        assert not os.path.exists(os.path.join(brain_dir, ".claude", "settings.json"))
        assert "Codex memory" in open(os.path.join(brain_dir, "prompt.md")).read()
        manifest = _read_manifest(brain_dir)
        assert manifest is not None


# ---------------------------------------------------------------------------
# Health check functions
# ---------------------------------------------------------------------------


class TestEnsureTmpDirHealth:
    def test_healthy_dir(self, tmp_path):
        from orchestrator.agents.deploy import deploy_worker_tmp_contents
        from orchestrator.session.health import ensure_tmp_dir_health

        tmp_dir = str(tmp_path / "healthy")
        deploy_worker_tmp_contents(tmp_dir, session_id="sid")

        result = ensure_tmp_dir_health(tmp_dir, session_id="sid")
        assert result["healthy"] is True
        assert result["regenerated"] is False
        assert result["missing"] == []

    def test_missing_manifest(self, tmp_path):
        from orchestrator.session.health import ensure_tmp_dir_health

        tmp_dir = str(tmp_path / "empty")
        os.makedirs(tmp_dir)

        result = ensure_tmp_dir_health(tmp_dir, session_id="sid")
        assert result["healthy"] is False
        assert result["regenerated"] is True

    def test_missing_files(self, tmp_path):
        from orchestrator.agents.deploy import deploy_worker_tmp_contents
        from orchestrator.session.health import ensure_tmp_dir_health

        tmp_dir = str(tmp_path / "partial")
        deploy_worker_tmp_contents(tmp_dir, session_id="sid")

        # Delete a key file
        settings = os.path.join(tmp_dir, "configs", "settings.json")
        if os.path.exists(settings):
            os.remove(settings)

        result = ensure_tmp_dir_health(tmp_dir, session_id="sid")
        assert result["healthy"] is False
        assert result["regenerated"] is True
        assert "configs/settings.json" in result["missing"]

        # After regeneration, file should exist again
        assert os.path.exists(settings)

    def test_codex_health_uses_codex_manifest(self, tmp_path):
        from orchestrator.agents.deploy import deploy_codex_worker_tmp_contents
        from orchestrator.session.health import ensure_tmp_dir_health

        tmp_dir = str(tmp_path / "codex-worker")
        deploy_codex_worker_tmp_contents(tmp_dir, session_id="sid")

        result = ensure_tmp_dir_health(tmp_dir, session_id="sid", provider="codex")
        assert result["healthy"] is True
        assert result["regenerated"] is False


class TestEnsureBrainTmpHealth:
    def test_healthy_brain(self, tmp_path):
        from orchestrator.agents.deploy import deploy_brain_tmp_contents
        from orchestrator.session.health import ensure_brain_tmp_health

        brain_dir = str(tmp_path / "brain")
        deploy_brain_tmp_contents(brain_dir)

        result = ensure_brain_tmp_health(brain_dir)
        assert result["healthy"] is True
        assert result["regenerated"] is False

    def test_wiped_brain(self, tmp_path):
        from orchestrator.session.health import ensure_brain_tmp_health

        brain_dir = str(tmp_path / "brain_wiped")
        os.makedirs(brain_dir)

        result = ensure_brain_tmp_health(brain_dir)
        assert result["healthy"] is False
        assert result["regenerated"] is True

    def test_codex_brain_health_uses_codex_manifest(self, db, tmp_path):
        from orchestrator.agents.deploy import deploy_codex_brain_tmp_contents
        from orchestrator.session.health import ensure_brain_tmp_health

        brain_dir = str(tmp_path / "codex-brain")
        deploy_codex_brain_tmp_contents(brain_dir, conn=db, provider="codex")

        result = ensure_brain_tmp_health(brain_dir, conn=db, provider="codex")
        assert result["healthy"] is True
        assert result["regenerated"] is False
