import boto3
import os
from botocore.exceptions import ClientError
from ..core.logging import setup_logger

logger = setup_logger(__name__)

class AWSService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.s3_client = boto3.client(
                's3',
                aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
            )
            self.bucket_name = os.getenv('AWS_BUCKET_NAME', 'explainai')
            self.initialized = True

    async def upload_file(self, file_content: bytes, file_name: str, content_type: str = 'application/pdf') -> str:
        """
        Upload a file to S3
        Returns the S3 path of the uploaded file
        """
        try:
            # Generate S3 path
            s3_path = f"documents/{file_name}"
            
            # Upload to S3
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_path,
                Body=file_content,
                ContentType=content_type
            )
            
            return s3_path
        except ClientError as e:
            logger.error(f"Error uploading file to S3: {e}")
            raise

    async def get_file(self, s3_path: str) -> bytes:
        """
        Retrieve a file from S3
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=s3_path
            )
            return response['Body'].read()
        except ClientError as e:
            logger.error(f"Error retrieving file from S3: {e}")
            raise

    async def delete_file(self, s3_path: str):
        """
        Delete a file from S3
        """
        try:
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=s3_path
            )
        except ClientError as e:
            logger.error(f"Error deleting file from S3: {e}")
            raise
