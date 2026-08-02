import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from core.files.file_upload_client import S3FileUploader


def make_upload_file(
    filename: str = "test.png",
    content: bytes = b"fake-image-bytes",
    content_type: str = "image/png",
    size: int | None = None,
) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(content),
        size=size if size is not None else len(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


@pytest.fixture(autouse=True)
def aws_settings(monkeypatch):
    monkeypatch.setattr("config.settings.AWS_BUCKET_NAME", "test-bucket")
    monkeypatch.setattr("config.settings.AWS_REGION", "eu-west-1")
    monkeypatch.setattr("config.settings.FILE_URL_EXPIRATION_SECONDS", 900)
    monkeypatch.setattr("config.settings.ALLOWED_EXTENSIONS", ["png", "jpg"])
    monkeypatch.setattr("config.settings.FILE_MAX_UPLOAD_SIZE", 1_000_000)


@pytest.fixture
def mock_boto_client():
    with patch("core.files.file_upload_client.boto3.client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client_factory.return_value = mock_client
        yield mock_client_factory, mock_client


def test_get_client_uses_configured_region(mock_boto_client):
    mock_client_factory, _ = mock_boto_client

    S3FileUploader.get_client()

    mock_client_factory.assert_called_once_with("s3", region_name="eu-west-1")


async def test_upload_file_puts_object_with_expected_params(mock_boto_client):
    _, mock_client = mock_boto_client
    uploader = S3FileUploader(make_upload_file())

    await uploader.upload_file()

    mock_client.put_object.assert_called_once_with(
        Bucket="test-bucket",
        Key="test.png",
        Body=b"fake-image-bytes",
        ContentType="image/png",
    )


def test_delete_file_calls_delete_object(mock_boto_client):
    _, mock_client = mock_boto_client

    S3FileUploader.delete_file("test.png")

    mock_client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="test.png")


def test_get_url_returns_presigned_url(mock_boto_client):
    _, mock_client = mock_boto_client
    mock_client.generate_presigned_url.return_value = "https://example.com/signed"

    url = S3FileUploader.get_url("test.png")

    assert url == "https://example.com/signed"
    mock_client.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "test-bucket", "Key": "test.png"},
        ExpiresIn=900,
    )


def test_validate_file_rejects_unsupported_extension(mock_boto_client):
    with pytest.raises(HTTPException) as exc_info:
        S3FileUploader(make_upload_file(filename="test.exe"))

    assert exc_info.value.status_code == 400


def test_validate_file_rejects_oversized_file(mock_boto_client, monkeypatch):
    monkeypatch.setattr("config.settings.FILE_MAX_UPLOAD_SIZE", 10)

    with pytest.raises(HTTPException) as exc_info:
        S3FileUploader(make_upload_file(content=b"x" * 100))

    assert exc_info.value.status_code == 400
