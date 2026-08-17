import base64
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from conftest import make_auth0_payload
from models import Structure, Tags


def _structure_file(filename="input.xyz", content=b"2\n\nH 0 0 0\nH 0 0 1\n"):
    return {"file": (filename, content, "chemical/x-xyz")}


def _structure_upload_files(
    filename="input.xyz",
    content=b"2\n\nH 0 0 0\nH 0 0 1\n",
    image_content=b"image-bytes",
):
    return {
        "file": (filename, content, "chemical/x-xyz"),
        "image": ("structure.png", image_content, "image/png"),
    }


def test_openapi_documents_structure_tag_update_mode(client):
    """Swagger should document additive and replacement tag updates."""
    schema = client.get("/openapi.json").json()
    request_schema = schema["paths"]["/structures/{structure_id}"]["patch"][
        "requestBody"
    ]["content"]["application/x-www-form-urlencoded"]["schema"]
    schema_name = request_schema["$ref"].rsplit("/", 1)[-1]
    properties = schema["components"]["schemas"][schema_name]["properties"]

    assert properties["replace_tags"]["default"] is False
    assert properties["tags"]["default"] == []


class TestStructuresAPI:
    def test_list_structures_returns_current_users_non_deleted_structures_newest_first(
        self,
        client,
        sql_statements,
        group_factory,
        user_factory,
        tag_factory,
        structure_factory,
    ):
        """
        GET /structures/ should only return current user's non-deleted structures newest first.
        """
        group = group_factory()
        current_user = user_factory(group=group, user_sub="auth0|testuser")
        other_user = user_factory(group=group, user_sub="auth0|other")
        tag = tag_factory(user_sub=current_user.user_sub, name="favorite")
        older_structure = structure_factory(
            user_sub=current_user.user_sub,
            name="Older water",
            uploaded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            tags=[tag],
        )
        newer_structure = structure_factory(
            user_sub=current_user.user_sub,
            name="Newer water",
            uploaded_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        structure_factory(
            user_sub=current_user.user_sub,
            name="Deleted water",
            is_deleted=True,
            uploaded_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )
        structure_factory(
            user_sub=other_user.user_sub,
            name="Other user's water",
            uploaded_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
        )

        sql_statements.clear()
        response = client.get("/structures/")

        assert response.status_code == 200
        result = response.json()
        assert [structure["structure_id"] for structure in result] == [
            str(newer_structure.structure_id),
            str(older_structure.structure_id),
        ]
        assert [structure["name"] for structure in result] == [
            "Newer water",
            "Older water",
        ]
        assert result[1]["tags"] == ["favorite"]
        assert "content" not in result[0]
        assert "thumbnail" not in result[0]
        assert "imageS3URL" not in result[0]
        structure_query = next(
            statement
            for statement in sql_statements
            if "FROM structures" in statement
            and "structures.user_sub" in statement
        )
        assert "structures.content" not in structure_query
        assert "structures.thumbnail" not in structure_query

    def test_get_structure_by_id_returns_owned_structure(
        self, client, user_factory, tag_factory, structure_factory
    ):
        """
        GET /structures/{structure_id} should return a structure owned by the current user.
        """
        user_factory(user_sub="auth0|testuser")
        tag = tag_factory(user_sub="auth0|testuser", name="baseline")
        structure = structure_factory(
            user_sub="auth0|testuser",
            name="Water",
            formula="H2O",
            notes="owned structure",
            tags=[tag],
        )

        response = client.get(f"/structures/{structure.structure_id}")

        assert response.status_code == 200
        result = response.json()
        assert result["structure_id"] == str(structure.structure_id)
        assert result["name"] == "Water"
        assert result["formula"] == "H2O"
        assert result["content"] == structure.content
        assert result["thumbnail"] == {
            "media_type": "image/png",
            "base64": base64.b64encode(structure.thumbnail).decode("ascii"),
        }
        assert result["notes"] == "owned structure"
        assert result["uploaded_at"] == structure.uploaded_at.isoformat()
        assert result["group_id"] is None
        assert result["is_public"] is False
        assert result["user_sub"] == "auth0|testuser"
        assert result["tags"] == ["baseline"]

    def test_get_structure_by_id_returns_public_group_structure_to_member(
        self, client, set_auth_user, group_factory, user_factory, structure_factory
    ):
        """
        Normal group members can read public structures with a matching persisted group_id.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        viewer = user_factory(group=group, user_sub="auth0|viewer")
        structure = structure_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            is_public=True,
        )
        set_auth_user(make_auth0_payload(viewer.user_sub))

        response = client.get(f"/structures/{structure.structure_id}")

        assert response.status_code == 200
        result = response.json()
        assert result["structure_id"] == str(structure.structure_id)
        assert result["group_id"] == str(group.group_id)
        assert "user_sub" not in result

    def test_get_structure_by_id_returns_404_for_missing_structure(self, client):
        """
        GET /structures/{structure_id} should return 404 when the structure does not exist.
        """
        response = client.get(f"/structures/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Structure not found."

    def test_get_structure_by_id_returns_403_for_cross_user_structure(
        self, client, group_factory, user_factory, structure_factory
    ):
        """
        Users should not be able to fetch another user's structure by ID.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser")
        other_user = user_factory(group=group, user_sub="auth0|other")
        structure = structure_factory(user_sub=other_user.user_sub)

        response = client.get(f"/structures/{structure.structure_id}")

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions"

    def test_get_structure_by_id_returns_404_for_invalid_id(self, client):
        """
        Invalid structure IDs should behave like missing structures.
        """
        response = client.get("/structures/not-a-uuid")

        assert response.status_code == 404
        assert response.json()["detail"] == "Structure not found."

    def test_get_user_tags_returns_only_current_users_tags(
        self, client, group_factory, user_factory, tag_factory
    ):
        """
        GET /structures/tags should return tag names for the current user only.
        """
        group = group_factory()
        current_user = user_factory(group=group, user_sub="auth0|testuser")
        other_user = user_factory(group=group, user_sub="auth0|other")
        tag_factory(user_sub=current_user.user_sub, name=" Alpha ")
        tag_factory(user_sub=current_user.user_sub, name="BETA")
        tag_factory(user_sub=other_user.user_sub, name="other")

        response = client.get("/structures/tags")

        assert response.status_code == 200
        assert sorted(response.json()) == ["alpha", "beta"]

    def test_get_user_tags_returns_empty_list_when_user_has_no_tags(self, client):
        """
        GET /structures/tags should return an empty list for users without tags.
        """
        response = client.get("/structures/tags")

        assert response.status_code == 200
        assert response.json() == []

    def test_owner_can_soft_delete_structure(
        self, client, db, user_factory, structure_factory
    ):
        """
        DELETE /structures/{structure_id} should soft-delete an owned structure.
        """
        user_factory(user_sub="auth0|testuser")
        structure = structure_factory(user_sub="auth0|testuser", is_deleted=False)

        response = client.delete(f"/structures/{structure.structure_id}")

        assert response.status_code == 204
        db.refresh(structure)
        assert structure.is_deleted is True

    def test_delete_structure_returns_404_for_missing_structure(self, client):
        """
        DELETE /structures/{structure_id} should return 404 when the structure is missing.
        """
        response = client.delete(f"/structures/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Structure not found."

    def test_delete_structure_returns_403_for_cross_user_structure(
        self, client, group_factory, user_factory, structure_factory
    ):
        """
        Users should not be able to delete another user's structure.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser")
        other_user = user_factory(group=group, user_sub="auth0|other")
        structure = structure_factory(user_sub=other_user.user_sub, is_deleted=False)

        response = client.delete(f"/structures/{structure.structure_id}")

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions"

    def test_delete_structure_returns_404_for_invalid_id(self, client):
        """
        Invalid structure IDs should not produce a server error.
        """
        response = client.delete("/structures/not-a-uuid")

        assert response.status_code == 404
        assert response.json()["detail"] == "Structure not found."

    def test_group_admin_can_soft_delete_group_structure(
        self, client, db, set_auth_user, group_factory, user_factory, structure_factory
    ):
        """
        Group admins can soft-delete structures with their persisted group_id.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        structure = structure_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            is_deleted=False,
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.delete(f"/structures/{structure.structure_id}")

        assert response.status_code == 204
        db.refresh(structure)
        assert structure.is_deleted is True

    def test_delete_structure_rolls_back_when_commit_fails(
        self, client, db, monkeypatch, user_factory, structure_factory
    ):
        """
        DELETE /structures/{structure_id} should roll back if the DB commit fails.
        """
        user_factory(user_sub="auth0|testuser")
        structure = structure_factory(user_sub="auth0|testuser", is_deleted=False)

        def fail_commit():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)

        response = client.delete(f"/structures/{structure.structure_id}")

        assert response.status_code == 500
        assert response.json()["detail"] == "Could not save changes"
        db.refresh(structure)
        assert structure.is_deleted is False

    def test_owner_can_update_structure_and_replace_tags(
        self, client, db, user_factory, tag_factory, structure_factory
    ):
        """
        PATCH /structures/{structure_id} should update fields and replace tag relationships.
        """
        user_factory(user_sub="auth0|testuser")
        old_tag = tag_factory(user_sub="auth0|testuser", name="old")
        existing_tag = tag_factory(user_sub="auth0|testuser", name="existing")
        structure = structure_factory(
            user_sub="auth0|testuser",
            name="Original",
            formula="H2O",
            notes="before",
            tags=[old_tag],
        )

        response = client.patch(
            f"/structures/{structure.structure_id}",
            data={
                "name": "Updated",
                "formula": "CO2",
                "notes": "after",
                "tags": ["existing", "new"],
                "replace_tags": "true",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["structure_id"] == str(structure.structure_id)
        assert result["name"] == "Updated"
        assert result["formula"] == "CO2"
        assert result["notes"] == "after"
        assert result["group_id"] is None
        assert result["is_public"] is False
        assert result["user_sub"] == "auth0|testuser"
        assert sorted(result["tags"]) == ["existing", "new"]

        db.refresh(structure)
        assert structure.name == "Updated"
        assert structure.formula == "CO2"
        assert structure.notes == "after"
        assert sorted(tag.name for tag in structure.tags) == ["existing", "new"]

        existing_tags = db.query(Tags).filter_by(user_sub="auth0|testuser", name="existing").all()
        assert [tag.tag_id for tag in existing_tags] == [existing_tag.tag_id]
        assert db.query(Tags).filter_by(user_sub="auth0|testuser", name="new").one()

    def test_owner_adds_structure_tags_by_default(
        self,
        client,
        db,
        user_factory,
        tag_factory,
        structure_factory,
    ):
        """Supplied structure tags are additive unless replacement is requested."""
        user = user_factory(user_sub="auth0|testuser")
        old_tag = tag_factory(user_sub=user.user_sub, name="old")
        structure = structure_factory(
            user_sub=user.user_sub,
            name="Original",
            formula="H2O",
            tags=[old_tag],
        )

        response = client.patch(
            f"/structures/{structure.structure_id}",
            data={
                "name": "Updated",
                "formula": "CO2",
                "tags": ["New"],
            },
        )

        assert response.status_code == 200
        assert sorted(response.json()["tags"]) == ["new", "old"]
        db.refresh(structure)
        assert sorted(tag.name for tag in structure.tags) == ["new", "old"]

    def test_owner_clears_structure_tags_with_replacement(
        self,
        client,
        db,
        user_factory,
        tag_factory,
        structure_factory,
    ):
        """An empty replacement list clears every tag linked to the structure."""
        user = user_factory(user_sub="auth0|testuser")
        old_tag = tag_factory(user_sub=user.user_sub, name="old")
        structure = structure_factory(
            user_sub=user.user_sub,
            name="Original",
            formula="H2O",
            tags=[old_tag],
        )

        response = client.patch(
            f"/structures/{structure.structure_id}",
            data={
                "name": "Updated",
                "formula": "CO2",
                "replace_tags": "true",
            },
        )

        assert response.status_code == 200
        assert response.json()["tags"] == []
        db.refresh(structure)
        assert structure.tags == []

    def test_group_admin_can_update_group_structure(
        self,
        client,
        db,
        set_auth_user,
        group_factory,
        user_factory,
        tag_factory,
        structure_factory,
    ):
        """
        Group admins can update structures with their persisted group_id.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        owner_tag = tag_factory(user_sub=owner.user_sub, name="keep")
        structure = structure_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            name="Original",
            formula="H2O",
            tags=[owner_tag],
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.patch(
            f"/structures/{structure.structure_id}",
            data={"name": "Updated", "formula": "CO2"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Updated"
        assert response.json()["user_sub"] == owner.user_sub
        assert response.json()["tags"] == ["keep"]
        db.refresh(structure)
        assert structure.name == "Updated"
        assert structure.formula == "CO2"
        assert structure.tags == [owner_tag]

    def test_owner_can_update_user_owned_structure_visibility(
        self, client, db, user_factory, structure_factory
    ):
        """
        Direct owners can change visibility for user-only structures.
        """
        user_factory(user_sub="auth0|testuser")
        structure = structure_factory(user_sub="auth0|testuser", group_id=None, is_public=False)

        response = client.patch(
            f"/structures/{structure.structure_id}/visibility",
            data={"is_public": "true"},
        )

        assert response.status_code == 200
        assert response.json()["is_public"] is True
        db.refresh(structure)
        assert structure.is_public is True

    def test_owner_cannot_update_co_owned_structure_visibility(
        self, client, db, group_factory, user_factory, structure_factory
    ):
        """
        Direct user co-owners cannot change visibility for group-owned structures.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|testuser")
        structure = structure_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            is_public=False,
        )

        response = client.patch(
            f"/structures/{structure.structure_id}/visibility",
            data={"is_public": "true"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions"
        db.refresh(structure)
        assert structure.is_public is False

    def test_group_admin_can_update_group_structure_visibility(
        self, client, db, set_auth_user, group_factory, user_factory, structure_factory
    ):
        """
        Group admins can change visibility for structures with their group_id.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        structure = structure_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            is_public=False,
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.patch(
            f"/structures/{structure.structure_id}/visibility",
            data={"is_public": "true"},
        )

        assert response.status_code == 200
        assert response.json()["is_public"] is True
        db.refresh(structure)
        assert structure.is_public is True

    def test_update_structure_returns_404_for_missing_structure(self, client):
        """
        PATCH /structures/{structure_id} should return 404 when the structure is missing.
        """
        response = client.patch(
            f"/structures/{uuid.uuid4()}",
            data={"name": "Updated", "formula": "CO2"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Structure not found."

    def test_update_structure_rolls_back_when_commit_fails(
        self, client, db, monkeypatch, user_factory, tag_factory, structure_factory
    ):
        """
        PATCH /structures/{structure_id} should roll back field and tag changes on commit failure.
        """
        user_factory(user_sub="auth0|testuser")
        old_tag = tag_factory(user_sub="auth0|testuser", name="old")
        structure = structure_factory(
            user_sub="auth0|testuser",
            name="Original",
            formula="H2O",
            notes="before",
            tags=[old_tag],
        )

        def fail_commit():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)

        response = client.patch(
            f"/structures/{structure.structure_id}",
            data={
                "name": "Updated",
                "formula": "CO2",
                "notes": "after",
                "tags": ["new"],
            },
        )

        assert response.status_code == 500
        assert "Could not update structure" in response.json()["detail"]
        db.refresh(structure)
        assert structure.name == "Original"
        assert structure.formula == "H2O"
        assert structure.notes == "before"
        assert [tag.name for tag in structure.tags] == ["old"]

    def test_update_structure_returns_403_for_cross_user_structure(
        self, client, group_factory, user_factory, structure_factory
    ):
        """
        Users should not be able to update another user's structure.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser")
        other_user = user_factory(group=group, user_sub="auth0|other")
        structure = structure_factory(user_sub=other_user.user_sub)

        response = client.patch(
            f"/structures/{structure.structure_id}",
            data={"name": "Updated", "formula": "CO2"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions"

    def test_update_structure_returns_404_for_invalid_id(self, client):
        """
        Invalid structure IDs should not produce a server error.
        """
        response = client.patch(
            "/structures/not-a-uuid",
            data={"name": "Updated", "formula": "CO2"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Structure not found."

    def test_formula_returns_ase_formula_and_removes_temp_file(
        self, client, monkeypatch, tmp_path
    ):
        """
        POST /structures/formula should return the ASE formula and clean up its temp file.
        """
        import structures.routes as structures_routes

        monkeypatch.chdir(tmp_path)
        read_calls = []

        def fake_read(path):
            read_calls.append(path)
            return SimpleNamespace(get_chemical_formula=lambda: "H2O")

        monkeypatch.setattr(structures_routes, "read", fake_read)

        response = client.post(
            "/structures/formula",
            files=_structure_file(content=b"water xyz"),
        )

        assert response.status_code == 200
        assert response.json() == {"formula": "H2O"}
        assert len(read_calls) == 1
        assert not list(tmp_path.glob("temp_*.xyz"))

    def test_formula_falls_back_to_pymatgen_and_removes_temp_file(
        self, client, monkeypatch, tmp_path
    ):
        """
        POST /structures/formula should use Pymatgen when ASE cannot parse the file.
        """
        import structures.routes as structures_routes

        monkeypatch.chdir(tmp_path)
        pymatgen_calls = []

        def fake_read(_path):
            raise ValueError("ASE failed")

        def fake_from_file(path):
            pymatgen_calls.append(path)
            return SimpleNamespace(composition=SimpleNamespace(reduced_formula="CO2"))

        monkeypatch.setattr(structures_routes, "read", fake_read)
        monkeypatch.setattr(structures_routes.Molecule, "from_file", fake_from_file)

        response = client.post(
            "/structures/formula",
            files=_structure_file(content=b"co2 xyz"),
        )

        assert response.status_code == 200
        assert response.json() == {"formula": "CO2"}
        assert len(pymatgen_calls) == 1
        assert not list(tmp_path.glob("temp_*.xyz"))

    def test_formula_returns_400_and_removes_temp_file_when_parsing_fails(
        self, client, monkeypatch, tmp_path
    ):
        """
        Invalid molecular files should return 400 and still clean up the temp file.
        """
        import structures.routes as structures_routes

        monkeypatch.chdir(tmp_path)

        def fake_read(_path):
            raise ValueError("ASE failed")

        def fake_from_file(_path):
            raise ValueError("Pymatgen failed")

        monkeypatch.setattr(structures_routes, "read", fake_read)
        monkeypatch.setattr(structures_routes.Molecule, "from_file", fake_from_file)

        response = client.post(
            "/structures/formula",
            files=_structure_file(content=b"not a molecule"),
        )

        assert response.status_code == 400
        assert response.json()["detail"].startswith("Could not calculate formula:")
        assert not list(tmp_path.glob("temp_*.xyz"))

    def test_create_structure_stores_content_thumbnail_and_links_tags(
        self,
        client,
        db,
        group_factory,
        user_factory,
        tag_factory,
    ):
        """
        POST /structures/ should persist structure data and tags without using S3.
        """
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        existing_tag = tag_factory(user_sub=user.user_sub, name="existing")

        response = client.post(
            "/structures/",
            data={
                "name": "Water",
                "formula": "H2O",
                "notes": "created structure",
                "tags": ["existing", "new"],
            },
            files=_structure_upload_files(
                filename="../unsafe/input.xyz",
                content=b"saved structure content",
                image_content=b"saved image content",
            ),
        )

        assert response.status_code == 200
        result = response.json()
        structure_id = uuid.UUID(result["structure_id"])
        assert result["name"] == "Water"
        assert result["formula"] == "H2O"
        assert result["notes"] == "created structure"
        assert result["content"] == "saved structure content"
        assert result["thumbnail"] == {
            "media_type": "image/png",
            "base64": base64.b64encode(b"saved image content").decode("ascii"),
        }
        assert result["user_sub"] == user.user_sub
        assert result["group_id"] == str(group.group_id)
        assert result["is_public"] is False
        assert sorted(result["tags"]) == ["existing", "new"]

        structure = db.query(Structure).filter_by(structure_id=structure_id).one()
        assert structure.user_sub == user.user_sub
        assert structure.group_id == group.group_id
        assert structure.name == "Water"
        assert structure.formula == "H2O"
        assert structure.notes == "created structure"
        assert structure.content == "saved structure content"
        assert structure.thumbnail == b"saved image content"
        assert structure.thumbnail_media_type == "image/png"
        assert structure.is_deleted is False
        assert sorted(tag.name for tag in structure.tags) == ["existing", "new"]

        existing_tags = db.query(Tags).filter_by(user_sub=user.user_sub, name="existing").all()
        assert [tag.tag_id for tag in existing_tags] == [existing_tag.tag_id]
        assert db.query(Tags).filter_by(user_sub=user.user_sub, name="new").one()

    def test_create_structure_rolls_back_when_commit_fails(
        self, client, db, monkeypatch, user_factory
    ):
        """
        POST /structures/ should not leave database rows if the commit fails.
        """
        user_factory(user_sub="auth0|testuser")

        def fail_commit():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)

        response = client.post(
            "/structures/",
            data={
                "name": "Water",
                "formula": "H2O",
                "notes": "created structure",
                "tags": ["new"],
            },
            files=_structure_upload_files(content=b"saved structure content"),
        )

        assert response.status_code == 500
        assert "Could not create structure" in response.json()["detail"]
        assert db.query(Structure).count() == 0
        assert db.query(Tags).filter_by(user_sub="auth0|testuser", name="new").first() is None

    def test_create_structure_rejects_non_utf8_content(
        self,
        client,
        user_factory,
    ):
        user_factory(user_sub="auth0|testuser")

        response = client.post(
            "/structures/",
            data={"name": "Invalid", "formula": "?"},
            files=_structure_upload_files(content=b"\xff\xfe"),
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "Structure file must be valid UTF-8 text"
        )

    def test_create_structure_rejects_empty_thumbnail(
        self,
        client,
        user_factory,
    ):
        user_factory(user_sub="auth0|testuser")

        response = client.post(
            "/structures/",
            data={"name": "No thumbnail", "formula": "H"},
            files=_structure_upload_files(image_content=b""),
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "Structure thumbnail must not be empty"
        )
