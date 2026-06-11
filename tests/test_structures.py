from datetime import datetime, timezone
import uuid


class FakeS3Client:
    """
    Small fake for structure list presigned image URLs.
    """

    def __init__(self):
        self.calls = []

    def generate_presigned_url(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        key = kwargs["Params"]["Key"]
        return f"presigned:{key}"


def _mock_structure_s3(monkeypatch):
    import structures.routes as structures_routes

    fake_s3 = FakeS3Client()
    monkeypatch.setattr(structures_routes, "s3", fake_s3)
    monkeypatch.setattr(structures_routes, "S3_BUCKET_NAME", "test-bucket")
    return fake_s3


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
