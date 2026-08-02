from datetime import timedelta

import boto3
from fastapi import HTTPException, UploadFile
from google.cloud.storage import Client


class FileUploader:
    def __init__(self, file: UploadFile):
        self.file = file
        self.validate_file()
        self.client = self.get_client()

    def validate_file(self):
        from config import settings

        if self.file.filename and self.file.filename.split(".")[-1] not in settings.ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail="Unsupported file format")
        if self.file.size and self.file.size > settings.FILE_MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=400, detail="File too large")

    async def upload_file(self):
        raise NotImplementedError

    @staticmethod
    def get_client():
        raise NotImplementedError

    @staticmethod
    def delete_file(filename: str):
        raise NotImplementedError

    @staticmethod
    def get_url(filename: str) -> str:
        raise NotImplementedError


class GcloudFileUploader(FileUploader):
    @staticmethod
    def get_client() -> Client:
        from config import settings

        return Client.from_service_account_json(settings.GCLOUD_KEY_FILE)

    async def upload_file(self):
        from config import settings

        self.validate_file()
        bucket = self.client.bucket(settings.GCLOUD_BUCKET_NAME)
        blob = bucket.blob(self.file.filename)
        contents = await self.file.read()
        blob.upload_from_string(contents, content_type=self.file.content_type)

    @staticmethod
    def delete_file(filename: str):
        from config import settings

        client = Client.from_service_account_json(settings.GCLOUD_KEY_FILE)
        bucket = client.bucket(settings.GCLOUD_BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.delete()

    @staticmethod
    def get_url(filename: str) -> str:
        from config import settings

        client = Client.from_service_account_json(settings.GCLOUD_KEY_FILE)
        bucket = client.bucket(settings.GCLOUD_BUCKET_NAME)
        blob = bucket.blob(filename)
        return blob.generate_signed_url(
            expiration=timedelta(seconds=settings.FILE_URL_EXPIRATION_SECONDS), method="GET"
        )


class S3FileUploader(FileUploader):
    @staticmethod
    def get_client():
        from config import settings

        return boto3.client("s3", region_name=settings.AWS_REGION)

    async def upload_file(self):
        from config import settings

        self.validate_file()
        print("Uploading file:")
        contents = await self.file.read()
        self.client.put_object(
            Bucket=settings.AWS_BUCKET_NAME,
            Key=self.file.filename,
            Body=contents,
            ContentType=self.file.content_type,
        )
        print("File uploaded successfully")

    @staticmethod
    def delete_file(filename: str):
        from config import settings

        client = boto3.client("s3", region_name=settings.AWS_REGION)
        client.delete_object(Bucket=settings.AWS_BUCKET_NAME, Key=filename)

    @staticmethod
    def get_url(filename: str) -> str:
        from config import settings

        client = boto3.client("s3", region_name=settings.AWS_REGION)
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.AWS_BUCKET_NAME, "Key": filename},
            ExpiresIn=settings.FILE_URL_EXPIRATION_SECONDS,
        )


class TestFileUploader(FileUploader):
    @staticmethod
    async def get_client():
        return None

    async def upload_file(self):
        return "FakeUrl"

    @staticmethod
    def delete_file(filename: str):
        return "Deleted"

    @staticmethod
    def get_url(filename: str) -> str:
        return "FakeUrl"


FILE_UPLOADER_CLASSES = {"google": GcloudFileUploader, "aws": S3FileUploader, "test": TestFileUploader}
