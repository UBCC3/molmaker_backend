from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from models import Structure, Tags


class FakeS3Client:
    """
    Small fake for structure list presigned image URLs.
    """

    def __init__(self):
        self.calls = []
        self.upload_file_calls = []
        self.upload_fileobj_calls = []

    def generate_presigned_url(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        key = kwargs["Params"]["Key"]
        return f"presigned:{key}"

    def upload_file(self, local_file_path, bucket, key):
        self.upload_file_calls.append((local_file_path, bucket, key))

    def upload_fileobj(self, fileobj, bucket, key):
        position = fileobj.tell()
        content = fileobj.read()
        fileobj.seek(position)
        self.upload_fileobj_calls.append((content, bucket, key))


def _mock_structure_s3(monkeypatch):
    import structures.routes as structures_routes

    fake_s3 = FakeS3Client()
    monkeypatch.setattr(structures_routes, "s3", fake_s3)
    monkeypatch.setattr(structures_routes, "S3_BUCKET_NAME", "test-bucket")
    return fake_s3


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


class TestStructuresAPI:
    def test_list_structures_returns_current_users_non_deleted_structures_newest_first(
        self,
        client,
        monkeypatch,
        group_factory,
        user_factory,
        tag_factory,
        structure_factory,
    ):
        """
        GET /structures/ should only return current user's non-deleted structures newest first.
        """
        fake_s3 = _mock_structure_s3(monkeypatch)
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
        assert result[0]["imageS3URL"] == f"presigned:structures/{newer_structure.structure_id}.png"
        assert result[1]["imageS3URL"] == f"presigned:structures/{older_structure.structure_id}.png"
        assert result[1]["tags"] == ["favorite"]
        assert fake_s3.calls == [
            (
                ("get_object",),
                {
                    "Params": {
                        "Bucket": "test-bucket",
                        "Key": f"structures/{newer_structure.structure_id}.png",
                    },
                    "ExpiresIn": 3600,
                },
            ),
            (
                ("get_object",),
                {
                    "Params": {
                        "Bucket": "test-bucket",
                        "Key": f"structures/{older_structure.structure_id}.png",
                    },
                    "ExpiresIn": 3600,
                },
            ),
        ]

    def test_get_structure_by_id_returns_owned_structure(
        self, client, tag_factory, structure_factory
    ):
        """
        GET /structures/{structure_id} should return a structure owned by the current user.
        """
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
        assert result["location"] == structure.location
        assert result["notes"] == "owned structure"
        assert result["uploaded_at"] == structure.uploaded_at.isoformat()
        assert result["tags"] == ["baseline"]

    def test_get_structure_by_id_returns_404_for_missing_structure(self, client):
        """
        GET /structures/{structure_id} should return 404 when the structure does not exist.
        """
        response = client.get(f"/structures/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Structure not found."

    def test_get_structure_by_id_returns_404_for_cross_user_structure(
        self, client, group_factory, user_factory, structure_factory
    ):
        """
        Users should not be able to fetch another user's structure by ID.
        """
        group = group_factory()
        other_user = user_factory(group=group, user_sub="auth0|other")
        structure = structure_factory(user_sub=other_user.user_sub)

        response = client.get(f"/structures/{structure.structure_id}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Structure not found."

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
        tag_factory(user_sub=current_user.user_sub, name="alpha")
        tag_factory(user_sub=current_user.user_sub, name="beta")
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

    def test_owner_can_soft_delete_structure(self, client, db, structure_factory):
        """
        DELETE /structures/{structure_id} should soft-delete an owned structure.
        """
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

    def test_delete_structure_returns_404_for_cross_user_structure(
        self, client, group_factory, user_factory, structure_factory
    ):
        """
        Users should not be able to delete another user's structure.
        """
        group = group_factory()
        other_user = user_factory(group=group, user_sub="auth0|other")
        structure = structure_factory(user_sub=other_user.user_sub, is_deleted=False)

        response = client.delete(f"/structures/{structure.structure_id}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Structure not found."

    def test_delete_structure_returns_404_for_invalid_id(self, client):
        """
        Invalid structure IDs should not produce a server error.
        """
        response = client.delete("/structures/not-a-uuid")

        assert response.status_code == 404
        assert response.json()["detail"] == "Structure not found."

    def test_owner_can_update_structure_and_replace_tags(
        self, client, db, tag_factory, structure_factory
    ):
        """
        PATCH /structures/{structure_id} should update fields and replace tag relationships.
        """
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
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["structure_id"] == str(structure.structure_id)
        assert result["name"] == "Updated"
        assert result["formula"] == "CO2"
        assert result["notes"] == "after"
        assert sorted(result["tags"]) == ["existing", "new"]

        db.refresh(structure)
        assert structure.name == "Updated"
        assert structure.formula == "CO2"
        assert structure.notes == "after"
        assert sorted(tag.name for tag in structure.tags) == ["existing", "new"]

        existing_tags = db.query(Tags).filter_by(user_sub="auth0|testuser", name="existing").all()
        assert [tag.tag_id for tag in existing_tags] == [existing_tag.tag_id]
        assert db.query(Tags).filter_by(user_sub="auth0|testuser", name="new").one()

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

    def test_update_structure_returns_404_for_cross_user_structure(
        self, client, group_factory, user_factory, structure_factory
    ):
        """
        Users should not be able to update another user's structure.
        """
        group = group_factory()
        other_user = user_factory(group=group, user_sub="auth0|other")
        structure = structure_factory(user_sub=other_user.user_sub)

        response = client.patch(
            f"/structures/{structure.structure_id}",
            data={"name": "Updated", "formula": "CO2"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Structure not found."

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

    def test_create_structure_saves_uploads_persists_and_links_tags(
        self, client, db, monkeypatch, tmp_path, tag_factory
    ):
        """
        POST /structures/ should save files, upload to S3, persist the row, and link tags.
        """
        import structures.routes as structures_routes

        fake_s3 = _mock_structure_s3(monkeypatch)
        monkeypatch.setattr(structures_routes, "JOB_DIR", str(tmp_path))
        existing_tag = tag_factory(user_sub="auth0|testuser", name="existing")

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
        assert result["location"] == f"s3://test-bucket/structures/{structure_id}.xyz"
        assert sorted(result["tags"]) == ["existing", "new"]

        saved_file = tmp_path / str(structure_id) / "input.xyz"
        assert saved_file.read_bytes() == b"saved structure content"
        assert not (tmp_path / str(structure_id) / "unsafe").exists()
        assert fake_s3.upload_file_calls == [
            (
                str(saved_file),
                "test-bucket",
                f"structures/{structure_id}.xyz",
            )
        ]
        assert fake_s3.upload_fileobj_calls == [
            (
                b"saved image content",
                "test-bucket",
                f"structures/{structure_id}.png",
            )
        ]

        structure = db.query(Structure).filter_by(structure_id=structure_id).one()
        assert structure.user_sub == "auth0|testuser"
        assert structure.name == "Water"
        assert structure.formula == "H2O"
        assert structure.location == f"s3://test-bucket/structures/{structure_id}.xyz"
        assert structure.notes == "created structure"
        assert structure.is_deleted is False
        assert sorted(tag.name for tag in structure.tags) == ["existing", "new"]

        existing_tags = db.query(Tags).filter_by(user_sub="auth0|testuser", name="existing").all()
        assert [tag.tag_id for tag in existing_tags] == [existing_tag.tag_id]
        assert db.query(Tags).filter_by(user_sub="auth0|testuser", name="new").one()
