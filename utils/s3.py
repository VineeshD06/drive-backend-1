import boto3, os
from dotenv import load_dotenv

load_dotenv()

s3_client = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION"),
)

BUCKET_NAME = os.getenv("AWS_S3_BUCKET")
BUCKET_REGION = os.getenv("AWS_REGION")

def upload_to_s3(local_path: str, content_type:str , s3_key: str):
    """Uploads file to S3 with given key"""
    s3_client.upload_file(local_path, BUCKET_NAME, s3_key,   ExtraArgs={
        "ContentType": content_type,
        "ContentDisposition": "inline",
    })
    return f"https://{BUCKET_NAME}.s3.{BUCKET_REGION}.amazonaws.com/{s3_key}"


def delete_from_s3(s3_key: str):
    """Deletes a file from S3 using its key"""

    try:
        print("deleting from s3",s3_key)
        s3_client.delete_object(Bucket=BUCKET_NAME, Key=s3_key)
        return True
    except Exception as e:
        print(f"Error deleting {s3_key} from S3: {e}")
        return False

def rename_in_s3(old_s3_key: str,new_s3_key: str):
    try:
        # Copy old object to new key
        s3_client.copy_object(
            Bucket=BUCKET_NAME,
            CopySource={'Bucket': BUCKET_NAME, 'Key': old_s3_key},
            Key=new_s3_key
        )
        # Delete old object
        s3_client.delete_object(Bucket=BUCKET_NAME, Key=old_s3_key)
    except Exception as e:
        print(f"Error renaming file in S3: {str(e)}")